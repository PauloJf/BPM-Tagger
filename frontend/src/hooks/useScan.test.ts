import { describe, it, expect } from "vitest";
import { deriveState } from "./useScan";
import type { Progress } from "../lib/types";

const base: Progress = {
  is_scanning: false, is_paused: false, is_stopping: false, completed: 0, total: 0,
  cumulative_completed: 0, current_file: "", current_step: "", step_index: 0,
  last_file: "", last_bpm: null,
};

describe("deriveState", () => {
  it("is idle with nothing running", () => {
    expect(deriveState(base)).toBe("idle");
  });

  it("shows watch-mode analysis as its own read-only state, not Idle", () => {
    expect(deriveState({ ...base, watching: true, batch_total: 2, batch_done: 1 })).toBe("watching");
  });

  it("a real scan wins over the watcher, and keeps its pause/stop states", () => {
    expect(deriveState({ ...base, is_scanning: true, watching: true })).toBe("analysing");
    expect(deriveState({ ...base, is_scanning: true, is_paused: true })).toBe("paused");
    expect(deriveState({ ...base, is_scanning: true, is_stopping: true })).toBe("stopping");
  });
});
