import * as esbuild from "esbuild";
import { describe, expect, it } from "vitest";
import { ART_KINDS, cardArt } from "../src/renderer/builtin-views/card-art";
import { artFor, buildCatalog, fold, fromLine, OWN_CARDS, searchCards, SECTIONS, type ListedCard } from "../src/renderer/card-catalog";

/** What surface.list gives on the dev canvas (the connectors' own titles and descriptions). */
const LISTED: ListedCard[] = [
  { server: "shell", uri: "ui://shell/status", name: "status", title: "Status", description: "How the computer is doing: the model in use, the helpers and the network." },
  { server: "files", uri: "ui://files/browser", name: "browser", title: "Files", description: "Your documents and pictures: look inside, move things to the trash, bring back earlier versions." },
  { server: "email", uri: "ui://email/inbox", name: "inbox", title: "Mail", description: "Your mail: search it and read it.\nSecond line." },
  { server: "kiwi", uri: "ui://kiwi/flights", name: "flights", title: "Flights", description: "Flights the service found.", service: "Kiwi.com" },
  { server: "todoist", uri: "ui://todoist/tasks", name: "tasks", title: "Tasks", description: "Your to-do list." },
  { server: "workshop", uri: "ui://workshop/show", name: "show", title: "Workshop surface" },
  { server: "workshop", uri: "ui://workshop/s/recent-documents", name: "recent-documents", title: "Recent documents", description: "The latest files in Documents" },
  { server: "sysmon", uri: "ui://sysmon/app.html", name: "ui://sysmon/app.html", title: "ui://sysmon/app.html" },
];

const NAMES = { helperTitles: { sysmon: "System monitor" }, services: { todoist: "Todoist" } };

describe("the card gallery's catalogue", () => {
  const cards = buildCatalog(LISTED, NAMES);
  const card = (uri: string) => cards.find((c) => c.uri === uri)!;

  it("lists every card a helper declares, plus the canvas's own, but not the Workshop's blank page", () => {
    expect(cards.map((c) => c.uri)).toEqual([
      "ui://shell/status", "ui://files/browser", "ui://email/inbox", "ui://kiwi/flights", "ui://todoist/tasks",
      "ui://workshop/s/recent-documents", "ui://sysmon/app.html", "foyelle://accounts",
    ]);
    expect(cards.at(-1)).toEqual(OWN_CARDS[0]);
  });

  it("takes each card's words from its helper: the title and the first line of the description", () => {
    expect(card("ui://email/inbox")).toMatchObject({ title: "Mail", description: "Your mail: search it and read it.", server: "email" });
    // A card without a real title gets one from its address, not the address itself.
    expect(card("ui://sysmon/app.html")).toMatchObject({ title: "App.html", description: "" });
  });

  it("says where each card comes from: a helper, an online service, or Foyelle's Workshop", () => {
    expect(card("ui://files/browser")).toMatchObject({ source: "helper", from: "Files" });
    expect(card("ui://kiwi/flights")).toMatchObject({ source: "service", from: "Kiwi.com" });
    // A service known from the canvas's configuration, for a helper that doesn't say.
    expect(card("ui://todoist/tasks")).toMatchObject({ source: "service", from: "Todoist" });
    expect(card("ui://workshop/s/recent-documents")).toMatchObject({ source: "made" });
    expect(card("ui://sysmon/app.html")).toMatchObject({ source: "helper", from: "System monitor" });
    expect(fromLine(card("ui://files/browser"))).toBe("Files helper");
    expect(fromLine(card("ui://kiwi/flights"))).toBe("Kiwi.com · online service");
    expect(fromLine(card("ui://workshop/s/recent-documents"))).toBe("Made by Foyelle");
    expect(fromLine(card("ui://shell/status"))).toBe("Foyelle itself");
    expect(fromLine(OWN_CARDS[0]!)).toBe("Foyelle itself");
    expect(SECTIONS.map((s) => s.source)).toEqual(["helper", "service", "made"]);
  });

  it("drops duplicates and entries that aren't cards", () => {
    const twice = buildCatalog([LISTED[1]!, LISTED[1]!, { server: "files", uri: "file:///etc/passwd" }, null as unknown as ListedCard]);
    expect(twice.map((c) => c.uri)).toEqual(["ui://files/browser", "foyelle://accounts"]);
  });

  it("picks a drawing for every card: known ones by address, services' by view, others by their words", () => {
    expect(card("ui://shell/status").art).toBe("status");
    expect(card("ui://kiwi/flights").art).toBe("flights");
    expect(card("ui://todoist/tasks").art).toBe("tasks");
    expect(card("ui://workshop/s/recent-documents").art).toBe("made");
    expect(artFor("weather", "ui://weather/forecast", "Weather")).toBe("card");
    expect(artFor("notes", "ui://notes/list", "My checklist")).toBe("tasks");
    expect(artFor("sysmon", "ui://sysmon/app.html", "System monitor")).toBe("status");
  });
});

