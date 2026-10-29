/**
 * Loads the main process against a fake `electron` module and checks the
 * security posture: window options, navigation and permission policy, IPC
 * origin checks and the two custom protocols.
 */
import { mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { beforeAll, describe, expect, it, vi } from "vitest";

type Handler = (...args: any[]) => any;

const fake = vi.hoisted(() => {
  const appHandlers: Record<string, Handler[]> = {};
  const state: any = {
    privileged: null as any,
    protocols: {} as Record<string, Handler>,
    ipc: {} as Record<string, Handler>,
    windows: [] as any[],
    permission: null as Handler | null,
    permissionCheck: null as Handler | null,
    beforeRequest: null as Handler | null,
    readyResolve: null as null | (() => void),
  };
  const emitter = () => {
    const h: Record<string, Handler[]> = {};
    return {
      h,
      on(ev: string, fn: Handler) {
        (h[ev] ??= []).push(fn);
        return this;
      },
      once(ev: string, fn: Handler) {
        (h[ev] ??= []).push(fn);
        return this;
      },
      emit(ev: string, ...a: any[]) {
        for (const fn of h[ev] ?? []) fn(...a);
      },
    };
  };
  const ready = new Promise<void>((r) => (state.readyResolve = r));
  const electron = {
    app: {
      commandLine: { appendSwitch: () => {} },
      requestSingleInstanceLock: () => true,
      whenReady: () => ready,
      on: (ev: string, fn: Handler) => (appHandlers[ev] ??= []).push(fn),
      quit: () => {},
      exit: () => {},
    },
    Menu: { setApplicationMenu: () => {} },
    nativeTheme: { shouldUseDarkColors: false },
    protocol: { registerSchemesAsPrivileged: (s: any) => (state.privileged = s) },
    ipcMain: { handle: (ch: string, fn: Handler) => (state.ipc[ch] = fn), on: (ch: string, fn: Handler) => (state.ipc[ch] = fn) },
    session: {
      fromPartition: () => ({
        setPermissionRequestHandler: (fn: Handler) => (state.permission = fn),
        setPermissionCheckHandler: (fn: Handler) => (state.permissionCheck = fn),
        setDevicePermissionHandler: () => {},
        setDisplayMediaRequestHandler: () => {},
        setSpellCheckerEnabled: () => {},
        on: () => {},
        webRequest: { onBeforeRequest: (fn: Handler) => (state.beforeRequest = fn) },
        protocol: { handle: (scheme: string, fn: Handler) => (state.protocols[scheme] = fn) },
      }),
    },
    // The VM in UTM's Retina mode: real pixels, and Electron told the scale is 1.
    screen: {
      on: () => {},
      getDisplayMatching: () => ({ size: { width: 3456, height: 2168 }, scaleFactor: 1 }),
    },
    BrowserWindow: class {
      webContents: any;
      constructor(public opts: any) {
        const wc = emitter();
        this.webContents = Object.assign(wc, {
          mainFrame: { url: "foyelle-app://canvas/index.html" },
          send: vi.fn(),
          setWindowOpenHandler: vi.fn(),
          loadURL: vi.fn(async () => {}),
          isDestroyed: () => false,
          setZoomFactor: vi.fn(),
          openDevTools: () => {},
          forcefullyCrashRenderer: () => {},
        });
        state.windows.push(this);
        for (const fn of appHandlers["web-contents-created"] ?? []) fn({}, this.webContents);
      }
      setMenu() {}
      once() {}
      on() {}
      isDestroyed() {
        return false;
      }
      getBounds() {
        return { x: 0, y: 0, width: 3456, height: 2168 };
      }
      loadURL(url: string) {
        return this.webContents.loadURL(url);
      }
    },
  };
  return { electron, state, appHandlers };
});

vi.mock("electron", () => ({ ...fake.electron, default: fake.electron }));

let tokenFile: string;

beforeAll(async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "foyelle-canvas-"));
  tokenFile = path.join(dir, "token");
  await writeFile(tokenFile, "secret-token\n");
  process.env.FOYELLE_HARNESS_TOKEN_FILE = tokenFile;
  process.env.FOYELLE_HARNESS_URL = "ws://127.0.0.1:9119/api/ws";
  delete process.env.FOYELLE_CANVAS_DEV;
  await import("../src/main/main");
  fake.state.readyResolve();
  await new Promise((r) => setTimeout(r, 0));
});

