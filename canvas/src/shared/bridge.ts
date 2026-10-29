import type { SurfaceCsp } from "./csp";

/** What `window.foyelle` offers the renderer (preload in Electron, dev shim on the web). */
export interface HarnessConfig {
  url: string;
  token: string;
  /** Set when the token file could not be read; `token` is then empty. */
  error?: string;
  /** Display names per MCP server, for surfaces that declare no title of their own. */
  surfaceTitles?: Record<string, string>;
  /** Services that need an account (through a bridge): server name -> name shown. */
  services?: Record<string, string>;
  /** A speech service runs on this computer, so the microphone button is offered. */
  voice?: boolean;
  /** A sign-in browser is installed, so "Sign in with Google" is offered. */
  signin?: boolean;
}

export interface SignInResult {
  code?: string;
  state?: string;
  error?: string;
}

export interface Transcript {
  text: string;
}

export interface RegisterSurfaceSpec {
  surfaceId: string;
  html: string;
  csp?: SurfaceCsp | null;
}

export type ShortcutName =
  | "command-bar"
  | "tile-1"
  | "tile-2"
  | "tile-3"
  | "tile-4"
  | "tile-5"
  | "tile-6"
  | "tile-7"
  | "tile-8"
  | "tile-9";

export interface FoyelleBridge {
  platform: "electron" | "web";
  /** Harness URL and token; the token file is re-read on every call. */
  harness(): Promise<HarnessConfig>;
  /** Stores surface HTML in the host and returns the URL the iframe loads. */
  registerSurface(spec: RegisterSurfaceSpec): Promise<string>;
  unregisterSurface(surfaceId: string): Promise<void>;
  /** Global shortcuts caught by the host even while a surface iframe has focus. */
  onShortcut(cb: (name: ShortcutName) => void): () => void;
  /** Writes out recorded speech (16 kHz mono WAV) with the speech service on this computer. */
  transcribe(wav: ArrayBuffer): Promise<Transcript>;
  /** Text size: zooms the whole canvas, cards included (0.75 to 2). */
  setZoom(factor: number): void;
  /** Sign in with Google in the sign-in browser: begin (the loopback address), open, end. */
  signinBegin(): Promise<{ redirectUri: string }>;
  signinOpen(authUrl: string): Promise<SignInResult>;
  signinEnd(): Promise<void>;
}

export const IPC = {
  harness: "harness:config",
  registerSurface: "surface:register",
  unregisterSurface: "surface:unregister",
  shortcut: "canvas:shortcut",
  transcribe: "voice:transcribe",
  zoom: "canvas:zoom",
  signinBegin: "signin:begin",
  signinOpen: "signin:open",
  signinEnd: "signin:end",
} as const;

export const SURFACE_SCHEME = "foyelle-surface";
export const APP_SCHEME = "foyelle-app";
export const APP_HOST = "canvas";
