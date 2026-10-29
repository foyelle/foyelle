/**
 * Accounts: connect your mail and calendar with an app password, and the services of the Services
 * directory (Todoist, Kiwi…): connecting one starts its helper (shell.service_connect), the harness
 * reconnects (services.reload), and a service with an account opens its sign-in page.
 *
 * The card talks to the Email and Calendar helpers through the canvas (tools named
 * "email.account_connect" and so on, see app.ts). The password is typed here and goes only to
 * those helpers, which check it against your provider and keep it on this computer; it never
 * passes through the conversation.
 */
import qrcode from "qrcode-generator";
import { el, startView, type CallToolResult, type ViewConnection } from "./view-runtime";

const root = document.getElementById("root")!;

interface ProviderInfo {
  id: string;
  name: string;
  mail: boolean;
  calendar: boolean;
  app_password_url: string | null;
  app_password_steps: string[];
  note?: string | null;
  google_signin?: boolean;
}

interface Status {
  connected: boolean;
  address?: string;
  provider?: string;
  calendar?: string;
  managed?: boolean;
}

/** One entry of the Services directory, as shell.services_list gives it. */
interface ServiceRow {
  id: string;
  title: string;
  category: string;
  summary: string;
  account: boolean;
  private: boolean;
  reviewed: string | null;
  connected: boolean;
  always: boolean;
  state: string;
}

const CATEGORY: Record<string, string> = {
  tasks: "Tasks", travel: "Travel", notes: "Notes", files: "Files", fitness: "Fitness",
  knowledge: "Knowledge", home: "Home", other: "Other",
};

interface Outcome {
  connected: boolean;
  stage?: string;
  message?: string;
  calendar?: string;
  calendars?: string[];
}

const NOTES: Record<string, string> = {
  gmail: "Mail only, for now",
  icloud: "Mail, calendar",
  fastmail: "Mail, calendar",
  other: "Type in the servers",
};

/** Plain words for what went wrong, by step. */
function hint(stage: string | undefined, provider: string): string {
  switch (stage) {
    case "imap-login":
    case "smtp-login":
    case "login":
      return provider === "gmail"
        ? "The password wasn't accepted. Use an app password made for Foyelle (2-Step Verification must be on), not your usual Google password."
        : "The password wasn't accepted. Use an app password made for Foyelle, not your usual password.";
    case "imap-connect":
    case "smtp-connect":
      return provider === "other"
        ? "Foyelle couldn't reach the mail server. Check the server names. A work network or VPN can also block mail."
        : "Foyelle couldn't reach the mail server. If this computer is on a work network or a VPN, it may be blocking mail: try again on another network.";
    case "connect":
      return "Foyelle couldn't reach the calendar server. Check that this computer is online.";
    case "empty":
      return "The account works, but it has no calendars.";
    default:
      return "Something went wrong.";
  }
}

function data<T>(r: CallToolResult): T {
  if (r.isError) throw new Error(r.content?.find((c) => c.type === "text")?.text ?? "failed");
  if (r.structuredContent && typeof r.structuredContent === "object") return r.structuredContent as T;
  const text = r.content?.find((c) => c.type === "text")?.text;
  return (text ? JSON.parse(text) : {}) as T;
}

/** A QR code of the URL, so the app-password page opens on a phone (Foyelle has no browser). */
function qr(url: string): SVGSVGElement {
  const code = qrcode(0, "M");
  code.addData(url);
  code.make();
  const n = code.getModuleCount();
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `-2 -2 ${n + 4} ${n + 4}`);
  svg.setAttribute("shape-rendering", "crispEdges");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `QR code for ${url}`);
  let d = "";
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (code.isDark(r, c)) d += `M${c} ${r}h1v1h-1z`;
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "#000");
  svg.append(path);
  return svg;
}

class Accounts {
  private providers: ProviderInfo[] = [];
  private mail: Status = { connected: false };
  private cal: Status = { connected: false };

  /** The canvas has a sign-in browser (Sign in with Google needs it). */
  private browser = false;
  /** Services that need an account, through their bridge: server name -> name shown. */
  private services: Record<string, string> = {};
  private serviceStatus: Record<string, Status> = {};
  /** The Services directory and what is connected (shell.services_list). */
  private directory: ServiceRow[] = [];

