import { describe, expect, it } from "vitest";
import {
  attention, botSoul, FOYELLE, parseRoster, parseRoutines, parseRuns, parseWaiting, pictureOf, sessionKey,
} from "../src/renderer/bot-model";

const givable = [
  { id: "calendar", title: "Calendar", private: true },
  { id: "kiwi", title: "Kiwi.com", private: false },
];

describe("bots", () => {
  it("reads the roster and never lists Foyelle as a bot", () => {
    const r = parseRoster({
      bots: [{ id: "default", shareOnline: ["calendar"] }, { id: "bot-travel", name: "Travel", job: "Flights", picture: "✈️", services: ["kiwi", 3], shareOnline: [] }, { name: "no id" }],
      foyelle: { shareOnline: ["calendar"] },
      givable,
    });
    expect(r.bots.map((b) => b.id)).toEqual(["bot-travel"]);
    expect(r.bots[0]!.services).toEqual(["kiwi"]);
    expect(r.foyelleShare).toEqual(["calendar"]);
    expect(r.givable[0]).toEqual({ id: "calendar", title: "Calendar", private: true });
  });

  it("keeps Foyelle's conversation where it always was", () => {
    expect(sessionKey(FOYELLE)).toBe("foyelle.session");
    expect(sessionKey("bot-travel")).toBe("foyelle.session.bot-travel");
  });

  it("draws a picture from the emoji, or the initial on the bot's own colour", () => {
    expect(pictureOf({ id: "bot-a", name: "Travel", picture: "✈️" }).emoji).toBe("✈️");
    const p = pictureOf({ id: "bot-a", name: "ärenden", picture: "" });
    expect(p).toMatchObject({ emoji: null, initial: "Ä" });
    expect(pictureOf({ id: "bot-a", name: "x", picture: "" }).hue).toBe(p.hue);
    expect(pictureOf({ id: "bot-b", name: "x", picture: "" }).hue).not.toBe(p.hue);
  });

  it("writes a persona from the name, the job and what it was given", () => {
    const soul = botSoul({ name: "Travel", job: "Watch flight prices.", services: ["kiwi", "calendar"] }, givable);
    expect(soul).toMatch(/^You are Travel, one of the helpers on this computer; Foyelle is the main one. Your job: Watch flight prices\./);
    expect(soul).toContain("only the services you were given: Kiwi.com, Calendar");
    expect(soul).toContain("cronjob_manage");
    // Where a routine's results go, so the model doesn't relay Hermes's advice to use Telegram.
    expect(soul).toContain("Each run's answer appears as a note in this conversation");
    expect(soul).toContain("never offer Telegram");
    expect(soul).toContain("Answer in the language the person writes in.");
    expect(botSoul({ name: "Empty", job: "", services: [] }, givable)).toContain("You were given no services yet");
  });

  it("reads routine runs, waiting items and routines", () => {
    const runs = parseRuns({ runs: [{ bot: "bot-travel", job: "ab", run: "r1", ran: 200, name: "Fares", failed: false, response: "€420" }, { job: "", ran: 1 }] });
    expect(runs).toEqual([{ bot: "bot-travel", job: "ab", run: "r1", ran: 200, name: "Fares", failed: false, response: "€420" }]);
    const waiting = parseWaiting({ items: [{ id: "a".repeat(16), connector: "todoist", tool: "add_tasks", args: { content: "Pay" }, summary: "todoist.add_tasks", bot: "bot-home", created: 5, expires: 9 }] });
    expect(waiting[0]).toMatchObject({ connector: "todoist", args: { content: "Pay" }, bot: "bot-home" });
    const routines = parseRoutines({ jobs: [{ job_id: "ab12", name: "Morning brief", schedule: "every day at 07:30", prompt_preview: "What's on", enabled: false, last_run_at: null }] });
    expect(routines).toEqual([{ id: "ab12", name: "Morning brief", schedule: "every day at 07:30", prompt: "What's on", paused: true, lastRun: null, lastStatus: null, nextRun: null }]);
  });

  it("counts what came in since the person last looked", () => {
    const runs = parseRuns({ runs: [{ bot: "bot-a", job: "j", run: "1", ran: 100 }, { bot: "bot-a", job: "j", run: "2", ran: 300 }, { bot: "default", job: "k", run: "1", ran: 50 }] });
    const waiting = parseWaiting({ items: [{ id: "w1", bot: "bot-a" }, { id: "w2", bot: "bot-b" }] });
    const got = attention(runs, waiting, { "bot-a": 200 });
    expect(got.get("bot-a")).toEqual({ results: 1, waiting: 1 });
    expect(got.get("bot-b")).toEqual({ results: 0, waiting: 1 });
    expect(got.get(FOYELLE)).toEqual({ results: 1, waiting: 0 });
  });
});
