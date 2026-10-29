import { describeTool } from "../words";
import { el, startView, type ViewConnection } from "./view-runtime";
import { CHOICE_LABELS, normalizeApproval, prettyJson, type ApprovalView, type FoyelleRequest } from "./normalize";

const root = document.getElementById("root")!;

/** How each kind of approval looks: the front door, a change to the computer, sharing. */
const KINDS: Record<string, { icon: string; kicker: string; sub: string; tone: string }> = {
  outbound: { icon: "door", kicker: "Leaving your home", sub: "Only what is shown below goes out.", tone: "out" },
  tainted: {
    icon: "door",
    kicker: "Check before it goes out",
    sub: "Earlier I read something from outside, like a web page or an email. Make sure this is what you want to send.",
    tone: "warn",
  },
  system: { icon: "house", kicker: "Changing your computer", sub: "You can always go back to the version before.", tone: "home" },
  "private-remote": { icon: "cloud", kicker: "Sharing with the online model", sub: "", tone: "out" },
  unknown: { icon: "question", kicker: "A helper you don't have", sub: "This helper is not part of your computer.", tone: "warn" },
  unreviewed: {
    icon: "question",
    kicker: "Not reviewed by Foyelle",
    sub: "Foyelle hasn't checked what this does. What you ask goes to the service, and what comes back is treated with care.",
    tone: "warn",
  },
};

/** What each connector holds, in words, for "share your … with the online model". */
const HOLDS: Record<string, string> = {
  files: "files", email: "email", calendar: "calendar", shell: "settings and memories",
  system: "computer settings", sysmon: "computer details", web: "web results", workshop: "cards",
};

/** Field names in plain words; anything internal is not shown. */
const FIELDS: Record<string, string> = {
  to: "To", cc: "Copy to", bcc: "Hidden copy to", subject: "Subject", body: "Message", attachments: "Attachments",
  query: "Search words", url: "Web page", title: "Title", start: "Starts", end: "Ends", location: "Where",
  response: "Answer", comment: "Note", path: "Where", content: "Content", city: "City",
};
const HIDDEN = new Set(["approval_token", "proposal_id", "generation", "session_id", "tool_call_id"]);

function headline(r: FoyelleRequest): { title: string; yes: string } {
  const a = r.args as Record<string, unknown>;
  const s = (k: string) => (typeof a[k] === "string" && a[k] ? String(a[k]) : "");
  switch (`${r.connector}.${r.tool}`) {
    case "email.send":
      return { title: `Send an email${s("to") ? ` to ${s("to")}` : ""}`, yes: "Send it" };
    case "web.search":
      return { title: `Search the web for “${s("query")}”`, yes: "Search" };
    case "web.fetch":
      return { title: `Open ${hostOf(s("url"))}`, yes: "Open it" };
    case "calendar.respond_invite":
      return { title: "Answer the invitation", yes: "Answer it" };
    case "system.apply":
      return { title: "Apply the change you reviewed", yes: "Apply it" };
    case "system.rollback":
      return { title: "Go back to an earlier version of your computer", yes: "Go back" };
    case "system.disk_passphrase":
      return { title: "Change the key to your disk", yes: "Open the key prompt" };
    case "system.recovery_password":
      return { title: "Set the recovery key", yes: "Open the key prompt" };
  }
  if (r.kind === "private-remote") {
    const holds = HOLDS[r.connector ?? ""] ?? r.connector ?? "data";
    return { title: `Share your ${holds} with the online model?`, yes: "Share for this conversation" };
  }
  if (r.kind === "unreviewed") {
    return r.tier === "read"
      ? { title: `Use ${r.connector} to look things up?`, yes: "Allow for this conversation" }
      : { title: `Let Foyelle use ${r.tool} on ${r.connector}?`, yes: "Allow for this conversation" };
  }
  const said = describeTool(`mcp__${r.connector}__${r.tool}`, a);
  return { title: [said.label, said.detail].filter(Boolean).join(" "), yes: "Allow" };
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url || "a web page";
  }
}

function icon(name: string): SVGSVGElement {
  const paths: Record<string, string> = {
    door: '<path d="M6 21V4a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v17M3 21h18M13 12h.01M16 7l4 0m-2-2 2 2-2 2"/>',
    house: '<path d="M4 11 12 4l8 7M6 10v10h12V10M10 20v-5h4v5"/>',
    cloud: '<path d="M7 18a4 4 0 0 1-.6-7.96A6 6 0 0 1 18 9a4.5 4.5 0 0 1-.5 9H7z"/>',
    question: '<path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17.5h.01"/><circle cx="12" cy="12" r="9"/>',
  };
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = `<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths[name] ?? paths.question}</g>`;
  return svg;
}

