/**
 * `npm run dev:web`: serves the canvas renderer to a normal browser at
 * http://localhost:5174 and runs a mock harness on ws://localhost:5174/api/ws
 * that speaks enough of the gateway dialect to demo the canvas.
 */
import { randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer } from "ws";
import { buildCanvasCsp, SURFACE_KEY_RE, surfaceHeaders, type SurfaceCsp } from "../src/shared/csp";
import { MockHarness, type ToolsHost } from "./mock-harness";

const here = path.dirname(fileURLToPath(import.meta.url));
const WEB_DIR = path.join(here, "web");
const SURFACE_DIR = path.join(here, "surfaces");
const PORT = Number(process.env.PORT ?? 5174);
const HOST = process.env.HOST ?? "127.0.0.1";
const TOKEN = process.env.FOYELLE_DEV_TOKEN ?? randomBytes(16).toString("hex");

const STATIC: Record<string, [string, string]> = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/index.html": ["index.html", "text/html; charset=utf-8"],
  "/app.js": ["app.js", "text/javascript; charset=utf-8"],
  "/app.css": ["app.css", "text/css; charset=utf-8"],
  "/fonts/Nunito.ttf": ["fonts/Nunito.ttf", "font/ttf"],
  "/fonts/Nunito-Italic.ttf": ["fonts/Nunito-Italic.ttf", "font/ttf"],
  "/fonts/GochiHand-Regular.ttf": ["fonts/GochiHand-Regular.ttf", "font/ttf"],
  "/fonts/JetBrainsMono.ttf": ["fonts/JetBrainsMono.ttf", "font/ttf"],
  "/fonts/JetBrainsMono-Italic.ttf": ["fonts/JetBrainsMono-Italic.ttf", "font/ttf"],
  "/fonts/DejaVuSansCondensed.ttf": ["fonts/DejaVuSansCondensed.ttf", "font/ttf"],
  "/fonts/DejaVuSansCondensed-Bold.ttf": ["fonts/DejaVuSansCondensed-Bold.ttf", "font/ttf"],
  "/dev-shim.js": ["dev-shim.js", "text/javascript; charset=utf-8"],
};

const surfaces = new Map<string, { html: string; csp: SurfaceCsp | null }>();

async function loadSurfaceHtml(name: string): Promise<string> {
  return readFile(path.join(SURFACE_DIR, `${name}.html`), "utf8");
}

// The real connectors on sample data (dev/tools_host.py), when FOYELLE_TOOLS_URL is set.
const TOOLS_URL = process.env.FOYELLE_TOOLS_URL;
async function toolsFetch(pathAndQuery: string, init?: RequestInit): Promise<any> {
  const res = await fetch(`${TOOLS_URL}${pathAndQuery}`, init);
  const data = await res.json();
  if (!res.ok) throw new Error(data?.error ?? `tools host: HTTP ${res.status}`);
  return data;
}
const tools: ToolsHost | null = TOOLS_URL
  ? {
      surfaces: async () => (await toolsFetch("/surfaces")).surfaces,
      resource: (server, uri) => toolsFetch(`/resource?server=${encodeURIComponent(server)}&uri=${encodeURIComponent(uri)}`),
      call: (server, name, args) =>
        toolsFetch("/call", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ server, name, arguments: args }) }),
    }
  : null;

const harness = new MockHarness({ loadSurfaceHtml, tools });

function readBody(req: http.IncomingMessage, limit = 10 * 1024 * 1024): Promise<string> {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => {
      size += c.length;
      if (size > limit) {
        reject(new Error("too large"));
        req.destroy();
      } else chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

const VOICE_SAMPLES = ["What's in my documents?", "How is my computer doing?", "What left my home today?"];
let voiceTurn = 0;

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
  const host = req.headers.host ?? `localhost:${PORT}`;
  try {
    if (req.method === "GET" && STATIC[url.pathname]) {
      const [file, type] = STATIC[url.pathname]!;
      const body = await readFile(path.join(WEB_DIR, file));
      res.writeHead(200, {
        "Content-Type": type,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": buildCanvasCsp({ connect: ["'self'", `ws://${host}`], frame: ["'self'"] }),
      });
      res.end(body);
      return;
    }
    if (req.method === "POST" && url.pathname === "/__dev/drive") {
      // Dev only (loopback): play a request into the open canvases, for design review.
      const n = harness.drive(await readBody(req));
      res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ canvases: n }));
      return;
    }
    if (req.method === "GET" && url.pathname === "/__dev/harness") {
      res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      res.end(JSON.stringify({ url: `ws://${host}/api/ws`, token: TOKEN, voice: true }));
      return;
    }
    if (req.method === "POST" && url.pathname === "/__dev/transcribe") {
      // Dev stand-in for the speech service: the recording is dropped and a sample request
      // comes back (FOYELLE_DEV_VOICE_TEXT, or the next of a few).
      req.resume();
      await new Promise((resolve) => req.on("end", resolve));
      const text = process.env.FOYELLE_DEV_VOICE_TEXT || VOICE_SAMPLES[voiceTurn++ % VOICE_SAMPLES.length];
      await new Promise((resolve) => setTimeout(resolve, 600)); // a little slower than the real service
      res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ text }));
      return;
    }
    if (req.method === "POST" && url.pathname === "/__surface/register") {
      const spec = JSON.parse(await readBody(req)) as { surfaceId?: string; html?: string; csp?: SurfaceCsp | null };
      if (typeof spec.surfaceId !== "string" || !SURFACE_KEY_RE.test(spec.surfaceId) || typeof spec.html !== "string") {
        res.writeHead(400).end("invalid surface");
        return;
      }
      surfaces.set(spec.surfaceId, { html: spec.html, csp: spec.csp ?? null });
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ url: `/__surface/${spec.surfaceId}/` }));
      return;
    }
    const m = /^\/__surface\/([a-z0-9-]+)\/?$/.exec(url.pathname);
    if (m) {
      const id = m[1]!;
      if (req.method === "DELETE") {
        surfaces.delete(id);
        res.writeHead(204).end();
        return;
      }
      const entry = surfaces.get(id);
      if (req.method === "GET" && entry && url.pathname.endsWith("/")) {
        res.writeHead(200, surfaceHeaders(entry.csp));
        res.end(entry.html);
        return;
      }
    }
    if (url.pathname === "/favicon.ico") {
      res.writeHead(204).end();
      return;
    }
    res.writeHead(404, { "Content-Type": "text/plain" }).end("not found");
  } catch (err) {
    res.writeHead(500, { "Content-Type": "text/plain" }).end(String(err));
  }
});

const wss = new WebSocketServer({ noServer: true });
server.on("upgrade", (req, socket, head) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  if (url.pathname !== "/api/ws" || url.searchParams.get("token") !== TOKEN) {
    socket.write("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
    socket.destroy();
    return;
  }
  wss.handleUpgrade(req, socket, head, (ws) => {
    harness.connect({
      send: (frame) => {
        if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(frame));
      },
      onMessage: (cb) => ws.on("message", (data) => cb(data.toString())),
      onClose: (cb) => ws.on("close", cb),
    });
  });
});

server.listen(PORT, HOST, () => {
  console.log(`\n  Foyelle canvas (web dev)  http://localhost:${PORT}\n  mock harness              ws://localhost:${PORT}/api/ws\n`);
  console.log("  Try: \"show my files\", \"system status\", \"send the report to Sam\", \"ask me a question\",");
  console.log("       \"I need a secret\", \"cancel test\", \"error\", or /surfaces, /help.\n");
});

const shutdown = () => {
  wss.close();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 500).unref();
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
