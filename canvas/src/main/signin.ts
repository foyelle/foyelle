/**
 * Sign in with Google, the desktop way: Google's sign-in page opens in a real browser (Firefox,
 * full screen over the canvas, which cage shows on top), and Google sends the browser back to a
 * loopback address on this computer with a one-time code. This module owns that loopback listener
 * and the browser; the helper (connector) that started the sign-in owns the PKCE verifier and
 * exchanges the code, so the code alone is useless to anyone else.
 *
 * The browser gets a fresh profile for one Accounts session (so the second approval doesn't ask for
 * the password again), deleted at the end; its network goes through its own egress proxy, which
 * allows Google's sign-in hosts only, and its locked policies (Nix) keep it on those pages.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";

export const GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth";
export const SIGN_IN_MS = 10 * 60 * 1000;

export interface SignInOptions {
  /** The browser to run (Firefox), "open" for the Mac's default browser in development, or null. */
  browser: string | null;
  /** host:port of the browser's egress proxy, or null to connect directly (development). */
  proxy: string | null;
  /** Development: no browser; answer the sign-in from here a moment later. */
  fake?: boolean;
  /** The sign-in pages that may be opened (default: Google's). */
  pages?: string[];
}

export interface SignInResult {
  code?: string;
  state?: string;
  error?: string;
}

/** Only a known sign-in page (Google's, or a service's pinned in the configuration), with PKCE,
 * coming back to our own listener. */
export function checkAuthUrl(authUrl: string, redirectUri: string, pages: string[] = [GOOGLE_AUTH]): URL {
  let u: URL;
  try {
    u = new URL(authUrl);
  } catch {
    throw new Error("not a URL");
  }
  if (u.protocol !== "https:" || !pages.includes(`${u.origin}${u.pathname}`)) {
    throw new Error("only a known sign-in page can be opened");
  }
  if (u.searchParams.get("redirect_uri") !== redirectUri) throw new Error("the sign-in must come back to this computer");
  if (u.searchParams.get("code_challenge_method") !== "S256" || !u.searchParams.get("state")) {
    throw new Error("the sign-in must use PKCE and a state");
  }
  return u;
}

/** Firefox preferences for the throwaway profile (the image's policies lock the same and more). */
export function userJs(proxy: string | null): string {
  const prefs: Array<[string, string | number | boolean]> = [
    ["browser.shell.checkDefaultBrowser", false],
    ["browser.aboutwelcome.enabled", false],
    ["browser.startup.homepage_override.mstone", "ignore"],
    ["datareporting.policy.dataSubmissionEnabled", false],
    ["toolkit.telemetry.reportingpolicy.firstRun", false],
    ["network.captive-portal-service.enabled", false],
    ["network.connectivity-service.enabled", false],
    ["signon.rememberSignons", false],
  ];
  if (proxy) {
    const [host, port] = proxy.split(":");
    prefs.push(
      ["network.proxy.type", 1],
      ["network.proxy.ssl", host!],
      ["network.proxy.ssl_port", Number(port)],
      ["network.proxy.http", host!],
      ["network.proxy.http_port", Number(port)],
      ["network.proxy.no_proxies_on", "127.0.0.1"],
      ["network.proxy.allow_hijacking_localhost", false],
    );
  }
  return prefs.map(([k, v]) => `user_pref(${JSON.stringify(k)}, ${JSON.stringify(v)});`).join("\n") + "\n";
}

const page = (title: string, text: string) =>
  `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${title}</title>` +
  `<style>body{font:20px/1.5 system-ui,sans-serif;background:#f6f0e6;color:#2e2621;display:grid;place-items:center;min-height:100vh;margin:0}` +
  `main{max-width:28em;text-align:center}</style><main><h1>${title}</h1><p>${text}</p></main>`;

export class SignInBrowser {
  private server: http.Server | null = null;
  private redirectUri: string | null = null;
  private profile: string | null = null;
  private child: ChildProcess | null = null;
  private waiting: { resolve: (r: SignInResult) => void; reject: (e: Error) => void; timer: NodeJS.Timeout } | null = null;

  constructor(private readonly opts: SignInOptions) {}

  get available(): boolean {
    return !!this.opts.browser || !!this.opts.fake;
  }

