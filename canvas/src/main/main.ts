/**
 * Foyelle canvas, main process.
 *
 * One full-screen kiosk window, no menu, no tray, no deep links, no updates.
 * The renderer is served from `foyelle-app://canvas/` with a strict CSP;
 * surface documents from `foyelle-surface://<id>/` with a per-surface CSP.
 */
import { app, BrowserWindow, ipcMain, Menu, nativeTheme, protocol, screen, session, type Session, type WebContents } from "electron";
import { watch } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { APP_HOST, APP_SCHEME, IPC, SURFACE_SCHEME, type ShortcutName } from "../shared/bridge";
import { buildCanvasCsp } from "../shared/csp";
import { harnessOrigin, loadHarnessConfig, readEnv, transcribe } from "./config";
import { zoomFor } from "./density";
import { SignInBrowser } from "./signin";
import { SurfaceStore } from "./surface-store";

const env = readEnv();
const signin = new SignInBrowser({ browser: env.signinBrowser, proxy: env.signinProxy, fake: env.fakeSignin, pages: env.signinPages });
const APP_ORIGIN = `${APP_SCHEME}://${APP_HOST}`;
const APP_URL = `${APP_ORIGIN}/index.html`;
const RENDERER_DIR = path.join(__dirname, "renderer");
const RENDERER_FILES: Record<string, string> = {
  "/": "index.html",
  "/index.html": "index.html",
  "/app.js": "app.js",
  "/app.css": "app.css",
  "/fonts/Nunito.ttf": "fonts/Nunito.ttf",
  "/fonts/Nunito-Italic.ttf": "fonts/Nunito-Italic.ttf",
  "/fonts/GochiHand-Regular.ttf": "fonts/GochiHand-Regular.ttf",
  "/fonts/JetBrainsMono.ttf": "fonts/JetBrainsMono.ttf",
  "/fonts/JetBrainsMono-Italic.ttf": "fonts/JetBrainsMono-Italic.ttf",
  "/fonts/DejaVuSansCondensed.ttf": "fonts/DejaVuSansCondensed.ttf",
  "/fonts/DejaVuSansCondensed-Bold.ttf": "fonts/DejaVuSansCondensed-Bold.ttf",
};
const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".ttf": "font/ttf",
};

const surfaces = new SurfaceStore();
let mainWindow: BrowserWindow | null = null;
let crashTimes: number[] = [];

// Must run before `ready`.
protocol.registerSchemesAsPrivileged([
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true } },
  { scheme: SURFACE_SCHEME, privileges: { standard: true, secure: true } },
]);

app.commandLine.appendSwitch(
  "disable-features",
  "HardwareMediaKeyHandling,MediaSessionService,OptimizationHints,MediaRouter,DialMediaRouteProvider,AutofillServerCommunication,CertificateTransparencyComponentUpdater,InterestFeedContentSuggestions,Translate",
);
// No Chromium background traffic: the canvas talks only to the harness on loopback. Without these,
// Chromium's component updater looked up redirector.gvt1.com at startup (end-to-end VM test).
for (const sw of [
  "disable-background-networking",
  "disable-component-update",
  "disable-domain-reliability",
  "disable-client-side-phishing-detection",
  "disable-sync",
  "disable-breakpad",
  "no-pings",
  "metrics-recording-only",
  "no-default-browser-check",
  "no-first-run",
]) {
  app.commandLine.appendSwitch(sw);
}
// And no name resolution at all: every host name fails inside Chromium without a DNS query. The
// canvas reaches only the harness by address and its own app:// and foyelle-surface:// schemes.
// The rules apply to IP literals too, so loopback must be excluded explicitly or the canvas cannot
// reach the harness at ws://127.0.0.1.
app.commandLine.appendSwitch("host-resolver-rules", "MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1, EXCLUDE [::1]");
if (env.dev) {
  // Dev only: keep painting while the window is behind others, so captures show the real state.
  app.commandLine.appendSwitch("disable-backgrounding-occluded-windows");
  app.commandLine.appendSwitch("disable-renderer-backgrounding");
}
Menu.setApplicationMenu(null);

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.whenReady().then(start).catch((err) => {
    console.error("[canvas] failed to start", err);
    app.exit(1);
  });
}

app.on("window-all-closed", () => app.quit());
app.on("will-quit", () => void signin.end());

