/**
 * The card gallery: every card that can be opened, with a search, a line on what each shows, where
 * it comes from and a drawing of it. The canvas gives the catalogue as the tool input (app.ts,
 * card-catalog.ts) and opens a card when this view calls `card_open {key}`.
 */
import { SECTIONS, fromLine, searchCards, type CardEntry } from "../card-catalog";
import { cardArt } from "./card-art";
import { el, startView, type ViewConnection } from "./view-runtime";

const root = document.getElementById("root")!;

/** The drawing as SVG nodes (fixed markup from card-art.ts, parsed rather than set as HTML). */
function art(card: CardEntry): Element {
  const doc = new DOMParser().parseFromString(cardArt(card.art), "image/svg+xml");
  return document.importNode(doc.documentElement, true);
}

function isCard(v: unknown): v is CardEntry {
  const c = v as CardEntry;
  return !!c && typeof c.key === "string" && typeof c.title === "string" && typeof c.uri === "string";
}

class Gallery {
  private cards: CardEntry[] = [];
  private query = "";
  private readonly search = el("input", {
    type: "search", class: "card-search", placeholder: "Search by name, what it shows, or where it comes from",
    "aria-label": "Search cards", spellcheck: "false", autocomplete: "off",
  });
  private readonly count = el("p", { class: "status", "aria-live": "polite" });
  private readonly list = el("div", { class: "card-sections" });
  private readonly status = el("p", { class: "status", role: "status" });

  constructor(private readonly view: ViewConnection) {
    this.search.addEventListener("input", () => {
      this.query = this.search.value;
      this.renderList();
    });
    this.search.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        const first = searchCards(this.cards, this.query)[0];
        if (first) void this.open(first);
      } else if (ev.key === "Escape" && this.search.value) {
        ev.stopPropagation();
        this.search.value = this.query = "";
        this.renderList();
      }
    });
  }

  show(input: Record<string, unknown>): void {
    this.cards = Array.isArray(input.cards) ? input.cards.filter(isCard) : [];
    root.replaceChildren(
      el("header", { class: "cards-head" },
        el("div", {},
          el("div", { class: "kicker" }, "Cards"),
          el("h1", {}, "Everything I can show you"),
          el("p", { class: "muted" },
            "Each card comes from a helper on this computer or an online service you connected. Open one here, or ask me in your own words."),
        ),
      ),
      el("div", { class: "card-find" }, this.search, this.count),
      this.list,
      this.status,
    );
    this.renderList();
    queueMicrotask(() => this.search.focus());
  }

  private renderList(): void {
    const found = searchCards(this.cards, this.query);
    const q = this.query.trim();
    const all = this.cards.length === 1 ? "1 card" : `${this.cards.length} cards`;
    this.count.textContent = q ? `${found.length} of ${all}` : all;
    if (!found.length) {
      const ask = el("button", { type: "button", class: "primary" }, "Ask Foyelle");
      ask.addEventListener("click", () => void this.view.sendMessage(`Can you show me ${q}?`));
      this.list.replaceChildren(
        el("div", { class: "card-none" },
          el("p", {}, q ? `No card matches “${q}”.` : "There are no cards yet."),
          el("p", { class: "muted" }, "Ask me instead: I may know where to find it, or make you a card for it."),
          q ? el("div", { class: "actions" }, ask) : null,
        ),
      );
      return;
    }
    // While searching, the best matches come first in one list; otherwise by where cards come from.
    if (q) {
      this.list.replaceChildren(this.grid(found));
      return;
    }
    this.list.replaceChildren(
      ...SECTIONS.map((s) => {
        const cards = found.filter((c) => c.source === s.source);
        if (!cards.length) return null;
        return el("section", { class: "card-section", "aria-label": s.title },
          el("h2", {}, s.title),
          el("p", { class: "muted small" }, s.about),
          this.grid(cards),
        );
      }).filter((n): n is HTMLElement => n !== null),
    );
  }

  private grid(cards: CardEntry[]): HTMLElement {
    return el("ul", { class: "card-grid", role: "list" }, ...cards.map((c) => el("li", {}, this.tile(c))));
  }

  private tile(card: CardEntry): HTMLElement {
    const b = el("button", { type: "button", class: `card-tile from-${card.source}`, title: `Open ${card.title}` },
      el("span", { class: "card-art" }, art(card)),
      el("span", { class: "card-text" },
        el("span", { class: "card-title" }, card.title),
        el("span", { class: "card-about" }, card.description || "No description yet."),
        el("span", { class: "card-from" }, fromLine(card)),
      ),
    );
    b.addEventListener("click", () => void this.open(card));
    return b;
  }

  private async open(card: CardEntry): Promise<void> {
    this.status.textContent = `Opening ${card.title}…`;
    try {
      const r = await this.view.callTool("card_open", { key: card.key });
      this.status.textContent = r.isError ? (r.content?.find((c) => c.type === "text")?.text ?? `Couldn't open ${card.title}.`) : "";
    } catch (err) {
      this.status.textContent = `Couldn't open ${card.title} (${(err as Error).message}).`;
    }
  }
}

void startView("foyelle-cards", (view) => {
  const gallery = new Gallery(view);
  view.ontoolinput = (args) => gallery.show(args);
});
