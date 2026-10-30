/**
 * Foyelle canvas renderer: the bots sidebar on the left, the current bot's chat and command bar
 * next to it, the surface area on the right, a quiet status strip on top.
 */
import "./zod-config";
import type { CallToolResult, ReadResourceResult } from "@modelcontextprotocol/client";
import accountsHtml from "builtin-view:accounts";
import cardsHtml from "builtin-view:cards";
import type { FoyelleBridge, ShortcutName } from "../shared/bridge";
import {
  attention, botSoul, FOYELLE, foyelleBot, parseRoster, parseRoutines, parseRuns, parseWaiting, sessionKey, whenShort,
  type Bot, type Roster, type RoutineRun, type WaitingItem,
} from "./bot-model";
import { avatar, BotSidebar, openBotDialog, type BotDraft } from "./bots";
import { buildCatalog, type CardEntry, type ListedCard } from "./card-catalog";
import { ChatLog } from "./chat";
import { COMMAND_HELP, completions, parseCommand, type Command } from "./commands";
import { byId, h, prefersDark } from "./dom";
import { Ember, moodFor } from "./ember";
import { GatewayClient, RpcError, type GatewayEvent, type GatewayState, type ServerRequest } from "./gateway";
import { Stage, type AppSurfaceMeta } from "./stage";
import { SystemPrompts } from "./system-prompts";
import { applyScheme, DEFAULT_PREFS, prefsFromSettings, schemeFor, type CanvasPrefs, type Theme } from "./prefs";
import { buildHostContext } from "./theme";
import { applyTheme, themeScheme } from "./themes";
import type { HostEnvironment } from "./surfaces";
import { mcpTool, needsAccount, onlyLooks, splitTool } from "./account-offer";
import { emberElement, mountEmber } from "./ember-art";
import { setupRailResize } from "./rail";
import { VoiceRecorder } from "./voice";
import { Welcome } from "./welcome";

declare global {
  interface Window {
    foyelle?: FoyelleBridge;
  }
}

const BOT_KEY = "foyelle.bot";
const SEEN_KEY = "foyelle.seen";
/** How often the canvas looks for routine results and waiting items. */
const ROUTINES_POLL_MS = 60_000;
/** Routine results shown in a bot's chat when the canvas starts: the last few days. */
const ROUTINES_SHOWN_S = 3 * 86_400;
const PRIVACY_URI = "ui://shell/privacy";
/** The Accounts card may call only these tools: the mail and calendar helpers' account tools. */
const ACCOUNT_TOOL = /^(email|calendar)\.(account_status|account_providers|account_connect|account_disconnect|account_google_start|account_google_finish)$/;
/** A service's account, through its bridge (the server must be one of the configured services). */
const SERVICE_TOOL = /^([a-z][a-z0-9_-]{0,31})\.(account_status|account_signin_start|account_signin_finish|account_disconnect)$/;
/** The Services directory, through the Shell helper: list, connect, disconnect. */
const SERVICES_TOOL = /^(shell)\.(services_list|service_connect|service_disconnect)$/;
const SURFACE_CALL_TIMEOUT_MS = 10 * 60_000; // a surface tool call may wait on an approval

/** One bot's conversation in this canvas: its chat log and its session in the harness. */
interface BotView {
  bot: Bot;
  el: HTMLElement;
  chat: ChatLog;
  /** The live session in the harness (a fresh id each time the conversation is resumed). */
  sessionId: string | null;
  /** The conversation's id in the harness's store, kept across restarts (session.info). */
  storedId: string | null;
  historyLoaded: boolean;
  busy: boolean;
  unread: boolean;
  line: string;
}

/** A message of a resumed conversation (session.resume). */
interface HistoryMessage {
  role?: string;
  text?: string;
  name?: string;
  context?: string;
  display_kind?: string;
}

/** A card to open by its helper and address: an entry of surface.list, or one the canvas knows. */
type SurfaceListItem = ListedCard;

interface SurfacePayload {
  surface_id?: string;
  session_id?: string;
  server?: string;
  tool?: string;
  tool_call_id?: string;
  uri?: string;
  name?: string;
  title?: string;
  html?: string;
  csp?: { connectDomains?: string[]; resourceDomains?: string[] } | null;
  tool_input?: Record<string, unknown>;
  tool_result?: CallToolResult;
}

function storageGet(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key: string, value: string | null): void {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

/** Display names per server (from the system configuration), for surfaces without a title. */
let surfaceTitles: Record<string, string> = {};

/** A title worth showing: not empty and not just the surface's ui:// address. */
function realTitle(t: string | null | undefined): string | undefined {
  return t && !t.startsWith("ui://") ? t : undefined;
}

const SUGGESTIONS = [
  "What's in my documents?",
  "What do you remember about me?",
  "Help me write a thank-you note",
  "How is my computer doing?",
];

/** A model on this machine (an Ollama-style name, "qwen3:4b") rather than an online one ("vendor/model"). */
function isLocalModel(model: string | null | undefined): boolean {
  return !!model && !model.includes("/") && model.includes(":");
}

function lockIcon(): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "home-lock");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M7 11V8a5 5 0 0 1 10 0v3M6 11h12v9H6z"/>';
  return svg;
}

function titleFor(p: SurfacePayload): string {
  const own = realTitle(p.title) ?? realTitle(p.name);
  if (own) return own;
  if (p.server && surfaceTitles[p.server]) return surfaceTitles[p.server]!;
  if (p.uri) {
    const m = /^ui:\/\/([^/]+)\/(.+)$/.exec(p.uri);
    if (m) return `${cap(m[1]!)} ${m[2]!.replace(/[-_/]+/g, " ")}`;
  }
  return p.server ? cap(p.server) : "Card";
}

