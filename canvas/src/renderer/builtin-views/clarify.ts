import { el, startView, type ViewConnection } from "./view-runtime";
import { normalizeClarify } from "./normalize";

const root = document.getElementById("root")!;

function render(view: ViewConnection, input: Record<string, unknown>): void {
  const { title, questions } = normalizeClarify(input.request);
  const inputs = new Map<string, HTMLInputElement>();
  const status = el("p", { class: "status", role: "status" });

  const blocks = questions.map((q, qi) => {
    const field = el("input", { type: "text", "aria-label": q.text, placeholder: q.choices.length ? "Or type an answer" : "Your answer" });
    inputs.set(q.id, field);
    const choiceButtons = q.choices.map((c) => {
      const b = el("button", { type: "button", "aria-pressed": "false" }, c);
      b.addEventListener("click", () => {
        field.value = c;
        for (const other of b.parentElement!.children) other.setAttribute("aria-pressed", String(other === b));
      });
      return b;
    });
    field.addEventListener("input", () => {
      for (const b of choiceButtons) b.setAttribute("aria-pressed", String(b.textContent === field.value));
    });
    field.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        void submit(false);
      }
    });
    if (qi === 0) queueMicrotask(() => field.focus());
    return el("div", { class: "question" }, el("p", {}, q.text), choiceButtons.length ? el("div", { class: "choices" }, ...choiceButtons) : null, field);
  });

  const send = el("button", { type: "button", class: "primary" }, "Answer");
  const skip = el("button", { type: "button" }, "Skip");

  const submit = async (skipAll: boolean) => {
    send.disabled = skip.disabled = true;
    const answers: Record<string, string | null> = {};
    for (const q of questions) {
      const v = inputs.get(q.id)!.value.trim();
      answers[q.id] = skipAll || !v ? null : v;
    }
    status.textContent = "Sending…";
    try {
      const r = await view.callTool("clarify_respond", { answers });
      if (r.isError) throw new Error(r.content?.[0]?.text ?? "failed");
      status.textContent = "Sent.";
    } catch (err) {
      status.textContent = `Could not send: ${(err as Error).message}`;
      send.disabled = skip.disabled = false;
    }
  };
  send.addEventListener("click", () => void submit(false));
  skip.addEventListener("click", () => void submit(true));

  root.replaceChildren(
    el("header", {}, el("div", { class: "kicker" }, "The agent asks"), el("h1", {}, title ?? (questions.length === 1 ? "A quick question" : "A few questions"))),
    ...blocks,
    el("div", { class: "actions" }, send, skip),
    status,
  );
}

void startView("foyelle-clarify", (view) => {
  view.ontoolinput = (args) => render(view, args);
});