  /** Starts the loopback listener (and the profile) for an Accounts session. */
  async begin(): Promise<{ redirectUri: string }> {
    if (!this.available) throw new Error("this computer has no sign-in browser");
    if (!this.server) {
      const server = http.createServer((req, res) => this.onRedirect(req, res));
      await new Promise<void>((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, "127.0.0.1", () => resolve());
      });
      this.server = server;
      this.redirectUri = `http://127.0.0.1:${(server.address() as AddressInfo).port}/`;
    }
    if (!this.profile && this.opts.browser && this.opts.browser !== "open") {
      this.profile = await mkdtemp(path.join(os.tmpdir(), "foyelle-signin-"));
      await writeFile(path.join(this.profile, "user.js"), userJs(this.opts.proxy), { mode: 0o600 });
    }
    return { redirectUri: this.redirectUri! };
  }

  /** Opens Google's sign-in and resolves with what Google sends back (or rejects if abandoned). */
  async open(authUrl: string): Promise<SignInResult> {
    if (!this.server || !this.redirectUri) throw new Error("call begin first");
    const url = checkAuthUrl(authUrl, this.redirectUri, this.opts.pages);
    if (this.waiting) throw new Error("a sign-in is already open");
    const result = new Promise<SignInResult>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.settle(new Error("the sign-in took too long"));
        this.closeBrowser();
      }, SIGN_IN_MS);
      this.waiting = { resolve, reject, timer };
    });
    if (this.opts.fake) {
      // Development: answer as Google would, after a moment.
      const back = new URL(this.redirectUri);
      back.searchParams.set("code", "4/dev");
      back.searchParams.set("state", url.searchParams.get("state")!);
      setTimeout(() => void fetch(back).catch(() => undefined), 1500);
    } else if (this.opts.browser === "open") {
      spawn("open", [url.toString()], { stdio: "ignore" }).unref(); // the Mac's own browser (development)
    } else {
      const child = spawn(
        this.opts.browser!,
        ["--kiosk", "--no-remote", "--new-instance", "--profile", this.profile!, url.toString()],
        {
          stdio: "ignore",
          env: { ...process.env, MOZ_CRASHREPORTER_DISABLE: "1", MOZ_DISABLE_AUTO_SAFE_MODE: "1" },
        },
      );
      this.child = child;
      child.once("exit", () => {
        if (this.child === child) this.child = null;
        // Closed before Google answered: the person gave up (Ctrl+Q, or the browser failed).
        setTimeout(() => this.settle(new Error("the sign-in window was closed")), 300);
      });
    }
    return result;
  }

  /** Ends the Accounts session: closes the browser and the listener, deletes the profile. */
  async end(): Promise<void> {
    this.settle(new Error("cancelled"));
    this.closeBrowser();
    this.server?.close();
    this.server = null;
    this.redirectUri = null;
    if (this.profile) {
      await rm(this.profile, { recursive: true, force: true }).catch(() => undefined);
      this.profile = null;
    }
  }

  private onRedirect(req: http.IncomingMessage, res: http.ServerResponse): void {
    const u = new URL(req.url ?? "/", "http://127.0.0.1");
    if (u.pathname !== "/" || (!u.searchParams.get("code") && !u.searchParams.get("error"))) {
      res.writeHead(404, { "content-type": "text/plain" }).end("not found");
      return;
    }
    const error = u.searchParams.get("error");
    res.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
    res.end(error ? page("Nothing was shared", "Going back to Foyelle…") : page("You're signed in", "Going back to Foyelle…"));
    this.settle({
      code: u.searchParams.get("code") ?? undefined,
      state: u.searchParams.get("state") ?? undefined,
      error: error ?? undefined,
    });
    setTimeout(() => this.closeBrowser(), 1200); // let the page show, then back to the canvas
  }

  private settle(outcome: SignInResult | Error): void {
    const w = this.waiting;
    if (!w) return;
    this.waiting = null;
    clearTimeout(w.timer);
    if (outcome instanceof Error) w.reject(outcome);
    else w.resolve(outcome);
  }

  private closeBrowser(): void {
    const child = this.child;
    this.child = null;
    if (child && child.exitCode === null) child.kill("SIGTERM");
  }
}