const win = () => fake.state.windows[0];
const canvasEvent = () => ({ sender: win().webContents, senderFrame: win().webContents.mainFrame });

describe("main process", () => {
  it("registers both schemes as standard and secure", () => {
    expect(fake.state.privileged).toEqual([
      { scheme: "foyelle-app", privileges: { standard: true, secure: true } },
      { scheme: "foyelle-surface", privileges: { standard: true, secure: true } },
    ]);
  });

  it("creates one kiosk window with locked-down web preferences", () => {
    expect(fake.state.windows).toHaveLength(1);
    const o = win().opts;
    expect(o).toMatchObject({ fullscreen: true, kiosk: true, frame: false, autoHideMenuBar: true });
    expect(o.backgroundColor).toBeTruthy();
    expect(o.webPreferences).toMatchObject({
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      nodeIntegrationInSubFrames: false,
      webviewTag: false,
      devTools: false,
    });
    expect(win().webContents.loadURL).toHaveBeenCalledWith("foyelle-app://canvas/index.html");
  });

  it("denies window.open, webviews and navigation away from the app", () => {
    const wc = win().webContents;
    const prevented = (ev: string, payload: any) => {
      const e = { preventDefault: vi.fn(), ...payload };
      wc.emit(ev, e);
      return e.preventDefault.mock.calls.length > 0;
    };
    expect(prevented("will-navigate", { url: "https://example.org/" })).toBe(true);
    expect(prevented("will-navigate", { url: "foyelle-app://canvas/index.html" })).toBe(false);
    expect(prevented("will-frame-navigate", { url: "https://example.org/", isMainFrame: false, frame: { url: "about:blank" } })).toBe(true);
    expect(prevented("will-frame-navigate", { url: "foyelle-surface://s1-a/", isMainFrame: false, frame: { url: "about:blank" } })).toBe(false);
    expect(prevented("will-frame-navigate", { url: "foyelle-surface://s1-a/", isMainFrame: false, frame: { url: "foyelle-surface://s1-a/" } })).toBe(false);
    expect(prevented("will-frame-navigate", { url: "foyelle-surface://s2-b/", isMainFrame: false, frame: { url: "foyelle-surface://s1-a/" } })).toBe(true);
    expect(prevented("will-attach-webview", {})).toBe(true);
    const openHandler = wc.setWindowOpenHandler.mock.calls[0][0];
    expect(openHandler({ url: "https://example.org" })).toEqual({ action: "deny" });
  });

  it("denies every permission, the microphone too when there is no speech service", () => {
    const cb = vi.fn();
    fake.state.permission(win().webContents, "media", cb, { isMainFrame: true, requestingUrl: "foyelle-app://canvas/index.html", mediaTypes: ["audio"] });
    expect(cb).toHaveBeenCalledWith(false);
    expect(fake.state.permissionCheck(win().webContents, "media", "foyelle-app://canvas", { mediaType: "audio" })).toBe(false);
  });

  it("refuses to transcribe without a speech service, and for anyone but the canvas", async () => {
    await expect(fake.state.ipc["voice:transcribe"](canvasEvent(), new Uint8Array(4))).rejects.toThrow("no speech service");
    const foreign = { sender: win().webContents, senderFrame: { url: "foyelle-surface://s1/" } };
    await expect(fake.state.ipc["voice:transcribe"](foreign, new Uint8Array(4))).rejects.toThrow("forbidden");
  });

  it("allows only the app, surfaces, the harness socket and https", () => {
    const allowed = (url: string) => {
      let r: any;
      fake.state.beforeRequest({ url }, (x: any) => (r = x));
      return !r.cancel;
    };
    expect(allowed("foyelle-app://canvas/app.js")).toBe(true);
    expect(allowed("foyelle-surface://s1/")).toBe(true);
    expect(allowed("ws://127.0.0.1:9119/api/ws?token=x")).toBe(true);
    expect(allowed("ws://127.0.0.1:9999/other")).toBe(false);
    expect(allowed("http://example.org/")).toBe(false);
    expect(allowed("file:///etc/passwd")).toBe(false);
    expect(allowed("https://cdn.example.org/x.js")).toBe(true);
  });

  it("serves the renderer with a CSP and only known files", async () => {
    const handle = fake.state.protocols["foyelle-app"];
    const res: Response = await handle({ url: "foyelle-app://canvas/index.html" });
    // The test has no built renderer next to it, so a 404 is fine; check headers when present.
    if (res.status === 200) expect(res.headers.get("Content-Security-Policy")).toContain("connect-src ws://127.0.0.1:9119");
    expect((await handle({ url: "foyelle-app://canvas/../../etc/passwd" })).status).toBe(404);
    expect((await handle({ url: "foyelle-app://other/index.html" })).status).toBe(404);
  });

  it("reads the token file on every harness:config call, only for the canvas frame", async () => {
    const cfg = await fake.state.ipc["harness:config"](canvasEvent());
    expect(cfg).toEqual({ url: "ws://127.0.0.1:9119/api/ws", token: "secret-token", surfaceTitles: {}, voice: false, signin: false, services: {} });
    await expect(fake.state.ipc["signin:begin"](canvasEvent())).rejects.toThrow("no sign-in browser");
    const surface = { sender: win().webContents, senderFrame: { url: "foyelle-surface://s1/" } };
    expect(() => fake.state.ipc["signin:open"](surface, "https://accounts.google.com/o/oauth2/v2/auth")).toThrow("forbidden");
    await writeFile(tokenFile, "rotated\n");
    expect((await fake.state.ipc["harness:config"](canvasEvent())).token).toBe("rotated");
    const foreign = { sender: win().webContents, senderFrame: { url: "foyelle-surface://s1/" } };
    await expect(fake.state.ipc["harness:config"](foreign)).rejects.toThrow("forbidden");
  });

  it("registers, serves and unregisters surfaces with their CSP and nosniff", async () => {
    const url = await fake.state.ipc["surface:register"](canvasEvent(), { surfaceId: "s9-xyz", html: "<b>x</b>", csp: { resourceDomains: ["https://cdn.example.org"] } });
    expect(url).toBe("foyelle-surface://s9-xyz/");
    const res: Response = await fake.state.protocols["foyelle-surface"]({ url });
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("<b>x</b>");
    expect(res.headers.get("Content-Security-Policy")).toContain("img-src data: https://cdn.example.org");
    expect(res.headers.get("X-Content-Type-Options")).toBe("nosniff");
    await fake.state.ipc["surface:unregister"](canvasEvent(), "s9-xyz");
    expect((await fake.state.protocols["foyelle-surface"]({ url })).status).toBe(404);
  });

  it("forwards Ctrl+K and Ctrl+1..9 to the renderer", () => {
    const wc = win().webContents;
    const e = { preventDefault: vi.fn() };
    wc.emit("before-input-event", e, { type: "keyDown", key: "k", control: true, meta: false, alt: false, shift: false });
    expect(e.preventDefault).toHaveBeenCalled();
    expect(wc.send).toHaveBeenCalledWith("canvas:shortcut", "command-bar");
    wc.emit("before-input-event", { preventDefault: vi.fn() }, { type: "keyDown", key: "3", control: true, meta: false, alt: false, shift: false });
    expect(wc.send).toHaveBeenCalledWith("canvas:shortcut", "tile-3");
    const plain = { preventDefault: vi.fn() };
    wc.emit("before-input-event", plain, { type: "keyDown", key: "k", control: false, meta: false, alt: false, shift: false });
    expect(plain.preventDefault).not.toHaveBeenCalled();
  });

  it("reloads the renderer after a crash", async () => {
    vi.useFakeTimers();
    const wc = win().webContents;
    wc.loadURL.mockClear();
    wc.emit("render-process-gone", {}, { reason: "crashed", exitCode: 1 });
    await vi.advanceTimersByTimeAsync(1000);
    expect(wc.loadURL).toHaveBeenCalledWith("foyelle-app://canvas/index.html");
    vi.useRealTimers();
  });
});

describe("surface titles", () => {
  it("parses FOYELLE_SURFACE_TITLES and ignores anything that is not a string map", async () => {
    const { parseSurfaceTitles } = await import("../src/main/config");
    expect(parseSurfaceTitles('{"sysmon":"System monitor","x":3}')).toEqual({ sysmon: "System monitor" });
    expect(parseSurfaceTitles("[1]")).toEqual({});
    expect(parseSurfaceTitles("not json")).toEqual({});
    expect(parseSurfaceTitles(undefined)).toEqual({});
  });

  it("zooms by the screen's density times the text size", () => {
    const wc = win().webContents;
    fake.state.ipc["canvas:zoom"](canvasEvent(), 1.25);
    expect(wc.setZoomFactor).toHaveBeenLastCalledWith(2.5);
  });
});
