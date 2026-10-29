/**
 * Bots, without the DOM: the person's helpers as the Shell helper keeps them (shell/bots.py), the
 * persona each one's Hermes profile gets, and what the sidebar shows about each (pictures, what
 * came in, what waits). Foyelle itself is the bot "default": always first, never deleted.
 */

export const FOYELLE = "default";

export interface Bot {
  id: string;
  name: string;
  job: string;
  picture: string;
  services: string[];
  shareOnline: string[];
}

/** A connector or connected service a bot can be given. */
export interface Givable {
  id: string;
  title: string;
  private: boolean;
}

export interface Roster {
  bots: Bot[];
  /** Which private services Foyelle's own routines may share with the online model. */
  foyelleShare: string[];
  givable: Givable[];
}

/** One routine run (shell.routine_results). */
export interface RoutineRun {
  bot: string;
  job: string;
  run: string;
  ran: number;
  name: string;
  failed: boolean;
  response: string;
}

/** What a routine left for the person (shell.waiting_list). */
export interface WaitingItem {
  id: string;
  toolName: string;
  connector: string | null;
  tool: string;
  tier: string;
  args: Record<string, unknown>;
  summary: string;
  bot: string;
  jobId: string | null;
  created: number;
  expires: number;
}

/** A routine as Hermes lists it (cron.manage list). */
export interface Routine {
  id: string;
  name: string;
  schedule: string;
  prompt: string;
  paused: boolean;
  lastRun: string | null;
  lastStatus: string | null;
  nextRun: string | null;
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((s): s is string => typeof s === "string") : []);
const text = (v: unknown): string => (typeof v === "string" ? v : "");

export function foyelleBot(): Bot {
  return { id: FOYELLE, name: "Foyelle", job: "Your helper for everything on this computer.", picture: "", services: [], shareOnline: [] };
}

export function parseRoster(raw: Record<string, unknown>): Roster {
  const bots = (Array.isArray(raw.bots) ? raw.bots : [])
    .filter((b): b is Record<string, unknown> => !!b && typeof b === "object" && typeof (b as { id?: unknown }).id === "string")
    .filter((b) => b.id !== FOYELLE)
    .map((b) => ({
      id: b.id as string,
      name: text(b.name) || (b.id as string),
      job: text(b.job),
      picture: text(b.picture),
      services: strings(b.services),
      shareOnline: strings(b.shareOnline),
    }));
  const foyelle = raw.foyelle && typeof raw.foyelle === "object" ? (raw.foyelle as Record<string, unknown>) : {};
  const givable = (Array.isArray(raw.givable) ? raw.givable : [])
    .filter((g): g is Record<string, unknown> => !!g && typeof g === "object" && typeof (g as { id?: unknown }).id === "string")
    .map((g) => ({ id: g.id as string, title: text(g.title) || (g.id as string), private: g.private === true }));
  return { bots, foyelleShare: strings(foyelle.shareOnline), givable };
}

export function parseRuns(raw: Record<string, unknown>): RoutineRun[] {
  return (Array.isArray(raw.runs) ? raw.runs : [])
    .filter((r): r is Record<string, unknown> => !!r && typeof r === "object")
    .map((r) => ({
      bot: text(r.bot) || FOYELLE,
      job: text(r.job),
      run: text(r.run),
      ran: typeof r.ran === "number" ? r.ran : 0,
      name: text(r.name),
      failed: r.failed === true,
      response: text(r.response),
    }))
    .filter((r) => r.job && r.ran > 0);
}

export function parseWaiting(raw: Record<string, unknown>): WaitingItem[] {
  return (Array.isArray(raw.items) ? raw.items : [])
    .filter((i): i is Record<string, unknown> => !!i && typeof i === "object" && typeof (i as { id?: unknown }).id === "string")
    .map((i) => ({
      id: i.id as string,
      toolName: text(i.toolName),
      connector: typeof i.connector === "string" ? i.connector : null,
      tool: text(i.tool),
      tier: text(i.tier),
      args: i.args && typeof i.args === "object" && !Array.isArray(i.args) ? (i.args as Record<string, unknown>) : {},
      summary: text(i.summary),
      bot: text(i.bot) || FOYELLE,
      jobId: typeof i.jobId === "string" ? i.jobId : null,
      created: typeof i.created === "number" ? i.created : 0,
      expires: typeof i.expires === "number" ? i.expires : 0,
    }));
}

