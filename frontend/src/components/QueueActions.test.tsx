import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { QueueActions } from "./QueueActions";

const h = vi.hoisted(() => ({
  played: [] as Array<{ tracks: unknown[]; shuffle?: boolean }>,
  enqueued: [] as unknown[][],
}));

vi.mock("../lib/player", () => ({
  usePlayer: () => ({
    playQueue: (tracks: unknown[], _i: number, opts?: { shuffle?: boolean }) =>
      h.played.push({ tracks, shuffle: opts?.shuffle }),
    enqueueMany: (tracks: unknown[]) => h.enqueued.push(tracks),
  }),
}));

const btn = (name: RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

beforeEach(() => {
  cleanup();
  h.played = [];
  h.enqueued = [];
});

describe("QueueActions — preloaded tracks", () => {
  const tracks = [{ path: "/m/a.mp3", title: "A" }, { path: "/m/b.mp3", title: "B" }];

  it("Play replaces the queue in order", () => {
    render(<QueueActions tracks={tracks} />);
    fireEvent.click(btn(/^Play/));
    expect(h.played).toEqual([{ tracks, shuffle: false }]);
  });

  it("Shuffle replaces the queue with shuffle:true", () => {
    render(<QueueActions tracks={tracks} />);
    fireEvent.click(btn(/^Shuffle/));
    expect(h.played).toEqual([{ tracks, shuffle: true }]);
  });

  it("Add to queue appends the whole batch via enqueueMany, not Play", () => {
    render(<QueueActions tracks={tracks} />);
    fireEvent.click(btn(/Add to queue/));
    expect(h.enqueued).toEqual([tracks]);
    expect(h.played).toEqual([]);
  });

  it("disables all three buttons when there are no tracks", () => {
    render(<QueueActions tracks={[]} disabledTitle="Nothing here" />);
    for (const name of [/^Play/, /^Shuffle/, /Add to queue/]) {
      expect(btn(name).disabled).toBe(true);
      expect(btn(name).title).toBe("Nothing here");
    }
  });

  it("appends the label to Play/Shuffle but not Add to queue", () => {
    render(<QueueActions tracks={tracks} label=" (2)" />);
    expect(screen.getByRole("button", { name: "Play (2)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Shuffle (2)" })).toBeTruthy();
  });
});

describe("QueueActions — lazy getTracks", () => {
  it("fetches the full set at click time and acts on the result", async () => {
    const getTracks = vi.fn().mockResolvedValue([{ path: "/m/z.mp3", title: "Z" }]);
    render(<QueueActions getTracks={getTracks} />);
    fireEvent.click(btn(/^Play/));
    await waitFor(() => expect(h.played).toHaveLength(1));
    expect(h.played[0].tracks).toEqual([{ path: "/m/z.mp3", title: "Z" }]);
  });

  it("disables buttons while the fetch is in flight", async () => {
    let resolve!: (v: unknown[]) => void;
    const getTracks = vi.fn(() => new Promise<unknown[]>((r) => { resolve = r; }));
    render(<QueueActions getTracks={getTracks} />);
    fireEvent.click(btn(/^Play/));
    expect(btn(/^Play/).disabled).toBe(true);
    resolve([{ path: "/m/z.mp3", title: "Z" }]);
    await waitFor(() => expect(btn(/^Play/).disabled).toBe(false));
  });

  it("honors an explicit `empty` flag independent of getTracks", () => {
    const getTracks = vi.fn();
    render(<QueueActions getTracks={getTracks} empty disabledTitle="No tracks in this view" />);
    expect(btn(/^Play/).disabled).toBe(true);
    fireEvent.click(btn(/^Play/));
    expect(getTracks).not.toHaveBeenCalled();
  });
});