describe("searching the gallery", () => {
  const cards = buildCatalog(LISTED, NAMES);
  const titles = (q: string) => searchCards(cards, q).map((c) => c.title);

  it("shows everything for an empty search", () => {
    expect(searchCards(cards, "  ")).toBe(cards);
  });

  it("finds a card by its name, what it shows, or where it comes from", () => {
    expect(titles("mail")[0]).toBe("Mail");
    expect(titles("trash")).toEqual(["Files"]);
    expect(titles("kiwi")).toEqual(["Flights"]);
    expect(titles("online")).toEqual(["Flights", "Tasks", "Accounts"]); // the services', then Accounts (connects them)
    expect(titles("helper")).toContain("Files");
  });

  it("needs every word to match, and ranks a title match first", () => {
    expect(titles("files trash")).toEqual(["Files"]);
    expect(titles("files nothing-like-this")).toEqual([]);
    // "documents" is in the title of the Workshop card and only in the description of Files.
    expect(titles("documents")).toEqual(["Recent documents", "Files"]);
  });

  it("ignores case and accents, whatever the language", () => {
    expect(fold("Écran Ünïcode")).toBe("ecran unicode");
    const listed: ListedCard[] = [{ server: "agenda", uri: "ui://agenda/semaine", title: "Agenda de la semaine", description: "Vos événements à venir." }];
    expect(searchCards(buildCatalog(listed), "evenements").map((c) => c.title)).toEqual(["Agenda de la semaine"]);
    expect(searchCards(buildCatalog(listed), "AGÉNDA").map((c) => c.title)).toEqual(["Agenda de la semaine"]);
  });
});

describe("the gallery's drawings", () => {
  it("draws every kind as SVG in the theme's colours, with nothing from the card in it", () => {
    for (const kind of ART_KINDS) {
      const svg = cardArt(kind);
      expect(svg, kind).toMatch(/^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg" class="art" viewBox="0 0 160 100"/);
      expect(svg, kind).toContain('aria-hidden="true"');
      // Colours come from the stylesheet (view.css), so a theme repaints them.
      expect(svg, kind).not.toMatch(/fill="#|stroke="#|style=/);
      expect(svg, kind).not.toMatch(/<(script|foreignObject|image|a)\b|on\w+=|href/);
      expect(svg.match(/<(\w+)/g)!.length, kind).toBeGreaterThan(4);
    }
    expect(cardArt("nope" as never)).toBe(cardArt("card"));
  });

  it("is a built-in view like the others: one script, no network, no HTML from strings", async () => {
    // As scripts/build.mjs bundles it (importing that file would run a whole build).
    const out = await esbuild.build({ entryPoints: ["src/renderer/builtin-views/cards.ts"], bundle: true, write: false, format: "iife", platform: "browser" });
    const js = out.outputFiles[0]!.text;
    expect(js).toContain("card_open");
    expect(js).not.toMatch(/\bfetch\(|XMLHttpRequest|WebSocket|\.innerHTML\s*=|insertAdjacentHTML/);
  });
});
