// Serves the built site (out/site, from scripts/deploy-site --dry-run) as Cloudflare's static assets
// do, for trying the demo and for its test: the headers from the site's _headers file (rules merged
// in order, a repeated header joined with a comma, "! Name" removes one), "/x" served from x.html,
// a folder from its index.html after a redirect to "/x/", and 404.html for anything else.
//
//   node demo/serve.mjs [dir] [port]     defaults: ../out/site and 5180; open http://localhost:5180/demo/
import { readFile, stat } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
  ".ttf": "font/ttf", ".mp4": "video/mp4", ".txt": "text/plain; charset=utf-8",
};

/** The rules of a _headers file: [{ pattern: RegExp, set: [[name, value]], remove: [name] }]. */
export function parseHeaders(text) {
  const rules = [];
  for (const line of text.split("\n")) {
    if (!line.trim() || line.trim().startsWith("#")) continue;
    if (!/^\s/.test(line)) {
      const source = line.trim().replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
      rules.push({ pattern: new RegExp(`^${source}$`), set: [], remove: [] });
    } else if (line.trim().startsWith("!")) {
      rules.at(-1).remove.push(line.trim().slice(1).trim().toLowerCase());
    } else {
      const at = line.indexOf(":");
      rules.at(-1).set.push([line.slice(0, at).trim(), line.slice(at + 1).trim()]);
    }
  }
  return rules;
}

export function headersFor(rules, pathname) {
  const out = new Map();
  for (const rule of rules.filter((r) => r.pattern.test(pathname))) {
    for (const [name, value] of rule.set) {
      const had = out.get(name.toLowerCase());
      out.set(name.toLowerCase(), had ? [had[0], `${had[1]}, ${value}`] : [name, value]);
    }
    for (const name of rule.remove) out.delete(name);
  }
  return Object.fromEntries([...out.values()]);
}

const isFile = (p) => stat(p).then((s) => s.isFile(), () => false);

export async function serve(dir, port, host = "127.0.0.1") {
  const rules = parseHeaders(await readFile(path.join(dir, "_headers"), "utf8").catch(() => ""));
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    const pathname = decodeURIComponent(url.pathname);
    const file = path.join(dir, pathname);
    if (!file.startsWith(dir)) return res.writeHead(400).end();
    let found = null;
    if (pathname.endsWith("/") && (await isFile(path.join(file, "index.html")))) found = path.join(file, "index.html");
    else if (!pathname.endsWith("/") && (await isFile(path.join(file, "index.html")))) {
      return res.writeHead(307, { Location: `${pathname}/${url.search}` }).end();
    } else if (pathname.endsWith(".html")) return res.writeHead(307, { Location: pathname.slice(0, -5) + url.search }).end();
    else if (await isFile(file)) found = file;
    else if (await isFile(`${file}.html`)) found = `${file}.html`;
    const status = found ? 200 : 404;
    found ??= path.join(dir, "404.html");
    const body = await readFile(found).catch(() => Buffer.from("not found"));
    res.writeHead(status, { "Content-Type": TYPES[path.extname(found)] ?? "application/octet-stream", ...headersFor(rules, pathname) });
    res.end(req.method === "HEAD" ? undefined : body);
  });
  await new Promise((resolve) => server.listen(port, host, resolve));
  return server;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const dir = path.resolve(process.argv[2] ?? path.join(here, "..", "..", "out", "site"));
  const port = Number(process.argv[3] ?? 5180);
  await serve(dir, port);
  console.log(`serving ${dir} at http://localhost:${port}/ (the demo: http://localhost:${port}/demo/)`);
}
