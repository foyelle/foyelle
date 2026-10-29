/**
 * The bots sidebar and the bot dialog. The sidebar lists Foyelle and the person's bots, each with
 * its picture, its last line and a dot when something came in or waits; the dialog makes a bot or
 * shows one's details (job, services, sharing with the online model, routines).
 */
import { FOYELLE, pictureOf, whenShort, type Bot, type Givable, type Routine } from "./bot-model";
import { h } from "./dom";
import { emberElement } from "./ember-art";

export interface BotRow {
  bot: Bot;
  line: string;
  /** New routine results the person hasn't seen, and items waiting for them. */
  results: number;
  waiting: number;
  unread: boolean;
  busy: boolean;
}

/** A bot's picture: Foyelle's ember, the bot's emoji, or its initial on its colour. */
export function avatar(bot: Bot, size: "sm" | "md" = "sm"): HTMLElement {
  if (bot.id === FOYELLE) return h("span", { class: `avatar avatar-${size} avatar-ember`, "aria-hidden": "true" }, emberElement(size === "md" ? "sm" : "xs"));
  const p = pictureOf(bot);
  const el = h("span", { class: `avatar avatar-${size}`, "aria-hidden": "true" }, p.emoji ?? p.initial);
  if (!p.emoji) el.style.setProperty("--avatar-hue", String(p.hue));
  else el.classList.add("avatar-emoji");
  return el;
}

export class BotSidebar {
  constructor(
    private readonly list: HTMLElement,
    private readonly handlers: { select: (id: string) => void; details: (id: string) => void },
  ) {}

  render(rows: BotRow[], current: string): void {
    this.list.replaceChildren(
      ...rows.map((r) => {
        const count = r.waiting || r.results;
        const badge = r.waiting
          ? h("span", { class: "bot-badge is-waiting", title: `${r.waiting} waiting for you` }, String(r.waiting))
          : r.results || r.unread
            ? h("span", { class: "bot-badge", title: count ? `${count} new` : "New" }, count ? String(count) : "")
            : null;
        const row = h(
          "button",
          {
            type: "button", class: `bot-row${r.bot.id === current ? " is-current" : ""}${r.busy ? " is-busy" : ""}`,
            "aria-current": r.bot.id === current ? "true" : undefined, title: r.bot.job || r.bot.name,
          },
          avatar(r.bot),
          h("span", { class: "bot-text" }, h("span", { class: "bot-name" }, r.bot.name), h("span", { class: "bot-line" }, r.busy ? "Working…" : r.line || r.bot.job)),
          badge,
        );
        row.addEventListener("click", () => this.handlers.select(r.bot.id));
        row.addEventListener("dblclick", () => this.handlers.details(r.bot.id));
        return row;
      }),
    );
  }
}

/** What the dialog saves: a new or changed bot (Foyelle: only its sharing). */
export interface BotDraft {
  name: string;
  job: string;
  picture: string;
  services: string[];
  shareOnline: string[];
}

export interface BotDialogHooks {
  save: (draft: BotDraft) => Promise<void>;
  remove?: () => Promise<void>;
  routines?: () => Promise<Routine[]>;
  routine?: (id: string, action: "pause" | "resume" | "remove") => Promise<void>;
}

const PICTURES = ["", "✈️", "🏡", "📬", "📅", "🧾", "🛒", "🎒", "🩺", "🌱", "🎵", "💼"];

