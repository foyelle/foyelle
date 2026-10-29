// Demo "Files" surface for the mock harness. Uses the canvas's View runtime,
// so it exercises the same MCP Apps protocol path as a connector surface.
import { el, startView, type CallToolResult, type ViewConnection } from "../../src/renderer/builtin-views/view-runtime";

interface Entry {
  name: string;
  kind: "file" | "folder";
  size: string;
  modified: string;
}

const root = document.getElementById("root")!;
let entries: Entry[] = [];
let path = "~/Documents";
let selected: string | null = null;

function text(r: CallToolResult): string {
  return r.content?.map((c) => c.text ?? "").join("\n") ?? "";
}

function render(view: ViewConnection): void {
  const note = el("p", { class: "note", role: "status" });
  const preview = el("div", { class: "preview" }, el("p", { class: "muted" }, "Select a file to preview it."));

  const show = async (e: Entry) => {
    selected = e.name;
    for (const b of list.querySelectorAll("button")) b.setAttribute("aria-selected", String(b.dataset.name === e.name));
    preview.replaceChildren(el("p", { class: "muted" }, "Loading…"));
    const r = await view.callTool("read_file", { name: e.name });
    const del = el("button", { class: "btn danger", type: "button" }, "Move to trash");
    const ask = el("button", { class: "btn", type: "button" }, "Ask the agent about it");
    const link = el("button", { class: "btn", type: "button" }, "Open on the web");
    del.addEventListener("click", async () => {
      del.disabled = true;
      note.textContent = "Waiting for your approval…";
      const res = await view.callTool("delete_file", { name: e.name });
      note.textContent = text(res);
      if (!res.isError) {
        entries = entries.filter((x) => x.name !== e.name);
        render(view);
      } else del.disabled = false;
    });
    ask.addEventListener("click", () => void view.sendMessage(`What is in “${e.name}”? Summarise it for me.`));
    link.addEventListener("click", () => void view.openLink(`https://example.org/share/${encodeURIComponent(e.name)}`));
    preview.replaceChildren(el("pre", {}, text(r)), el("div", { class: "actions" }, del, ask, link));
  };

  const list = el(
    "ul",
    { class: "files", role: "listbox", "aria-label": "Files" },
    ...entries.map((e) => {
      const b = el(
        "button",
        { type: "button", role: "option", "aria-selected": String(e.name === selected), "data-name": e.name },
        el("span", { class: `icon ${e.kind}` }),
        el("span", {}, e.name),
        el("span", { class: "meta" }, `${e.size} · ${e.modified}`),
      );
      b.addEventListener("click", () => void show(e));
      return el("li", {}, b);
    }),
  );

  root.replaceChildren(
    el("div", {}, el("h1", {}, "Documents"), el("div", { class: "path" }, path)),
    el("div", { class: "layout" }, list, preview),
    note,
  );
}

void startView("demo-files", (view) => {
  view.ontoolinput = (args) => {
    if (typeof args.path === "string") path = args.path;
  };
  view.ontoolresult = (r) => {
    const sc = r.structuredContent as { entries?: Entry[]; path?: string } | undefined;
    entries = sc?.entries ?? [];
    if (sc?.path) path = sc.path;
    render(view);
  };
}).then(async (view) => {
  // Opened without a tool call (from /surfaces): fetch the listing ourselves.
  await new Promise((r) => setTimeout(r, 150));
  if (!entries.length) {
    const r = await view.callTool("list", { path });
    const sc = r.structuredContent as { entries?: Entry[] } | undefined;
    entries = sc?.entries ?? [];
    render(view);
  }
});
