// Records the README's demo (docs/media/foyelle-demo.gif) from the real canvas in Electron, against
// the mock harness and the real connectors on fresh sample data, as `npm run dev` runs them.
//
//   npm i --no-save playwright-core@1.49.1     once (not a dependency: the lockfile stays as it is)
//   node dev/record-demo.mjs                   from canvas/, after `node scripts/build.mjs && node scripts/build.mjs --web`
//   node dev/record-demo.mjs --explore         screenshots after every step instead of a video
//
// It resets canvas/.dev-state (the sample data), and writes .dev-demo/foyelle-demo.{mp4,gif}; ffmpeg
// must be on the PATH. Playwright's recordVideo doesn't start with this Electron, so frames come from
// the DevTools screencast with their timestamps, and ffmpeg keeps that timing.
import { _electron as electron } from "playwright-core";
import { spawn, execFileSync } from "node:child_process";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repo = path.resolve(root, "..");
const out = path.join(root, ".dev-demo");
const explore = process.argv.includes("--explore");
const W = 1440, H = 900;
const TOKEN = "foyelle-demo-token";
const frames = path.join(out, "frames");
await rm(out, { recursive: true, force: true });
await mkdir(frames, { recursive: true });

const children = [];
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
start("uvx", ["--from", "uv==0.13.1", "uv", "run", "--quiet", "--project", "connectors", "--python", "3.14", "python",
  path.join(root, "dev", "tools_host.py"), "--reset"], { FOYELLE_TOOLS_PORT: "5175", PYTHONDONTWRITEBYTECODE: "1" });
await waitFor("http://127.0.0.1:5175/health");
start(process.execPath, [path.join(root, "dist-dev", "dev-server.mjs")],
  { PORT: "5174", FOYELLE_DEV_TOKEN: TOKEN, FOYELLE_TOOLS_URL: "http://127.0.0.1:5175" });
await waitFor("http://127.0.0.1:5174/");
const tokenFile = path.join(out, "harness-token");
await writeFile(tokenFile, TOKEN, { mode: 0o600 });

const app = await electron.launch({
  executablePath: path.join(root, "node_modules", ".bin", "electron"),
  args: [path.join(root, "dist", "main.js")],
  env: {
    ...process.env,
    FOYELLE_CANVAS_DEV: "1",
    FOYELLE_HARNESS_URL: "ws://127.0.0.1:5174/api/ws",
    FOYELLE_HARNESS_TOKEN_FILE: tokenFile,
    FOYELLE_SURFACE_TITLES: JSON.stringify({ sysmon: "System monitor", workshop: "Workshop" }),
    FOYELLE_VOICE_URL: "http://127.0.0.1:5174/__dev/transcribe",
    FOYELLE_DEV_FAKE_SIGNIN: "1",
    FOYELLE_SERVICES: JSON.stringify({ todoist: "Todoist" }),
  },
});
const page = await app.firstWindow();
await app.evaluate(({ BrowserWindow }, [w, h]) => {
  const win = BrowserWindow.getAllWindows()[0];
  win.setContentSize(w, h);
  win.center();
}, [W, H]);
await page.waitForLoadState("domcontentloaded");

const painted = [];
let cdp = null;
if (!explore) {
  cdp = await page.context().newCDPSession(page);
  cdp.on("Page.screencastFrame", async ({ data, metadata, sessionId }) => {
    const file = path.join(frames, `f${String(painted.length).padStart(5, "0")}.jpg`);
    painted.push({ file, t: metadata.timestamp });
    await writeFile(file, Buffer.from(data, "base64"));
    await cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: W * 2, maxHeight: H * 2 });
}

let n = 0;
const shot = async (name) => { if (explore) await page.screenshot({ path: path.join(out, `${String(++n).padStart(2, "0")}-${name}.png`) }); };
const pause = (ms) => page.waitForTimeout(ms);
// Messages also show in the sidebar's preview of the conversation: wait for the newest one.
const shown = (text) => page.getByText(text).last().waitFor({ timeout: 20000 });
const say = async (text) => {
  await page.locator("#input").click();
  await page.keyboard.type(text, { delay: 55 });
  await pause(400);
  await page.keyboard.press("Enter");
};
// Cards (approvals too) are sandboxed frames of their own.
const clickInCard = async (name) => {
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
  // The first start: a name, a look, and the disk key left for later.
  const name = page.getByPlaceholder("Your name");
  await name.waitFor({ timeout: 30000 });
  await pause(1500); await shot("welcome");
  await name.click();
  await page.keyboard.type("Sam", { delay: 120 });
  await pause(500);
  await page.getByRole("button", { name: "That's me" }).click();
  await shown("Which feels more like home?");
  await pause(1500); await shot("look");
  await page.getByRole("button", { name: /Daylight/ }).click();
  await pause(1200);
  await page.getByRole("button", { name: "This one" }).click();
  await shown("a key for your home");
  await pause(1800); await shot("key");
  await page.getByRole("button", { name: "Skip for now" }).click();
  await pause(1500); await shot("home");

  // A card from the Files helper, opened down to a folder.
  await say("What's in my documents?");
  await shown("Here are your documents.");
  await pause(3500); await shot("files");
  const card = page.frameLocator("iframe").first();
  for (const folder of ["documents/", "Letters/"]) {
    const link = card.getByText(folder, { exact: true }).first();
    await link.waitFor({ timeout: 10000 });
    await link.click();
    await pause(2500); await shot(folder.replace("/", ""));
  }

  // A letter drafted in the conversation.
  await say("Write a thank-you note to Rita for the dinner");
  await page.getByText(/Dear Rita/).first().waitFor({ timeout: 20000 });
  await pause(3500); await shot("note");

  // Anything that sends asks first.
  await say("Email the Q3 report to Jo");
  await shown(/I need your approval/);
  await pause(3000); await shot("approval");
  await clickInCard(/Send it/);
  await shown(/Sent \(approval/);
  await pause(2500); await shot("sent");

  // A different look, applied at once.
  await say("Switch to the Tokyo Night theme");
  await shown(/Switched to/);
  await pause(4000); await shot("tokyo");
} catch (err) {
  failed = true;
  console.error("demo stopped:", err.message);
  await page.screenshot({ path: path.join(out, "error.png") });
} finally {
  if (cdp) await cdp.send("Page.stopScreencast").catch(() => {});
  await app.close();
  for (const child of children) child.kill("SIGTERM");
}

if (!explore && !failed && painted.length) {
  // Each frame shows until the next one; the last one for two seconds.
  const list = painted.map((f, i) => `file '${f.file}'\nduration ${((painted[i + 1]?.t ?? f.t + 2) - f.t).toFixed(3)}`);
  list.push(`file '${painted.at(-1).file}'`);
  await writeFile(path.join(out, "frames.txt"), list.join("\n") + "\n");
  const mp4 = path.join(out, "foyelle-demo.mp4"), gif = path.join(out, "foyelle-demo.gif");
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", path.join(out, "frames.txt"),
    "-vf", `scale=${W}:${H}:flags=lanczos,fps=30,format=yuv420p`, "-c:v", "libx264", "-preset", "slow", "-crf", "20",
    "-movflags", "+faststart", mp4], { stdio: "inherit" });
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-i", mp4, "-vf",
    "fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=192:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
    "-loop", "0", gif], { stdio: "inherit" });
  console.log(`wrote ${mp4} and ${gif}`);
}
process.exit(failed ? 1 : 0);
