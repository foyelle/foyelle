/**
 * The card gallery's catalogue (no DOM): every card that can be opened, in the words people see.
 *
 * Words (docs/architecture.md, Glossary): a helper is an MCP server on this computer; a service is
 * an online MCP server, reached through a bridge helper; a card is what a helper or service shows
 * (an MCP App, a `ui://` resource). Each card's title and one-line description come from the
 * helper that declares it (its resource's title and description, through `surface.list`); the
 * canvas adds only its own cards (Accounts) and the names of its helpers.
 */

/** One entry of `surface.list` (architecture.md §1). */
export interface ListedCard {
  server: string;
  uri: string;
  name?: string;
  title?: string;
  /** What the card shows, in one line (the resource's description). */
  description?: string;
  /** The online service the card belongs to, when its helper is a bridge (`_meta.foyelle.service`). */
  service?: string | null;
  pinned?: boolean;
}

/** Where a card comes from: a helper on this computer, an online service, or the Workshop (made by Foyelle). */
export type CardSource = "helper" | "service" | "made";

/** The drawn preview of a card (builtin-views/card-art.ts). */
export type CardArt =
  | "mail" | "compose" | "calendar" | "files" | "status" | "memory" | "settings" | "themes" | "audit"
  | "privacy" | "changes" | "web" | "flights" | "tasks" | "accounts" | "made" | "card";

export interface CardEntry {
  /** `server uri`, unique in the gallery. */
  key: string;
  /** The helper that serves it; null for the canvas's own cards. */
  server: string | null;
  uri: string;
  title: string;
  description: string;
  /** Who it comes from, as people read it: "the Files helper", "Todoist". */
  from: string;
  source: CardSource;
  art: CardArt;
}

export interface CatalogNames {
  /** Display names of helpers from the system configuration (FOYELLE_SURFACE_TITLES). */
  helperTitles?: Record<string, string>;
  /** Services with an account, by their bridge helper's name (FOYELLE_SERVICES). */
  services?: Record<string, string>;
}

/** The canvas's own cards, which no helper serves. */
export const OWN_CARDS: CardEntry[] = [
  {
    key: "foyelle://accounts",
    server: null,
    uri: "foyelle://accounts",
    title: "Accounts",
    description: "Connect your mail and calendar, and the online services you use.",
    from: "Foyelle",
    source: "helper",
    art: "accounts",
  },
];

/** Foyelle's own helpers, by name, for those the system configuration doesn't name. */
const HELPERS: Record<string, string> = {
  shell: "Foyelle",
  files: "Files",
  email: "Mail",
  calendar: "Calendar",
  web: "Web",
  system: "System",
  workshop: "Workshop",
  sysmon: "System monitor",
};

/** Resources a helper lists that aren't cards to open by themselves (the Workshop's blank page). */
const NOT_CARDS = new Set(["ui://workshop/show"]);

const KNOWN_ART: Record<string, CardArt> = {
  "ui://email/inbox": "mail",
  "ui://email/compose": "compose",
  "ui://calendar/agenda": "calendar",
  "ui://files/browser": "files",
  "ui://shell/status": "status",
  "ui://shell/memory": "memory",
  "ui://shell/settings": "settings",
  "ui://shell/themes": "themes",
  "ui://shell/audit": "audit",
  "ui://shell/privacy": "privacy",
  "ui://system/review": "changes",
  "ui://web/results": "web",
};

/** Foyelle's cards for services' results, by view name (connectors/…/bridge, VIEW_TITLES). */
const VIEW_ART: Record<string, CardArt> = { flights: "flights", tasks: "tasks" };

/** A guess from the words, for cards Foyelle doesn't know (a machine's own helper, an MCP App). */
const GUESSES: Array<[RegExp, CardArt]> = [
  [/compose|draft/, "compose"],
  [/mail|inbox|message/, "mail"],
  [/calendar|agenda|event|schedule/, "calendar"],
  [/task|to-?do|checklist/, "tasks"],
  [/flight|travel|trip/, "flights"],
  [/file|document|folder|photo/, "files"],
  [/monitor|status|health|cpu|system info/, "status"],
  [/search|web|results/, "web"],
  [/theme|look|colou?r/, "themes"],
  [/setting|preference/, "settings"],
];

