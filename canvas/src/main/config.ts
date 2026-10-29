import { readFile } from "node:fs/promises";
import type { HarnessConfig } from "../shared/bridge";
import { GOOGLE_AUTH } from "./signin";

export const DEFAULT_HARNESS_URL = "ws://127.0.0.1:9119/api/ws";
export const DEFAULT_TOKEN_FILE = "/run/foyelle/harness-token";

export interface CanvasEnv {
  harnessUrl: string;
  tokenFile: string;
  dev: boolean;
  /** Dev only: open the developer tools at start (FOYELLE_CANVAS_DEVTOOLS=1). */
  devtools: boolean;
  surfaceTitles: Record<string, string>;
  /** The speech service on this computer, or null when there is none. */
  voiceUrl: string | null;
  /** The sign-in browser (Firefox) and its egress proxy, or null without Google sign-in. */
  signinBrowser: string | null;
  signinProxy: string | null;
  /** Development: answer sign-ins without a browser. */
  fakeSignin: boolean;
  /** The sign-in pages the sign-in browser may open (Google's, and each service's). */
  signinPages: string[];
  /** Services reached through a bridge that need an account: server name -> name shown. */
  services: Record<string, string>;
}

/** FOYELLE_VOICE_URL must be http on a loopback address: spoken words never leave the computer. */
export function parseVoiceUrl(raw: string | undefined): string | null {
  if (!raw) return null;
  try {
    const u = new URL(raw);
    const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(u.hostname);
    return u.protocol === "http:" && loopback ? u.toString() : null;
  } catch {
    return null;
  }
}

/** Notes some speech models print for silence and noise rather than words ("[BLANK_AUDIO]"). */
export function cleanTranscript(text: string): string {
  return text
    .replace(/\[[^\]]*\]|\([^)]*\)|\*[^*]*\*/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Sends 16 kHz WAV to the speech service (`/inference`, as whisper.cpp's server takes it) and returns the words. */
export async function transcribe(url: string, wav: Uint8Array, timeoutMs = 60_000): Promise<{ text: string }> {
  const form = new FormData();
  form.append("file", new Blob([new Uint8Array(wav)], { type: "audio/wav" }), "speech.wav");
  form.append("response_format", "json");
  form.append("temperature", "0");
  const res = await fetch(url, { method: "POST", body: form, signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) throw new Error(`the speech service answered ${res.status}`);
  const body = (await res.json()) as { text?: unknown };
  return { text: cleanTranscript(typeof body.text === "string" ? body.text : "") };
}

/** FOYELLE_SURFACE_TITLES: a JSON object of server name -> display name; anything else is ignored. */
export function parseSurfaceTitles(raw: string | undefined): Record<string, string> {
  try {
    const v: unknown = JSON.parse(raw ?? "{}");
    if (!v || typeof v !== "object" || Array.isArray(v)) return {};
    return Object.fromEntries(Object.entries(v as Record<string, unknown>).filter((e): e is [string, string] => typeof e[1] === "string"));
  } catch {
    return {};
  }
}

/** FOYELLE_SIGNIN_PAGES: a JSON list of https sign-in page addresses (no query); default Google's. */
export function parseSigninPages(raw: string | undefined, fallback: string): string[] {
  try {
    const v: unknown = JSON.parse(raw ?? "null");
    if (!Array.isArray(v)) return [fallback];
    return v.filter((p): p is string => {
      if (typeof p !== "string") return false;
      try {
        const u = new URL(p);
        return u.protocol === "https:" && !u.search && !u.hash && p === `${u.origin}${u.pathname}`;
      } catch {
        return false;
      }
    });
  } catch {
    return [fallback];
  }
}

/** FOYELLE_SERVICES: a JSON object of server name -> name shown, names in plain identifiers. */
export function parseServices(raw: string | undefined): Record<string, string> {
  return Object.fromEntries(Object.entries(parseSurfaceTitles(raw)).filter(([k]) => /^[a-z][a-z0-9_-]{0,31}$/.test(k)));
}

export function readEnv(env: NodeJS.ProcessEnv = process.env): CanvasEnv {
  return {
    harnessUrl: env.FOYELLE_HARNESS_URL || DEFAULT_HARNESS_URL,
    tokenFile: env.FOYELLE_HARNESS_TOKEN_FILE || DEFAULT_TOKEN_FILE,
    dev: env.FOYELLE_CANVAS_DEV === "1",
    devtools: env.FOYELLE_CANVAS_DEV === "1" && env.FOYELLE_CANVAS_DEVTOOLS === "1",
    surfaceTitles: parseSurfaceTitles(env.FOYELLE_SURFACE_TITLES),
    voiceUrl: parseVoiceUrl(env.FOYELLE_VOICE_URL),
    signinBrowser: env.FOYELLE_SIGNIN_BROWSER || null,
    signinProxy: /^127\.0\.1\.\d{1,3}:\d{2,5}$/.test(env.FOYELLE_SIGNIN_PROXY ?? "") ? env.FOYELLE_SIGNIN_PROXY! : null,
    fakeSignin: env.FOYELLE_CANVAS_DEV === "1" && env.FOYELLE_DEV_FAKE_SIGNIN === "1",
    signinPages: parseSigninPages(env.FOYELLE_SIGNIN_PAGES, GOOGLE_AUTH),
    services: parseServices(env.FOYELLE_SERVICES),
  };
}

/** Origin of the harness URL in the form CSP `connect-src` expects. */
export function harnessOrigin(url: string): string {
  const u = new URL(url);
  if (u.protocol !== "ws:" && u.protocol !== "wss:") {
    throw new Error(`FOYELLE_HARNESS_URL must be ws:// or wss://, got ${u.protocol}`);
  }
  return `${u.protocol}//${u.host}`;
}

/** Reads the token file fresh on every call so a rotated token is picked up on reconnect. */
export async function loadHarnessConfig(env: CanvasEnv): Promise<HarnessConfig> {
  try {
    const token = (await readFile(env.tokenFile, "utf8")).trim();
    return { url: env.harnessUrl, token, surfaceTitles: env.surfaceTitles, voice: !!env.voiceUrl, signin: !!env.signinBrowser || env.fakeSignin, services: env.services };
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code ?? "error";
    return {
      url: env.harnessUrl,
      token: "",
      error: `cannot read token file ${env.tokenFile} (${code})`,
      surfaceTitles: env.surfaceTitles,
      voice: !!env.voiceUrl,
      signin: !!env.signinBrowser || env.fakeSignin,
      services: env.services,
    };
  }
}