// Hardening for every webContents, including any that might appear later.
app.on("web-contents-created", (_e, contents) => {
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
  contents.on("will-attach-webview", (e) => e.preventDefault());
  contents.on("will-navigate", (e) => {
    if (!isAppUrl(e.url)) e.preventDefault();
  });
  contents.on("will-redirect", (e) => {
    if (e.isMainFrame && !isAppUrl(e.url)) e.preventDefault();
  });
  contents.on("will-frame-navigate", (e) => {
    if (e.isMainFrame) {
      if (!isAppUrl(e.url)) e.preventDefault();
    } else {
      // A surface frame loads its own surface URL once (from about:blank) and
      // may reload it, but may not navigate to any other URL, including
      // another surface's.
      const current = e.frame?.url ?? "";
      const fresh = current === "" || current === "about:blank";
      if (!isSurfaceUrl(e.url) || (!fresh && surfaceKey(current) !== surfaceKey(e.url))) e.preventDefault();
    }
  });
});

/** A minute of 16 kHz 16-bit mono is under 2 MB. */
const MAX_WAV_BYTES = 4 * 1024 * 1024;

function isAppUrl(url: string): boolean {
  if (env.dev && url.startsWith("devtools://")) return true; // DevTools' own pages
  return url === APP_ORIGIN || url.startsWith(`${APP_ORIGIN}/`);
}

function isSurfaceUrl(url: string): boolean {
  return url.startsWith(`${SURFACE_SCHEME}://`);
}

function surfaceKey(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === `${SURFACE_SCHEME}:` ? u.hostname : null;
  } catch {
    return null;
  }
}

async function start(): Promise<void> {
  // In-memory partition: nothing the canvas or a surface does is persisted.
  const ses = session.fromPartition("foyelle-canvas");
  hardenSession(ses);
  registerProtocols(ses);
  registerIpc();
  createWindow(ses);
}

function hardenSession(ses: Session): void {
  // The one permission: the microphone, for the canvas itself (never a card), when this
  // computer has a speech service. Audio only.
  ses.setPermissionRequestHandler((wc, permission, callback, details) => {
    const media = details as Electron.MediaAccessPermissionRequest;
    callback(
      !!env.voiceUrl &&
        permission === "media" &&
        wc === mainWindow?.webContents &&
        details.isMainFrame &&
        isAppUrl(details.requestingUrl) &&
        (media.mediaTypes ?? []).every((t) => t === "audio"),
    );
  });
  ses.setPermissionCheckHandler((wc, permission, origin, details) =>
    !!env.voiceUrl &&
    permission === "media" &&
    details.mediaType === "audio" &&
    wc === mainWindow?.webContents &&
    details.isMainFrame !== false &&
    isAppUrl(details.requestingUrl ?? origin),
  );
  ses.setDevicePermissionHandler(() => false);
  ses.setDisplayMediaRequestHandler((_req, callback) => callback({}));
  ses.on("will-download", (e) => e.preventDefault());
  ses.setSpellCheckerEnabled(false);

  const harness = harnessOrigin(env.harnessUrl);
  ses.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: !isAllowedRequest(details.url, harness) });
  });
}

/** Network allowlist: the app, surfaces, the harness socket, and https (bounded per surface by its CSP). */
function isAllowedRequest(url: string, harness: string): boolean {
  if (url.startsWith(`${APP_SCHEME}:`) || url.startsWith(`${SURFACE_SCHEME}:`)) return true;
  if (url.startsWith("data:") || url.startsWith("blob:") || url === "about:blank") return true;
  if (url.startsWith("devtools:") || url.startsWith("chrome-extension:")) return env.dev;
  if (url.startsWith("ws:") || url.startsWith("wss:")) {
    try {
      const u = new URL(url);
      return `${u.protocol}//${u.host}` === harness;
    } catch {
      return false;
    }
  }
  return url.startsWith("https:");
}

