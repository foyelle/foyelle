# Making an instant surface

An instant surface is a view on the canvas that you write and the user sees right away: a
dashboard, a list, a form. It uses tools that already exist (Files, Shell, Web, Email, Calendar,
System); it cannot add new tools or reach the network itself. For new capabilities, write a
connector instead (`howto("connector")`).

## Save and show it

- `workshop.save(name, title, body, description)`: `name` is 1-40 lowercase letters, digits or
  `-`; `body` is the HTML body: markup plus one `<script>`. Saving again replaces it and keeps the
  previous version (`workshop.versions`, `workshop.restore`).
- `workshop.show(name)` opens it on the canvas. It also appears in the user's surface list.
- `workshop.read(name)` returns the body, to change it; `workshop.remove(name)` moves it to the
  workshop's trash.

## The page

The body runs in a sandbox with Foyelle's styles and the `FoyelleView` runtime. Call tools with
`FoyelleView.run("<connector>.<tool>", args, busyText)`; it resolves with the tool's data, or
null after showing the error. Approvals the user would see for you appear for the surface too,
and some tools (key entry, image data) are refused to surfaces you write.

Helpers: `V.$(selector)`, `V.h(tag, attrs, ...children)` (builds elements; never build HTML from
strings with data in them), `V.clear(el)`, `V.status(text)`, `V.fmtDate(iso)`,
`V.fmtBytes(n)`, `V.onSubmit(form, handler)` (forms inside the sandbox need it).
`V.start({name, load})` runs `load()` when the surface opens.

## Example: recent documents

```html
<main>
  <div class="toolbar"><h1>Recent documents</h1><span class="spacer"></span>
    <button id="refresh" type="button">Refresh</button></div>
  <div id="status" class="status" role="status" aria-live="polite"></div>
  <ul id="list" class="list"></ul>
</main>
<script>
(function () {
  var V = FoyelleView, h = V.h;
  function load() {
    return V.run("files.list", { path: "documents" }, "Loading…").then(function (d) {
      if (!d) return;
      var list = V.clear(V.$("#list"));
      d.entries.filter(function (e) { return e.type === "file"; })
        .sort(function (a, b) { return a.modified < b.modified ? 1 : -1; })
        .slice(0, 10)
        .forEach(function (e) {
          list.appendChild(h("li", null, h("strong", { text: e.name }), " ",
            h("span", { class: "muted", text: V.fmtDate(e.modified) })));
        });
    });
  }
  V.$("#refresh").addEventListener("click", load);
  V.start({ name: "recent-documents", load: load });
})();
</script>
```

Keep surfaces small and focused, show errors through `#status`, and tell the user what you made.