export function artFor(server: string | null, uri: string, title: string): CardArt {
  const known = KNOWN_ART[uri];
  if (known) return known;
  if (server === "workshop") return "made";
  const view = /^ui:\/\/[^/]+\/([^/?#]+)/.exec(uri)?.[1] ?? "";
  if (VIEW_ART[view]) return VIEW_ART[view]!;
  const words = `${uri} ${title}`.toLowerCase();
  return GUESSES.find(([re]) => re.test(words))?.[1] ?? "card";
}

/** A title worth showing: not empty and not just the card's ui:// address. */
function ownTitle(t: string | undefined): string | undefined {
  return t && t.trim() && !t.startsWith("ui://") ? t.trim() : undefined;
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** The first line of a description, without trailing space. */
function oneLine(s: string | undefined): string {
  return (s ?? "").split(/\r?\n/)[0]!.trim();
}

/** Every card that can be opened, from `surface.list` plus the canvas's own, without duplicates. */
export function buildCatalog(listed: ListedCard[], names: CatalogNames = {}): CardEntry[] {
  const seen = new Set<string>();
  const out: CardEntry[] = [];
  for (const c of listed) {
    if (!c || typeof c.server !== "string" || typeof c.uri !== "string" || !c.uri.startsWith("ui://")) continue;
    if (NOT_CARDS.has(c.uri)) continue;
    const key = `${c.server} ${c.uri}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const service = (typeof c.service === "string" && c.service.trim()) || names.services?.[c.server] || null;
    const source: CardSource = service ? "service" : c.server === "workshop" ? "made" : "helper";
    const helper = names.helperTitles?.[c.server] ?? HELPERS[c.server] ?? cap(c.server);
    const view = /^ui:\/\/[^/]+\/(.+)$/.exec(c.uri)?.[1]?.replace(/[-_/]+/g, " ");
    const title = ownTitle(c.title) ?? ownTitle(c.name) ?? cap(view ?? c.server);
    out.push({
      key,
      server: c.server,
      uri: c.uri,
      title,
      description: oneLine(c.description),
      from: source === "service" ? service! : source === "made" ? "Foyelle" : helper,
      source,
      art: artFor(c.server, c.uri, title),
    });
  }
  return [...out, ...OWN_CARDS];
}

/** The gallery's sections, in order, with the line that explains each. */
export const SECTIONS: ReadonlyArray<{ source: CardSource; title: string; about: string }> = [
  { source: "helper", title: "On this computer", about: "Cards from the helpers that run on this computer." },
  { source: "service", title: "Online services", about: "Cards from the online services you connected. What they show comes from the service." },
  { source: "made", title: "Made for you", about: "Cards Foyelle made when you asked for them." },
];

/** Where a card comes from, in a few words under its title. */
export function fromLine(c: Pick<CardEntry, "from" | "source" | "server">): string {
  if (c.source === "service") return `${c.from} · online service`;
  if (c.source === "made") return "Made by Foyelle";
  return c.server === "shell" || c.server === null ? "Foyelle itself" : `${c.from} helper`;
}

/** Lower case, accents dropped ("Agenda" matches "agénda"), so a search works in any language. */
export function fold(s: string): string {
  return s.normalize("NFKD").replace(/\p{M}+/gu, "").toLowerCase();
}

/**
 * The cards matching a search, best first: every word must appear in the card's title, what it
 * shows, where it comes from or its address. A word at the start of the title counts most.
 */
export function searchCards<T extends CardEntry>(cards: T[], query: string): T[] {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (!words.length) return cards;
  const scored: Array<{ card: T; score: number; i: number }> = [];
  cards.forEach((card, i) => {
    const title = fold(card.title);
    const about = fold(card.description);
    const from = fold(`${card.from} ${fromLine(card)} ${card.server ?? ""}`);
    const where = fold(card.uri);
    let score = 0;
    for (const w of words) {
      const s = title.split(/\s+/).some((t) => t.startsWith(w)) ? 4 : title.includes(w) ? 3 : from.includes(w) ? 2 : about.includes(w) ? 1.5 : where.includes(w) ? 1 : 0;
      if (!s) return;
      score += s;
    }
    scored.push({ card, score, i });
  });
  return scored.sort((a, b) => b.score - a.score || a.i - b.i).map((s) => s.card);
}