function fieldRows(args: Record<string, unknown>): Node[] {
  const rows: Node[] = [];
  for (const [k, v] of Object.entries(args)) {
    if (HIDDEN.has(k) || v === null || v === undefined || v === "") continue;
    const label = FIELDS[k] ?? k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
    const text = typeof v === "string" ? v : Array.isArray(v) ? v.join(", ") : prettyJson(v);
    const long = text.length > 80 || text.includes("\n");
    rows.push(el("dt", {}, label), el("dd", {}, long ? el("div", { class: "field-long" }, text) : text));
  }
  return rows;
}

function render(view: ViewConnection, input: Record<string, unknown>): void {
  const a = normalizeApproval(input.request);
  const status = el("p", { class: "status", role: "status" });
  const buttons: HTMLButtonElement[] = [];

  const choose = async (choice: string) => {
    for (const b of buttons) b.disabled = true;
    status.textContent = choice === "deny" ? "Okay, not now…" : "Going ahead…";
    try {
      const r = await view.callTool("approval_respond", { choice });
      if (r.isError) throw new Error(r.content?.[0]?.text ?? "failed");
      status.textContent = "";
    } catch (err) {
      status.textContent = `That didn't reach Foyelle (${(err as Error).message}). Try again.`;
      for (const b of buttons) b.disabled = false;
    }
  };
  const button = (choice: string, label: string, cls: string) => {
    const b = el("button", { class: cls, type: "button", "data-choice": choice }, el("span", { class: "key" }, String(buttons.length + 1)), label);
    b.addEventListener("click", () => void choose(choice));
    buttons.push(b);
    return b;
  };

  root.replaceChildren(...(a.request ? frontDoor(a, a.request, button) : generic(a, button)), status);

  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const n = Number(e.key);
    if (Number.isInteger(n) && n >= 1 && n <= buttons.length && !buttons[n - 1]!.disabled) {
      e.preventDefault();
      buttons[n - 1]!.click();
    }
  });
}

type MakeButton = (choice: string, label: string, cls: string) => HTMLButtonElement;

function frontDoor(a: ApprovalView, r: FoyelleRequest, button: MakeButton): Node[] {
  const kind = KINDS[r.kind] ?? KINDS.unknown!;
  const { title, yes } = headline(r);
  const has = (c: string) => a.choices.includes(c);
  const actions: HTMLButtonElement[] = [];
  if (r.kind === "private-remote" || r.kind === "unreviewed") {
    if (has("session")) actions.push(button("session", yes, "primary"));
    if (has("once")) actions.push(button("once", "Just this once", has("session") ? "" : "primary"));
    if (has("deny")) actions.push(button("deny", "Keep it here", ""));
  } else {
    // One clear yes and one no: an outbound or system approval never carries over to later calls.
    if (has("once")) actions.push(button("once", yes, "primary"));
    if (has("deny")) actions.push(button("deny", "Not now", ""));
  }
  const holds = HOLDS[r.connector ?? ""] ?? r.connector ?? "data";
  const sub = r.kind === "private-remote"
    ? `To answer, the online model needs to read what I found in your ${holds}. It leaves this computer, for this conversation only.`
    : kind.sub;
  const rows = fieldRows(r.args);
  return [
    el("header", { class: `door tone-${kind.tone}` },
      el("div", { class: "door-icon" }, icon(kind.icon)),
      el("div", {}, el("div", { class: "kicker" }, kind.kicker), el("h1", {}, title))),
    sub ? el("p", { class: "door-sub" }, sub) : null,
    rows.length ? el("dl", { class: "fields" }, ...rows) : null,
    r.origin === "surface" ? el("p", { class: "muted small" }, "Asked by a card on your screen.") : null,
    el("div", { class: "actions" }, ...actions),
    el("details", {}, el("summary", {}, "Technical details"),
      el("pre", {}, `${r.connector}.${r.tool} (${r.tier} tier)\n\n${prettyJson(a.raw)}`)),
  ].filter((n): n is HTMLElement => n !== null);
}

/** An approval that did not come from Foyelle's policy (Hermes's own prompts). */
function generic(a: ApprovalView, button: MakeButton): Node[] {
  const actions = a.choices.map((c, i) => button(c, CHOICE_LABELS[c] ?? c, c === "deny" ? "" : i === 0 ? "primary" : ""));
  const rows: Node[] = [];
  if (a.args && typeof a.args === "object" && !Array.isArray(a.args)) rows.push(...fieldRows(a.args as Record<string, unknown>));
  return [
    el("header", { class: "door tone-home" }, el("div", { class: "door-icon" }, icon("question")),
      el("div", {}, el("div", { class: "kicker" }, "Before I go on"), el("h1", {}, a.plain ?? a.description ?? "May I go ahead?"))),
    rows.length ? el("dl", { class: "fields" }, ...rows) : null,
    el("div", { class: "actions" }, ...actions),
    el("details", {}, el("summary", {}, "Technical details"), el("pre", {}, prettyJson(a.raw))),
  ].filter((n): n is HTMLElement => n !== null);
}

void startView("foyelle-approval", (view) => {
  view.ontoolinput = (args) => render(view, args);
});
