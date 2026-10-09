import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import type { SubsonicClient } from "./SubsonicClients";

const h = vi.hoisted(() => ({ clients: [] as unknown[], now: 10_000 }));

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: { now: h.now, clients: h.clients }, isLoading: false }),
}));
vi.mock("../lib/api", () => ({ api: { get: vi.fn() } }));

import SubsonicClients from "./SubsonicClients";

const base = (over: Partial<SubsonicClient>): SubsonicClient => ({
  owner: "admin", username: "admin", app: "Symfonium", version: "1.16.1", ip: "10.0.0.2",
  user_agent: "UA", first_seen: 9_000, last_seen: 9_990, requests: 12,
  playing: null, last_played: null, ...over,
});

beforeEach(() => { h.clients = []; });
afterEach(() => cleanup());

describe("SubsonicClients", () => {
  it("says so when nothing has connected", () => {
    render(<SubsonicClients />);
    expect(screen.getByText("No app has connected in the last hour.")).toBeTruthy();
  });

  it("shows the app, account and what it's playing", () => {
    h.clients = [base({ playing: { track_id: 1, title: "Stride", artist: "Night Runners", album: "",
      duration_s: 200, elapsed_s: 65, source: "report" } })];
    render(<SubsonicClients />);
    const row = screen.getByTestId("subsonic-client");
    expect(row.textContent).toContain("Symfonium");
    expect(row.textContent).toContain("active now");
    expect(row.textContent).toContain("Stride");
    expect(row.textContent).toContain("1:05 / 3:20");
    expect(row.textContent).not.toContain("from its last stream");
  });

  it("flags plays inferred from streams", () => {
    h.clients = [base({ app: "DSub", playing: { track_id: 1, title: "Kick", artist: "", album: "",
      duration_s: null, elapsed_s: 10, source: "stream" } })];
    render(<SubsonicClients />);
    expect(screen.getByTestId("subsonic-client").textContent).toContain("from its last stream");
  });

  it("falls back to the last played track, then to browsing", () => {
    h.clients = [
      base({ app: "Feishin", ip: "a", last_seen: 9_000,
             last_played: { track_id: 1, title: "Hills", artist: "Ann", ago_s: 600 } }),
      base({ app: "Other", ip: "b" }),
    ];
    render(<SubsonicClients />);
    const [a, b] = screen.getAllByTestId("subsonic-client");
    expect(a.textContent).toContain("Last played Hills — Ann · 10 min ago");
    expect(a.textContent).toContain("seen 17 min ago");
    expect(b.textContent).toContain("Browsing");
  });
});