export function parseRoutines(raw: Record<string, unknown>): Routine[] {
  return (Array.isArray(raw.jobs) ? raw.jobs : [])
    .filter((j): j is Record<string, unknown> => !!j && typeof j === "object")
    .map((j): Record<string, unknown> & { id: string } => ({ ...j, id: text(j.job_id) || text(j.id) }))
    .filter((j) => j.id)
    .map((j) => ({
      id: j.id,
      name: text(j.name) || j.id,
      schedule: text(j.schedule_display) || text(j.schedule),
      prompt: text(j.prompt) || text(j.prompt_preview),
      paused: j.enabled === false || j.state === "paused",
      lastRun: text(j.last_run_at) || null,
      lastStatus: text(j.last_status) || null,
      nextRun: text(j.next_run_at) || null,
    }));
}

/** The browser storage key of a bot's conversation (Foyelle keeps the one it always had). */
export function sessionKey(botId: string): string {
  return botId === FOYELLE ? "foyelle.session" : `foyelle.session.${botId}`;
}

/** A picture: the bot's emoji, or the first letter of its name on a colour from its id. */
export function pictureOf(bot: Pick<Bot, "id" | "name" | "picture">): { emoji: string | null; initial: string; hue: number } {
  const emoji = bot.picture.trim() || null;
  const initial = (Array.from(bot.name.trim())[0] ?? "?").toLocaleUpperCase();
  let hash = 0;
  for (const ch of bot.id) hash = (hash * 31 + ch.codePointAt(0)!) >>> 0;
  return { emoji, initial, hue: hash % 360 };
}

/** The persona a bot's Hermes profile gets (its SOUL.md), from its name, job and services. */
export function botSoul(bot: Pick<Bot, "name" | "job" | "services">, givable: Givable[]): string {
  const titles = bot.services.map((s) => givable.find((g) => g.id === s)?.title ?? s);
  const job = bot.job.trim() || "Help the person with whatever they ask, within what you were given.";
  return [
    `You are ${bot.name}, one of the helpers on this computer; Foyelle is the main one. Your job: ${job}`,
    titles.length
      ? `You can use only the services you were given: ${titles.join(", ")}. When a request needs something else, say so plainly and suggest asking Foyelle.`
      : "You were given no services yet: you can talk, remember and keep a to-do list. When a request needs a service, say which one, and that the person can give it to you on your details.",
    "Answer in the language the person writes in. Be direct: match the length of a reply to the weight of the ask, and report finished work briefly.",
    "You act only through the tools you are given. Some tools ask for the person's approval first, which is expected.",
    "Routines: when something should happen on its own (a summary every morning, a check every few hours), offer to set up a routine with cronjob_manage, with a short name, a schedule and a prompt that says exactly what to do; the person confirms it. A routine runs with nobody watching: it can read and report, and anything that would change or send something is saved for the person to approve. Each run's answer appears as a note in this conversation on the screen, and what waits for approval shows above the message box. There is no notification to a phone or any other app: never offer Telegram, email or another channel, even if a tool's reply suggests one, and never call this conversation a terminal.",
    "When a tool returns an error, read it and change the arguments before trying again. Never repeat the same failing call; after two failures, tell the person what went wrong.",
    "Never ask for a password, passphrase or API key in chat.",
  ].join("\n\n");
}

/** What came in for each bot since the person last looked: new routine results and waiting items. */
export function attention(runs: RoutineRun[], waiting: WaitingItem[], seen: Record<string, number>): Map<string, { results: number; waiting: number }> {
  const out = new Map<string, { results: number; waiting: number }>();
  const at = (bot: string) => out.get(bot) ?? out.set(bot, { results: 0, waiting: 0 }).get(bot)!;
  for (const r of runs) if (r.ran > (seen[r.bot] ?? 0)) at(r.bot).results++;
  for (const w of waiting) at(w.bot).waiting++;
  return out;
}

/** "07:30" today, "Mon 07:30" this week, "27 Oct" before that. */
export function whenShort(seconds: number, now: Date = new Date()): string {
  const d = new Date(seconds * 1000);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return time;
  if (now.getTime() - d.getTime() < 6 * 86_400_000) return `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
  return d.toLocaleDateString([], { day: "numeric", month: "short" });
}
