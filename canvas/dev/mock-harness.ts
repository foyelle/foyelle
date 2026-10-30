/**
 * A mock of the harness gateway for `npm run dev:web`. It speaks the legacy
 * tui_gateway dialect from docs/architecture.md §1 (events, requests,
 * server→client requests, request.cancel) plus the surface methods, with
 * canned flows chosen by keywords in the prompt.
 */

export interface MockConnection {
  send(frame: unknown): void;
  onMessage(cb: (text: string) => void): void;
  onClose(cb: () => void): void;
}

/** The real connectors on sample data (dev/tools_host.py), when it is running. */
export interface ToolsHost {
  surfaces(): Promise<Array<{ server: string; uri: string; name?: string; title?: string; description?: string; service?: string | null }>>;
  resource(server: string, uri: string): Promise<{ html: string; csp: unknown }>;
  call(server: string, name: string, args: Record<string, unknown>): Promise<any>;
}

interface Options {
  loadSurfaceHtml(name: string): Promise<string>;
  tools?: ToolsHost | null;
}

/** Calls that ask the user first on a real machine (system tier, outbound). */
const ASKS: Record<string, string> = {
  "system.apply": "Apply this change to your computer",
  "system.rollback": "Go back to an earlier version of your computer",
  "system.disk_passphrase": "Open the prompt to change the disk passphrase",
  "system.recovery_password": "Open the prompt to set the recovery password",
};

const MODEL = "opencode-zen/qwen3-coder (mock)";
const CHOICES = ["once", "session", "always", "deny"];