  constructor(private readonly view: ViewConnection, input: Record<string, unknown> = {}) {
    this.browser = input.signin === true;
    const services = input.services;
    if (services && typeof services === "object" && !Array.isArray(services)) {
      this.services = Object.fromEntries(
        Object.entries(services as Record<string, unknown>).filter((e): e is [string, string] => typeof e[1] === "string"),
      );
    }
  }

  private call<T>(name: string, args: Record<string, unknown> = {}): Promise<T> {
    return this.view.callTool(name, args).then((r) => data<T>(r));
  }

  private show(...nodes: Array<Node | null>): void {
    root.replaceChildren(...nodes.filter((n): n is Node => n !== null));
  }

  private header(kicker: string, title: string): HTMLElement {
    return el("header", {}, el("div", { class: "kicker" }, kicker), el("h1", {}, title));
  }

  private close(): void {
    void this.view.callTool("close", {}).catch(() => undefined);
  }

  async start(): Promise<void> {
    this.show(el("p", { class: "muted" }, "Looking at your accounts…"));
    try {
      const [mail, cal, list] = await Promise.all([
        this.call<Status>("email.account_status"),
        this.call<Status>("calendar.account_status"),
        this.call<{ providers: ProviderInfo[] }>("email.account_providers"),
      ]);
      this.mail = mail;
      this.cal = cal;
      // The directory; a Shell helper that doesn't answer leaves it empty, never blocking mail.
      this.directory = await this.call<{ services: ServiceRow[] }>("shell.services_list")
        .then((d) => d.services ?? [])
        .catch(() => []);
      // A connected service's account; one that doesn't answer shows as signed out.
      const withAccount = this.directory.filter((s) => s.connected && s.account && s.id in this.services);
      const statuses = await Promise.all(
        withAccount.map((s) => this.call<Status>(`${s.id}.account_status`).catch(() => ({ connected: false }) as Status)),
      );
      this.serviceStatus = Object.fromEntries(withAccount.map((s, i) => [s.id, statuses[i]!]));
      // Google sign-in needs both: a Google client in the helpers and a browser on this computer.
      this.providers = (list.providers ?? []).map((p) => ({ ...p, google_signin: !!p.google_signin && this.browser }));
    } catch (err) {
      this.show(
        this.header("Accounts", "Mail and calendar aren't available"),
        el("p", { class: "muted" }, `Foyelle's mail and calendar helpers didn't answer (${(err as Error).message}).`),
        el("div", { class: "actions" }, this.button("Close", () => this.close())),
      );
      return;
    }
    this.overview();
  }

  private button(label: string, onClick: () => void, cls = ""): HTMLButtonElement {
    const b = el("button", { type: "button", class: cls }, label);
    b.addEventListener("click", onClick);
    return b;
  }

  private row(name: string, s: Status, server: "email" | "calendar"): HTMLElement {
    const detail = s.connected ? `${s.address ?? ""}${s.calendar ? ` · ${s.calendar}` : ""}` : "Not connected";
    const action = s.connected && !s.managed
      ? this.button("Disconnect", () => void this.disconnect(server, name), "danger")
      : null;
    return el(
      "div",
      { class: "acct-row" },
      el("span", { class: `dot${s.connected ? " on" : ""}`, "aria-hidden": "true" }),
      el("div", { class: "what" }, el("div", { class: "name" }, name), el("div", { class: "detail" }, detail)),
      action,
    );
  }

  private serviceRow(s: ServiceRow): HTMLElement {
    const signedIn = !s.account || !!this.serviceStatus[s.id]?.connected;
    const detail = !s.connected
      ? s.summary
      : s.state === "failed"
        ? "Connected, but its helper isn't running"
        : signedIn
          ? s.always ? "Always on" : "Connected"
          : "Connected: sign in to use it";
    const actions: HTMLButtonElement[] = [];
    if (!s.connected) actions.push(this.button("Connect", () => void this.serviceAdd(s)));
    else {
      if (!signedIn && this.browser) actions.push(this.button("Sign in", () => void this.serviceConnect(s.id, s.title), "primary"));
      if (!s.always) actions.push(this.button("Disconnect", () => this.serviceDisconnect(s), "danger"));
    }
    return el(
      "div",
      { class: "acct-row" },
      el("span", { class: `dot${s.connected && signedIn ? " on" : ""}`, "aria-hidden": "true" }),
      el("div", { class: "what" },
        el("div", { class: "name" }, s.title, s.reviewed ? null : el("span", { class: "muted" }, " · added by you, not reviewed")),
        el("div", { class: "detail" }, detail)),
      ...actions,
    );
  }

