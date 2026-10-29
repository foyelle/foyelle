/**
 * First start: Foyelle introduces itself and asks three things, in the middle of the screen.
 * No account and no sign-in: a name, a look, and a key for the disk. Everything is saved
 * through the Shell and System helpers, like any card would.
 */
import { h } from "./dom";
import { emberElement } from "./ember-art";
import type { Theme } from "./prefs";

export interface WelcomeHost {
  /** A helper's tool, called as the canvas's own card; resolves with its result, throws on an error. */
  callTool(server: string, name: string, args: Record<string, unknown>): Promise<Record<string, unknown>>;
  /** Shows a look on screen without saving it. */
  preview(theme: Theme, textScale: number): void;
  /** The microphone can be used. */
  voice(): boolean;
  /** The welcome is over; the home screen takes its place. */
  finished(name: string | null): void;
  /** Mail and calendar can still be connected (the helpers run and aren't both connected). */
  canConnectAccounts(): boolean;
  /** Opens the Accounts card. */
  openAccounts(): void;
}

export interface Look {
  theme: Theme;
  title: string;
  note: string;
}

export const LOOKS: readonly Look[] = [
  { theme: "light", title: "Daylight", note: "Bright and warm" },
  { theme: "dark", title: "Evening", note: "Soft and dim" },
  { theme: "time", title: "Follows the day", note: "Light by day, dim after seven" },
];

