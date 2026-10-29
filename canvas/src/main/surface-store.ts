import { SURFACE_KEY_RE, surfaceHeaders, type SurfaceCsp } from "../shared/csp";

export const MAX_SURFACE_HTML_BYTES = 8 * 1024 * 1024;
export const MAX_SURFACES = 64;

interface StoredSurface {
  html: string;
  csp: SurfaceCsp | null;
}

/**
 * Surface documents the renderer registered, served by the
 * `foyelle-surface://<surfaceId>/` protocol. Kept separate from Electron so
 * it can be unit-tested.
 */
export class SurfaceStore {
  private readonly surfaces = new Map<string, StoredSurface>();

  register(spec: unknown): string {
    if (!spec || typeof spec !== "object") throw new Error("invalid surface spec");
    const { surfaceId, html, csp } = spec as { surfaceId?: unknown; html?: unknown; csp?: unknown };
    if (typeof surfaceId !== "string" || !SURFACE_KEY_RE.test(surfaceId)) {
      throw new Error("invalid surfaceId");
    }
    if (typeof html !== "string") throw new Error("invalid html");
    if (Buffer.byteLength(html, "utf8") > MAX_SURFACE_HTML_BYTES) throw new Error("surface html too large");
    if (!this.surfaces.has(surfaceId) && this.surfaces.size >= MAX_SURFACES) {
      throw new Error("too many surfaces");
    }
    const cleanCsp = csp && typeof csp === "object" ? (csp as SurfaceCsp) : null;
    this.surfaces.set(surfaceId, { html, csp: cleanCsp });
    return `foyelle-surface://${surfaceId}/`;
  }

  unregister(surfaceId: unknown): void {
    if (typeof surfaceId === "string") this.surfaces.delete(surfaceId);
  }

  clear(): void {
    this.surfaces.clear();
  }

  get size(): number {
    return this.surfaces.size;
  }

  /** Resolves a request URL to a response description. Only `/` of a registered surface is served. */
  resolve(url: string): { status: number; body: string; headers: Record<string, string> } {
    let u: URL;
    try {
      u = new URL(url);
    } catch {
      return notFound();
    }
    if (u.protocol !== "foyelle-surface:") return notFound();
    const entry = this.surfaces.get(u.hostname);
    if (!entry || (u.pathname !== "/" && u.pathname !== "")) return notFound();
    return { status: 200, body: entry.html, headers: surfaceHeaders(entry.csp) };
  }
}

function notFound() {
  return {
    status: 404,
    body: "not found",
    headers: { "Content-Type": "text/plain; charset=utf-8", "X-Content-Type-Options": "nosniff" },
  };
}
