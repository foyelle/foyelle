// Records what the web demo (foyelle.org/demo) replays: the real connectors' cards and tool results on
// the sample data (Sam in Lisbon), from canvas/dev/tools_host.py, as the canvas asks for them.
//
//   npm i --no-save playwright-core@1.63.0 && npx playwright@1.63.0 install chromium    once
//   node scripts/build.mjs --web && node demo/record.mjs                                 from canvas/
//
// A proxy between the dev server's mock harness and the tools host writes down every call while
// Chromium goes through the demo's paths in the browser build: the welcome, the suggestions, every
// card in /cards, every folder and file in Documents and every theme. It writes demo/recording.json
// (the cards' titles and tool results) and demo/cards/*.html (each card's page as the helper serves
// it), with the sample data's folder replaced by the machine's own (/var/lib/foyelle). Nothing here
// needs the network.
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, readdir, realpath, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repo = path.resolve(root, "..");
const here = path.join(root, "demo");
const TOOLS_PORT = 5195, PROXY_PORT = 5196, DEV_PORT = 5194;
const TOOLS = `http://127.0.0.1:${TOOLS_PORT}`;
// Cards that need the network (Kiwi's flight search) or only exist for designing the canvas.
const LEFT_OUT = new Set(["design", "kiwi"]);
const state = await realpath(await mkdtemp(path.join(os.tmpdir(), "foyelle-demo-state-")));

const children = [];
process.on("exit", () => { for (const child of children) child.kill("SIGTERM"); });
const start = (cmd, args, env) => {
  const child = spawn(cmd, args, { cwd: repo, stdio: ["ignore", "ignore", "inherit"], env: { ...process.env, ...env } });
  children.push(child);
  return child;
};
const waitFor = async (url) => {
  for (let i = 0; i < 240; i++) {
    try { if ((await fetch(url)).ok) return; } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`${url} did not start`);
};

// ----- the recording proxy ---------------------------------------------------
const calls = [];
let surfaces = [];
const resources = new Map();
async function tools(pathAndQuery, init) {
  const res = await fetch(`${TOOLS}${pathAndQuery}`, init);
  return { status: res.status, body: await res.text() };
}
async function call(server, name, args) {
  const { body } = await tools("/call", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ server, name, arguments: args }) });
  const result = JSON.parse(body);
  calls.push({ server, name, arguments: args, result });
  return result;
}
const proxy = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks).toString("utf8");
  const out = await tools(url.pathname + url.search, req.method === "POST" ? { method: "POST", headers: { "Content-Type": "application/json" }, body } : undefined);
  try {
    const data = JSON.parse(out.body);
    if (url.pathname === "/surfaces") surfaces = data.surfaces;
    if (url.pathname === "/resource") resources.set(`${url.searchParams.get("server")} ${url.searchParams.get("uri")}`, data);
    if (url.pathname === "/call") {
      const spec = JSON.parse(body);
      calls.push({ server: spec.server, name: spec.name, arguments: spec.arguments ?? {}, result: data });
    }
  } catch {}
  res.writeHead(out.status, { "Content-Type": "application/json" }).end(out.body);
});
await new Promise((resolve) => proxy.listen(PROXY_PORT, "127.0.0.1", resolve));

start("uvx", ["--from", "uv==0.13.1", "uv", "run", "--quiet", "--project", "connectors", "--python", "3.14", "python",
  path.join(root, "dev", "tools_host.py"), "--reset"],
  { FOYELLE_TOOLS_PORT: String(TOOLS_PORT), FOYELLE_DEV_STATE: state, PYTHONDONTWRITEBYTECODE: "1" });
await waitFor(`${TOOLS}/health`);
// Sam's mail, calendar and Todoist, connected on the tools host's stand-ins (any password works there).
const setup = async (server, name, args) => {
  const { body } = await tools("/call", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ server, name, arguments: args }) });
  const result = JSON.parse(body);
  if (result.isError) throw new Error(`${server}.${name}: ${result.content?.[0]?.text}`);
  return JSON.parse(result.content?.[0]?.text ?? "{}");
};
await setup("email", "account_connect", { provider: "fastmail", address: "sam@example.org", password: "sample", display_name: "Sam" });
await setup("calendar", "account_connect", { provider: "fastmail", address: "sam@example.org", password: "sample" });
const signin = await setup("todoist", "account_signin_start", { redirect_uri: "http://127.0.0.1:5197/" });
await setup("todoist", "account_signin_finish", { code: "4/dev", state: new URL(signin.auth_url).searchParams.get("state") });
start(process.execPath, [path.join(root, "dist-dev", "dev-server.mjs")],
  { PORT: String(DEV_PORT), FOYELLE_TOOLS_URL: `http://127.0.0.1:${PROXY_PORT}` });
await waitFor(`http://127.0.0.1:${DEV_PORT}/`);

// ----- the paths --------------------------------------------------------------
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const pause = (ms) => page.waitForTimeout(ms);
const say = async (text, ms = 4000) => {
  await page.locator("#input").fill(text);
  await page.keyboard.press("Enter");
  await pause(ms);
};
const clickInCards = async (name) => {
  for (let i = 0; i < 20; i++) {
    for (const frame of page.frames()) {
      const button = frame.getByRole("button", { name });
      if (await button.count().catch(() => 0)) return button.first().click();
    }
    await pause(500);
  }
  throw new Error(`no ${name} button in any card`);
};

