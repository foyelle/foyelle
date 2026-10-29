import { el, startView, type ViewConnection } from "./view-runtime";
import { normalizeSecret } from "./normalize";

const root = document.getElementById("root")!;
const nodes = (...n: Array<Node | null>): Node[] => n.filter((x): x is Node => x !== null);

function render(view: ViewConnection, input: Record<string, unknown>): void {
  const sudo = input.kind === "sudo";
  const s = normalizeSecret(input.request);
  const field = el("input", { type: "password", autocomplete: "off", spellcheck: "false", "aria-label": sudo ? "Password" : "Secret value" });
  const status = el("p", { class: "status", role: "status" });
  const send = el("button", { type: "button", class: "primary" }, "Provide");
  const cancel = el("button", { type: "button" }, "Cancel");

  const finish = async (tool: string, args: Record<string, unknown>) => {
    send.disabled = cancel.disabled = field.disabled = true;
    status.textContent = "Sending…";
    try {
      const r = await view.callTool(tool, args);
      if (r.isError) throw new Error(r.content?.[0]?.text ?? "failed");
      field.value = "";
      status.textContent = "Sent.";
    } catch (err) {
      status.textContent = `Could not send: ${(err as Error).message}`;
      send.disabled = cancel.disabled = field.disabled = false;
    }
  };
  send.addEventListener("click", () => void finish("secret_respond", { value: field.value }));
  cancel.addEventListener("click", () => void finish("secret_cancel", {}));
  field.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      send.click();
    }
  });

  root.replaceChildren(
    ...nodes(
    el("header", {}, el("div", { class: "kicker" }, sudo ? "Administrator password" : "Secret needed"), el("h1", {}, s.prompt)),
    s.name ? el("p", { class: "muted" }, "Name: ", el("span", { class: "mono" }, s.name)) : null,
    el("p", { class: "notice" }, "The value goes to the harness only. It is not shown in the chat or kept by the canvas."),
    field,
    el("div", { class: "actions" }, send, cancel),
    status,
    ),
  );
  queueMicrotask(() => field.focus());
}

void startView("foyelle-secret", (view) => {
  view.ontoolinput = (args) => render(view, args);
});
