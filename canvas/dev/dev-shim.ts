/**
 * `window.foyelle` for `npm run dev:web`, standing in for the Electron
 * preload: the harness URL/token come from the dev server, and surfaces are
 * registered with it and served from /__surface/<id>/ with the same CSP
 * headers the Electron protocol uses. The iframe is still sandboxed without
 * allow-same-origin, so it has an opaque origin as in production.
 */
import type { FoyelleBridge, HarnessConfig, RegisterSurfaceSpec, Transcript } from "../src/shared/bridge";

const bridge: FoyelleBridge = {
  platform: "web",
  async harness(): Promise<HarnessConfig> {
    const res = await fetch("/__dev/harness", { cache: "no-store" });
    if (!res.ok) throw new Error(`dev server: ${res.status}`);
    return (await res.json()) as HarnessConfig;
  },
  async registerSurface(spec: RegisterSurfaceSpec): Promise<string> {
    const res = await fetch("/__surface/register", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(spec),
    });
    if (!res.ok) throw new Error(`could not register surface: ${await res.text()}`);
    return ((await res.json()) as { url: string }).url;
  },
  async unregisterSurface(surfaceId: string): Promise<void> {
    await fetch(`/__surface/${encodeURIComponent(surfaceId)}`, { method: "DELETE" }).catch(() => undefined);
  },
  onShortcut() {
    return () => undefined; // the page's own keydown handler covers the browser
  },
  async transcribe(wav: ArrayBuffer): Promise<Transcript> {
    const res = await fetch("/__dev/transcribe", { method: "POST", body: wav });
    if (!res.ok) throw new Error(`dev server: ${res.status}`);
    return (await res.json()) as Transcript;
  },
  setZoom() {
    // The browser's own zoom (Ctrl +) stands in; CSS zoom would break the stage layout.
  },
  async signinBegin() {
    throw new Error("signing in needs the Electron canvas");
  },
  async signinOpen() {
    throw new Error("signing in needs the Electron canvas");
  },
  async signinEnd() {},
};

(window as unknown as { foyelle: FoyelleBridge }).foyelle = bridge;
