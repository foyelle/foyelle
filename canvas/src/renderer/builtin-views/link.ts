import { el, startView, type ViewConnection } from "./view-runtime";

const root = document.getElementById("root")!;

function render(view: ViewConnection, input: Record<string, unknown>): void {
  const url = typeof input.url === "string" ? input.url : "";
  const from = typeof input.from === "string" ? input.from : "A card";
  const close = el("button", { type: "button", class: "primary" }, "Close");
  close.addEventListener("click", () => {
    close.disabled = true;
    void view.callTool("link_close", {}).catch(() => (close.disabled = false));
  });
  root.replaceChildren(
    el("header", {}, el("div", { class: "kicker" }, "Link"), el("h1", {}, `${from} wants to open a link`)),
    el("div", { class: "url" }, url),
    el("p", { class: "muted" }, "Foyelle has no web browser yet, so links are shown here and not opened. You can ask the agent to fetch the page."),
    el("div", { class: "actions" }, close),
  );
  queueMicrotask(() => close.focus());
}

void startView("foyelle-link", (view) => {
  view.ontoolinput = (args) => render(view, args);
});
