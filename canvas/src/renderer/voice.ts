/**
 * Talking to Foyelle: the microphone records here, the words are written out by the speech
 * service on this computer, and nothing is sent online.
 */

/** Speech models listen at 16 kHz, one channel. */
export const VOICE_RATE = 16_000;
/** A spoken request longer than this is cut off. */
export const MAX_SECONDS = 60;

export class VoiceRecorder {
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private limit: ReturnType<typeof setTimeout> | null = null;

  get recording(): boolean {
    return this.recorder?.state === "recording";
  }

  /** Opens the microphone. `onLimit` runs when the time limit stops the recording. */
  async start(onLimit: () => void): Promise<void> {
    if (this.recording) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
    this.chunks = [];
    this.recorder = new MediaRecorder(this.stream);
    this.recorder.ondataavailable = (e) => {
      if (e.data.size) this.chunks.push(e.data);
    };
    this.recorder.start(250);
    this.limit = setTimeout(onLimit, MAX_SECONDS * 1000);
  }

  /** Stops and returns what was said, as 16 kHz mono WAV (null when nothing was recorded). */
  async stop(): Promise<ArrayBuffer | null> {
    const rec = this.recorder;
    if (!rec) return null;
    const done = new Promise<void>((resolve) => rec.addEventListener("stop", () => resolve(), { once: true }));
    if (rec.state !== "inactive") rec.stop();
    await done;
    const type = rec.mimeType;
    this.release();
    const blob = new Blob(this.chunks, { type });
    this.chunks = [];
    if (!blob.size) return null;
    return toWav(await blob.arrayBuffer());
  }

  cancel(): void {
    if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    this.release();
    this.chunks = [];
  }

  private release(): void {
    if (this.limit) clearTimeout(this.limit);
    this.limit = null;
    for (const t of this.stream?.getTracks() ?? []) t.stop();
    this.stream = null;
    this.recorder = null;
  }
}

/** Decodes recorded audio and resamples it to 16 kHz mono WAV. */
async function toWav(encoded: ArrayBuffer): Promise<ArrayBuffer | null> {
  const ctx = new AudioContext();
  try {
    const audio = await ctx.decodeAudioData(encoded);
    const frames = Math.ceil(audio.duration * VOICE_RATE);
    if (frames < VOICE_RATE / 4) return null; // under a quarter of a second: a tap, not speech
    const offline = new OfflineAudioContext(1, frames, VOICE_RATE);
    const src = offline.createBufferSource();
    src.buffer = audio;
    src.connect(offline.destination);
    src.start();
    const mono = await offline.startRendering();
    return encodeWav(mono.getChannelData(0), VOICE_RATE);
  } finally {
    void ctx.close();
  }
}

/** 16-bit PCM WAV. */
export function encodeWav(samples: Float32Array, rate: number): ArrayBuffer {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const text = (at: number, s: string) => {
    for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i));
  };
  text(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  text(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]!));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}