/** The dialog for a new bot, or for one's details. Resolves when it closes. */
export function openBotDialog(host: HTMLElement, bot: Bot | null, givable: Givable[], foyelleShare: string[], hooks: BotDialogHooks): Promise<void> {
  const isFoyelle = bot?.id === FOYELLE;
  const dialog = h("dialog", { class: "bot-dialog", "aria-label": bot ? `${bot.name}'s details` : "New bot" });
  const form = h("form", { method: "dialog", class: "bot-form" });
  const error = h("p", { class: "bot-error", role: "alert", hidden: true });

  const name = h("input", { type: "text", name: "name", maxlength: "40", required: true, placeholder: "Travel, Home, Bills…", autocomplete: "off" });
  name.value = bot?.name ?? "";
  const job = h("textarea", { name: "job", rows: "3", maxlength: "240", placeholder: "Watches flight prices for our trips and tells me when they drop." });
  job.value = bot?.job ?? "";
  let picture = bot?.picture ?? "";
  const pictures = h("div", { class: "bot-pictures", role: "radiogroup", "aria-label": "Picture" });
  const drawPictures = () =>
    pictures.replaceChildren(
      ...PICTURES.map((p) => {
        const b = h("button", { type: "button", class: "bot-picture", role: "radio", "aria-checked": String(p === picture), title: p ? p : "Its first letter" }, p || (Array.from(name.value.trim())[0] ?? "A").toLocaleUpperCase());
        b.addEventListener("click", () => {
          picture = p;
          drawPictures();
        });
        return b;
      }),
    );
  name.addEventListener("input", drawPictures);
  drawPictures();

  const given = new Set(isFoyelle ? givable.map((g) => g.id) : bot?.services ?? []);
  const shared = new Set(isFoyelle ? foyelleShare : bot?.shareOnline ?? []);
  const services = h("fieldset", { class: "bot-services" }, h("legend", {}, isFoyelle ? "Routines may share with the online model" : "What it may use"));
  const drawServices = () => {
    const rows = givable.map((g) => {
      const use = h("input", { type: "checkbox", checked: given.has(g.id), disabled: isFoyelle });
      use.addEventListener("change", () => {
        if (use.checked) given.add(g.id);
        else {
          given.delete(g.id);
          shared.delete(g.id);
        }
        drawServices();
      });
      const share = g.private
        ? h("input", { type: "checkbox", checked: shared.has(g.id), disabled: !given.has(g.id), title: "Its routines may send what they read here to the online model" })
        : null;
      share?.addEventListener("change", () => (share.checked ? shared.add(g.id) : shared.delete(g.id)));
      return h(
        "div",
        { class: "bot-service" },
        isFoyelle ? null : h("label", {}, use, g.title),
        isFoyelle ? h("span", {}, g.title) : null,
        share ? h("label", { class: "bot-share" }, share, isFoyelle ? "share" : "routines may share online") : h("span", { class: "bot-share is-public" }, "public"),
      );
    });
    services.replaceChildren(services.firstElementChild!, ...(rows.length ? rows : [h("p", { class: "bot-hint" }, "Connect services on the Accounts card first.")]));
  };
  drawServices();

  const routines = h("section", { class: "bot-routines", hidden: !hooks.routines }, h("h3", {}, "Routines"));
  const drawRoutines = async () => {
    if (!hooks.routines) return;
    let list: Routine[];
    try {
      list = await hooks.routines();
    } catch (err) {
      routines.replaceChildren(routines.firstElementChild!, h("p", { class: "bot-hint" }, `Couldn't read the routines (${(err as Error).message}).`));
      return;
    }
    const rows = list.map((r) => {
      const act = (action: "pause" | "resume" | "remove", label: string) => {
        const b = h("button", { type: "button", class: "btn btn-small" }, label);
        b.addEventListener("click", async () => {
          b.disabled = true;
          try {
            await hooks.routine?.(r.id, action);
          } catch (err) {
            error.textContent = (err as Error).message;
            error.hidden = false;
          }
          await drawRoutines();
        });
        return b;
      };
      const last = r.lastRun ? `last ran ${whenShort(Date.parse(r.lastRun) / 1000)}${r.lastStatus && r.lastStatus !== "ok" ? ` (${r.lastStatus})` : ""}` : "not run yet";
      return h(
        "li",
        { class: `bot-routine${r.paused ? " is-paused" : ""}` },
        h("div", { class: "bot-routine-text" }, h("strong", {}, r.name), h("span", {}, `${r.paused ? "Paused · " : ""}${r.schedule} · ${last}`), r.prompt ? h("span", { class: "bot-routine-prompt" }, r.prompt) : null),
        h("div", { class: "bot-routine-actions" }, r.paused ? act("resume", "Resume") : act("pause", "Pause"), act("remove", "Remove")),
      );
    });
    routines.replaceChildren(
      routines.firstElementChild!,
      rows.length ? h("ul", {}, ...rows) : h("p", { class: "bot-hint" }, `None yet. Ask ${bot?.name ?? "the bot"} in its chat, for example “every morning at 7:30, tell me what's on today”.`),
    );
  };

  const save = h("button", { type: "submit", class: "btn btn-primary" }, bot ? "Save" : "Create");
  const cancel = h("button", { type: "button", class: "btn" }, bot ? "Close" : "Cancel");
  const remove = hooks.remove ? h("button", { type: "button", class: "btn btn-danger" }, "Delete bot") : null;

  const parts: Array<HTMLElement | null> = [
    h("h2", { class: "bot-title" }, bot ? (isFoyelle ? "Foyelle" : bot.name) : "A new bot"),
    isFoyelle ? h("p", { class: "bot-hint" }, "Foyelle uses everything you connected. Its routines read and report; choose which private services they may share with the online model.") : null,
    isFoyelle ? null : h("label", { class: "bot-field" }, h("span", {}, "Name"), name),
    isFoyelle ? null : h("div", { class: "bot-field" }, h("span", {}, "Picture"), pictures),
    isFoyelle ? null : h("label", { class: "bot-field" }, h("span", {}, "Its job"), job),
    services,
    routines,
    error,
    h("div", { class: "bot-actions" }, remove, h("span", { class: "spacer" }), cancel, save),
  ];
  form.append(...parts.filter((n): n is HTMLElement => !!n));
  dialog.append(form);
  host.append(dialog);

  return new Promise((resolve) => {
    const close = () => {
      dialog.close();
      dialog.remove();
      resolve();
    };
    cancel.addEventListener("click", close);
    dialog.addEventListener("cancel", (e) => {
      e.preventDefault();
      close();
    });
    remove?.addEventListener("click", async () => {
      if (remove.dataset.confirm !== "1") {
        remove.dataset.confirm = "1";
        remove.textContent = "Delete it and its routines?";
        return;
      }
      remove.disabled = true;
      try {
        await hooks.remove!();
        close();
      } catch (err) {
        error.textContent = (err as Error).message;
        error.hidden = false;
        remove.disabled = false;
      }
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      save.disabled = true;
      error.hidden = true;
      try {
        await hooks.save({
          name: isFoyelle ? "Foyelle" : name.value.trim(),
          job: job.value.trim(),
          picture,
          services: isFoyelle ? [] : [...given],
          shareOnline: [...shared],
        });
        close();
      } catch (err) {
        error.textContent = (err as Error).message;
        error.hidden = false;
        save.disabled = false;
      }
    });
    dialog.showModal();
    if (!isFoyelle) name.focus();
    void drawRoutines();
  });
}