function registerProtocols(ses: Session): void {
  const csp = buildCanvasCsp({
    connect: [harnessOrigin(env.harnessUrl)],
    frame: [`${SURFACE_SCHEME}:`],
  });

  ses.protocol.handle(APP_SCHEME, async (request) => {
    const u = new URL(request.url);
    const file = u.host === APP_HOST ? RENDERER_FILES[u.pathname] : undefined;
    if (!file) return new Response("not found", { status: 404 });
    try {
      const body = await readFile(path.join(RENDERER_DIR, file));
      return new Response(body, {
        status: 200,
        headers: {
          "Content-Type": MIME[path.extname(file)] ?? "application/octet-stream",
          "Content-Security-Policy": csp,
          "X-Content-Type-Options": "nosniff",
          "Referrer-Policy": "no-referrer",
          "Cache-Control": "no-store",
        },
      });
    } catch {
      return new Response("not found", { status: 404 });
    }
  });

  ses.protocol.handle(SURFACE_SCHEME, (request) => {
    const r = surfaces.resolve(request.url);
    return new Response(r.body, { status: r.status, headers: r.headers });
  });
}

/** IPC is accepted only from the canvas top-level frame. */
function fromCanvas(event: Electron.IpcMainInvokeEvent): boolean {
  const frame = event.senderFrame;
  return !!frame && frame === event.sender.mainFrame && isAppUrl(frame.url);
}

function registerIpc(): void {
  ipcMain.handle(IPC.harness, async (event) => {
    if (!fromCanvas(event)) throw new Error("forbidden");
    return loadHarnessConfig(env);
  });
  ipcMain.handle(IPC.registerSurface, (event, spec: unknown) => {
    if (!fromCanvas(event)) throw new Error("forbidden");
    return surfaces.register(spec);
  });
  ipcMain.handle(IPC.unregisterSurface, (event, surfaceId: unknown) => {
    if (!fromCanvas(event)) throw new Error("forbidden");
    surfaces.unregister(surfaceId);
  });
  ipcMain.handle(IPC.transcribe, async (event, wav: unknown) => {
    if (!fromCanvas(event)) throw new Error("forbidden");
    if (!env.voiceUrl) throw new Error("there is no speech service on this computer");
    if (!(wav instanceof Uint8Array) || wav.byteLength > MAX_WAV_BYTES) throw new Error("invalid recording");
    return transcribe(env.voiceUrl, wav);
  });
  ipcMain.handle(IPC.signinBegin, (event) => {
    if (!fromCanvas(event)) throw new Error("forbidden");
    return signin.begin();
  });
  ipcMain.handle(IPC.signinOpen, (event, authUrl: unknown) => {
    if (!fromCanvas(event)) throw new Error("forbidden");
    return signin.open(String(authUrl));
  });
  ipcMain.handle(IPC.signinEnd, (event) => {
    if (!fromCanvas(event)) throw new Error("forbidden");
    return signin.end();
  });
  ipcMain.on(IPC.zoom, (event, factor: unknown) => {
    const frame = event.senderFrame;
    if (!frame || frame !== event.sender.mainFrame || !isAppUrl(frame.url)) return;
    if (typeof factor === "number" && factor >= 0.75 && factor <= 2) {
      textScale = factor;
      rezoom();
    }
  });
}

/** The user's text size (Settings); the zoom also follows the screen's density (density.ts). */
let textScale = 1;

function rezoom(): void {
  const win = mainWindow;
  if (!win || win.isDestroyed()) return;
  win.webContents.setZoomFactor(zoomFor(textScale, screen.getDisplayMatching(win.getBounds())));
}

