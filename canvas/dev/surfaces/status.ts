// Demo "System status" surface: refreshes itself through tools/call.
import { el, startView, type ViewConnection } from "../../src/renderer/builtin-views/view-runtime";

interface Snapshot {
  model: string;
  route: string;
  memory: { usedGb: number; totalGb: number };
  uptime: string;
  approvalsToday: number;
  connectors: Array<{ name: string; state: string; sensitivity: string }>;
}

const root = document.getElementById("root")!;

function card(k: string, v: string, extra?: Node): HTMLElement {
  return el("div", { class: "card" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v), extra ?? null);
}

function render(s: Snapshot): void {
  const pct = Math.round((s.memory.usedGb / s.memory.totalGb) * 100);
  const bar = el("div", { class: "bar" }, el("span", {}));
  (bar.firstChild as HTMLElement).style.width = `${pct}%`;
  root.replaceChildren(
    el("h1", {}, "System status"),
    el(
      "div",
      { class: "cards" },
      card("Model", s.model.replace(/ \(mock\)$/, "")),
      card("Route", s.route),
      card("VM memory", `${s.memory.usedGb.toFixed(1)} / ${s.memory.totalGb} GB`, bar),
      card("Approvals today", String(s.approvalsToday)),
    ),
    el(
      "table",
      {},
      el("thead", {}, el("tr", {}, el("th", {}, "Connector"), el("th", {}, "State"), el("th", {}, "Sensitivity"))),
      el(
        "tbody",
        {},
        ...s.connectors.map((c) =>
          el("tr", {}, el("td", {}, c.name), el("td", {}, el("span", { class: `dot ${c.state === "running" ? "" : "idle"}` }), c.state), el("td", {}, c.sensitivity)),
        ),
      ),
    ),
    el("p", { class: "muted" }, `Harness up ${s.uptime}. Refreshes every 5 seconds.`),
  );
}

async function refresh(view: ViewConnection): Promise<void> {
  try {
    const r = await view.callTool("status", {});
    if (r.structuredContent) render(r.structuredContent as unknown as Snapshot);
  } catch {
    /* host may be tearing us down */
  }
}

void startView("demo-status", (view) => {
  view.ontoolresult = (r) => {
    if (r.structuredContent) render(r.structuredContent as unknown as Snapshot);
  };
  view.onteardown = () => clearInterval(timer);
}).then((view) => {
  void refresh(view);
  timer = setInterval(() => void refresh(view), 5000);
});

let timer: ReturnType<typeof setInterval> | undefined;
