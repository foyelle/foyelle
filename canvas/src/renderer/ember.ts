/**
 * The ember is Foyelle's presence: it breathes when idle, brightens while listening, flickers
 * while thinking, glows steadily while it waits for you, and settles when it is done.
 * Every ember on the page (home, title, status strip) takes its mood from `data-ember` on <html>.
 */

export type EmberMood = "asleep" | "idle" | "listening" | "thinking" | "speaking" | "waiting" | "settling";

export interface EmberInputs {
  connected: boolean;
  /** The microphone is open. */
  recording: boolean;
  /** The user is writing to Foyelle (the message box has text and focus). */
  typing: boolean;
  /** Approvals or questions waiting for the user. */
  waiting: number;
  /** A turn is running. */
  busy: boolean;
  /** Foyelle's words are arriving. */
  streaming: boolean;
}

export function moodFor(s: EmberInputs): Exclude<EmberMood, "settling"> {
  if (!s.connected) return "asleep";
  if (s.recording) return "listening";
  if (s.waiting > 0) return "waiting";
  if (s.streaming) return "speaking";
  if (s.busy) return "thinking";
  if (s.typing) return "listening";
  return "idle";
}

/** How long the ember takes to settle after a turn. */
export const SETTLE_MS = 1400;

export class Ember {
  private mood: EmberMood = "asleep";
  private settleTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly root: HTMLElement = document.documentElement) {
    root.dataset.ember = this.mood;
  }

  get current(): EmberMood {
    return this.mood;
  }

  set(next: Exclude<EmberMood, "settling">): void {
    if (this.mood === "settling" && next === "idle") return; // let it finish settling
    const worked = this.mood === "thinking" || this.mood === "speaking";
    if (this.settleTimer) {
      clearTimeout(this.settleTimer);
      this.settleTimer = null;
    }
    if (worked && next === "idle") {
      this.show("settling");
      this.settleTimer = setTimeout(() => {
        this.settleTimer = null;
        this.show("idle");
      }, SETTLE_MS);
      return;
    }
    this.show(next);
  }

  private show(mood: EmberMood): void {
    this.mood = mood;
    this.root.dataset.ember = mood;
  }
}
