import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

// Run and Listen are heavy; stand in for them with pages that just draw the switch
// slot Player hands down, so what's under test is Player's own behaviour.
const h = vi.hoisted(() => ({
  role: "admin" as string | null,
  lock: null as null | { target: number },
  setLock: vi.fn(),
  navigated: [] as string[],
}));

vi.mock("react-router-dom", () => ({ useNavigate: () => (to: string) => { h.navigated.push(to); } }));
vi.mock("../lib/auth", () => ({ useAuth: () => ({ role: h.role }) }));
vi.mock("../lib/player", () => ({ usePlayer: () => ({ tempoLock: h.lock, setTempoLock: h.setLock }) }));
vi.mock("./Run", () => ({
  default: ({ modeSwitch }: { modeSwitch?: (fill: boolean) => React.ReactNode }) =>
    <div data-testid="run-page">{modeSwitch?.(false)}<div data-testid="run-mobile">{modeSwitch?.(true)}</div></div>,
}));
vi.mock("./Listen", () => ({
  default: ({ modeSwitch }: { modeSwitch?: (fill: boolean) => React.ReactNode }) =>
    <div data-testid="listen-page">{modeSwitch?.(false)}</div>,
}));

import Player from "./Player";

const tab = (name: string) => screen.getAllByRole("tab", { name })[0];

beforeEach(() => {
  cleanup();
  h.role = "admin";
  h.lock = null;
  h.setLock.mockClear();
  h.navigated = [];
});

describe("Player — which page", () => {
  it("mounts Run for the run route and Listen for the listen route", () => {
    const { unmount } = render(<Player mode="run" />);
    expect(screen.getByTestId("run-page")).toBeTruthy();
    expect(screen.queryByTestId("listen-page")).toBeNull();
    unmount();
    render(<Player mode="listen" />);
    expect(screen.getByTestId("listen-page")).toBeTruthy();
  });

  it("marks the current mode on the switch", () => {
    render(<Player mode="listen" />);
    expect(tab("Listen").getAttribute("aria-selected")).toBe("true");
    expect(tab("Run").getAttribute("aria-selected")).toBe("false");
  });

  it("hands the page an inline switch for the header and a full-width one for a phone", () => {
    render(<Player mode="run" />);
    const [inline, full] = screen.getAllByTestId("mode-switch");
    expect(inline.style.width).toBe("");
    expect(full.style.width).toBe("100%");
  });
});

describe("Player — Run to Listen", () => {
  it("releases the tempo lock, keeps the queue, and moves to Listen", () => {
    h.lock = { target: 155 };
    render(<Player mode="run" />);
    fireEvent.click(tab("Listen"));
    expect(h.setLock).toHaveBeenCalledWith(null);
    expect(h.navigated).toEqual(["/listen"]);
  });

  it("has no lock to release when there isn't one", () => {
    render(<Player mode="run" />);
    fireEvent.click(tab("Listen"));
    expect(h.setLock).not.toHaveBeenCalled();
    expect(h.navigated).toEqual(["/listen"]);
  });
});

describe("Player — Listen to Run", () => {
  it("just moves to Run and leaves the lock and queue alone", () => {
    h.lock = { target: 155 };
    render(<Player mode="listen" />);
    fireEvent.click(tab("Run"));
    expect(h.navigated).toEqual(["/run"]);
    expect(h.setLock).not.toHaveBeenCalled();
  });
});

describe("Player — the current mode", () => {
  it("does nothing when you tap the mode you are already in", () => {
    h.lock = { target: 155 };
    render(<Player mode="run" />);
    fireEvent.click(tab("Run"));
    expect(h.navigated).toEqual([]);
    expect(h.setLock).not.toHaveBeenCalled();
  });
});

describe("Player — the kiosk", () => {
  it("has no switch of its own: its nav tabs already switch modes", () => {
    h.role = "player";
    render(<Player mode="run" />);
    expect(screen.getByTestId("run-page")).toBeTruthy();
    expect(screen.queryByRole("tablist", { name: "Player mode" })).toBeNull();
  });
});