/** The description Foyelle's policy plugin writes (harness/plugin, describe()), for realistic cards. */
function foyelleDescription(kind: string, connector: string, tool: string, args: unknown, origin = "model"): string {
  const plain: Record<string, string> = {
    outbound: `This will leave your home (${connector}.${tool}).`,
    system: `This changes your computer (${connector}.${tool}).`,
    "private-remote": `To answer, I would send what is in your ${connector} to the online model, for this conversation.`,
  };
  const request = { kind, connector, tool, tier: kind === "private-remote" ? "read" : kind, origin, args };
  return `${plain[kind] ?? "May I?"}\n\nFoyelle-Request: ${JSON.stringify(request)}\n\n(mock) ${connector}.${tool} with ${JSON.stringify(args)}`;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

class Interrupted extends Error {}

const FILES = [
  { name: "Quarterly report.pdf", kind: "file", size: "2.4 MB", modified: "Today 09:12" },
  { name: "Notes", kind: "folder", size: "14 items", modified: "Yesterday" },
  { name: "budget-2027.ods", kind: "file", size: "88 KB", modified: "Mon 16:40" },
  { name: "Holiday photos", kind: "folder", size: "212 items", modified: "12 Oct" },
  { name: "foyelle-pitch.md", kind: "file", size: "6 KB", modified: "18 Oct" },
  { name: "invoice-0042.pdf", kind: "file", size: "120 KB", modified: "2 Oct" },
];

export class MockHarness {
  private sessionCounter = 0;
  /** Each conversation's messages (as session.resume returns them), while the mock runs. */
  private readonly sessions = new Map<string, Array<{ role: string; text: string }>>();
  private surfaceCounter = 0;

  constructor(private readonly opts: Options) {}

  private readonly live = new Set<MockSession>();

  connect(conn: MockConnection): void {
    const session = new MockSession(this, conn, this.opts);
    this.live.add(session);
    conn.onClose(() => this.live.delete(session));
  }

  /**
   * Dev: play a request into every open conversation, as if typed there (design review).
   * Once per conversation: a canvas can hold more than one connection to the same one.
   */
  drive(text: string): number {
    const done = new Set<string>();
    for (const s of this.live) {
      const sid = s.drive(text, done);
      if (sid) done.add(sid);
    }
    return done.size;
  }

  createSession(): string {
    const id = `mock-${Date.now().toString(36)}-${++this.sessionCounter}`;
    this.sessions.set(id, []);
    return id;
  }

  hasSession(id: string): boolean {
    return this.sessions.has(id);
  }

  /** The real harness resumes by the stored id (session.info's stored_session_id). */
  sessionOfStored(stored: string): string | null {
    const id = stored.replace(/^stored-/, "");
    return this.sessions.has(id) ? id : null;
  }

  history(id: string): Array<{ role: string; text: string }> {
    return this.sessions.get(id) ?? [];
  }

  remember(id: string, role: string, text: string): void {
    if (text.trim()) this.sessions.get(id)?.push({ role, text });
  }

  nextSurfaceId(): string {
    return `s-${Date.now().toString(36)}-${++this.surfaceCounter}`;
  }
}

class MockSession {
  private lastSession: string | null = null;
  private serverRequests = new Map<string, (result: any) => void>();
  private srqCounter = 0;
  private running: { interrupted: boolean } | null = null;
  private closed = false;

  constructor(
    private readonly harness: MockHarness,
    private readonly conn: MockConnection,
    private readonly opts: Options,
  ) {
    conn.onMessage((text) => this.onFrame(text));
    conn.onClose(() => {
      this.closed = true;
      if (this.running) this.running.interrupted = true;
    });
    setTimeout(() => this.event("gateway.ready", { version: "mock-0.1", server_requests: true }), 50);
  }

  // ----- framing ---------------------------------------------------------

  private event(type: string, payload: unknown, sessionId?: string): void {
    if (this.closed) return;
    this.conn.send({ jsonrpc: "2.0", method: "event", params: { type, ...(sessionId ? { session_id: sessionId } : {}), payload } });
  }

  private reply(id: unknown, result: unknown): void {
    this.conn.send({ jsonrpc: "2.0", id, result });
  }

  private error(id: unknown, code: number, message: string): void {
    this.conn.send({ jsonrpc: "2.0", id, error: { code, message } });
  }

  private ask(method: "approval" | "clarify" | "secret" | "sudo", params: unknown, run: { interrupted: boolean }, cancelAfterMs?: number): Promise<any> {
    const id = `srq-${++this.srqCounter}`;
    this.conn.send({ jsonrpc: "2.0", id, method, params });
    return new Promise((resolve, reject) => {
      this.serverRequests.set(id, resolve);
      if (cancelAfterMs) {
        setTimeout(() => {
          if (!this.serverRequests.delete(id)) return;
          this.event("request.cancel", { id });
          resolve({ cancelled: true });
        }, cancelAfterMs);
      }
      const poll = setInterval(() => {
        if (run.interrupted && this.serverRequests.delete(id)) {
          clearInterval(poll);
          this.event("request.cancel", { id });
          reject(new Interrupted());
        }
        if (!this.serverRequests.has(id)) clearInterval(poll);
      }, 100);
    });
  }

  private onFrame(text: string): void {
    let msg: any;
    try {
      msg = JSON.parse(text);
    } catch {
      return;
    }
    if (msg.method === undefined && msg.id !== undefined) {
      const resolve = this.serverRequests.get(String(msg.id));
      if (resolve) {
        this.serverRequests.delete(String(msg.id));
        resolve(msg.error ? { error: msg.error } : msg.result);
      }
      return;
    }
    // A failure in a request (the tools host restarting, say) answers that request with an error
    // instead of taking the whole mock harness down.
    this.onRequest(msg.id, msg.method, msg.params ?? {}).catch((err: unknown) =>
      this.error(msg.id, -32000, `mock harness: ${(err as Error)?.message ?? String(err)}`),
    );
  }

  private async onRequest(id: unknown, method: string, params: any): Promise<void> {
    switch (method) {
      case "client.capabilities":
        return this.reply(id, { ok: true });
      case "ping":
        return this.reply(id, { pong: true });
      case "session.create": {
        const sid = this.harness.createSession();
        this.lastSession = sid;
        this.reply(id, { session_id: sid });
        this.event("session.info", { model: MODEL, cwd: "/home/foyelle", stored_session_id: `stored-${sid}` }, sid);
        return;
      }
      case "session.resume": {
        const sid = this.harness.sessionOfStored(String(params.session_id ?? ""));
        if (!sid) return this.error(id, 5000, "no such session in the store");
        this.lastSession = sid;
        this.reply(id, { session_id: sid, messages: this.harness.history(sid) });
        this.event("session.info", { model: MODEL, stored_session_id: `stored-${sid}` }, sid);
        return;
      }
      case "profiles.create":
      case "profiles.configure":
        return this.reply(id, { ok: true, name: params.name });
      case "cron.manage":
        // Routines are made by asking a bot; the mock has none, but answers like the harness.
        return this.reply(id, params.action === "list" ? { success: true, count: 0, jobs: [] } : { success: true });
      case "session.list":
        return this.reply(id, { sessions: [] });
      case "session.interrupt":
        if (this.running) this.running.interrupted = true;
        return this.reply(id, { ok: true });
      case "prompt.submit":
        if (!this.harness.hasSession(params.session_id)) return this.error(id, -32004, "unknown session");
        this.harness.remember(params.session_id, "user", String(params.text ?? ""));
        this.reply(id, { ok: true });
        void this.runPrompt(params.session_id, String(params.text ?? ""));
        return;
      case "surface.list":
        if (this.opts.tools) return this.reply(id, { surfaces: await this.opts.tools.surfaces() });
        return this.reply(id, {
          surfaces: [
            { server: "shell", uri: "ui://shell/status", name: "status", title: "System status", pinned: true,
              description: "How the computer is doing: the model in use, the helpers and the network." },
            { server: "files", uri: "ui://files/browser", name: "browser", title: "Files", pinned: false,
              description: "Your documents and pictures: look inside, move things to the trash, bring back earlier versions." },
          ],
        });
      case "surface.open_resource": {
        if (this.opts.tools) {
          const doc = await this.opts.tools.resource(params.server, params.uri);
          return this.reply(id, { surface_id: this.harness.nextSurfaceId(), session_id: params.session_id, server: params.server, uri: params.uri, ...doc });
        }
        const name = params.uri === "ui://shell/status" ? "status" : params.uri === "ui://files/browser" ? "files" : null;
        if (!name) return this.error(id, -32602, `unknown resource ${params.uri}`);
        return this.reply(id, {
          surface_id: this.harness.nextSurfaceId(),
          session_id: params.session_id,
          server: params.server,
          uri: params.uri,
          title: name === "status" ? "System status" : "Files",
          html: await this.opts.loadSurfaceHtml(name),
          csp: { connectDomains: [], resourceDomains: [] },
        });
      }
      case "surface.call_tool":
        return this.surfaceCallTool(id, params);
      case "reload.mcp":
        // The real harness reconnects its MCP servers; the tools host's helpers are always up.
        return this.reply(id, { status: "reloaded", message: "MCP servers reconciled (dev)" });
      case "surface.read_resource":
        return this.reply(id, { contents: [{ uri: params.uri, mimeType: "text/plain", text: `Contents of ${params.uri}` }] });
      default:
        return this.error(id, -32601, `method not found: ${method}`);
    }
  }

  drive(text: string, skip: Set<string>): string | null {
    if (!this.lastSession || this.closed || skip.has(this.lastSession)) return null;
    void this.runPrompt(this.lastSession, text);
    return this.lastSession;
  }

  private async surfaceCallTool(id: unknown, params: any): Promise<void> {
    const args = params.arguments ?? {};
    if (this.opts.tools) {
      const key = params.server === "workshop" && String(params.name).includes(".") ? String(params.name) : `${params.server}.${params.name}`;
      if (ASKS[key]) {
        const answer = await this.ask(
          "approval",
          { tool_name: key, server: key.split(".")[0], args, description: foyelleDescription("system", key.split(".")[0]!, key.split(".")[1]!, args, "surface"), origin: "surface", choices: CHOICES },
          { interrupted: false },
        ).catch(() => ({ choice: "deny" }));
        if (!answer?.choice || answer.choice === "deny") {
          return this.reply(id, { content: [{ type: "text", text: "You declined." }], isError: true });
        }
        args.approval_token = "dev-approval-token-0000";
      }
      return this.reply(id, await this.opts.tools.call(params.server, params.name, args));
    }
    switch (params.name) {
      case "status":
        return this.reply(id, {
          content: [{ type: "text", text: "ok" }],
          structuredContent: statusSnapshot(),
        });
      case "list":
        return this.reply(id, { content: [{ type: "text", text: `${FILES.length} entries` }], structuredContent: { path: args.path ?? "~/Documents", entries: FILES } });
      case "read_file": {
        const f = FILES.find((x) => x.name === args.name);
        return this.reply(id, {
          content: [{ type: "text", text: f ? `${f.name}\n${f.kind === "folder" ? `A folder with ${f.size}.` : `${f.size}, last changed ${f.modified}.`}\n\n(This is a mock preview.)` : "No such file." }],
          isError: !f,
        });
      }
      case "delete_file": {
        // A surface-initiated call goes through the same approval flow as the model's.
        const run = { interrupted: false };
        const answer = await this.ask(
          "approval",
          {
            tool_name: "mcp_files_delete",
            server: "files",
            args: { path: `~/Documents/${args.name}` },
            description: `Move “${args.name}” to the trash`,
            origin: "surface",
            choices: CHOICES,
          },
          run,
        ).catch(() => ({ choice: "deny" }));
        if (answer?.choice && answer.choice !== "deny") {
          return this.reply(id, { content: [{ type: "text", text: `Moved ${args.name} to the trash.` }], structuredContent: { deleted: args.name } });
        }
        return this.reply(id, { content: [{ type: "text", text: "You declined." }], isError: true });
      }
      default:
        return this.reply(id, { content: [{ type: "text", text: `unknown tool ${params.name}` }], isError: true });
    }
  }

  // ----- canned flows ----------------------------------------------------

  private async runPrompt(sid: string, text: string): Promise<void> {
    if (this.running) this.running.interrupted = true;
    const run = { interrupted: false };
    this.running = run;
    const t = text.toLowerCase();
    let said = "";
    const check = () => {
      if (run.interrupted || this.closed) throw new Interrupted();
    };
    const say = async (s: string) => {
      for (const chunk of s.match(/[\s\S]{1,7}/g) ?? []) {
        check();
        said += chunk;
        this.event("message.delta", { text: chunk }, sid);
        await sleep(18 + Math.random() * 30);
      }
    };
    const status = (s: string) => this.event("status.update", { kind: "activity", text: s }, sid);
    const tool = async (name: string, args: unknown, ms: number, summary: string) => {
      const toolId = `t-${Math.random().toString(36).slice(2, 8)}`;
      this.event("tool.start", { tool_id: toolId, name, args }, sid);
      await sleep(ms);
      check();
      this.event("tool.complete", { tool_id: toolId, name, summary, result_text: summary }, sid);
      return toolId;
    };

    try {
      status("Thinking");
      await sleep(350);
      check();
      this.event("message.start", {}, sid);

      if (/\berror\b/.test(t)) {
        this.event("error", { message: "Model endpoint returned 503 (mock error)." }, sid);
        this.event("message.complete", { text: "", status: "error", error: "The model did not answer." }, sid);
        return;
      }

      const real = this.opts.tools;
      const openReal = async (server: string, uri: string, toolName: string, args: Record<string, unknown>, intro: string, outro: string) => {
        await say(intro);
        const toolId = `t-${Math.random().toString(36).slice(2, 8)}`;
        this.event("tool.start", { tool_id: toolId, name: `mcp__${server}__${toolName}`, args }, sid);
        const result = await real!.call(server, toolName, args);
        if (result?.isError) {
          // Like the harness: the tool's error text is the summary, and nothing opens.
          const text = String(result.content?.[0]?.text ?? "failed");
          this.event("tool.complete", { tool_id: toolId, name: `mcp__${server}__${toolName}`, summary: text, is_error: true }, sid);
          await say(text);
          return;
        }
        const doc = await real!.resource(server, uri);
        this.event("tool.complete", { tool_id: toolId, name: `mcp__${server}__${toolName}`, summary: "done" }, sid);
        this.event("surface.open", { surface_id: this.harness.nextSurfaceId(), session_id: sid, server, tool: toolName, tool_call_id: toolId,
          uri, ...doc, tool_input: args, tool_result: result }, sid);
        await say(outro);
      };

      if (/\b(cards|surfaces)\b/.test(t) && /\b(what|which|all|every|show|list|browse|see|have)\b/.test(t)) {
        // The agent opens the card gallery (shell.open_cards); the canvas draws it from surface.list.
        await say("Here are all the cards I can show you.");
        await tool("mcp__shell__open_cards", {}, 300, '{"opened": true}');
        await say("\n\nSearch for one, or open it from there.");
      } else if (real && /file|browse|document|folder|photo|picture/.test(t) && !/share|private|online|privacy|left/.test(t)) {
        await openReal("files", "ui://files/browser", "browse", { path: "documents" }, "Here are your documents.", "\n\nPick one to look inside, edit it, or go back to an earlier version.");
      } else if (real && /(check|read|new|show|what).*\b(e-?mail|mail|inbox)\b|\bmy (e-?mail|mail|inbox)\b/.test(t) && !/send/.test(t)) {
        await openReal("email", "ui://email/inbox", "search", {}, "Let me look at your mail.", "");
      } else if (real && /\b(make|create|design)\b.*\btheme\b/.test(t)) {
        // The agent making a theme: a canned one here, the real shell tool (and its checks) underneath.
        const toolId = `t-${Math.random().toString(36).slice(2, 8)}`;
        const args = {
          name: "Forest at night", based_on: "meadow", schemes: "dark",
          description: "Pine greens under a dark sky, with a moonlit yellow.",
          fonts: { body: "rounded", display: "serif" },
          shape: { radius: 16, edge_width: 1, lift: 0, drawn: false, grain: true, tilt: false },
          dark: { background: "#0c140e", sidebar: "#101a12", surface: "#16231a", surface_alt: "#1d2e22", line: "#2a3f2f",
            edge: "#4d6b54", primary: "#9fdc7c", highlight: "#f2e27a", on_highlight: "#16231a", accent: "#b8ea9a" },
        };
        await say("I'll make that and switch to it.");
        this.event("tool.start", { tool_id: toolId, name: "mcp__shell__theme_create", args }, sid);
        const result = await real!.call("shell", "theme_create", args);
        const text = result?.isError ? String(result.content?.[0]?.text) : "done";
        this.event("tool.complete", { tool_id: toolId, name: "mcp__shell__theme_create", summary: text, is_error: !!result?.isError }, sid);
        await say(result?.isError ? `\n\nThat didn't work: ${text}` : "\n\n**Forest at night** is on: pine greens under a dark sky. Say if you'd like it lighter, or another colour for the buttons.");
      } else if (real && /\b(ember|paper|midnight|meadow|ocean|pop|terminal|blossom|contrast|dusk|tokyo[ -]night|millennium)\b/.test(t) && /\b(use|switch|theme|try)\b/.test(t)) {
        const id = /\b(ember|paper|midnight|meadow|ocean|pop|terminal|blossom|contrast|dusk|tokyo[ -]night|millennium)\b/.exec(t)![1]!.replace(" ", "-");
        const toolId = `t-${Math.random().toString(36).slice(2, 8)}`;
        this.event("tool.start", { tool_id: toolId, name: "mcp__shell__theme_use", args: { theme_id: id } }, sid);
        const result = await real!.call("shell", "theme_use", { theme_id: id });
        this.event("tool.complete", { tool_id: toolId, name: "mcp__shell__theme_use", summary: "done", is_error: !!result?.isError }, sid);
        const named = (() => { try { return JSON.parse(result?.content?.[0]?.text ?? "{}").name; } catch { return undefined; } })();
        await say(result?.isError ? "That didn't work." : `Switched to ${String(result?.structuredContent?.name ?? result?.structuredContent?.result?.name ?? named ?? id)}.`);
      } else if (real && /\bthemes?\b/.test(t)) {
        await openReal("shell", "ui://shell/themes", "theme_list", {}, "Here are the themes.", "\n\nPick one, or tell me what you'd like and I'll make you your own.");
      } else if (real && /\b(characters?|embers?|mascots?)\b/.test(t)) {
        await openReal("design", "ui://design/ember", "show_ember_characters", {}, "Here are ten takes on the ember as a character.",
          "\n\nEach shows its moods and how it reads as an icon.");
      } else if (real && /\b(2026|trends?|six)\b/.test(t)) {
        await openReal("design", "ui://design/2026", "show_2026_directions", {}, "Here are six directions from 2026.",
          "\n\nThe ember character and the ember matrix show their moods under the screen.");
      } else if (real && /\b(glass|variations?)\b/.test(t)) {
        await openReal("design", "ui://design/glass", "show_glass_variations", {}, "Here are ten variations of Ember glass.",
          "\n\nEach changes one or two things. Tell me which ones to keep, or what to mix.");
      } else if (real && /\b(design|directions|mockups?|looks?)\b/.test(t)) {
        await openReal("design", "ui://design/directions", "show_directions", {}, "Here are twelve directions for Foyelle's look.",
          "\n\nEach draws the same screen. Tell me which ones feel right, or what to mix.");
      } else if (real && /\badd\b.*\b(tasks?|to-?dos?)\b/.test(t)) {
        // A change made by the agent while the Tasks card is open: the card reloads by itself.
        const toolId = `t-${Math.random().toString(36).slice(2, 8)}`;
        const args = { tasks: [{ content: "Try the new Tasks card", dueString: "today" }] };
        this.event("tool.start", { tool_id: toolId, name: "mcp__todoist__add_tasks", args }, sid);
        const result = await real!.call("todoist", "add_tasks", args);
        this.event("tool.complete", { tool_id: toolId, name: "mcp__todoist__add_tasks", summary: result?.isError ? String(result.content?.[0]?.text) : "done",
          is_error: !!result?.isError }, sid);
        await say(result?.isError ? "That didn't work." : "Added “Try the new Tasks card” for today.");
      } else if (real && /\b(tasks?|to-?dos?|todoist)\b/.test(t)) {
        await openReal("todoist", "ui://todoist/tasks", "find_tasks_by_date", { startDate: "today", daysCount: 1 },
          "Here's what's on your list today.", "\n\nTick one off when it's done, or move it to another day.");
      } else if (real && /\bfl(y|ight)/.test(t)) {
        // Kiwi's real flight search through the bridge, two months ahead.
        const d = new Date(Date.now() + 60 * 864e5);
        const date = `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
        await openReal("kiwi", "ui://kiwi/flights", "search_flight", { flyFrom: "LIS", flyTo: "IST", departureDate: date },
          "Let me look for flights.", "\n\nThe cheapest and the fastest are marked. Booking happens on Kiwi.com.");
      } else if (real && /calendar|agenda|this week|schedule/.test(t)) {
        await openReal("calendar", "ui://calendar/agenda", "list_events", {}, "Here's your week.", "");
      } else if (real && /privacy|what left|what went out/.test(t)) {
        await openReal("shell", "ui://shell/privacy", "privacy_today", {}, "Here is what left your home today.", "");
      } else if (/share|private|online/.test(t)) {
        await say("To answer that I'd need to read your files.");
        const answer = await this.ask(
          "approval",
          { tool_name: "files.list", server: "files", args: { path: "documents" }, description: foyelleDescription("private-remote", "files", "list", { path: "documents" }), choices: CHOICES },
          run,
        );
        await say(answer?.choice && answer.choice !== "deny" ? `\n\nThanks (${answer.choice}). In a real conversation I'd go on now.` : "\n\nAlright, your files stay here. I'll answer without them.");
      } else if (/thank|note|letter|write/.test(t) && !/file|document/.test(t)) {
        await say("Here's a first draft:\n\n*Dear Rita,*\n\n*Thank you for the lovely dinner on Saturday. It was so good to see everyone, and your orange cake was the best I've had.*\n\n*Warmly, Sam*\n\nShall I save it in your Letters folder, or change anything first?");
      } else if (real && /memor|remember|about me/.test(t)) {
        await openReal("shell", "ui://shell/memory", "memory_read", {}, "This is what I remember.", "\n\nYou can change or remove anything.");
      } else if (real && /setting|preference/.test(t)) {
        await openReal("shell", "ui://shell/settings", "settings_get", {}, "Your settings.", "");
      } else if (real && /change|update|install|firefox|propos/.test(t)) {
        await openReal("system", "ui://system/review", "status", {}, "Here are the changes waiting for you.", "\n\nOpen one to see what it does, then approve or reject it.");
      } else if (real && /audit|log|history|what did you do/.test(t)) {
        await openReal("shell", "ui://shell/audit", "audit_query", {}, "Everything I did, and what left the house.", "");
      } else if (real && /status|health|system|computer|busy|doing/.test(t)) {
        await openReal("shell", "ui://shell/status", "status", {}, "Here's how the computer is doing.", "");
      } else if (/file|browse|document|folder/.test(t)) {
        await say("Let me look in your Documents folder.");
        status("Listing files");
        const toolId = await tool("mcp_files_browse", { path: "~/Documents" }, 700, `${FILES.length} entries`);
        this.event(
          "surface.open",
          {
            surface_id: this.harness.nextSurfaceId(),
            session_id: sid,
            server: "files",
            tool: "browse",
            tool_call_id: toolId,
            uri: "ui://files/browser",
            title: "Files · Documents",
            html: await this.opts.loadSurfaceHtml("files"),
            csp: { connectDomains: [], resourceDomains: [] },
            tool_input: { path: "~/Documents" },
            tool_result: {
              content: [{ type: "text", text: `${FILES.length} entries` }],
              structuredContent: { path: "~/Documents", entries: FILES },
              isError: false,
            },
          },
          sid,
        );
        await say(`\n\nI opened **Files** with ${FILES.length} entries. Select one to preview it; deleting asks for your approval first.`);
      } else if (/status|system|health/.test(t)) {
        await say("Here is the system status.");
        const toolId = await tool("mcp_shell_status", {}, 400, "6 connectors, all healthy");
        this.event(
          "surface.open",
          {
            surface_id: this.harness.nextSurfaceId(),
            session_id: sid,
            server: "shell",
            tool: "status",
            tool_call_id: toolId,
            uri: "ui://shell/status",
            title: "System status",
            html: await this.opts.loadSurfaceHtml("status"),
            csp: { connectDomains: [], resourceDomains: [] },
            tool_input: {},
            tool_result: { content: [{ type: "text", text: "ok" }], structuredContent: statusSnapshot() },
          },
          sid,
        );
        await say("\n\nIt refreshes itself every few seconds through `surface.call_tool`.");
      } else if (/send|email|mail|approv|delete/.test(t)) {
        await say("I drafted the email. Sending it is an outbound action, so I need your approval.");
        status("Waiting for approval");
        const toolId = `t-${Math.random().toString(36).slice(2, 8)}`;
        const args = { to: "jo@example.org", subject: "Q3 report", body: "Hi Jo,\n\nThe Q3 report is attached.\n\nBest,\nSam", attachments: ["Quarterly report.pdf"] };
        this.event("tool.start", { tool_id: toolId, name: "mcp_email_send", args }, sid);
        const cancelTest = /cancel/.test(t);
        const answer = await this.ask(
          "approval",
          {
            tool_name: "mcp_email_send",
            server: "email",
            args,
            description: foyelleDescription("outbound", "email", "send", args),
            choices: CHOICES,
          },
          run,
          cancelTest ? 6000 : undefined,
        );
        check();
        if (answer?.cancelled) {
          this.event("tool.complete", { tool_id: toolId, name: "mcp_email_send", summary: "withdrawn" }, sid);
          await say("\n\nI withdrew that request (this was the cancel test), so the approval card went away.");
        } else if (answer?.choice === "deny" || !answer?.choice) {
          this.event("tool.complete", { tool_id: toolId, name: "mcp_email_send", summary: "denied by you" }, sid);
          await say("\n\nUnderstood. I did **not** send it. The draft is still in your Drafts folder.");
        } else {
          await sleep(500);
          this.event("tool.complete", { tool_id: toolId, name: "mcp_email_send", summary: "sent" }, sid);
          await say(`\n\nSent (approval: \`${answer.choice}\`).`);
        }
      } else if (/cancel/.test(t)) {
        await say("I'll ask for approval and then withdraw the request after 6 seconds.");
        const answer = await this.ask(
          "approval",
          { tool_name: "mcp_web_search", server: "web", args: { query: "cheap flights to Lisbon" }, description: foyelleDescription("outbound", "web", "search", { query: "cheap flights to Lisbon" }), choices: CHOICES },
          run,
          6000,
        );
        await say(answer?.cancelled ? "\n\nWithdrawn: the card should be gone." : `\n\nYou answered \`${answer?.choice}\` before I withdrew it.`);
      } else if (/question|clarify|meeting|schedule/.test(t)) {
        await say("Before I schedule this, I have two questions.");
        status("Waiting for your answer");
        const answer = await this.ask(
          "clarify",
          {
            title: "Scheduling the review",
            questions: [
              { id: "when", question: "When should the review happen?", choices: ["Tomorrow 10:00", "Friday 14:00", "Next Monday"] },
              { id: "note", question: "Anything to add to the invitation?" },
            ],
          },
          run,
        );
        const a = answer?.answers ?? {};
        await say(`\n\nThanks. **When:** ${a.when ?? "no preference"}. **Note:** ${a.note ?? "none"}. (Mock: nothing was scheduled.)`);
      } else if (/secret|token|password|api key|key/.test(t)) {
        await say("I need an API key to continue.");
        const answer = await this.ask("secret", { name: "OPENCODE_API_KEY", prompt: "Paste your OpenCode Zen API key" }, run);
        if (answer?.error) await say("\n\nYou cancelled, so I'll stop here.");
        else await say(`\n\nThanks, I received a value of ${String(answer?.value ?? "").length} characters. It is not shown anywhere.`);
      } else if (/long|markdown/.test(t)) {
        await say(LONG);
      } else {
        await say(HELP(text));
      }
      check();
      this.event("message.complete", { text: said, status: "complete" }, sid);
      this.harness.remember(sid, "assistant", said);
    } catch (err) {
      if (err instanceof Interrupted) {
        this.event("message.complete", { text: said, status: "interrupted" }, sid);
      } else {
        this.event("error", { message: String(err) }, sid);
      }
    } finally {
      if (this.running === run) this.running = null;
      this.event("status.update", { kind: "idle", text: "" }, sid);
    }
  }
}

function statusSnapshot() {
  const mem = 3.1 + Math.random() * 0.6;
  return {
    model: MODEL,
    route: "remote (OpenCode Zen)",
    memory: { usedGb: Number(mem.toFixed(2)), totalGb: 6 },
    uptime: `${Math.floor(process.uptime() / 60)} min`,
    connectors: [
      { name: "shell", state: "running", sensitivity: "private" },
      { name: "files", state: "running", sensitivity: "private" },
      { name: "email", state: "running", sensitivity: "private" },
      { name: "calendar", state: "running", sensitivity: "private" },
      { name: "web", state: "running", sensitivity: "public" },
      { name: "system", state: "idle", sensitivity: "private" },
    ],
    approvalsToday: 3,
  };
}

const HELP = (text: string) => `You said: “${text.replace(/[\r\n]+/g, " ").slice(0, 120)}”.

This is the **mock harness**, so I only know a few tricks:

- *show my files* opens the Files card (try selecting and deleting a file)
- *system status* opens a live status card
- *what cards are there* opens the card gallery
- *send the report to Sam* asks for an approval
- *schedule a meeting* asks clarifying questions
- *I need a secret* asks for a secret
- *cancel test* asks, then withdraws the request
- *error* reports an error, *long* prints more markdown

Commands such as \`/cards\`, \`/pin\`, \`/dismiss\` and \`/help\` run in the canvas itself.`;

const LONG = `## A longer answer

Here is some **bold**, some *italic*, \`inline code\` and a link to [the spec](https://github.com/modelcontextprotocol/ext-apps) shown as text.

1. First, the canvas escapes everything.
2. Then it adds a small set of tags.
3. Links are never clickable: there is no browser.

\`\`\`nix
{ pkgs, ... }: {
  services.foyelle.canvas.enable = true;
  environment.systemPackages = [ pkgs.ripgrep ];
}
\`\`\`

<script>alert("this stays text")</script>

That's all.`;
