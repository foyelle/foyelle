import { describe, expect, it } from "vitest";
import { buildCanvasCsp, buildSurfaceCsp, DEFAULT_SURFACE_CSP, normalizeHttpsOrigin, surfaceHeaders } from "../src/shared/csp";
import { SurfaceStore } from "../src/main/surface-store";

describe("buildSurfaceCsp", () => {
  it("starts with exactly the contract's default policy", () => {
    expect(DEFAULT_SURFACE_CSP).toBe("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:");
    expect(buildSurfaceCsp()).toBe(`${DEFAULT_SURFACE_CSP}; base-uri 'none'; form-action 'none'`);
    expect(buildSurfaceCsp({ connectDomains: [], resourceDomains: [] })).toBe(buildSurfaceCsp(null));
  });

  it("adds https connect and resource origins", () => {
    const csp = buildSurfaceCsp({
      connectDomains: ["https://api.example.org"],
      resourceDomains: ["https://cdn.example.org/", "https://*.tiles.example.net:8443"],
    });
    expect(csp).toContain("connect-src https://api.example.org");
    expect(csp).toContain("script-src 'unsafe-inline' https://cdn.example.org https://*.tiles.example.net:8443");
    expect(csp).toContain("style-src 'unsafe-inline' https://cdn.example.org");
    expect(csp).toContain("img-src data: https://cdn.example.org");
    expect(csp).toContain("font-src https://cdn.example.org");
    expect(csp.startsWith("default-src 'none'")).toBe(true);
  });

  it("drops anything that is not a plain https origin", () => {
    const csp = buildSurfaceCsp({
      connectDomains: [
        "http://insecure.example.org",
        "wss://socket.example.org",
        "https://ok.example.org/path",
        "https://evil.example.org; script-src *",
        "'unsafe-eval'",
        "*",
        "https://*",
        "data:",
        "https://exa mple.org",
        42 as unknown as string,
      ],
      resourceDomains: "https://not-a-list.example.org" as unknown as string[],
    });
    expect(csp).toBe(`${DEFAULT_SURFACE_CSP}; base-uri 'none'; form-action 'none'`);
  });

  it("deduplicates and lower-cases origins", () => {
    expect(normalizeHttpsOrigin("HTTPS://API.Example.org/")).toBe("https://api.example.org");
    expect(normalizeHttpsOrigin("https://a.example.org:99999")).toBeNull();
    const csp = buildSurfaceCsp({ connectDomains: ["https://a.example.org", "https://A.example.org/"] });
    expect(csp).toContain("connect-src https://a.example.org;");
  });
});

describe("surfaceHeaders", () => {
  it("sends CSP and nosniff", () => {
    const h = surfaceHeaders(null);
    expect(h["Content-Security-Policy"]).toBe(buildSurfaceCsp(null));
    expect(h["X-Content-Type-Options"]).toBe("nosniff");
    expect(h["Content-Type"]).toBe("text/html; charset=utf-8");
  });
});

describe("buildCanvasCsp", () => {
  it("allows only self scripts, the harness socket and surface frames", () => {
    const csp = buildCanvasCsp({ connect: ["ws://127.0.0.1:9119"], frame: ["foyelle-surface:"] });
    expect(csp).toContain("script-src 'self'");
    expect(csp).toContain("connect-src ws://127.0.0.1:9119");
    expect(csp).toContain("frame-src foyelle-surface:");
    expect(csp).not.toContain("unsafe");
  });
});

describe("SurfaceStore", () => {
  it("serves only / of a registered surface, with its CSP", () => {
    const store = new SurfaceStore();
    const url = store.register({ surfaceId: "s1-abc", html: "<p>hi</p>", csp: { connectDomains: ["https://x.example.org"] } });
    expect(url).toBe("foyelle-surface://s1-abc/");
    const r = store.resolve("foyelle-surface://s1-abc/");
    expect(r.status).toBe(200);
    expect(r.body).toBe("<p>hi</p>");
    expect(r.headers["Content-Security-Policy"]).toContain("connect-src https://x.example.org");
    expect(store.resolve("foyelle-surface://s1-abc/other").status).toBe(404);
    expect(store.resolve("foyelle-surface://nope/").status).toBe(404);
    expect(store.resolve("https://s1-abc/").status).toBe(404);
    store.unregister("s1-abc");
    expect(store.resolve("foyelle-surface://s1-abc/").status).toBe(404);
  });

  it("rejects bad ids and payloads", () => {
    const store = new SurfaceStore();
    expect(() => store.register({ surfaceId: "Bad_ID", html: "" })).toThrow(/surfaceId/);
    expect(() => store.register({ surfaceId: "../x", html: "" })).toThrow(/surfaceId/);
    expect(() => store.register({ surfaceId: "ok", html: 5 })).toThrow(/html/);
    expect(() => store.register(null)).toThrow();
  });
});