  /** Waits until the Shell helper sees the service's helper running (it starts in a few seconds). */
  private async waitForService(id: string, want: boolean): Promise<ServiceRow | undefined> {
    let row: ServiceRow | undefined;
    for (let i = 0; i < 30; i++) {
      row = (await this.call<{ services: ServiceRow[] }>("shell.services_list")).services.find((s) => s.id === id);
      if (!row || row.connected !== want) break;
      if (!want || row.state === "active" || row.state === "unknown" || row.state === "failed") break;
      await new Promise((r) => setTimeout(r, 1000));
    }
    return row;
  }

  /** Connect a directory service: its helper starts, the harness picks it up, then the sign-in. */
  private async serviceAdd(s: ServiceRow): Promise<void> {
    this.show(this.header("Services", `Connecting ${s.title}…`), el("p", { class: "muted" }, "This takes a few seconds."));
    try {
      await this.call("shell.service_connect", { service_id: s.id });
      const row = await this.waitForService(s.id, true);
      if (row?.state === "failed") throw new Error(`${s.title}'s helper didn't start`);
      await this.call("services.reload");
    } catch (err) {
      this.show(
        this.header("Services", `${s.title} isn't connected`),
        el("p", {}, (err as Error).message),
        el("div", { class: "actions" }, this.button("Back to accounts", () => void this.start(), "primary")),
      );
      return;
    }
    if (s.account && s.id in this.services && this.browser) return this.serviceConnect(s.id, s.title);
    this.show(
      this.header(s.title, "You're connected"),
      el("p", {}, `Foyelle can now use ${s.title} when you ask. What you ask of it goes to ${s.title}, and nowhere else.`),
      el("div", { class: "actions" }, this.button("Done", () => void this.start(), "primary")),
    );
  }

  /** The service's own sign-in page, in the sign-in browser, like Google's. */
  private async serviceConnect(name: string, title: string): Promise<void> {
    const done = (ok: boolean, text: string) =>
      this.show(
        this.header(title, ok ? "You're connected" : "Not connected"),
        el("p", {}, text),
        el("div", { class: "actions" }, this.button(ok ? "Done" : "Back to accounts", () => void this.start(), "primary")),
      );
    this.show(
      this.header(title, `Sign in to ${title}`),
      el("p", { class: "muted" }, `${title}'s own sign-in page opens over the whole screen. Sign in there and allow Foyelle; you come back here by yourself. To stop, press Ctrl+W.`),
    );
    try {
      const { redirectUri } = await this.call<{ redirectUri: string }>("signin.begin");
      const started = await this.call<{ auth_url?: string; message?: string }>(`${name}.account_signin_start`, { redirect_uri: redirectUri });
      if (!started.auth_url) return done(false, started.message ?? `${title} didn't start a sign-in.`);
      const back = await this.call<{ code?: string; state?: string; error?: string }>("signin.open", { auth_url: started.auth_url });
      const out = await this.call<{ connected?: boolean; message?: string }>(`${name}.account_signin_finish`, back);
      done(!!out.connected, out.connected ? `Foyelle can now use ${title} when you ask.` : out.message ?? "The sign-in didn't finish.");
    } catch (err) {
      const message = (err as Error).message;
      done(false, /closed|cancelled/.test(message) ? "The sign-in page was closed before it finished." : message);
    } finally {
      await this.call("signin.end").catch(() => undefined);
    }
  }

