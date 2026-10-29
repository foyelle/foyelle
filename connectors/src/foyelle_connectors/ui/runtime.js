/* Foyelle view runtime: the View side of MCP Apps (SEP-1865), protocol 2026-01-26.
 * JSON-RPC 2.0 over postMessage with window.parent. No network, no dependencies.
 * Untrusted text is always rendered with textContent (see h()). */
(function () {
  "use strict";
  var PROTOCOL_VERSION = "2026-01-26";
  var parent = window.parent;
  var nextId = 1;
  var pending = new Map();

  var view = {
    hostContext: {},
    hostCapabilities: {},
    hostInfo: null,
    toolInput: null,
    onToolInput: null,
    onToolInputPartial: null,
    onToolResult: null,
    onToolCancelled: null,
    onHostContext: null,
    onTeardown: null,
    onChanged: null,
  };

  function post(msg) { parent.postMessage(msg, "*"); }

  function request(method, params) {
    var id = nextId++;
    return new Promise(function (resolve, reject) {
      pending.set(id, { resolve: resolve, reject: reject });
      post({ jsonrpc: "2.0", id: id, method: method, params: params || {} });
    });
  }

  function notify(method, params) {
    post({ jsonrpc: "2.0", method: method, params: params || {} });
  }

  function respond(id, result) { post({ jsonrpc: "2.0", id: id, result: result || {} }); }

  function respondError(id, code, message) {
    post({ jsonrpc: "2.0", id: id, error: { code: code, message: message } });
  }

  function call(fn, arg) {
    if (typeof fn !== "function") return;
    try { fn(arg); } catch (e) { console.error(e); }
  }

  function applyHostContext(ctx) {
    if (!ctx) return;
    Object.assign(view.hostContext, ctx);
    var root = document.documentElement;
    if (ctx.theme === "light" || ctx.theme === "dark") root.setAttribute("data-theme", ctx.theme);
    // The spec's style variables, then Foyelle's own extras (display font, highlight colours).
    [ctx.styles && ctx.styles.variables, ctx.foyelle && ctx.foyelle.styles].forEach(function (vars) {
      if (!vars) return;
      Object.keys(vars).forEach(function (k) {
        if (/^--[a-z0-9-]+$/.test(k) && typeof vars[k] === "string") root.style.setProperty(k, vars[k]);
      });
    });
    if (ctx.locale) root.lang = ctx.locale;
  }

  function onMessage(event) {
    if (event.source !== parent) return;
    var m = event.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.method === undefined && m.id !== undefined) {
      var p = pending.get(m.id);
      if (!p) return;
      pending.delete(m.id);
      if (m.error) {
        var err = new Error(m.error.message || "request failed");
        err.code = m.error.code;
        err.data = m.error.data;
        p.reject(err);
      } else {
        p.resolve(m.result);
      }
      return;
    }
    var params = m.params || {};
    if (m.id !== undefined) {
      switch (m.method) {
        case "ping": respond(m.id, {}); return;
        case "ui/resource-teardown": call(view.onTeardown, params); respond(m.id, {}); return;
        default: respondError(m.id, -32601, "Method not found: " + m.method); return;
      }
    }
    switch (m.method) {
      case "ui/notifications/tool-input":
        view.toolInput = params.arguments || {};
        call(view.onToolInput, view.toolInput);
        break;
      case "ui/notifications/tool-input-partial": call(view.onToolInputPartial, params.arguments || {}); break;
      case "ui/notifications/tool-result": call(view.onToolResult, params); break;
      case "ui/notifications/tool-cancelled": call(view.onToolCancelled, params); break;
      case "ui/notifications/host-context-changed":
        applyHostContext(params);
        call(view.onHostContext, view.hostContext);
        break;
      // Foyelle: the agent changed this helper's data (added a task, saved a file): reload.
      case "foyelle/notifications/changed": call(view.onChanged, params); break;
      default: break;
    }
  }

  window.addEventListener("message", onMessage);

  var sizeTimer = 0;
  function reportSize() {
    cancelAnimationFrame(sizeTimer);
    sizeTimer = requestAnimationFrame(function () {
      var el = document.documentElement;
      notify("ui/notifications/size-changed", { width: Math.ceil(el.scrollWidth), height: Math.ceil(el.scrollHeight) });
    });
  }

  view.connect = function (appInfo) {
    return request("ui/initialize", {
      appInfo: appInfo,
      appCapabilities: {},
      protocolVersion: PROTOCOL_VERSION,
    }).then(function (result) {
      result = result || {};
      view.hostInfo = result.hostInfo || null;
      view.hostCapabilities = result.hostCapabilities || {};
      applyHostContext(result.hostContext || {});
      notify("ui/notifications/initialized", {});
      if (typeof ResizeObserver === "function") new ResizeObserver(reportSize).observe(document.body);
      reportSize();
      return result;
    });
  };

  /** Call a tool on this view's own MCP server, through the host (policy applies). */
  view.callTool = function (name, args) {
    return request("tools/call", { name: name, arguments: args || {} });
  };
  view.readResource = function (uri) { return request("resources/read", { uri: uri }); };
  /** Post a user message into the chat. */
  view.sendMessage = function (text) {
    return request("ui/message", { role: "user", content: [{ type: "text", text: text }] });
  };
  view.openLink = function (url) { return request("ui/open-link", { url: url }); };
  view.updateModelContext = function (text) {
    return request("ui/update-model-context", { content: [{ type: "text", text: text }] });
  };
  /** Ask the host to save a text file for the user (ui/download-file). */
  view.downloadFile = function (filename, text, mimeType) {
    return request("ui/download-file", {
      contents: [{ type: "resource", resource: { uri: "file:///" + encodeURIComponent(filename), mimeType: mimeType || "text/plain", text: text } }],
    });
  };
  view.requestTeardown = function () { notify("ui/notifications/request-teardown", {}); };

  /** structuredContent of a CallToolResult, or the JSON in its first text block. */
  view.data = function (result) {
    if (!result) return null;
    if (result.structuredContent) return result.structuredContent;
    var block = (result.content || []).find(function (c) { return c.type === "text"; });
    if (!block) return null;
    try { return JSON.parse(block.text); } catch (e) { return { text: block.text }; }
  };
  view.errorText = function (result) {
    if (!result || !result.isError) return null;
    var block = (result.content || []).find(function (c) { return c.type === "text"; });
    return block ? block.text : "The tool returned an error.";
  };

  /* ---- small DOM helpers (textContent only; never innerHTML with data) ---- */
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k.slice(0, 2) === "on" && typeof v === "function") el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? "" : String(v));
      });
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { append(el, c); }); return; }
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  function $(sel) { return document.querySelector(sel); }

  function status(text, kind) {
    var el = $("#status");
    if (!el) return;
    el.textContent = text || "";
    el.className = "status" + (kind ? " " + kind : "");
  }

  function fmtBytes(n) {
    if (n === null || n === undefined) return "";
    var u = ["B", "KB", "MB", "GB", "TB"], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : String(n)) + " " + u[i];
  }
  function fmtDate(value, opts) {
    if (!value) return "";
    var d = new Date(typeof value === "number" ? value * 1000 : value);
    if (isNaN(d)) return String(value);
    opts = Object.assign({}, opts || { dateStyle: "medium", timeStyle: "short" });
    if (!opts.timeZone && view.hostContext.timeZone) opts.timeZone = view.hostContext.timeZone;
    try { return d.toLocaleString(view.hostContext.locale || undefined, opts); }
    catch (e) { return d.toISOString(); }
  }

  /**
   * Run a tool, show progress and errors in #status, resolve with its data
   * (or null on error).
   */
  function run(name, args, busy) {
    if (busy !== null) status(busy || "Working…", "busy");
    return view.callTool(name, args).then(function (result) {
      var err = view.errorText(result);
      if (err) { status(err, "error"); return null; }
      if (busy !== null) status("");
      return view.data(result);
    }, function (e) {
      status((e && e.message) || "Request failed", "error");
      return null;
    });
  }

  /**
   * Start a view. options: {name, render(data, args), load(), refresh()}. refresh, when given,
   * runs when the agent changes this helper's data (several changes in a row run it once).
   * When the host opened the view for a tool call, the tool result is
   * rendered; otherwise (opened from the surface list) load() fetches data.
   */
  view.start = function (options) {
    var gotResult = false, refreshTimer = 0;
    if (options.refresh) {
      view.onChanged = function () {
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(function () { options.refresh(); }, 400);
      };
    }
    view.onToolResult = function (result) {
      gotResult = true;
      var err = view.errorText(result);
      if (err) { status(err, "error"); return; }
      status("");
      // A view without render (one that loads its own data) loads when opened by a tool too.
      if (options.render) options.render(view.data(result), view.toolInput || {});
      else if (options.load) options.load();
    };
    return view.connect({ name: options.name, version: "0.1.0" }).then(function () {
      // A tool-driven open gets tool-input then tool-result; give them a moment.
      var toolDriven = !!view.hostContext.toolInfo || view.toolInput !== null;
      setTimeout(function () {
        if (!gotResult && view.toolInput === null && options.load) options.load();
      }, toolDriven ? 1500 : 0);
    }, function (e) {
      status("Could not connect to the host: " + ((e && e.message) || e), "error");
    });
  };

  // Surfaces run in a sandbox without allow-forms, where the browser never fires "submit". Wire
  // a form's submit button and Enter in its single-line fields to the handler instead.
  view.onSubmit = function (form, handler) {
    var fire = function (e) { if (e) e.preventDefault(); handler(); };
    form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (b) {
      b.addEventListener("click", fire);
    });
    form.querySelectorAll("input").forEach(function (input) {
      if (input.type === "submit" || input.type === "button" || input.type === "checkbox") return;
      input.addEventListener("keydown", function (e) { if (e.key === "Enter") fire(e); });
    });
    form.addEventListener("submit", fire);
  };

  view.h = h;
  view.clear = clear;
  view.$ = $;
  view.status = status;
  view.run = run;
  view.fmtBytes = fmtBytes;
  view.fmtDate = fmtDate;
  window.FoyelleView = view;
})();
