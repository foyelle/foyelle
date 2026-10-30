// Drives the web demo (foyelle.org/demo) through the README demo's steps (dev/record-demo.mjs) in
// Chromium and WebKit, at a desktop's and a phone's size, from the built site (scripts/deploy-site
// --dry-run) served with its _headers (demo/serve.mjs). It fails on a page error, a request that
// leaves the site, or a step that doesn't answer as scripted.
//
//   npm i --no-save playwright-core@1.63.0 && npx playwright@1.63.0 install chromium webkit    once
//   node demo/test.mjs [--shots <dir>] [--browser chromium|webkit] [--channel chrome]       from canvas/
//
// --channel chrome runs the Chromium half in the installed Google Chrome.
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, devices, webkit } from "playwright-core";
import { serve } from "./serve.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const arg = (name) => (process.argv.includes(name) ? process.argv[process.argv.indexOf(name) + 1] : null);
const shots = arg("--shots") && path.resolve(arg("--shots"));
const only = arg("--browser");
const channel = arg("--channel");
const PORT = 5181;
const origin = `http://127.0.0.1:${PORT}`;
const server = await serve(path.join(root, "out", "site"), PORT);
if (shots) await mkdir(shots, { recursive: true });

const SIZES = {
  desktop: { viewport: { width: 1440, height: 900 } },
  phone: { chromium: devices["Pixel 7"], webkit: devices["iPhone 13"] },
};
// The canvas sets a few style attributes that its policy (style-src 'self') refuses, in Electron too.
const KNOWN = [/Applying inline style violates/, /Refused to apply a stylesheet because its hash/];

async function run(browserType, size) {
  const name = `${browserType.name()}-${size}`;
  const browser = await browserType.launch(browserType.name() === "chromium" && channel ? { channel } : {});
  const options = size === "phone" ? SIZES.phone[browserType.name()] : SIZES.desktop;
  const context = await browser.newContext({ ...options, colorScheme: "light", reducedMotion: "no-preference" });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !KNOWN.some((re) => re.test(m.text()))) problems.push(`console: ${m.text()}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 400) problems.push(`${r.status()}: ${r.url()}`);
  });
  context.on("request", (r) => {
    const url = r.url();
    if (!url.startsWith(`${origin}/`) && !/^(data|blob|about):/.test(url)) problems.push(`left the site: ${url}`);
  });

  const pause = (ms) => page.waitForTimeout(ms);
  let n = 0;
  const shot = async (label) => {
    if (shots) await page.screenshot({ path: path.join(shots, `${name}-${String(++n).padStart(2, "0")}-${label}.png`) });
  };
  // Attached rather than visible: on a phone the conversation hides while a card has the screen.
  const shown = (text) => page.getByText(text).last().waitFor({ state: "attached", timeout: 20000 });
  const view = async (which) => {
    if (size !== "phone") return;
    const tab = page.locator(".demo-views button", { hasText: which === "chat" ? "Chat" : "Screen" });
    if ((await tab.getAttribute("aria-pressed")) !== "true") await tab.click();
  };
  const say = async (text) => {
    await view("chat");
    await page.locator("#input").click();
    await page.keyboard.type(text, { delay: 5 });
    await page.keyboard.press("Enter");
  };
  // Cards (approvals too) are sandboxed frames of their own.
  const inCards = async (find, tries = 40) => {
    for (let i = 0; i < tries; i++) {
      for (const frame of page.frames()) {
        if (frame === page.mainFrame()) continue;
        const target = find(frame);
        if (await target.count().catch(() => 0)) return target.first();
      }
      await pause(250);
    }
    throw new Error("not in any card");
  };
  // A click while a card is still connecting or redrawing can go unheard, as in Foyelle: a person
  // clicks again, and so does this, until what the click should bring is there.
  const clickUntil = async (find, done) => {
    for (let i = 0; ; i++) {
      await (await inCards(find)).click().catch(() => undefined);
      try {
        return await done();
      } catch (err) {
        if (i === 2) throw err;
      }
    }
  };

  try {
    await page.goto(`${origin}/demo/`);
    await page.getByText("Demo with canned answers, no AI model").waitFor();

    // The first start: a name, a look, and the disk key left for later.
    const nameField = page.getByPlaceholder("Your name");
    await nameField.waitFor({ timeout: 20000 });
    await shot("welcome");
    await nameField.click();
    await page.keyboard.type("Sam", { delay: 20 });
    await page.getByRole("button", { name: "That's me" }).click();
    await shown("Which feels more like home?");
    await page.getByRole("button", { name: /Daylight/ }).click();
    await page.getByRole("button", { name: "This one" }).click();
    await shown("a key for your home");
    await page.getByRole("button", { name: "Skip for now" }).click();
    await pause(800);
    await shot("home");

    // A card from the Files helper, opened down to a folder.
    await say("What's in my documents?");
    await shown("Here are your documents.");
    await view("screen");
    await clickUntil((f) => f.getByText("documents/", { exact: true }), () => inCards((f) => f.getByText("Letters/", { exact: true }), 20));
    await clickUntil((f) => f.getByText("Letters/", { exact: true }), () => inCards((f) => f.getByText("Thank you, Rita.md"), 20));
    await shot("files");

    // A letter drafted in the conversation.
    await say("Write a thank-you note to Rita for the dinner");
    await shown(/Dear Rita/);
    await pause(1500);
    await shot("note");

    // Anything that sends asks first.
    await say("Email the Q3 report to Jo");
    await shown(/I need your approval/);
    await (await inCards((f) => f.getByRole("button", { name: /Send it/ }))).waitFor();
    await shot("approval");
    await clickUntil((f) => f.getByRole("button", { name: /Send it/ }),
      () => page.getByText(/Sent \(approval/).last().waitFor({ state: "attached", timeout: 5000 }));

    // The suggestions: the Themes card, then a different look, applied at once.
    await view("chat");
    await page.locator(".demo-chip", { hasText: "The themes" }).click();
    await shown("Here are the themes.");
    await inCards((f) => f.getByText("Tokyo Night"));
    await shot("themes");
    const before = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    await say("Switch to the Tokyo Night theme");
    await shown(/Switched to Tokyo Night/);
    await page.waitForFunction((b) => getComputedStyle(document.body).backgroundColor !== b, before, { timeout: 10000 });
    await pause(1200);
    await shot("tokyo-night");

    // All the cards, and one opened from the list.
    await view("chat");
    await page.locator(".demo-chip", { hasText: "All the cards" }).click();
    await shown(/Your cards/);
    await page.locator(".list-btn", { hasText: "Memory" }).last().click();
    await inCards((f) => f.getByRole("tab", { name: /About you/ }));
    await shot("memory");

    // Anything else gets a friendly word about the demo.
    await say("Book me a table for two tonight");
    await shown(/canned answers, and no AI model/);
    await shot("fallback");
  } catch (err) {
    problems.push(`stopped: ${err.message.split("\n")[0]}`);
    if (shots) await page.screenshot({ path: path.join(shots, `${name}-error.png`) });
  }
  await browser.close();
  return { name, problems };
}

let failed = false;
for (const type of [chromium, webkit].filter((t) => !only || t.name() === only)) {
  for (const size of ["desktop", "phone"]) {
    const { name, problems } = await run(type, size);
    console.log(`${problems.length ? "FAIL" : "ok  "} ${name}`);
    for (const p of problems) console.log(`     ${p}`);
    failed ||= problems.length > 0;
  }
}
server.close();
process.exit(failed ? 1 : 0);
