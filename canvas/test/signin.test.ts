import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { checkAuthUrl, GOOGLE_AUTH, SignInBrowser, userJs } from "../src/main/signin";

const REDIRECT = "http://127.0.0.1:53111/";
const auth = (over: Record<string, string | null> = {}, base = GOOGLE_AUTH, redirect = REDIRECT) => {
  const u = new URL(base);
  const params: Record<string, string | null> = { redirect_uri: redirect, code_challenge_method: "S256", state: "st", ...over };
  for (const [k, v] of Object.entries(params)) if (v !== null) u.searchParams.set(k, v);
  return u.toString();
};

describe("checkAuthUrl", () => {
  it("accepts only Google's sign-in page, with PKCE and a state, coming back here", () => {
    expect(checkAuthUrl(auth(), REDIRECT).searchParams.get("state")).toBe("st");
    expect(() => checkAuthUrl("not a url", REDIRECT)).toThrow("not a URL");
    expect(() => checkAuthUrl(auth({}, "https://accounts.google.com.evil.example/o/oauth2/v2/auth"), REDIRECT)).toThrow("only a known sign-in page");
    expect(() => checkAuthUrl(auth({}, "http://accounts.google.com/o/oauth2/v2/auth"), REDIRECT)).toThrow("only a known sign-in page");
    expect(() => checkAuthUrl(auth({}, "https://accounts.google.com/signin"), REDIRECT)).toThrow("only a known sign-in page");
    expect(() => checkAuthUrl(auth({ redirect_uri: "https://elsewhere.example/" }), REDIRECT)).toThrow("come back");
    expect(() => checkAuthUrl(auth({ code_challenge_method: "plain" }), REDIRECT)).toThrow("PKCE");
    expect(() => checkAuthUrl(auth({ state: null }), REDIRECT)).toThrow("PKCE");
  });

  it("opens a service's sign-in page only when the configuration names it", () => {
    const todoist = "https://todoist.com/oauth/authorize";
    expect(() => checkAuthUrl(auth({}, todoist), REDIRECT)).toThrow("only a known sign-in page");
    expect(checkAuthUrl(auth({}, todoist), REDIRECT, [GOOGLE_AUTH, todoist]).host).toBe("todoist.com");
    expect(() => checkAuthUrl(auth({}, GOOGLE_AUTH), REDIRECT, [todoist])).toThrow("only a known sign-in page");
  });
});

describe("userJs", () => {
  it("sends everything but loopback through the sign-in proxy, and saves no passwords", () => {
    const js = userJs("127.0.1.101:3128");
    expect(js).toContain('user_pref("network.proxy.type", 1);');
    expect(js).toContain('user_pref("network.proxy.ssl", "127.0.1.101");');
    expect(js).toContain('user_pref("network.proxy.ssl_port", 3128);');
    expect(js).toContain('user_pref("network.proxy.no_proxies_on", "127.0.0.1");');
    expect(js).toContain('user_pref("signon.rememberSignons", false);');
    expect(userJs(null)).not.toContain("network.proxy");
  });
});

describe("SignInBrowser", () => {
  let browser: SignInBrowser | null = null;
  afterEach(async () => {
    await browser?.end();
    browser = null;
  });

  it("is unavailable without a browser", async () => {
    browser = new SignInBrowser({ browser: null, proxy: null });
    expect(browser.available).toBe(false);
    await expect(browser.begin()).rejects.toThrow("no sign-in browser");
  });

  it("listens on loopback and resolves with what Google sends back", async () => {
    browser = new SignInBrowser({ browser: null, proxy: null, fake: true });
    const { redirectUri } = await browser.begin();
    expect(redirectUri).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/$/);
    expect((await browser.begin()).redirectUri).toBe(redirectUri); // one listener per Accounts session
    await expect(browser.open(auth({}, GOOGLE_AUTH, "http://127.0.0.1:1/"))).rejects.toThrow("come back");
    const got = await browser.open(auth({ state: "abc" }, GOOGLE_AUTH, redirectUri));
    expect(got).toEqual({ code: "4/dev", state: "abc", error: undefined });
    expect((await fetch(`${redirectUri}favicon.ico`)).status).toBe(404);
  });

  it("passes Google's refusal on, and answers the browser with a page", async () => {
    browser = new SignInBrowser({ browser: null, proxy: null, fake: true });
    const { redirectUri } = await browser.begin();
    const waiting = browser.open(auth({}, GOOGLE_AUTH, redirectUri));
    const page = await fetch(`${redirectUri}?error=access_denied&state=st`);
    expect(await page.text()).toContain("Nothing was shared");
    expect(await waiting).toEqual({ code: undefined, state: "st", error: "access_denied" });
  });

  it("gives the browser a throwaway profile and rejects when it closes early", async () => {
    // Node refuses Firefox's options and exits at once, like a browser the person closed.
    browser = new SignInBrowser({ browser: process.execPath, proxy: "127.0.1.101:3128" });
    const { redirectUri } = await browser.begin();
    const profile = (browser as unknown as { profile: string }).profile;
    expect(readFileSync(path.join(profile, "user.js"), "utf8")).toContain("127.0.1.101");
    await expect(browser.open(auth({}, GOOGLE_AUTH, redirectUri))).rejects.toThrow("closed");
    await browser.end();
    expect(existsSync(profile)).toBe(false);
    await expect(browser.open(auth({}, GOOGLE_AUTH, redirectUri))).rejects.toThrow("begin first");
  });
});

describe("canvas configuration for sign-in", () => {
  it("takes https sign-in pages and plain service names only", async () => {
    const { parseSigninPages, parseServices } = await import("../src/main/config");
    expect(parseSigninPages(undefined, GOOGLE_AUTH)).toEqual([GOOGLE_AUTH]);
    expect(parseSigninPages('["https://todoist.com/oauth/authorize","http://x.example/a","https://y.example/a?b=1",3]', GOOGLE_AUTH))
      .toEqual(["https://todoist.com/oauth/authorize"]);
    expect(parseServices('{"todoist":"Todoist","Bad Name":"x","kiwi":4}')).toEqual({ todoist: "Todoist" });
  });
});