  private serviceDisconnect(s: ServiceRow): void {
    const title = s.title;
    this.show(
      this.header("Services", `Disconnect ${title}?`),
      el("p", {}, s.account
        ? `Foyelle stops using ${title}, and its access is deleted from this computer. Nothing changes in your ${title} account.`
        : `Foyelle stops using ${title}. You can connect it again any time.`),
      el(
        "div",
        { class: "actions" },
        this.button("Disconnect", () => {
          void (async () => {
            if (s.account && s.id in this.services) await this.call(`${s.id}.account_disconnect`).catch(() => undefined);
            await this.call("shell.service_disconnect", { service_id: s.id });
            await this.waitForService(s.id, false);
            await this.call("services.reload").catch(() => undefined);
            await this.start();
          })().catch((err) => this.show(el("p", { class: "status" }, (err as Error).message)));
        }, "danger primary"),
        this.button("Keep it", () => this.overview()),
      ),
    );
  }

  /** Connected services first, then the directory by category. */
  private servicesSection(): HTMLElement {
    const on = this.directory.filter((s) => s.connected);
    const off = this.directory.filter((s) => !s.connected);
    const groups = [...new Set(off.map((s) => s.category))];
    return el(
      "section",
      {},
      el("h2", {}, "Services"),
      on.length ? el("div", { class: "acct-rows" }, ...on.map((s) => this.serviceRow(s))) : null,
      ...groups.map((g) => el("div", { class: "acct-group" },
        el("div", { class: "kicker" }, CATEGORY[g] ?? g),
        el("div", { class: "acct-rows" }, ...off.filter((s) => s.category === g).map((s) => this.serviceRow(s))))),
      el("p", { class: "privacy-note" }, "Foyelle reaches these services from this computer, with their own connections. What you ask of one goes to that service, and nowhere else. Foyelle reviewed each one; what they return is treated as untrusted."),
    );
  }

  private overview(): void {
    const any = this.mail.connected || this.cal.connected;
    this.show(
      this.header("Accounts", any ? "Your mail and calendar" : "Bring your mail and calendar"),
      el("div", { class: "acct-rows" }, this.row("Mail", this.mail, "email"), this.row("Calendar", this.cal, "calendar")),
      el(
        "p",
        { class: "privacy-note" },
        "Foyelle reads your mail and calendar from this computer. Sending mail or answering an invitation always asks you first. Your password stays on this computer.",
      ),
      this.directory.length ? this.servicesSection() : null,
      el(
        "div",
        { class: "actions" },
        !this.mail.connected || !this.cal.connected
          ? this.button(any ? "Connect another" : "Connect", () => this.choose(), "primary")
          : null,
        this.button("Close", () => this.close()),
      ),
    );
  }

  private async disconnect(server: "email" | "calendar", name: string): Promise<void> {
    this.show(
      this.header("Accounts", `Disconnect ${name.toLowerCase()}?`),
      el("p", {}, "Foyelle stops reading it, and the password is deleted from this computer. Nothing changes in the account itself."),
      el(
        "div",
        { class: "actions" },
        this.button("Disconnect", () => {
          void this.call(`${server}.account_disconnect`)
            .then(() => this.start())
            .catch((err) => this.show(el("p", { class: "status" }, (err as Error).message)));
        }, "danger primary"),
        this.button("Keep it", () => this.overview()),
      ),
    );
  }

  private choose(): void {
    const tiles = this.providers.map((p) => {
      const note = p.google_signin ? "Mail, calendar" : NOTES[p.id] ?? "";
      const b = el("button", { type: "button" }, p.name, el("span", {}, note));
      b.addEventListener("click", () => (p.google_signin ? this.google(p) : this.howto(p)));
      return b;
    });
    this.show(
      this.header("Connect", "Which mail do you use?"),
      el("div", { class: "providers" }, ...tiles),
      el("p", { class: "privacy-note" }, "Using Outlook or Hotmail? Microsoft needs a different kind of sign-in that Foyelle doesn't have yet."),
      el("div", { class: "actions" }, this.button("Back", () => this.overview())),
    );
  }

