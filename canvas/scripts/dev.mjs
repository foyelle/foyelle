// `npm run dev`: the real canvas in Electron on this laptop, against a mock harness backed by the
// real connectors on sample data. No VM, no model, no network. Edits reload in about a second:
//
//   src/renderer, src/shared   rebuild; the window reloads
//   src/main, src/preload      rebuild; Electron restarts
//   dev/*.ts                   the mock harness restarts (the canvas reconnects)
//   connectors/ (Python, views) the tools host restarts
//
//   npm run dev                 start (keeps the sample data in canvas/.dev-state)
//   npm run dev -- --reset      start from fresh sample data
//   FOYELLE_CANVAS_DEVTOOLS=1 npm run dev   also open the developer tools
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { watch } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repo = path.resolve(root, "..");
const state = path.join(root, ".dev-state");
const reset = process.argv.includes("--reset");
const TOOLS_PORT = 5175;
const HARNESS_PORT = 5174;
const token = randomBytes(16).toString("hex");
const tokenFile = path.join(state, "harness-token");

const children = new Map();
function start(name, cmd, args, opts = {}) {
  const child = spawn(cmd, args, { stdio: ["ignore", "inherit", "inherit"], ...opts });
  children.set(name, child);
  child.on("exit", (code, signal) => {
    if (children.get(name) === child) {
      children.delete(name);
      if (!stopping && signal !== "SIGTERM") console.log(`[dev] ${name} exited (${code ?? signal})`);
    }
  });
  return child;
}
function stop(name) {
  const child = children.get(name);
  if (!child) return Promise.resolve();
  children.delete(name);
  return new Promise((resolve) => {
    child.once("exit", resolve);
    child.kill("SIGTERM");
    setTimeout(resolve, 2000).unref();
  });
}
function run(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { stdio: "inherit", cwd: root, ...opts });
    child.on("exit", (code) => resolve(code === 0));
  });
}
async function waitFor(url, seconds = 60) {
  for (let i = 0; i < seconds * 2; i++) {
    try {
      if ((await fetch(url)).ok) return true;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

const tools = () =>
  start("tools", "uvx", ["--from", "uv==0.13.1", "uv", "run", "--quiet", "--project", path.join(repo, "connectors"),
    "--python", "3.14", "python", path.join(root, "dev", "tools_host.py"), ...(reset ? ["--reset"] : [])], {
    // No bytecode caches in the source tree: their writes would look like source changes.
    env: { ...process.env, FOYELLE_TOOLS_PORT: String(TOOLS_PORT), PYTHONDONTWRITEBYTECODE: "1" },
  });
const harness = () =>
  start("harness", process.execPath, [path.join(root, "dist-dev", "dev-server.mjs")], {
    env: { ...process.env, PORT: String(HARNESS_PORT), FOYELLE_DEV_TOKEN: token, FOYELLE_TOOLS_URL: `http://127.0.0.1:${TOOLS_PORT}` },
  });
const electron = () =>
  start("electron", path.join(root, "node_modules", ".bin", "electron"), [path.join(root, "dist", "main.js")], {
    env: {
      ...process.env,
      FOYELLE_CANVAS_DEV: "1",
      FOYELLE_CANVAS_CAPTURE: path.join(state, "canvas.png"),
      FOYELLE_CANVAS_EVAL: path.join(state, "eval.json"),
      FOYELLE_HARNESS_URL: `ws://127.0.0.1:${HARNESS_PORT}/api/ws`,
      FOYELLE_HARNESS_TOKEN_FILE: tokenFile,
      FOYELLE_SURFACE_TITLES: JSON.stringify({ sysmon: "System monitor", workshop: "Workshop" }),
      // The mock's stand-in for the speech service: the recording is dropped, a sample request returns.
      FOYELLE_VOICE_URL: `http://127.0.0.1:${HARNESS_PORT}/__dev/transcribe`,
      // No sign-in browser: the canvas answers Google's sign-in itself (the tools host fakes Google).
      FOYELLE_DEV_FAKE_SIGNIN: process.env.FOYELLE_DEV_FAKE_SIGNIN ?? "1",
      // A service with an account, as bridges.nix sets it up (the tools host stands in for Todoist).
      FOYELLE_SERVICES: JSON.stringify({ todoist: "Todoist" }),
      FOYELLE_SIGNIN_PAGES: JSON.stringify(["https://accounts.google.com/o/oauth2/v2/auth", "https://todoist.com/oauth/authorize"]),
    },
  });

let stopping = false;
async function shutdown() {
  stopping = true;
  await Promise.all([...children.keys()].map(stop));
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

await mkdir(state, { recursive: true });
console.log("[dev] building the canvas and the mock harness…");
if (!(await run(process.execPath, ["scripts/build.mjs"])) || !(await run(process.execPath, ["scripts/build.mjs", "--web"]))) {
  console.error("[dev] build failed");
  process.exit(1);
}
tools();
if (!(await waitFor(`http://127.0.0.1:${TOOLS_PORT}/health`, 120))) console.error("[dev] the tools host did not start; surfaces fall back to demos");
// After the tools host: --reset empties the state folder this file lives in.
await mkdir(state, { recursive: true });
await writeFile(tokenFile, token, { mode: 0o600 });
harness();
await waitFor(`http://127.0.0.1:${HARNESS_PORT}/`);
electron();
console.log(`
[dev] Foyelle canvas is running in a window. Try: "show my files", "what do you remember",
      "settings", "changes waiting", "what did you do", "send the report to Sam", or /surfaces.
      Edit canvas/src, canvas/dev or connectors/ and it reloads. Ctrl-C stops everything.
`);

// Rebuild and reload on changes.
let pending = new Set();
let timer = null;
function changed(kind) {
  pending.add(kind);
  if (timer) clearTimeout(timer);
  timer = setTimeout(async () => {
    const kinds = pending;
    pending = new Set();
    if (kinds.has("tools")) {
      console.log("[dev] connectors changed: restarting the tools host");
      await stop("tools");
      tools();
      await waitFor(`http://127.0.0.1:${TOOLS_PORT}/health`, 120);
    }
    if (kinds.has("mock")) {
      console.log("[dev] mock harness changed: rebuilding and restarting it");
      if (await run(process.execPath, ["scripts/build.mjs", "--web"])) {
        await stop("harness");
        harness();
      }
    }
    if (kinds.has("main") || kinds.has("renderer")) {
      const ok = await run(process.execPath, ["scripts/build.mjs"]);
      // The same renderer also serves http://localhost:5174 (a browser, for quick looks).
      if (kinds.has("renderer") && !kinds.has("mock")) await run(process.execPath, ["scripts/build.mjs", "--web"]);
      if (ok && kinds.has("main")) {
        console.log("[dev] main process changed: restarting Electron");
        await stop("electron");
        electron();
      }
    }
  }, 200);
}
watch(path.join(root, "src"), { recursive: true }, (_e, file) => {
  if (!file) return;
  changed(/^(main|preload)[/\\]/.test(file) ? "main" : "renderer");
});
watch(path.join(root, "dev"), { recursive: true }, (_e, file) => {
  if (file && /\.(ts|html|css)$/.test(file)) changed("mock");
});
watch(path.join(repo, "connectors", "src"), { recursive: true }, (_e, file) => {
  if (file && !file.includes("__pycache__") && /\.(py|html|css|js|md)$/.test(file)) changed("tools");
});
watch(path.join(root, "dev", "tools_host.py"), () => changed("tools"));