/** Tools whose result changes the look or the name the canvas shows. */
function changesPrefs(tool: string): boolean {
  return /(^|[._])(settings_set|user_name_set|theme_use|theme_create|theme_delete)$/.test(tool);
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

class Canvas {
  private readonly foyelle: FoyelleBridge;
  private readonly gw: GatewayClient;
  /** The current bot's chat log. */
  private chat: ChatLog;
  private readonly stage: Stage;
  private readonly prompts: SystemPrompts;
  private readonly input: HTMLTextAreaElement;
  private readonly hint: HTMLElement;
  /** The current bot's live session. */
  private sessionId: string | null = null;
  private currentBot = FOYELLE;
  private readonly views = new Map<string, BotView>();
  private roster: Roster = { bots: [], foyelleShare: [], givable: [] };
  private sidebar!: BotSidebar;
  private runs: RoutineRun[] = [];
  private waiting: WaitingItem[] = [];
  /** When the person last looked at each bot (seconds), for its dot. */
  private seen: Record<string, number> = readSeen();
  /** Routine runs already drawn in a chat (bot/job/run). */
  private readonly shownRuns = new Set<string>();
  private routinesTimer: ReturnType<typeof setInterval> | null = null;
  private model: string | null = null;
  private activity = "";
  private busy = false;
  private tokenWarning: string | null = null;
  private lastSurfaceList: SurfaceListItem[] = [];
  private history: string[] = [];
  private historyIndex = -1;
  private readonly ensuring = new Map<string, Promise<void>>();
  private lastState: GatewayState = "idle";
  private lostConnection = false;
  /** The user's look, text size and name (from the Shell helper's settings). */
  private prefs: CanvasPrefs = { ...DEFAULT_PREFS };
  /** A look tried on during the welcome, shown before it is saved. */
  private previewing: { theme: Theme; textScale: number } | null = null;
  private readonly ember = new Ember();
  private readonly recorder = new VoiceRecorder();
  private readonly welcome: Welcome;
  private voice = false;
  /** A sign-in browser is installed (Sign in with Google). */
  private signin = false;
  private services: Record<string, string> = {};
  /** The status line's ember, drawn once and moved into each new status line. */
  private readonly statusEmber = emberElement("xs");
  /** Foyelle's words are arriving (rather than tools running). */
  private streaming = false;
  private transcribing = false;
  /** Whether mail and calendar are connected (null: not known, e.g. the helpers are off). */
  private accounts: { mail: boolean; calendar: boolean } | null = null;
  /** The welcome ran (or was skipped) in this run of the canvas: it does not start by itself again. */
  private welcomeSeen = false;
  /** Refreshes what shows the user's name (the home greeting). */
  private onPrefs: () => void = () => undefined;

  constructor(foyelle: FoyelleBridge) {
    this.foyelle = foyelle;
    this.input = byId<HTMLTextAreaElement>("input");
    this.hint = byId("bar-hint");
    document.querySelectorAll<HTMLElement>(".ember").forEach(mountEmber);
    this.chat = this.viewFor(foyelleBot()).chat;
    this.views.get(FOYELLE)!.el.hidden = false;
    this.sidebar = new BotSidebar(byId("bot-list"), {
      select: (id) => this.switchBot(id),
      details: (id) => void this.openBot(id),
    });
    byId("new-bot").addEventListener("click", () => void this.openBot(null));
    byId("bot-head").addEventListener("click", () => void this.openBot(this.currentBot));
    setupRailResize(byId("rail-resize"));

    const env: HostEnvironment = {
      foyelle,
      hostContext: () => buildHostContext({ dark: this.isDark(), theme: this.prefs.style, ...this.stage.focusSize() }),
      log: console,
    };
    this.stage = new Stage(byId("area"), byId("focus-head"), byId("focus-box"), byId("tiles"), byId("frames"), byId("empty"), env, () =>
      this.renderStatus(),
    );
    this.prompts = new SystemPrompts(byId("system-layer"), byId("system-host"), byId("system-head"), env, () => this.renderStatus(), () =>
      Math.max(200, byId("area").getBoundingClientRect().height * 0.8),
    );

    this.gw = new GatewayClient({
      config: async () => {
        const cfg = await foyelle.harness();
        surfaceTitles = cfg.surfaceTitles ?? {};
        this.tokenWarning = cfg.error ?? null;
        this.voice = !!cfg.voice;
        this.signin = !!cfg.signin;
        this.services = cfg.services ?? {};
        byId("mic").hidden = !this.voice;
        this.renderStatus();
        return cfg;
      },
    });
    this.gw.onState((s) => this.onState(s));
    this.gw.on("*", (ev) => this.onEvent(ev));
    this.gw.onServerRequest((req) => this.onServerRequest(req));

    this.welcome = new Welcome(byId("welcome"), {
      callTool: (server, name, args) => this.callTool(server, name, args),
      preview: (theme, textScale) => {
        this.previewing = { theme, textScale };
        this.applyLook();
      },
      voice: () => this.voice,
      canConnectAccounts: () => !!this.accounts && !(this.accounts.mail && this.accounts.calendar),
      openAccounts: () => void this.openAccounts(),
      finished: () => {
        this.previewing = null;
        byId("empty").classList.remove("is-welcome");
        void this.loadPrefs();
        this.input.focus();
      },
    });

    this.bindInput();
    this.bindKeys();
    this.bindVoice();
    this.applyLook();
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => this.applyLook());
    this.startClock();
    this.renderStatus();
    this.renderBots();
    this.chat.notice("Ask me anything below. Type / for shortcuts.");
    this.gw.start();
  }

  // ----- connection and session --------------------------------------------

  private onState(s: GatewayState): void {
    const prev = this.lastState;
    this.lastState = s;
    this.renderStatus();
    if (s === "ready") {
      if (this.lostConnection) this.chat.notice("I'm back.");
      this.lostConnection = false;
      // Live sessions don't survive a reconnect: each bot resumes its conversation when shown.
      for (const v of this.views.values()) {
        v.sessionId = null;
        v.busy = false;
      }
      this.sessionId = null;
      void this.ensureSession().then(() => this.loadRoster());
      this.routinesTimer ??= setInterval(() => void this.loadRoutines(), ROUTINES_POLL_MS);
    } else if (s === "reconnecting" && prev === "ready") {
      this.busy = false;
      this.activity = "";
      this.lostConnection = true;
      this.chat.notice("One moment, I'm reconnecting…", "warn");
    }
  }

  /** The current bot's conversation: resumed from the harness's store, or a new one. */
  private ensureSession(botId: string = this.currentBot): Promise<void> {
    const running = this.ensuring.get(botId);
    if (running) return running;
    const job = (async () => {
      const view = this.view(botId);
      try {
        if (view.sessionId) return;
        if (view.storedId) {
          try {
            const r = await this.gw.request<{ session_id?: string; messages?: HistoryMessage[] }>("session.resume", {
              session_id: view.storedId, ...this.profileOf(botId),
            });
            if (!r || typeof r.session_id !== "string") throw new Error("session.resume returned no session_id");
            if (!view.historyLoaded) this.showHistory(view, r.messages ?? []);
            this.setSession(r.session_id, botId);
            void this.loadPrefs();
            return;
          } catch {
            this.storeSession(view, null);
            view.chat.notice("Starting a fresh conversation.");
          }
        }
        await this.newSession(false, botId);
        void this.loadPrefs();
      } catch (err) {
        view.chat.notice(`Couldn't start a conversation (${(err as Error).message}).`, "error");
      } finally {
        view.historyLoaded = true;
        this.ensuring.delete(botId);
      }
    })();
    this.ensuring.set(botId, job);
    return job;
  }

  private async newSession(announce: boolean, botId: string = this.currentBot): Promise<void> {
    const view = this.view(botId);
    const r = await this.gw.request<{ session_id: string; model?: string; info?: { model?: string; stored_session_id?: string } }>(
      "session.create", this.profileOf(botId),
    );
    if (!r || typeof r.session_id !== "string") throw new Error("session.create returned no session_id");
    this.storeSession(view, this.storedIds.get(r.session_id) ?? r.info?.stored_session_id ?? null);
    this.setSession(r.session_id, botId);
    const m = r.model ?? r.info?.model;
    if (m) this.model = m;
    if (announce) {
      view.chat.clear();
      view.chat.divider("New conversation");
    }
    this.renderStatus();
  }

  private setSession(id: string | null, botId: string = this.currentBot): void {
    const view = this.view(botId);
    view.sessionId = id;
    const stored = id ? this.storedIds.get(id) : null;
    if (stored) this.storeSession(view, stored);
    if (botId !== this.currentBot) return;
    this.sessionId = id;
    // The session's info may have come before this (session.info right after session.create).
    this.storedSessionId = view.storedId;
    this.renderChips([], { on: [], off: [] });
    void this.loadChips();
  }

  /** Remember a bot's conversation across restarts (its id in the harness's store). */
  private storeSession(view: BotView, storedId: string | null): void {
    view.storedId = storedId;
    storageSet(sessionKey(view.bot.id), storedId);
  }

  /** Foyelle's live session (made if needed). The canvas's own controls (settings, accounts, the bots,
   * what waits, cards opened from the list) act as Foyelle, whichever bot is on screen. */
  private async foyelleSession(): Promise<string | null> {
    const view = this.view(FOYELLE);
    if (!view.sessionId) await this.ensureSession(FOYELLE);
    return view.sessionId;
  }

  /** A card's calls belong to the bot conversation it opened in (the policy decides them as that bot's),
   * while that conversation is live; otherwise, after a restart, to Foyelle's. */
  private async cardSession(sessionId: string | null | undefined): Promise<string | null> {
    return sessionId && this.viewBySession(sessionId) ? sessionId : this.foyelleSession();
  }

  /** The ``profile`` a bot's calls name: each bot is a Hermes profile; Foyelle is the default. */
  private profileOf(botId: string): { profile?: string } {
    return botId === FOYELLE ? {} : { profile: botId };
  }

  // ----- bots ----------------------------------------------------------------------------------

  private view(botId: string = this.currentBot): BotView {
    return this.views.get(botId) ?? this.views.get(FOYELLE)!;
  }

  /** A bot's chat, made the first time it is needed (hidden until shown). */
  private viewFor(bot: Bot): BotView {
    const existing = this.views.get(bot.id);
    if (existing) {
      existing.bot = bot;
      return existing;
    }
    const el = h("div", { class: "chat", role: "log", "aria-live": "polite", "aria-relevant": "additions", hidden: true });
    byId("chats").append(el);
    const view: BotView = {
      bot, el, chat: new ChatLog(el), sessionId: null, storedId: storageGet(sessionKey(bot.id)),
      historyLoaded: false, busy: false, unread: false, line: "",
    };
    this.views.set(bot.id, view);
    return view;
  }

  /** A resumed conversation's messages, as they were (tool rows as finished ones). */
  private showHistory(view: BotView, messages: HistoryMessage[]): void {
    const chat = view.chat;
    let n = 0;
    for (const m of messages) {
      if (m.display_kind === "internal_notification" || m.display_kind === "hidden") continue;
      const text = typeof m.text === "string" ? m.text : "";
      if (m.role === "user" && text.trim()) chat.user(text);
      else if (m.role === "assistant" && text.trim()) {
        chat.messageStart();
        chat.messageComplete(text);
        view.line = firstLine(text);
      } else if (m.role === "tool" && m.name) {
        const id = `history-${++n}`;
        chat.toolStart(id, m.name, undefined);
        chat.toolComplete(id, m.name);
      }
    }
    if (messages.length) chat.divider("Earlier");
  }

  private async loadRoster(): Promise<void> {
    try {
      this.roster = parseRoster(await this.callTool("shell", "bots_list", {}));
    } catch (err) {
      console.warn("[canvas] bots:", (err as Error).message);
      return;
    }
    for (const bot of [foyelleBot(), ...this.roster.bots]) this.viewFor(bot);
    for (const id of [...this.views.keys()]) {
      if (id !== FOYELLE && !this.roster.bots.some((b) => b.id === id)) this.dropView(id);
    }
    const wanted = storageGet(BOT_KEY);
    if (wanted && wanted !== this.currentBot && this.views.has(wanted)) this.switchBot(wanted);
    this.renderBots();
    await this.loadRoutines();
  }

  private dropView(id: string): void {
    const view = this.views.get(id);
    if (!view) return;
    if (this.currentBot === id) this.switchBot(FOYELLE);
    view.el.remove();
    this.views.delete(id);
  }

  /** Show another bot's conversation; its session resumes in the background. */
  private switchBot(id: string): void {
    if (!this.views.has(id)) return;
    if (id !== this.currentBot) {
      const from = this.view();
      from.busy = this.busy;
      from.el.hidden = true;
      this.currentBot = id;
      storageSet(BOT_KEY, id);
      const to = this.view(id);
      to.el.hidden = false;
      this.chat = to.chat;
      this.sessionId = to.sessionId;
      this.storedSessionId = to.storedId;
      this.busy = to.busy;
      this.streaming = false;
      this.activity = "";
      this.renderChips([], { on: [], off: [] });
      void this.loadChips();
    }
    this.markSeen(id);
    this.renderBots();
    this.renderStatus();
    if (this.gw.state === "ready" && !this.sessionId) void this.ensureSession();
    this.input.focus();
  }

  private markSeen(id: string): void {
    const view = this.views.get(id);
    if (view) view.unread = false;
    this.seen[id] = Date.now() / 1000;
    storageSet(SEEN_KEY, JSON.stringify(this.seen));
  }

  private renderBots(): void {
    const counts = attention(this.runs, this.waiting, this.seen);
    const rows = [...this.views.values()].map((v) => ({
      bot: v.bot,
      line: v.line,
      results: v.bot.id === this.currentBot ? 0 : counts.get(v.bot.id)?.results ?? 0,
      // The current bot's waiting items are on screen, above the message box.
      waiting: v.bot.id === this.currentBot ? 0 : counts.get(v.bot.id)?.waiting ?? 0,
      unread: v.unread && v.bot.id !== this.currentBot,
      busy: v.bot.id === this.currentBot ? this.busy : v.busy,
    }));
    this.sidebar.render(rows, this.currentBot);
    const bot = this.view().bot;
    byId("bot-head").replaceChildren(
      avatar(bot, "md"),
      h("span", { class: "bot-head-text" }, h("span", { class: "bot-head-name" }, bot.name), h("span", { class: "bot-head-job" }, bot.job)),
    );
    byId("bot-head").title = `${bot.name}: details, services and routines`;
    this.input.placeholder = bot.id === FOYELLE ? "Ask, or type / for commands" : `Message ${bot.name}`;
    this.renderWaiting();
  }

  /** Routine results (as notes in each bot's chat) and what waits for the person. */
  private async loadRoutines(): Promise<void> {
    if (this.gw.state !== "ready") return;
    const since = Date.now() / 1000 - ROUTINES_SHOWN_S;
    try {
      const [runs, waiting] = await Promise.all([
        this.callTool("shell", "routine_results", { since, limit: 80 }),
        this.callTool("shell", "waiting_list", {}),
      ]);
      this.runs = parseRuns(runs);
      this.waiting = parseWaiting(waiting);
    } catch (err) {
      console.warn("[canvas] routines:", (err as Error).message);
      return;
    }
    for (const run of [...this.runs].reverse()) {
      const key = `${run.bot}/${run.job}/${run.run}`;
      const view = this.views.get(run.bot);
      if (this.shownRuns.has(key) || !view) continue;
      this.shownRuns.add(key);
      view.chat.routineNote(run.name || "Routine", whenShort(run.ran), run.response, run.failed);
      view.line = `${run.name}: ${firstLine(run.response)}`;
      if (run.bot === this.currentBot && run.ran > (this.seen[run.bot] ?? 0)) this.markSeen(run.bot);
    }
    this.renderBots();
  }

  /** Above the message box: what the current bot's routines left for the person. */
  private renderWaiting(): void {
    const box = byId("waiting");
    const items = this.waiting.filter((w) => w.bot === this.currentBot);
    box.hidden = items.length === 0;
    box.replaceChildren(
      ...(items.length ? [h("div", { class: "waiting-title" }, items.length === 1 ? "Waiting for you" : `${items.length} waiting for you`)] : []),
      ...items.map((item) => {
        const approve = h("button", { type: "button", class: "btn btn-small btn-primary" }, "Approve");
        const drop = h("button", { type: "button", class: "btn btn-small" }, "Drop");
        approve.addEventListener("click", () => void this.approveWaiting(item, approve, drop));
        drop.addEventListener("click", () => void this.dropWaiting(item));
        return h(
          "div",
          { class: "waiting-item" },
          h("span", { class: "waiting-text", title: item.summary }, item.summary, h("span", { class: "waiting-when" }, ` · ${whenShort(item.created)}`)),
          h("span", { class: "waiting-actions" }, drop, approve),
        );
      }),
    );
  }

  /** Run what a routine left, now with the person as the origin: outbound and system actions show
   * their usual approval card before they run. */
  private async approveWaiting(item: WaitingItem, ...buttons: HTMLButtonElement[]): Promise<void> {
    if (!item.connector) return;
    for (const b of buttons) b.disabled = true;
    try {
      // As the bot whose routine left it: its policy, its services.
      const bot = this.views.has(item.bot) ? item.bot : FOYELLE;
      await this.ensureSession(bot);
      await this.toolIn(this.view(bot).sessionId, item.connector, item.tool, item.args);
      this.chat.notice(`Done: ${item.summary}`);
      await this.callTool("shell", "waiting_drop", { item_id: item.id }).catch(() => undefined);
    } catch (err) {
      this.chat.notice(`That didn't run (${(err as Error).message}).`, "warn");
    }
    await this.loadRoutines();
  }

  private async dropWaiting(item: WaitingItem): Promise<void> {
    try {
      await this.callTool("shell", "waiting_drop", { item_id: item.id });
    } catch (err) {
      this.chat.notice((err as Error).message, "warn");
    }
    await this.loadRoutines();
  }

  /** The New bot dialog, or a bot's details (Foyelle's: its routines and sharing). */
  private async openBot(botId: string | null): Promise<void> {
    if (this.gw.state === "ready" && !this.roster.givable.length) await this.loadRoster();
    const bot = botId ? this.views.get(botId)?.bot ?? null : null;
    const profile = bot ? this.profileOf(bot.id) : {};
    await openBotDialog(document.body, bot, this.roster.givable, this.roster.foyelleShare, {
      save: async (draft) => {
        const saved = await this.saveBot(bot, draft);
        if (saved && !bot) this.switchBot(saved);
      },
      remove: bot && bot.id !== FOYELLE ? () => this.deleteBot(bot.id) : undefined,
      routines: bot
        ? async () => parseRoutines(await this.gw.request<Record<string, unknown>>("cron.manage", { action: "list", include_disabled: true, ...profile }))
        : undefined,
      routine: bot
        ? async (id, action) => {
            const r = await this.gw.request<Record<string, unknown>>("cron.manage", { action, name: id, ...profile });
            if (r && r.success === false) throw new Error(String(r.error ?? `couldn't ${action} it`));
          }
        : undefined,
    });
    this.input.focus();
  }

  /** Save a bot in the Shell helper and its Hermes profile (persona from its job and services). */
  private async saveBot(bot: Bot | null, draft: BotDraft): Promise<string | null> {
    if (bot?.id === FOYELLE) {
      await this.callTool("shell", "foyelle_share_set", { share_online: draft.shareOnline });
      await this.loadRoster();
      return FOYELLE;
    }
    const saved = await this.callTool("shell", "bot_save", {
      name: draft.name, job: draft.job, picture: draft.picture, services: draft.services, share_online: draft.shareOnline,
      ...(bot ? { bot_id: bot.id } : {}),
    });
    const id = typeof saved.id === "string" ? saved.id : null;
    if (!id) throw new Error("the bot was not saved");
    const soul = botSoul(draft, this.roster.givable);
    try {
      if (!bot) {
        await this.gw.request("profiles.create", { name: id, clone_from: "default", soul, description: draft.job, no_alias: true });
      } else {
        await this.gw.request("profiles.configure", { name: id, soul, description: draft.job });
      }
    } catch (err) {
      // A profile left from an earlier bot of the same name: give it this bot's persona.
      if (!bot && err instanceof RpcError) await this.gw.request("profiles.configure", { name: id, soul, description: draft.job });
      else throw err;
    }
    await this.loadRoster();
    return id;
  }

  /** Take a bot off the list: its routines are removed first, so nothing runs for it any more. */
  private async deleteBot(id: string): Promise<void> {
    const profile = this.profileOf(id);
    const listed = parseRoutines(await this.gw.request<Record<string, unknown>>("cron.manage", { action: "list", include_disabled: true, ...profile }));
    for (const r of listed) await this.gw.request("cron.manage", { action: "remove", name: r.id, ...profile });
    await this.callTool("shell", "bot_delete", { bot_id: id });
    storageSet(sessionKey(id), null);
    this.dropView(id);
    await this.loadRoster();
  }

  // ----- services in this conversation (chips above the message box) --------------------------

  /** The conversation's id inside the harness (session.info), which keys its services. */
  private storedSessionId: string | null = null;
  /** Every session's stored id seen in a session.info, whichever session is current. */
  private readonly storedIds = new Map<string, string>();

  /** Connected services, as chips: pressed ones are loaded in this conversation; the others load
   * when you name them (the policy plugin's conversation.py). */
  private async loadChips(): Promise<void> {
    const stored = this.storedSessionId;
    if (!stored || !this.sessionId) return; // never start a session just to draw the chips
    try {
      const [list, state] = await Promise.all([
        this.callTool("shell", "services_list", {}),
        this.callTool("shell", "conversation_services", { session_id: stored }),
      ]);
      const services = (Array.isArray(list.services) ? list.services : []) as Array<{ id: string; title: string; connected: boolean }>;
      if (stored !== this.storedSessionId) return;
      // A bot's chips are only the services it was given.
      const bot = this.view().bot;
      const given = (id: string) => bot.id === FOYELLE || bot.services.includes(id);
      this.renderChips(services.filter((s) => s.connected && given(s.id)), state as { on?: string[]; off?: string[] });
    } catch (err) {
      console.warn("[canvas] services in this conversation:", (err as Error).message);
      this.renderChips([], { on: [], off: [] });
    }
  }

  private renderChips(services: Array<{ id: string; title: string }>, state: { on?: string[]; off?: string[] }): void {
    const box = byId("bar-chips");
    box.hidden = services.length === 0;
    const on = new Set(state.on ?? []);
    box.replaceChildren(
      ...services.map((s) => {
        const pressed = on.has(s.id);
        const chip = h("button", {
          type: "button", class: "chip", "aria-pressed": String(pressed),
          title: pressed ? `${s.title} is loaded in this conversation` : `${s.title} loads when you name it. Press to load it now.`,
        }, s.title.split(/[ .]/)[0]!);
        chip.addEventListener("click", () => void this.toggleChip(s.id, !pressed));
        return chip;
      }),
    );
  }

  private async toggleChip(service: string, on: boolean): Promise<void> {
    if (!this.storedSessionId) return;
    try {
      await this.callTool("shell", "conversation_service_set", { session_id: this.storedSessionId, service_id: service, on });
    } catch (err) {
      this.chat.notice((err as Error).message, "warn");
    }
    await this.loadChips();
  }

  // ----- events ------------------------------------------------------------

  private onEvent(ev: GatewayEvent): void {
    if (ev.type === "session.info" && ev.session_id) {
      const stored = ev.payload?.stored_session_id ?? ev.payload?.info?.stored_session_id;
      if (typeof stored === "string" && stored) {
        this.storedIds.set(ev.session_id, stored);
        const view = this.viewBySession(ev.session_id);
        if (view && view.storedId !== stored) this.storeSession(view, stored);
      }
    }
    if (ev.session_id && this.sessionId && ev.session_id !== this.sessionId) {
      const other = this.viewBySession(ev.session_id);
      if (other) this.onOtherBotEvent(other, ev);
      return;
    }
    const p = ev.payload ?? {};
    switch (ev.type) {
      case "message.start":
        this.busy = true;
        this.chat.messageStart();
        break;
      case "message.delta":
        if (typeof p.text === "string") this.chat.assistantDelta(p.text);
        this.streaming = true;
        break;
      case "message.complete":
        this.busy = false;
        this.streaming = false;
        this.activity = "";
        this.chat.messageComplete(typeof p.text === "string" ? p.text : undefined, p.status, p.error);
        if (typeof p.text === "string" && p.text.trim()) this.view().line = firstLine(p.text);
        this.renderBots();
        break;
      case "tool.start":
        this.busy = true;
        this.streaming = false;
        this.chat.toolStart(String(p.tool_id ?? p.name), String(p.name ?? "tool"), p.args);
        break;
      case "tool.complete":
        this.chat.toolComplete(String(p.tool_id ?? p.name), String(p.name ?? "tool"), p.summary, p.is_error ?? p.isError);
        if (changesPrefs(String(p.name ?? ""))) void this.loadPrefs();
        if (mcpTool(String(p.name ?? ""), ["shell"])?.tool === "use_service") void this.loadChips();
        this.offerAccounts(String(p.name ?? ""), p.summary, p.is_error ?? p.isError);
        // The agent changed something: open cards from the same helper reload what they show.
        if (!(p.is_error ?? p.isError)) {
          const t = splitTool(String(p.name ?? ""));
          if (t && !onlyLooks(t.tool)) this.stage.notifyServer(t.server, t.tool);
        }
        // The agent asked to show the Accounts card or the card gallery (shell.open_accounts, shell.open_cards).
        if (!(p.is_error ?? p.isError) && mcpTool(String(p.name ?? ""), ["shell"])?.tool === "open_accounts") void this.openAccounts();
        if (!(p.is_error ?? p.isError) && mcpTool(String(p.name ?? ""), ["shell"])?.tool === "open_cards") void this.openCards();
        break;
      case "status.update":
        this.activity = typeof p.text === "string" ? p.text : "";
        break;
      case "error":
        this.busy = false;
        this.streaming = false;
        this.chat.notice(String(p.message ?? "Something went wrong on my side."), "error");
        break;
      case "session.info": {
        this.model = p.model ?? p.model_name ?? p.info?.model ?? this.model;
        const stored = (ev.session_id && this.storedIds.get(ev.session_id)) || p.stored_session_id || p.info?.stored_session_id;
        if (typeof stored === "string" && stored && stored !== this.storedSessionId) {
          this.storedSessionId = stored;
          void this.loadChips();
        }
        break;
      }
      case "surface.open":
        void this.openSurface(p as SurfacePayload, true);
        break;
      default:
        return;
    }
    this.renderStatus();
  }

  private viewBySession(sessionId: string): BotView | null {
    for (const v of this.views.values()) if (v.sessionId === sessionId) return v;
    return null;
  }

  /** A bot that isn't on screen kept talking (a long answer, a heartbeat): into its own chat. */
  private onOtherBotEvent(view: BotView, ev: GatewayEvent): void {
    const p = ev.payload ?? {};
    switch (ev.type) {
      case "message.start":
        view.busy = true;
        view.chat.messageStart();
        break;
      case "message.delta":
        if (typeof p.text === "string") view.chat.assistantDelta(p.text);
        return;
      case "message.complete":
        view.busy = false;
        view.chat.messageComplete(typeof p.text === "string" ? p.text : undefined, p.status, p.error);
        if (typeof p.text === "string" && p.text.trim()) view.line = firstLine(p.text);
        view.unread = true;
        break;
      case "tool.start":
        view.busy = true;
        view.chat.toolStart(String(p.tool_id ?? p.name), String(p.name ?? "tool"), p.args);
        break;
      case "tool.complete":
        view.chat.toolComplete(String(p.tool_id ?? p.name), String(p.name ?? "tool"), p.summary, p.is_error ?? p.isError);
        return;
      case "error":
        view.busy = false;
        view.chat.notice(String(p.message ?? "Something went wrong on my side."), "error");
        view.unread = true;
        break;
      default:
        return;
    }
    this.renderBots();
  }

  private onServerRequest(req: ServerRequest): void {
    if (this.prompts.enqueue(req)) return; // the status strip counts waiting approvals
    req.fail(`canvas cannot handle ${req.method}`, -32601);
  }

  // ----- surfaces ----------------------------------------------------------

  private async openSurface(p: SurfacePayload, fromTool: boolean): Promise<void> {
    if (typeof p.html !== "string" || !p.html) {
      this.chat.notice(`A card arrived empty (${p.uri ?? "unknown"}).`, "error");
      return;
    }
    const title = titleFor(p);
    const meta: AppSurfaceMeta = {
      surfaceId: p.surface_id ?? null,
      server: p.server ?? null,
      uri: p.uri ?? null,
      sessionId: p.session_id ?? this.sessionId,
      tool: p.tool ?? null,
    };
    try {
      const key = await this.stage.open({
        title,
        html: p.html,
        csp: p.csp ?? null,
        meta,
        // Opened from the list, a surface may still come with its tool's result (the gateway runs
        // a surface's no-argument tool so apps that start from a result work).
        toolInput: fromTool || p.tool_result ? (p.tool_input ?? {}) : undefined,
        toolResult: p.tool_result,
        replaceSame: fromTool,
        handlers: (key) => ({
          callTool: (params) => this.surfaceCallTool(key, params),
          readResource: (params) => this.surfaceReadResource(key, params),
          message: (text) => this.submitPrompt(text, this.stage.title(key)),
          openLink: async (url) => this.prompts.showLink(url, this.stage.title(key)),
          requestFocus: () => this.stage.focus(key),
          requestClose: () => void this.stage.dismiss(key),
        }),
      });
      this.chat.surfaceLink(title, () => this.stage.focus(key));
    } catch (err) {
      this.chat.notice(`Couldn't open ${title} (${(err as Error).message}).`, "error");
    }
  }

  private async surfaceCallTool(key: string, params: { name: string; arguments?: Record<string, unknown> }): Promise<CallToolResult> {
    const m = this.stage.meta(key);
    if (!m?.server) throw new Error("this card has no helper");
    if (changesPrefs(`${m.server}.${params.name}`)) setTimeout(() => void this.loadPrefs(), 500);
    return this.gw.request<CallToolResult>(
      "surface.call_tool",
      {
        session_id: (await this.cardSession(m.sessionId)) ?? undefined,
        surface_id: m.surfaceId ?? undefined,
        server: m.server,
        name: params.name,
        arguments: params.arguments ?? {},
      },
      { timeoutMs: SURFACE_CALL_TIMEOUT_MS },
    );
  }

  private surfaceReadResource(key: string, params: { uri: string }): Promise<ReadResourceResult> {
    const m = this.stage.meta(key);
    if (!m?.server) return Promise.reject(new Error("this card has no helper"));
    return this.gw.request<ReadResourceResult>("surface.read_resource", { server: m.server, uri: params.uri });
  }

  private async listSurfaces(): Promise<SurfaceListItem[]> {
    const r = await this.gw.request<{ surfaces?: SurfaceListItem[] }>("surface.list", {});
    this.lastSurfaceList = Array.isArray(r?.surfaces) ? r.surfaces : [];
    return this.lastSurfaceList;
  }

  /** Whether mail and calendar are connected; the home screen offers to connect them if not. */
  private async loadAccounts(): Promise<void> {
    const connected = async (server: string) => {
      try {
        return (await this.callTool(server, "account_status", {})).connected === true;
      } catch {
        return null;
      }
    };
    void this.loadChips(); // a service connected or disconnected on the Accounts card
    const [mail, calendar] = await Promise.all([connected("email"), connected("calendar")]);
    this.accounts = mail === null && calendar === null ? null : { mail: !!mail, calendar: !!calendar };
    this.renderHome();
  }

  /** The Accounts card: connect mail and calendar with an app password. */
  private async openAccounts(): Promise<void> {
    const existing = this.stage.state.resolve("Accounts");
    if (existing) {
      this.stage.focus(existing);
      return;
    }
    try {
      await this.stage.open({
        title: "Accounts",
        html: accountsHtml,
        csp: null,
        meta: { surfaceId: null, server: null, uri: "foyelle://accounts", sessionId: this.view(FOYELLE).sessionId, tool: null },
        toolInput: { signin: this.signin, services: this.services },
        handlers: (key) => ({
          callTool: async (params) => {
            if (params.name === "close") {
              void this.stage.dismiss(key);
              return { content: [] };
            }
            // The sign-in browser (Google): begin gives the loopback address, open shows Google's page.
            if (params.name === "signin.begin" || params.name === "signin.open" || params.name === "signin.end") {
              const args = params.arguments ?? {};
              const data =
                params.name === "signin.begin"
                  ? await this.foyelle.signinBegin()
                  : params.name === "signin.open"
                    ? await this.foyelle.signinOpen(String(args.auth_url ?? ""))
                    : await this.foyelle.signinEnd();
              return { content: [{ type: "text", text: JSON.stringify(data ?? {}) }], structuredContent: (data ?? {}) as Record<string, unknown> };
            }
            // A service connected or disconnected: the harness reconnects its MCP servers, so the
            // service's tools reach the agent (or leave it).
            if (params.name === "services.reload") {
              const reload = await this.gw.request<Record<string, unknown>>(
                "reload.mcp",
                { session_id: (await this.foyelleSession()) ?? undefined, confirm: true },
                { timeoutMs: 120_000 },
              );
              return { content: [{ type: "text", text: JSON.stringify(reload ?? {}) }], structuredContent: reload ?? {} };
            }
            const service = SERVICE_TOOL.exec(params.name);
            const m = ACCOUNT_TOOL.exec(params.name) ?? SERVICES_TOOL.exec(params.name) ??
              (service && service[1]! in this.services ? service : null);
            if (!m) throw new Error(`the Accounts card cannot call ${params.name}`);
            const result = await this.gw.request<CallToolResult>(
              "surface.call_tool",
              { session_id: (await this.foyelleSession()) ?? undefined, server: m[1], name: m[2], arguments: params.arguments ?? {} },
              { timeoutMs: SURFACE_CALL_TIMEOUT_MS },
            );
            if (m[2] !== "account_status" && m[2] !== "account_providers" && m[2] !== "services_list") void this.loadAccounts();
            return result;
          },
          readResource: () => Promise.reject(new Error("no resources")),
          message: (text) => this.submitPrompt(text, "Accounts"),
          openLink: async (url) => this.prompts.showLink(url, "Accounts"),
          requestFocus: () => this.stage.focus(key),
          requestClose: () => void this.stage.dismiss(key),
        }),
      });
    } catch (err) {
      this.chat.notice(`Couldn't open Accounts (${(err as Error).message}).`, "error");
    }
  }

  /** The card gallery: every card there is, to search and open (card-catalog.ts, builtin-views/cards.ts). */
  private async openCards(): Promise<void> {
    let cards: CardEntry[];
    try {
      cards = buildCatalog(await this.listSurfaces(), { helperTitles: surfaceTitles, services: this.services });
    } catch (err) {
      this.chat.notice(`Couldn't list the cards (${(err as Error).message}).`, "error");
      return;
    }
    // Opened again, it lists the cards afresh (a service connected, a card made since).
    const existing = this.stage.state.resolve("Cards");
    if (existing && this.stage.meta(existing)?.uri === "foyelle://cards") await this.stage.dismiss(existing);
    const open = async (key: string) => {
      const card = cards.find((c) => c.key === key);
      if (!card) throw new Error("no such card");
      if (card.uri === "foyelle://accounts") await this.openAccounts();
      else if (card.server) await this.openResource({ server: card.server, uri: card.uri, title: card.title });
    };
    try {
      await this.stage.open({
        title: "Cards",
        html: cardsHtml,
        csp: null,
        meta: { surfaceId: null, server: null, uri: "foyelle://cards", sessionId: this.view(FOYELLE).sessionId, tool: null },
        toolInput: { cards },
        handlers: (key) => ({
          callTool: async (params) => {
            if (params.name === "close") {
              void this.stage.dismiss(key);
              return { content: [] };
            }
            if (params.name !== "card_open") throw new Error(`the card gallery cannot call ${params.name}`);
            try {
              await open(String(params.arguments?.key ?? ""));
              return { content: [] };
            } catch (err) {
              return { content: [{ type: "text", text: `Couldn't open it (${(err as Error).message}).` }], isError: true };
            }
          },
          readResource: () => Promise.reject(new Error("no resources")),
          message: (text) => this.submitPrompt(text, "Cards"),
          openLink: async (url) => this.prompts.showLink(url, "Cards"),
          requestFocus: () => this.stage.focus(key),
          requestClose: () => void this.stage.dismiss(key),
        }),
      });
    } catch (err) {
      this.chat.notice(`Couldn't open the cards (${(err as Error).message}).`, "error");
    }
  }

  /** The privacy panel: what left this computer today. */
  private openPrivacy(): void {
    void this.openResource({ server: "shell", uri: PRIVACY_URI, title: "What left your home" }).catch((e) =>
      this.chat.notice((e as Error).message, "error"),
    );
  }

  private async openResource(item: SurfaceListItem): Promise<void> {
    const r = await this.gw.request<SurfacePayload>("surface.open_resource", {
      server: item.server,
      uri: item.uri,
      session_id: (await this.foyelleSession()) ?? undefined,
    });
    await this.openSurface({ ...r, title: realTitle(r.title) ?? realTitle(item.title) ?? realTitle(item.name) }, false);
  }

  // ----- command bar -------------------------------------------------------

  private async submitPrompt(text: string, via?: string): Promise<void> {
    if (this.gw.state !== "ready") throw new Error("not connected to the harness");
    if (!this.sessionId) await this.ensureSession();
    if (!this.sessionId) throw new Error("no session");
    this.chat.user(text, via);
    this.busy = true;
    this.view().line = `You: ${firstLine(text)}`;
    this.renderBots();
    this.renderStatus();
    try {
      await this.gw.request("prompt.submit", { session_id: this.sessionId, text });
    } catch (err) {
      this.busy = false;
      this.renderStatus();
      throw err;
    }
  }

  private async run(cmd: Command): Promise<void> {
    switch (cmd.kind) {
      case "empty":
        return;
      case "harness": {
        if (!this.sessionId) await this.ensureSession();
        if (!this.sessionId) throw new Error("no session");
        const res = await this.gw.request<Record<string, unknown>>("slash.exec", { session_id: this.sessionId, command: cmd.command });
        const text = [res.output, res.display, res.notice, res.warning].filter((v): v is string => typeof v === "string" && v.length > 0).join("\n\n");
        this.chat.notice(text || `${cmd.command}: done.`);
        return;
      }
      case "prompt":
        await this.submitPrompt(cmd.text);
        return;
      case "help":
        this.chat.list(
          "Shortcuts",
          COMMAND_HELP.map(([c, d]) => ({ key: "", label: c, detail: d })).concat([
            { key: "", label: "Ctrl+K", detail: "Jump to the message box" },
            { key: "", label: "Ctrl+1…9", detail: "Bring a card to the front" },
            { key: "", label: "Esc", detail: "Close the card in front" },
          ]),
        );
        return;
      case "focus": {
        const key = this.stage.state.resolve(cmd.ref);
        if (!key) throw new Error(`No card called “${cmd.ref}”.`);
        this.stage.focus(key);
        return;
      }
      case "pin":
      case "unpin":
      case "dismiss": {
        let key: string | null = null;
        if (cmd.ref) {
          key = this.stage.state.resolve(cmd.ref);
          if (!key) throw new Error(`No card called “${cmd.ref}”.`);
        }
        const change = cmd.kind === "pin" ? this.stage.pin(key) : cmd.kind === "unpin" ? this.stage.unpin(key) : await this.stage.dismiss(key);
        if (!change.ok) throw new Error(cap(change.error ?? "nothing to do"));
        return;
      }
      case "surfaces": {
        if (!cmd.ref) {
          await this.openCards();
          return;
        }
        const list = this.lastSurfaceList.length ? this.lastSurfaceList : await this.listSurfaces();
        const ref = cmd.ref.toLowerCase();
        const item = /^\d+$/.test(ref)
          ? list[Number(ref) - 1]
          : list.find((s) => [s.title, s.name, s.uri, s.server].some((v) => v?.toLowerCase() === ref)) ??
            list.find((s) => [s.title, s.name, s.uri].some((v) => v?.toLowerCase().includes(ref)));
        if (!item) throw new Error(`No card called “${cmd.ref}”. Type /cards to see them all.`);
        await this.openResource(item);
        return;
      }
      case "new":
        await this.newSession(true);
        return;
      case "accounts":
        await this.openAccounts();
        return;
      case "welcome":
        if (this.stage.state.focused) await this.stage.dismiss();
        this.startWelcome();
        return;
      case "stop":
        if (!this.sessionId) throw new Error("No session to stop.");
        await this.gw.request("session.interrupt", { session_id: this.sessionId });
        this.activity = "Stopping…";
        this.renderStatus();
        return;
      case "error":
        throw new Error(cmd.message);
    }
  }

  private bindInput(): void {
    const form = byId<HTMLFormElement>("bar");
    const grow = () => {
      this.input.style.height = "auto";
      this.input.style.height = `${Math.min(this.input.scrollHeight, 200)}px`;
    };
    const updateHint = () => {
      const c = completions(this.input.value);
      this.hint.textContent = c.length && this.input.value.trim() !== c[0] ? c.join("   ") : "";
      this.hint.hidden = !this.hint.textContent;
    };
    const submit = async () => {
      const value = this.input.value;
      const cmd = parseCommand(value);
      if (cmd.kind === "empty") return;
      this.history.push(value);
      this.historyIndex = -1;
      this.input.value = "";
      grow();
      updateHint();
      try {
        await this.run(cmd);
      } catch (err) {
        const msg = err instanceof RpcError ? `${err.message}` : (err as Error).message;
        this.chat.notice(msg, "error");
      }
    };
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      void submit();
    });
    this.input.addEventListener("input", () => {
      grow();
      updateHint();
      this.updateEmber();
    });
    this.input.addEventListener("focus", () => this.updateEmber());
    this.input.addEventListener("blur", () => this.updateEmber());
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        void submit();
      } else if (e.key === "Tab" && this.input.value.startsWith("/")) {
        const c = completions(this.input.value);
        if (c.length === 1) {
          e.preventDefault();
          this.input.value = `${c[0]} `;
          updateHint();
        }
      } else if (e.key === "ArrowUp" && (this.input.value === "" || this.historyIndex >= 0) && this.history.length) {
        e.preventDefault();
        this.historyIndex = this.historyIndex < 0 ? this.history.length - 1 : Math.max(0, this.historyIndex - 1);
        this.input.value = this.history[this.historyIndex] ?? "";
        grow();
      } else if (e.key === "ArrowDown" && this.historyIndex >= 0) {
        e.preventDefault();
        this.historyIndex++;
        if (this.historyIndex >= this.history.length) {
          this.historyIndex = -1;
          this.input.value = "";
        } else this.input.value = this.history[this.historyIndex] ?? "";
        grow();
      }
    });
    updateHint();
  }

  private bindKeys(): void {
    const onShortcut = (name: ShortcutName) => {
      if (name === "command-bar") {
        this.input.focus();
        this.input.select();
        return;
      }
      const n = Number(name.slice(5));
      const tile = this.stage.state.tiles()[n - 1];
      if (tile) this.stage.focus(tile.entry.key);
    };
    this.foyelle.onShortcut(onShortcut);

    document.addEventListener("keydown", (e) => {
      const mod = (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey;
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onShortcut("command-bar");
      } else if (mod && /^[1-9]$/.test(e.key)) {
        e.preventDefault();
        onShortcut(`tile-${e.key}` as ShortcutName);
      } else if (e.key === "Escape" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (document.activeElement === this.input && this.input.value) {
          this.input.value = "";
          this.input.dispatchEvent(new Event("input"));
          return;
        }
        if (this.prompts.active) return; // a prompt needs an explicit answer
        if (this.stage.state.focused) {
          e.preventDefault();
          void this.stage.dismiss();
        }
      }
    });
  }

  // ----- the user's look and name ------------------------------------------

  private currentTheme(): Theme {
    return this.previewing?.theme ?? this.prefs.theme;
  }

  /** Dark or light: what the user chose, unless the theme only comes in one of them. */
  private isDark(): boolean {
    return themeScheme(this.prefs.style, schemeFor(this.currentTheme(), new Date(), prefersDark())) === "dark";
  }

  /** Colours, text size, and the host context the cards see. */
  private applyLook(): void {
    const scheme = this.isDark() ? "dark" : "light";
    const changed = applyScheme(scheme);
    const restyled = applyTheme(this.prefs.style, scheme);
    this.foyelle.setZoom(this.previewing?.textScale ?? this.prefs.textScale);
    if (changed || restyled) {
      this.stage.pushHostContext();
      this.prompts.updateHostContext();
    }
  }

  /** Reads the user's settings from the Shell helper; on first start, the welcome begins. */
  private async loadPrefs(): Promise<void> {
    try {
      this.prefs = prefsFromSettings(await this.callTool("shell", "settings_get", {}));
    } catch {
      return; // no Shell helper: keep the defaults, and no welcome
    }
    this.applyLook();
    this.onPrefs();
    void this.loadAccounts();
    if (this.prefs.welcomed === false && !this.welcomeSeen && !this.welcome.active && !this.stage.state.focused) this.startWelcome();
  }

  private offerAccounts(tool: string, summary: unknown, isError: unknown): void {
    const which = needsAccount(tool, summary, isError, this.services);
    if (!which) return;
    if (which === "mail") {
      if (this.accounts?.mail && this.accounts.calendar) return;
      this.chat.surfaceLink("Connect your mail and calendar", () => void this.openAccounts());
    } else {
      this.chat.surfaceLink(`Connect ${which}`, () => void this.openAccounts());
    }
  }

  private startWelcome(): void {
    this.welcomeSeen = true;
    void this.loadAccounts(); // fresh by the time the welcome asks about them
    byId("empty").classList.add("is-welcome");
    this.welcome.start({ name: this.prefs.name, theme: this.prefs.theme, textScale: this.prefs.textScale });
  }

  /** A helper's tool, called by the canvas itself (the welcome, its settings, the bots): as Foyelle. */
  private async callTool(server: string, name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.toolIn(await this.foyelleSession(), server, name, args);
  }

  /** A tool call in one conversation's session: the policy decides it as that conversation's bot. */
  private async toolIn(sessionId: string | null, server: string, name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const r = await this.gw.request<CallToolResult>(
      "surface.call_tool",
      { session_id: sessionId ?? undefined, server, name, arguments: args },
      { timeoutMs: SURFACE_CALL_TIMEOUT_MS },
    );
    const text = r.content?.find((c) => c.type === "text")?.text;
    if (r.isError) throw new Error(text || `${name} failed`);
    if (r.structuredContent && typeof r.structuredContent === "object") return r.structuredContent as Record<string, unknown>;
    try {
      const v: unknown = JSON.parse(text ?? "{}");
      return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  }

  // ----- voice -------------------------------------------------------------

  /** Hold the microphone button, or hold Space in an empty message box, and talk. */
  private bindVoice(): void {
    const mic = byId<HTMLButtonElement>("mic");
    mic.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      mic.setPointerCapture(e.pointerId);
      void this.startListening();
    });
    const release = () => void this.stopListening();
    mic.addEventListener("pointerup", release);
    mic.addEventListener("pointercancel", () => this.cancelListening());
    // Keyboard: Enter or Space on the focused button toggles.
    mic.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && !e.repeat) {
        e.preventDefault();
        if (this.recorder.recording) release();
        else void this.startListening();
      }
    });

    let hold: ReturnType<typeof setTimeout> | null = null;
    const spaceTalk = (e: KeyboardEvent) =>
      this.voice && e.key === " " && !e.ctrlKey && !e.metaKey && !e.altKey &&
      (document.activeElement === this.input ? this.input.value === "" : document.activeElement === document.body);
    document.addEventListener("keydown", (e) => {
      if (!spaceTalk(e) || this.welcome.active) return;
      e.preventDefault();
      if (e.repeat || hold || this.recorder.recording) return;
      hold = setTimeout(() => {
        hold = null;
        void this.startListening();
      }, 250);
    });
    document.addEventListener("keyup", (e) => {
      if (e.key !== " ") return;
      if (hold) {
        clearTimeout(hold); // a tap, not a hold
        hold = null;
      } else if (this.recorder.recording) release();
    });
  }

  private async startListening(): Promise<void> {
    if (!this.voice || this.recorder.recording || this.transcribing) return;
    try {
      await this.recorder.start(() => void this.stopListening());
    } catch (err) {
      this.chat.notice(`I can't hear you: the microphone isn't available (${(err as Error).message}).`, "warn");
      return;
    }
    byId("bar").classList.add("is-listening");
    this.activity = "Listening… let go when you're done";
    this.renderStatus();
  }

  private cancelListening(): void {
    this.recorder.cancel();
    byId("bar").classList.remove("is-listening");
    this.activity = "";
    this.renderStatus();
  }

  private async stopListening(): Promise<void> {
    if (!this.recorder.recording) return;
    byId("bar").classList.remove("is-listening");
    this.transcribing = true;
    this.activity = "Writing down what you said…";
    this.renderStatus();
    try {
      const wav = await this.recorder.stop();
      const text = wav ? (await this.foyelle.transcribe(wav)).text : "";
      this.activity = "";
      if (!text) {
        this.chat.notice("I didn't catch that. Hold the microphone and try again?");
        return;
      }
      await this.submitPrompt(text);
    } catch (err) {
      this.activity = "";
      this.chat.notice(`I couldn't make out the words (${(err as Error).message}).`, "warn");
    } finally {
      this.transcribing = false;
      this.renderStatus();
    }
  }

  // ----- status strip ------------------------------------------------------

  /** The home screen's privacy line: honest about where the words go. */
  private renderHome(): void {
    const connect = document.getElementById("home-connect");
    if (connect) connect.hidden = !this.accounts || (this.accounts.mail && this.accounts.calendar);
    const el = document.getElementById("home-private");
    if (!el) return;
    const local = isLocalModel(this.model);
    el.classList.toggle("is-remote", !local);
    el.replaceChildren(
      lockIcon(),
      local
        ? "Everything stays in this house"
        : "Your messages go to an online model to think. Your files and memories stay here.",
    );
  }

  private modelButton(): HTMLElement {
    const b = h("button", { type: "button", class: "st-model", title: `Model: ${this.model}. See what left this computer today.` },
      isLocalModel(this.model) ? "Thinking on this computer" : "Thinking online");
    b.addEventListener("click", () => this.openPrivacy());
    return b;
  }

  private renderStatus(): void {
    this.renderHome();
    const s = this.gw?.state ?? "idle";
    const conn: Record<GatewayState, [string, string]> = {
      idle: ["off", "Waking up"],
      connecting: ["wait", "Waking up"],
      handshaking: ["wait", "Waking up"],
      ready: ["ok", "Ready"],
      reconnecting: ["bad", "One moment"],
      stopped: ["off", "Stopped"],
    };
    const [cls, label] = conn[s];
    const approvals = this.prompts?.pendingApprovals ?? 0;
    const pieces: Array<Node | null> = [
      h("span", { class: `st-conn st-${cls}`, title: this.tokenWarning ?? "" }, this.statusEmber, label),
      this.tokenWarning && s !== "ready" ? h("span", { class: "st-warn" }, "Not connected") : null,
      this.busy || this.activity ? h("span", { class: "st-activity" }, h("span", { class: "pulse", "aria-hidden": "true" }), this.activity || "Working") : null,
    ];
    byId("status-left").replaceChildren(...pieces.filter((n): n is Node => !!n));
    byId("status-right").replaceChildren(
      ...[
        approvals
          ? h("span", { class: "st-approvals", role: "status" }, `${approvals} approval${approvals === 1 ? "" : "s"} waiting`)
          : null,
        this.model ? this.modelButton() : null,
      ].filter((n): n is HTMLElement => !!n),
    );
    const stop = byId("status-left");
    stop.setAttribute("aria-busy", String(this.busy));
    this.updateEmber();
  }

  private updateEmber(): void {
    this.ember.set(
      moodFor({
        connected: this.gw?.state === "ready",
        recording: this.recorder.recording,
        typing: document.activeElement === this.input && this.input.value.trim() !== "",
        waiting: this.prompts?.pendingApprovals ?? 0,
        busy: this.busy || this.transcribing,
        streaming: this.streaming,
      }),
    );
  }

  private startClock(): void {
    byId("new-chat").addEventListener("click", () => {
      void this.newSession(true).catch((e) => this.chat.notice((e as Error).message, "error"));
      this.input.focus();
    });
    const clock = byId("clock");
    const big = byId("empty-time");
    const date = byId("empty-date");
    const greeting = byId("home-greeting");
    const tick = () => {
      const now = new Date();
      const t = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      clock.textContent = t;
      big.textContent = t;
      date.textContent = now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
      const hour = now.getHours();
      const hello = hour < 5 ? "Good night" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
      greeting.textContent = this.prefs.name ? `${hello}, ${this.prefs.name}` : hello;
      if (this.currentTheme() === "time") this.applyLook(); // dusk and dawn
    };
    this.onPrefs = tick;
    const chips = byId("home-suggestions");
    for (const text of SUGGESTIONS) {
      const chip = h("button", { type: "button", class: "home-chip" }, text);
      chip.addEventListener("click", () => {
        void this.submitPrompt(text).catch((e) => this.chat.notice((e as Error).message, "error"));
      });
      chips.append(chip);
    }
    byId("home-private").addEventListener("click", () => this.openPrivacy());
    byId("home-connect").addEventListener("click", () => void this.openAccounts());
    this.renderHome();
    tick();
    setInterval(tick, 10_000);
  }
}

function readSeen(): Record<string, number> {
  try {
    const v: unknown = JSON.parse(storageGet(SEEN_KEY) ?? "{}");
    return v && typeof v === "object" ? (v as Record<string, number>) : {};
  } catch {
    return {};
  }
}

/** A message's first line, short enough for the sidebar. */
function firstLine(text: string): string {
  const line = text.replace(/[#*_`>]/g, "").split("\n").map((l) => l.trim()).find(Boolean) ?? "";
  return line.length > 80 ? `${line.slice(0, 79)}…` : line;
}

function boot(): void {
  const foyelle = window.foyelle;
  if (!foyelle) {
    document.body.textContent = "The canvas bridge is missing (no preload).";
    return;
  }
  new Canvas(foyelle);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