let failed = false;
try {
  await page.goto(`http://127.0.0.1:${DEV_PORT}/`);
  // The welcome, as the demo's visitor and its test go through it.
  const name = page.getByPlaceholder("Your name");
  await name.waitFor({ timeout: 30000 });
  await name.fill("Sam");
  await page.getByRole("button", { name: "That's me" }).click();
  await page.getByRole("button", { name: /Daylight/ }).click();
  await page.getByRole("button", { name: "This one" }).click();
  await page.getByRole("button", { name: "Skip for now" }).click();
  await pause(1500);

  // The demo's suggestions and the home screen's, and what someone might type next to them.
  for (const text of ["What's in my documents?", "What do you remember about me?", "How is my computer doing?",
    "Show me the themes", "Check my email", "What's on my calendar this week?", "Show my tasks for today",
    "What left my home today?", "Show my settings", "What did you do? Show the audit log", "Install Firefox"]) {
    await say(text);
  }
  await say("Email the Q3 report to Jo", 3000);
  await clickInCards(/Send it/);
  await pause(1500);

  // Every card, as /cards opens them (each loads its own data).
  await say("/cards", 1500);
  for (const s of surfaces.filter((s) => !LEFT_OUT.has(s.server))) await say(`/cards ${s.uri}`, 3500);
} catch (err) {
  failed = true;
  console.error("recording stopped:", err.message);
  await page.screenshot({ path: path.join(state, "error.png") });
}
await browser.close();

if (!failed) {
  // Every folder and file in Documents, as the Files card asks for them.
  const walk = async (dir) => {
    const listed = JSON.parse((await call("files", "list", { path: dir })).content?.[0]?.text ?? "{}");
    for (const e of listed.entries ?? []) {
      if (e.type === "dir") await walk(e.path);
      else {
        const image = /\.(png|jpe?g|gif|webp|svg)$/i.test(e.name);
        await call("files", image ? "read_image" : "read", { path: e.path });
        const versions = JSON.parse((await call("files", "versions", { path: e.path })).content?.[0]?.text ?? "{}");
        for (const v of versions.versions ?? []) await call("files", "read_version", { path: e.path, version_id: v.version_id });
      }
    }
  };
  await call("files", "list", { path: "" });
  await walk("documents");
  // Settings as they read with every theme on (the canvas takes its colours from them).
  const themes = JSON.parse((await call("shell", "theme_list", {})).content[0].text).themes;
  for (const t of themes) {
    await call("shell", "theme_use", { theme_id: t.id });
    await call("shell", "settings_get", {});
  }
  await call("shell", "theme_use", { theme_id: "ember" });
}
for (const child of children) child.kill("SIGTERM");
proxy.close();
if (failed) process.exit(1);

// ----- the recording -----------------------------------------------------------
// The sample data's folder reads as a Foyelle's own; nothing from this laptop stays in.
const scrub = (text) =>
  text.replaceAll(state, "/var/lib/foyelle").replace(/\/tmp\/foyelle-dev-\d+-\d+\.sock/g, "/run/foyelle/apply.sock")
    .replaceAll(os.hostname(), "foyelle").replaceAll(os.hostname().split(".")[0], "foyelle");
// As demo/recorded-tools.ts looks them up: the arguments as JSON, keys sorted.
const key = (args) => JSON.stringify(args, (_, v) =>
  v && typeof v === "object" && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) : v);
// Answered by the demo itself (demo/recorded-tools.ts keeps the settings), not replayed.
const KEPT_BY_DEMO = new Set(["shell.settings_set", "shell.user_name_set", "shell.theme_use"]);
const replies = {};
const settings = {};
// Every theme's settings start as the first start's (no name, not welcomed): the demo keeps the visit's own.
let fresh = null;
for (const c of calls) {
  const id = `${c.server}.${c.name}`;
  if (KEPT_BY_DEMO.has(id)) continue;
  if (id === "shell.settings_get") {
    const doc = JSON.parse(c.result.content[0].text);
    fresh ??= { effective: doc.effective, overrides: doc.overrides };
    if (doc.theme?.id) settings[doc.theme.id] = { content: [{ type: "text", text: JSON.stringify({ ...doc, ...fresh }, null, 2) }] };
    continue;
  }
  replies[`${id} ${key(c.arguments)}`] = c.result;
}
if (!fresh || fresh.effective?.ui?.welcomed === true) throw new Error("the first settings_get was not a first start's");
const cards = path.join(here, "cards");
await rm(cards, { recursive: true, force: true });
await mkdir(cards, { recursive: true });
const shown = surfaces.filter((s) => !LEFT_OUT.has(s.server));
const pages = {};
for (const s of shown) {
  const doc = resources.get(`${s.server} ${s.uri}`) ?? JSON.parse((await tools(`/resource?server=${s.server}&uri=${encodeURIComponent(s.uri)}`)).body);
  const file = `${s.server}-${s.uri.split("/").pop()}.html`;
  await writeFile(path.join(cards, file), scrub(doc.html));
  pages[`${s.server} ${s.uri}`] = { file, title: doc.title ?? null, csp: doc.csp ?? null };
}
const recording = scrub(JSON.stringify({ surfaces: shown, pages, settings, replies }, null, 1)) + "\n";
const leaks = [os.homedir(), os.userInfo().username, os.hostname(), repo, "/private/", "/Users/"].filter((s) => s && recording.includes(s));
if (leaks.length) throw new Error(`the recording still names this laptop: ${leaks.join(", ")}`);
await writeFile(path.join(here, "recording.json"), recording);
await rm(state, { recursive: true, force: true });
console.log(`recorded ${Object.keys(replies).length} answers, ${Object.keys(settings).length} themes and ${shown.length} cards`
  + ` (${(await readdir(cards)).length} files) into ${path.relative(process.cwd(), here)}`);
process.exit(0);