  private howto(p: ProviderInfo): void {
    const steps = el("ol", { class: "steps" }, ...p.app_password_steps.map((s) => el("li", {}, s)));
    const text = el(
      "div",
      { class: "text" },
      el("p", {}, `Foyelle uses an app password: a separate password just for Foyelle, which you can remove from your ${p.name === "Other" ? "provider" : p.name} account at any time.`),
      steps,
      p.app_password_url ? el("div", { class: "url" }, p.app_password_url) : null,
      p.app_password_url ? el("p", { class: "muted small" }, "Scan the code to open this page on your phone.") : null,
    );
    this.show(
      this.header(p.name, p.id === "other" ? "You'll need your mail servers" : "First, make an app password"),
      p.note && !p.google_signin ? el("p", { class: "notice" }, p.note) : null,
      el("div", { class: "howto" }, text, p.app_password_url ? el("div", { class: "qr" }, qr(p.app_password_url)) : null),
      el(
        "div",
        { class: "actions" },
        this.button(p.id === "other" ? "I have them" : "I have it", () => this.form(p), "primary"),
        this.button("Back", () => this.choose()),
      ),
    );
  }

  // ----- Sign in with Google: a QR code per helper, approved on the phone ---------------------

  private google(p: ProviderInfo): void {
    const wantMail = el("input", { type: "checkbox" }) as HTMLInputElement;
    const wantCal = el("input", { type: "checkbox" }) as HTMLInputElement;
    wantMail.checked = !this.mail.connected;
    wantCal.checked = !this.cal.connected;
    const status = el("p", { class: "status", role: "status" });
    const start = this.button("Sign in with Google", () => {
      if (!wantMail.checked && !wantCal.checked) {
        status.textContent = "Choose mail, calendar, or both.";
        return;
      }
      void this.googleRun(p, [...(wantMail.checked ? ["email"] : []), ...(wantCal.checked ? ["calendar"] : [])] as Array<"email" | "calendar">);
    }, "primary");
    const appPassword = this.button("Use an app password instead (mail only)", () => this.howto({ ...p, google_signin: false, note: null }));
    this.show(
      this.header(p.name, "Sign in with Google"),
      el("p", {}, "Google's own sign-in page opens over the whole screen. Sign in there as usual; Foyelle never sees your Google password."),
      el(
        "ol",
        { class: "steps" },
        el("li", {}, "Sign in to your Google account, with your usual 2-Step Verification."),
        el("li", {}, "Allow Foyelle. Mail and calendar are asked for one at a time, so each part of Foyelle gets only what it needs."),
        el("li", {}, "You come back here by yourself when it's done. To stop, press Ctrl+W."),
      ),
      el("div", { class: "toggles" }, el("label", {}, wantMail, "Mail"), el("label", {}, wantCal, "Calendar")),
      el("p", { class: "privacy-note" }, "Google will show that Foyelle hasn't been reviewed by Google yet. That's expected for now. The sign-in page can only reach Google, and nothing from it is kept afterwards."),
      status,
      el("div", { class: "actions" }, start, appPassword, this.button("Back", () => this.choose())),
    );
  }