export const LARGE_TEXT = 1.25;

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export class Welcome {
  /** Bumped on every start or stop, so a script still running from before goes quiet. */
  private run = 0;
  private readonly lines: HTMLElement;
  private readonly answer: HTMLElement;
  private name: string | null = null;

  constructor(
    private readonly root: HTMLElement,
    private readonly host: WelcomeHost,
  ) {
    this.lines = h("div", { class: "welcome-lines", "aria-live": "polite" });
    this.answer = h("div", { class: "welcome-answer" });
    const skip = h("button", { type: "button", class: "welcome-skip" }, "Skip for now");
    skip.addEventListener("click", () => void this.finish(true));
    root.replaceChildren(emberElement("xl"), this.lines, this.answer, skip);
  }

  get active(): boolean {
    return !this.root.hidden;
  }

  start(look: { name: string | null; theme: Theme; textScale: number }): void {
    const run = ++this.run;
    this.name = look.name;
    this.root.hidden = false;
    this.lines.replaceChildren();
    this.answer.replaceChildren();
    void this.script(run, look).catch(() => undefined);
  }

  private async script(run: number, look: { theme: Theme; textScale: number }): Promise<void> {
    await this.say(run, ["Hi, I'm Foyelle.", "I live here, on this computer, and I'm here to help with whatever you need.", "What should I call you?"]);
    await this.askName(run);
    await this.say(run, [`Nice to meet you, ${this.name}.`, "Which feels more like home?"], true);
    await this.askLook(run, look);
    await this.askKey(run);
    if (this.host.canConnectAccounts() && (await this.askAccounts(run))) {
      await this.finish(false);
      this.host.openAccounts();
      return;
    }
    await this.say(run, [
      `That's everything, ${this.name}.`,
      this.host.voice() ? "Ask me anything: type below, or hold the microphone and talk." : "Ask me anything: just type below.",
    ], true);
    await this.ask(run, (resolve) => {
      const go = h("button", { type: "button", class: "welcome-go primary" }, "Let's begin");
      go.addEventListener("click", () => resolve());
      return [go];
    });
    await this.finish(false);
  }

  // ----- the three questions ---------------------------------------------------

  private askName(run: number): Promise<void> {
    return this.ask(run, (resolve) => {
      const input = h("input", { type: "text", class: "welcome-input", maxlength: "60", placeholder: "Your name", "aria-label": "Your name", autocomplete: "off" });
      input.value = this.name ?? "";
      const status = h("p", { class: "welcome-status", role: "status" });
      const save = h("button", { type: "submit", class: "primary" }, "That's me");
      const form = h("form", { class: "welcome-row" }, input, save);
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const name = input.value.trim();
        if (!name) {
          input.focus();
          return;
        }
        save.disabled = true;
        status.textContent = "";
        this.host
          .callTool("shell", "user_name_set", { name })
          .then((r) => {
            this.name = typeof r.name === "string" ? r.name : name;
            resolve();
          })
          .catch((err: Error) => {
            status.textContent = `I couldn't keep that (${err.message}). Try again?`;
            save.disabled = false;
          });
      });
      setTimeout(() => input.focus(), 0);
      return [form, status];
    });
  }

  private askLook(run: number, start: { theme: Theme; textScale: number }): Promise<void> {
    let theme: Theme = start.theme === "system" ? "light" : start.theme;
    let scale = start.textScale;
    return this.ask(run, (resolve) => {
      const cards = LOOKS.map((look) => {
        const b = h(
          "button",
          { type: "button", class: "look", "aria-pressed": String(look.theme === theme), "data-look": look.theme },
          h("span", { class: `look-swatch look-${look.theme}`, "aria-hidden": "true" }, h("span", { class: "look-dot" })),
          h("span", { class: "look-title" }, look.title),
          h("span", { class: "look-note" }, look.note),
        );
        b.addEventListener("click", () => {
          theme = look.theme;
          for (const c of cards) c.setAttribute("aria-pressed", String(c === b));
          this.host.preview(theme, scale);
        });
        return b;
      });
      const larger = h("button", { type: "button", class: "look-larger", "aria-pressed": String(scale > 1) }, "Larger text");
      larger.addEventListener("click", () => {
        scale = scale > 1 ? 1 : LARGE_TEXT;
        larger.setAttribute("aria-pressed", String(scale > 1));
        this.host.preview(theme, scale);
      });
      const status = h("p", { class: "welcome-status", role: "status" });
      const save = h("button", { type: "button", class: "primary" }, "This one");
      save.addEventListener("click", () => {
        save.disabled = true;
        Promise.all([
          this.host.callTool("shell", "settings_set", { key: "ui.theme", value: theme }),
          this.host.callTool("shell", "settings_set", { key: "ui.text_scale", value: scale }),
        ])
          .then(() => resolve())
          .catch((err: Error) => {
            status.textContent = `I couldn't keep that (${err.message}). Try again?`;
            save.disabled = false;
          });
      });
      this.host.preview(theme, scale);
      return [h("div", { class: "looks", role: "group", "aria-label": "Looks" }, ...cards), h("div", { class: "welcome-row" }, larger, save), status];
    });
  }

  /** Only while the disk still opens with the public starter key. */
  private async askKey(run: number): Promise<void> {
    let disk: Record<string, unknown>;
    try {
      disk = await this.host.callTool("system", "disk_status", {});
    } catch {
      return; // no System helper, or no encrypted disk to speak of
    }
    if (disk.encrypted !== true || disk.initial_passphrase !== true) return;
    await this.say(run, [
      "One more thing: a key for your home.",
      "Everything on this computer is locked, so no one else can read it, even if the computer is taken. " +
        "Right now it opens with the starter key that every new Foyelle has. Let's make one only you know.",
      "I'll ask you to confirm, then the screen changes to a key prompt. Type your new key there, and you'll be right back here.",
    ], true);
    const made = await this.ask<boolean>(run, (resolve) => {
      const status = h("p", { class: "welcome-status", role: "status" });
      const make = h("button", { type: "button", class: "primary" }, "Make my key");
      const later = h("button", { type: "button" }, "Later");
      make.addEventListener("click", () => {
        make.disabled = later.disabled = true;
        status.textContent = "Waiting for you to confirm…";
        this.host
          .callTool("system", "disk_passphrase", {})
          .then(() => resolve(true))
          .catch(() => resolve(false));
      });
      later.addEventListener("click", () => resolve(false));
      return [h("div", { class: "welcome-row" }, make, later), status];
    });
    await this.say(run, [
      made
        ? "The key prompt is open. Keep your key somewhere safe: you'll type it each time the computer starts."
        : "No problem. Whenever you like, ask me to change the key to your disk.",
    ], true);
  }

  /** Optional: bring mail and calendar now, or later from the home screen. */
  private async askAccounts(run: number): Promise<boolean> {
    await this.say(run, [
      "Would you like me to look after your mail and calendar too?",
      "I'd read them here on this computer, and I always ask before sending anything or answering an invitation.",
    ], true);
    return this.ask<boolean>(run, (resolve) => {
      const yes = h("button", { type: "button", class: "primary" }, "Connect them");
      const later = h("button", { type: "button" }, "Later");
      yes.addEventListener("click", () => resolve(true));
      later.addEventListener("click", () => resolve(false));
      return [h("div", { class: "welcome-row" }, yes, later)];
    });
  }

  // ----- plumbing ----------------------------------------------------------------

  /** Foyelle's lines, one after another; `fresh` clears what was said before. */
  private async say(run: number, lines: string[], fresh = false): Promise<void> {
    if (run !== this.run) throw new Error("stopped");
    if (fresh) this.lines.replaceChildren();
    this.answer.replaceChildren();
    for (const [i, text] of lines.entries()) {
      if (i) await pause(Math.min(1400, 300 + text.length * 18));
      if (run !== this.run) throw new Error("stopped");
      this.lines.append(h("p", { class: "welcome-line" }, text));
    }
    await pause(350);
  }

  /** Shows an answer and waits for it. */
  private ask<T = void>(run: number, build: (resolve: (v: T) => void) => Node[]): Promise<T> {
    if (run !== this.run) return Promise.reject(new Error("stopped"));
    return new Promise<T>((resolve) => {
      this.answer.replaceChildren(...build((v) => {
        if (run === this.run) resolve(v);
      }));
    });
  }

  private async finish(skipped: boolean): Promise<void> {
    this.run++;
    try {
      await this.host.callTool("shell", "settings_set", { key: "ui.welcomed", value: true });
    } catch {
      // Not kept: the welcome comes back next time, which is better than never asking.
    }
    this.root.hidden = true;
    this.host.finished(skipped ? null : this.name);
  }
}
