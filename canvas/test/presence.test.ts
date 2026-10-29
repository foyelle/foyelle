import { describe, expect, it, vi } from "vitest";
import { cleanTranscript, parseVoiceUrl } from "../src/main/config";
import { parseCommand } from "../src/renderer/commands";
import { Ember, moodFor, SETTLE_MS, type EmberInputs } from "../src/renderer/ember";
import { prefsFromSettings, schemeFor } from "../src/renderer/prefs";
import { encodeWav } from "../src/renderer/voice";

const calm: EmberInputs = { connected: true, recording: false, typing: false, waiting: 0, busy: false, streaming: false };

describe("the ember's mood", () => {
  it("follows what Foyelle is doing, most pressing first", () => {
    expect(moodFor({ ...calm, connected: false, recording: true })).toBe("asleep");
    expect(moodFor({ ...calm, recording: true, waiting: 1 })).toBe("listening");
    expect(moodFor({ ...calm, waiting: 1, busy: true })).toBe("waiting");
    expect(moodFor({ ...calm, busy: true, streaming: true })).toBe("speaking");
    expect(moodFor({ ...calm, busy: true })).toBe("thinking");
    expect(moodFor({ ...calm, typing: true })).toBe("listening");
    expect(moodFor(calm)).toBe("idle");
  });

  it("settles after a turn, then rests", () => {
    vi.useFakeTimers();
    const root = { dataset: {} as Record<string, string> } as unknown as HTMLElement;
    const ember = new Ember(root);
    ember.set("idle");
    expect(root.dataset.ember).toBe("idle");
    ember.set("thinking");
    ember.set("idle");
    expect(root.dataset.ember).toBe("settling");
    ember.set("idle"); // a re-render does not cut the settling short
    vi.advanceTimersByTime(SETTLE_MS);
    expect(root.dataset.ember).toBe("idle");
    ember.set("speaking");
    ember.set("idle");
    ember.set("thinking"); // a new turn interrupts it
    vi.advanceTimersByTime(SETTLE_MS);
    expect(root.dataset.ember).toBe("thinking");
    vi.useRealTimers();
  });
});

describe("prefs", () => {
  it("reads the canvas's part of the settings", () => {
    expect(prefsFromSettings({ effective: { ui: { theme: "time", text_scale: 1.25, welcomed: true }, user: { name: " Ada " } } })).toEqual({
      theme: "time", style: null, textScale: 1.25, name: "Ada", welcomed: true,
    });
    expect(prefsFromSettings({ effective: { ui: { theme: "neon", text_scale: 9 } } })).toEqual({ theme: "system", style: null, textScale: 1, name: null, welcomed: false });
    expect(prefsFromSettings(null).theme).toBe("system");
  });

  it("picks the colours from the look, the system and the time of day", () => {
    const at = (h: number) => new Date(2026, 9, 20, h, 30);
    expect(schemeFor("light", at(23), true)).toBe("light");
    expect(schemeFor("dark", at(12), false)).toBe("dark");
    expect(schemeFor("system", at(12), true)).toBe("dark");
    expect(schemeFor("time", at(6), false)).toBe("dark");
    expect(schemeFor("time", at(7), false)).toBe("light");
    expect(schemeFor("time", at(18), false)).toBe("light");
    expect(schemeFor("time", at(19), false)).toBe("dark");
  });

  it("has a /welcome command", () => {
    expect(parseCommand("/welcome")).toEqual({ kind: "welcome" });
    expect(parseCommand("/welcome again").kind).toBe("error");
  });
});

describe("voice", () => {
  it("talks only to a speech service on this computer", () => {
    expect(parseVoiceUrl("http://127.0.0.1:7310/inference")).toBe("http://127.0.0.1:7310/inference");
    expect(parseVoiceUrl("http://localhost:7310/inference")).toBe("http://localhost:7310/inference");
    expect(parseVoiceUrl("http://[::1]:7310/inference")).toBe("http://[::1]:7310/inference");
    expect(parseVoiceUrl("https://speech.example.org/inference")).toBeNull();
    expect(parseVoiceUrl("http://192.168.1.4:7310/inference")).toBeNull();
    expect(parseVoiceUrl("")).toBeNull();
    expect(parseVoiceUrl("not a url")).toBeNull();
  });

  it("drops whisper's notes about silence and noise", () => {
    expect(cleanTranscript(" [BLANK_AUDIO] ")).toBe("");
    expect(cleanTranscript("(wind blowing) What's in my documents? *cough*")).toBe("What's in my documents?");
  });

  it("writes 16-bit mono WAV", () => {
    const wav = new DataView(encodeWav(new Float32Array([0, 1, -1, 0.5]), 16000));
    const tag = (at: number) => String.fromCharCode(...[0, 1, 2, 3].map((i) => wav.getUint8(at + i)));
    expect([tag(0), tag(8), tag(12), tag(36)]).toEqual(["RIFF", "WAVE", "fmt ", "data"]);
    expect(wav.getUint32(24, true)).toBe(16000);
    expect(wav.getUint16(22, true)).toBe(1);
    expect(wav.getUint32(40, true)).toBe(8);
    expect([wav.getInt16(44, true), wav.getInt16(46, true), wav.getInt16(48, true)]).toEqual([0, 32767, -32768]);
  });
});