  private async googleRun(p: ProviderInfo, parts: Array<"email" | "calendar">): Promise<void> {
    const results: Array<{ ok: boolean; text: string; hint?: string }> = [];
    let redirect: string;
    try {
      redirect = (await this.call<{ redirectUri: string }>("signin.begin")).redirectUri;
    } catch (err) {
      this.show(
        this.header(p.name, "Signing in isn't available"),
        el("p", { class: "muted" }, (err as Error).message),
        el("div", { class: "actions" }, this.button("Back", () => this.choose())),
      );
      return;
    }
    for (const [i, server] of parts.entries()) {
      const label = server === "email" ? "Mail" : "Calendar";
      this.show(
        this.header(p.name, parts.length > 1 ? `${label}: allow Foyelle (${i + 1} of ${parts.length})` : `${label}: allow Foyelle`),
        el("p", { class: "muted" }, `Google's page is open. Allow Foyelle to use your ${server === "email" ? "Gmail" : "Google Calendar"}, and you'll come back here.`),
      );
      try {
        const started = await this.call<{ auth_url: string }>(`${server}.account_google_start`, { redirect_uri: redirect });
        const back = await this.call<{ code?: string; state?: string; error?: string }>("signin.open", { auth_url: started.auth_url });
        const out = await this.call<Outcome>(`${server}.account_google_finish`, back);
        if (out.connected) {
          const text = server === "email"
            ? "Mail: Foyelle can read and send your Gmail."
            : `Calendar: Foyelle uses “${out.calendar}”${(out.calendars?.length ?? 0) > 1 ? ` (it has ${out.calendars!.length} calendars)` : ""}.`;
          results.push({ ok: true, text });
        } else {
          results.push({ ok: false, text: `${label} isn't connected.`, hint: out.stage === "google" ? out.message : hint(out.stage, p.id) });
        }
      } catch (err) {
        const message = (err as Error).message;
        const closed = /closed|cancelled|too long/.test(message);
        results.push({
          ok: false,
          text: `${label} isn't connected.`,
          hint: !closed ? message : /too long/.test(message)
            ? "The sign-in took more than ten minutes, so Foyelle closed it."
            : "The sign-in page was closed before it finished.",
        });
        if (closed) break; // the person stopped: don't open Google again for the next part
      }
    }
    await this.call("signin.end").catch(() => undefined); // closes the browser, forgets its profile
    const allOk = results.every((r) => r.ok);
    const list = el("ul", { class: "checks" }, ...results.map((r) =>
      el("li", { class: r.ok ? "ok" : "bad" }, el("span", { class: "mark" }, r.ok ? "✓" : "✗"),
        el("span", {}, r.text, r.hint ? el("span", { class: "hint" }, r.hint) : null))));
    this.show(
      this.header(p.name, allOk ? "You're connected" : "Not everything worked"),
      list,
      allOk ? el("p", {}, "Try asking: “What's new in my mail?” or “What's on my calendar this week?”") : null,
      el(
        "div",
        { class: "actions" },
        allOk ? this.button("Done", () => void this.start(), "primary") : this.button("Try again", () => this.google(p), "primary"),
        this.button(allOk ? "Close" : "Back to accounts", () => (allOk ? this.close() : void this.start())),
      ),
    );
  }

  private form(p: ProviderInfo, error?: string, keep?: Record<string, string>): void {
    const v = keep ?? {};
    const input = (name: string, type = "text", placeholder = "", value = v[name] ?? "") => {
      const i = el("input", { type, name, placeholder, autocomplete: "off", spellcheck: "false" }) as HTMLInputElement;
      i.value = value;
      return i;
    };
    const address = input("address", "email", "you@example.org");
    const password = input("password", "password", "app password", "");
    const name = input("display_name", "text", "optional");
    const other = p.id === "other";
    const imapHost = input("imap_host", "text", "imap.example.org");
    const imapPort = input("imap_port", "text", "993", v.imap_port ?? "993");
    const smtpHost = input("smtp_host", "text", "smtp.example.org");
    const smtpPort = input("smtp_port", "text", "465", v.smtp_port ?? "465");
    const caldav = input("url", "text", "https://… (optional)");
    const wantMail = el("input", { type: "checkbox" }) as HTMLInputElement;
    const wantCal = el("input", { type: "checkbox" }) as HTMLInputElement;
    wantMail.checked = v.mail !== "0";
    wantCal.checked = v.cal !== "0";
    const status = el("p", { class: "status", role: "status" }, error ?? "");
    const go = this.button("Connect", () => {
      const values: Record<string, string> = {
        address: address.value.trim(),
        display_name: name.value.trim(),
        imap_host: imapHost.value.trim(),
        imap_port: imapPort.value.trim(),
        smtp_host: smtpHost.value.trim(),
        smtp_port: smtpPort.value.trim(),
        url: caldav.value.trim(),
        mail: wantMail.checked ? "1" : "0",
        cal: wantCal.checked ? "1" : "0",
      };
      if (!values.address || !password.value) {
        status.textContent = "Both your address and the app password are needed.";
        return;
      }
      if (!wantMail.checked && !wantCal.checked) {
        status.textContent = "Choose mail, calendar, or both.";
        return;
      }
      void this.connect(p, values, password.value);
      password.value = "";
    }, "primary");
    const rows: Node[] = [
      el("label", {}, "Email address"), address,
      el("label", {}, "App password"), password,
      el("label", {}, "Your name"), name,
    ];
    if (other) {
      rows.push(
        el("label", {}, "Mail server (IMAP)"), el("div", { class: "pair" }, imapHost, imapPort),
        el("label", {}, "Sending server (SMTP)"), el("div", { class: "pair" }, smtpHost, smtpPort),
        el("label", {}, "Calendar (CalDAV)"), caldav,
      );
    }
    const calendarPossible = p.calendar || other;
    this.show(
      this.header(p.name, "Connect your account"),
      el("div", { class: "form" }, ...rows),
      el(
        "div",
        { class: "toggles" },
        el("label", {}, wantMail, "Mail"),
        calendarPossible ? el("label", {}, wantCal, "Calendar") : null,
      ),
      status,
      el("div", { class: "actions" }, go, this.button("Back", () => this.howto(p))),
    );
    if (!calendarPossible) wantCal.checked = false;
    queueMicrotask(() => (address.value ? password : address).focus());
  }

