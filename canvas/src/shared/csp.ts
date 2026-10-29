/**
 * Content-Security-Policy for one surface, built from the `csp` object the
 * harness sends with `surface.open` (MCP Apps `McpUiResourceCsp`).
 *
 * The default (no domains) is exactly the contract's policy:
 *   default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:
 * followed by `base-uri 'none'; form-action 'none'`, which `default-src`
 * does not cover.
 *
 * Domains are accepted only as https origins (optionally with a leading
 * `*.` wildcard label); anything else is dropped, so a hostile `csp` object
 * cannot inject directives or widen the policy to other schemes.
 */

export interface SurfaceCsp {
  connectDomains?: string[];
  resourceDomains?: string[];
  [key: string]: unknown;
}

const ORIGIN_RE = /^https:\/\/(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*(:\d{1,5})?$/;

/** Normalise one declared domain to an https origin, or null if unacceptable. */
export function normalizeHttpsOrigin(value: unknown): string | null {
  if (typeof value !== "string") return null;
  let v = value.trim().toLowerCase();
  if (v.endsWith("/")) v = v.slice(0, -1);
  if (!ORIGIN_RE.test(v)) return null;
  const port = /:(\d+)$/.exec(v);
  if (port && (Number(port[1]) < 1 || Number(port[1]) > 65535)) return null;
  return v;
}

function origins(list: unknown): string[] {
  if (!Array.isArray(list)) return [];
  const out: string[] = [];
  for (const item of list) {
    const o = normalizeHttpsOrigin(item);
    if (o && !out.includes(o)) out.push(o);
  }
  return out;
}

export const DEFAULT_SURFACE_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:";

export function buildSurfaceCsp(csp?: SurfaceCsp | null): string {
  const connect = origins(csp?.connectDomains);
  const resource = origins(csp?.resourceDomains);
  const withRes = (base: string) => [base, ...resource].join(" ");

  const directives = [
    "default-src 'none'",
    withRes("script-src 'unsafe-inline'"),
    withRes("style-src 'unsafe-inline'"),
    withRes("img-src data:"),
  ];
  if (resource.length) {
    directives.push(`font-src ${resource.join(" ")}`);
    directives.push(`media-src ${resource.join(" ")}`);
  }
  if (connect.length) directives.push(`connect-src ${connect.join(" ")}`);
  directives.push("base-uri 'none'", "form-action 'none'");
  return directives.join("; ");
}

/** Headers served with every surface document. */
export function surfaceHeaders(csp?: SurfaceCsp | null): Record<string, string> {
  return {
    "Content-Type": "text/html; charset=utf-8",
    "Content-Security-Policy": buildSurfaceCsp(csp),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy":
      "camera=(), microphone=(), geolocation=(), usb=(), serial=(), hid=(), bluetooth=(), payment=(), display-capture=(), clipboard-read=()",
  };
}

/** Policy for the canvas renderer itself. */
export function buildCanvasCsp(opts: { connect: string[]; frame: string[] }): string {
  return [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src ${opts.connect.join(" ") || "'none'"}`,
    `frame-src ${opts.frame.join(" ") || "'none'"}`,
    "base-uri 'none'",
    "form-action 'none'",
    "object-src 'none'",
  ].join("; ");
}

/** Surface host keys become the authority of `foyelle-surface://<key>/`. */
export const SURFACE_KEY_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;