function createWindow(ses: Session): void {
  // Dev (a laptop, `npm run dev`): an ordinary window. Otherwise the whole screen, kiosk.
  const win = new BrowserWindow({
    fullscreen: !env.dev,
    kiosk: !env.dev,
    frame: env.dev,
    ...(env.dev ? { width: 1440, height: 900 } : {}),
    autoHideMenuBar: true,
    show: false,
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#121417" : "#f4f3ef",
    title: "Foyelle",
    webPreferences: {
      session: ses,
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      nodeIntegrationInSubFrames: false,
      nodeIntegrationInWorker: false,
      webviewTag: false,
      devTools: env.dev,
      spellcheck: false,
      navigateOnDragDrop: false,
      safeDialogs: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      experimentalFeatures: false,
      enableWebSQL: false,
      backgroundThrottling: !env.dev,
    },
  });
  mainWindow = win;
  win.setMenu(null);
  win.once("ready-to-show", () => win.show());
  win.on("closed", () => {
    mainWindow = null;
  });

  const wc = win.webContents;
  wc.on("did-finish-load", rezoom);
  // A resized VM window (UTM's dynamic resolution) or another screen: the density may change.
  screen.on("display-metrics-changed", rezoom);
  screen.on("display-added", rezoom);
  screen.on("display-removed", rezoom);
  win.on("resize", rezoom);
  wc.on("before-input-event", (event, input) => forwardShortcut(wc, event, input));
  wc.on("render-process-gone", (_e, details) => {
    console.error(`[canvas] renderer gone: ${details.reason} (exit ${details.exitCode})`);
    surfaces.clear();
    if (details.reason === "clean-exit") return;
    scheduleReload(wc);
  });
  let unresponsiveTimer: NodeJS.Timeout | null = null;
  wc.on("unresponsive", () => {
    unresponsiveTimer ??= setTimeout(() => {
      console.error("[canvas] renderer unresponsive for 10s, restarting it");
      wc.forcefullyCrashRenderer();
    }, 10_000);
  });
  wc.on("responsive", () => {
    if (unresponsiveTimer) clearTimeout(unresponsiveTimer);
    unresponsiveTimer = null;
  });
  wc.on("did-start-navigation", (details) => {
    // A full reload of the canvas drops every surface it had registered.
    if (details.isMainFrame && !details.isSameDocument) surfaces.clear();
  });
  if (env.devtools) wc.openDevTools({ mode: "detach" });
  if (env.dev) {
    // Dev screenshots for design review: `kill -USR2 <pid>` saves the window as a PNG.
    process.on("SIGUSR2", () => {
      const out = process.env.FOYELLE_CANVAS_CAPTURE || path.join(app.getPath("temp"), "foyelle-canvas.png");
      // A covered window stops painting, so force a fresh frame before capturing it.
      wc.invalidate();
      void new Promise((r) => setTimeout(r, 150))
        .then(() => wc.capturePage(undefined, { stayHidden: true, stayAwake: true }))
        .then((img) => writeFile(out, img.toPNG()))
        .then(() => console.log(`[canvas] saved ${out}`));
    });
    // Dev scripting for checking flows inside cards (sandboxed frames the browser pane can't load):
    // `kill -USR1 <pid>` runs {"frame": "main" | "<part of a frame URL>", "code": "..."} from
    // FOYELLE_CANVAS_EVAL and writes {"result"} or {"error"} next to it (.result.json).
    process.on("SIGUSR1", () => {
      const file = process.env.FOYELLE_CANVAS_EVAL;
      if (!file) return;
      const out = file.replace(/\.json$/, "") + ".result.json";
      void (async () => {
        try {
          const spec = JSON.parse(await readFile(file, "utf8")) as { frame?: string; code: string };
          const frame = !spec.frame || spec.frame === "main"
            ? wc.mainFrame
            : wc.mainFrame.framesInSubtree.find((f) => f.url.includes(spec.frame!));
          if (!frame) throw new Error(`no frame matching ${spec.frame}`);
          const result: unknown = await frame.executeJavaScript(spec.code, true);
          await writeFile(out, JSON.stringify({ result }, null, 1));
        } catch (err) {
          await writeFile(out, JSON.stringify({ error: String((err as Error).message ?? err) }));
        }
      })();
    });
    // Live reload: the dev runner rebuilds the renderer; reload when its files change.
    let timer: NodeJS.Timeout | null = null;
    watch(RENDERER_DIR, () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        if (!wc.isDestroyed()) wc.reloadIgnoringCache();
      }, 150);
    });
  }

  void win.loadURL(APP_URL);
}

/** Reloads after a crash, backing off if the renderer keeps crashing. */
function scheduleReload(wc: WebContents): void {
  const now = Date.now();
  crashTimes = [...crashTimes.filter((t) => now - t < 60_000), now];
  const delay = Math.min(30_000, 250 * 2 ** (crashTimes.length - 1));
  setTimeout(() => {
    if (!wc.isDestroyed()) void wc.loadURL(APP_URL);
  }, delay);
}

/**
 * Global shortcuts reach the canvas even when a surface iframe has keyboard
 * focus (the renderer cannot see keys typed into a cross-origin frame).
 */
function forwardShortcut(wc: WebContents, event: Electron.Event, input: Electron.Input): void {
  if (input.type !== "keyDown" || !(input.control || input.meta) || input.alt || input.shift) return;
  let name: ShortcutName | null = null;
  const key = input.key.toLowerCase();
  if (key === "k") name = "command-bar";
  else if (/^[1-9]$/.test(key)) name = `tile-${key}` as ShortcutName;
  if (!name) return;
  event.preventDefault();
  wc.send(IPC.shortcut, name);
}