  private async connect(p: ProviderInfo, v: Record<string, string>, password: string): Promise<void> {
    const other = p.id === "other";
    const wantCal = v.cal === "1" && (p.calendar || (other && !!v.url));
    const items: Array<{ label: string; li: HTMLElement }> = [];
    const list = el("ul", { class: "checks" });
    const add = (label: string) => {
      const li = el("li", {}, el("span", { class: "mark" }, "…"), el("span", {}, `${label}: checking…`));
      list.append(li);
      items.push({ label, li });
      return li;
    };
    const mark = (li: HTMLElement, ok: boolean, text: string, detail?: string) => {
      li.className = ok ? "ok" : "bad";
      li.replaceChildren(
        el("span", { class: "mark" }, ok ? "✓" : "✗"),
        el("span", {}, text, detail ? el("span", { class: "hint" }, detail) : null),
      );
    };
    this.show(this.header(p.name, "Checking your account…"), list);

    let allOk = true;
    if (v.mail === "1") {
      const li = add("Mail");
      try {
        const args: Record<string, unknown> = { provider: p.id, address: v.address, password };
        if (v.display_name) args.display_name = v.display_name;
        if (other) {
          args.imap_host = v.imap_host;
          args.imap_port = Number(v.imap_port) || 993;
          args.smtp_host = v.smtp_host;
          args.smtp_port = Number(v.smtp_port) || 465;
        }
        const out = await this.call<Outcome>("email.account_connect", args);
        if (out.connected) mark(li, true, "Mail: Foyelle can read and send your mail.");
        else {
          allOk = false;
          mark(li, false, "Mail isn't connected.", hint(out.stage, p.id));
        }
      } catch (err) {
        allOk = false;
        mark(li, false, "Mail isn't connected.", (err as Error).message);
      }
    }
    if (wantCal) {
      const li = add("Calendar");
      try {
        const args: Record<string, unknown> = { provider: p.id, address: v.address, password };
        if (other) args.url = v.url;
        const out = await this.call<Outcome>("calendar.account_connect", args);
        if (out.connected) {
          const more = (out.calendars?.length ?? 0) > 1 ? ` (it has ${out.calendars!.length} calendars)` : "";
          mark(li, true, `Calendar: Foyelle uses “${out.calendar}”${more}.`);
        } else {
          allOk = false;
          mark(li, false, "Calendar isn't connected.", hint(out.stage, p.id));
        }
      } catch (err) {
        allOk = false;
        mark(li, false, "Calendar isn't connected.", (err as Error).message);
      }
    }
    password = "";
    root.querySelector("h1")!.textContent = allOk ? "You're connected" : "Not everything worked";
    root.append(
      allOk
        ? el("p", {}, "Try asking: “What's new in my mail?” or “What's on my calendar this week?”")
        : el("p", { class: "muted" }, "You can try again with a new app password."),
      el(
        "div",
        { class: "actions" },
        allOk ? this.button("Done", () => void this.start(), "primary") : this.button("Try again", () => this.form(p, undefined, v), "primary"),
        this.button(allOk ? "Close" : "Back to accounts", () => (allOk ? this.close() : void this.start())),
      ),
    );
  }
}

void startView("foyelle-accounts", (view) => {
  view.ontoolinput = (input) => void new Accounts(view, input).start();
});
