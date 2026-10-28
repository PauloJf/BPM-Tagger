import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

vi.mock("../hooks/useAudioTime", () => ({ fmtTime: (s: number) => `t${Math.round(s)}` }));
vi.mock("./LyricsPanel", () => ({
  LyricsPanel: ({ path, onClose }: { path: string; onClose: () => void }) =>
    <button data-testid="lyrics" onClick={onClose}>{path}</button>,
}));

import { ctlButtonStyle, GlowLayer, LyricsDrawer, QueueCard, TransportButtons, WaveformStrip } from "./PlayerShell";

beforeEach(() => cleanup());

describe("ctlButtonStyle", () => {
  it("is a round button of the given diameter", () => {
    const s = ctlButtonStyle(54);
    expect([s.width, s.height, s.borderRadius]).toEqual([54, 54, 999]);
  });
});

describe("GlowLayer", () => {
  it("is portaled to <body>, behind the page, and takes its look from the glow", () => {
    const { container } = render(<GlowLayer glow={{ background: "red", opacity: 0.4 }} />);
    expect(container.querySelector("[aria-hidden]")).toBeNull(); // not inside the host
    const el = document.body.querySelector<HTMLElement>("div[aria-hidden='true']")!;
    expect(el.style.position).toBe("fixed");
    expect(el.style.zIndex).toBe("-1");
    expect(el.style.pointerEvents).toBe("none");
    expect(el.style.background).toBe("red");
    expect(el.style.opacity).toBe("0.4");
  });
});

describe("LyricsDrawer", () => {
  it("anchors the lyrics panel to the bottom of the viewport and closes it", () => {
    const onClose = vi.fn();
    render(<LyricsDrawer path="/m/a.mp3" audioRef={{ current: null }} onClose={onClose} />);
    const btn = screen.getByTestId("lyrics");
    expect(btn.textContent).toBe("/m/a.mp3");
    expect((btn.parentElement as HTMLElement).style.position).toBe("fixed");
    fireEvent.click(btn);
    expect(onClose).toHaveBeenCalled();
  });
});

describe("WaveformStrip", () => {
  it("shows elapsed and remaining time under the canvas", () => {
    const { container } = render(<WaveformStrip canvasRef={{ current: null }} time={79} dur={209} />);
    expect(container.querySelector("canvas")).toBeTruthy();
    expect(screen.getByText("t79")).toBeTruthy();
    expect(screen.getByText("-t130")).toBeTruthy();
  });

  it("never shows negative remaining time when the playhead is past the end", () => {
    render(<WaveformStrip canvasRef={{ current: null }} time={300} dur={209} />);
    expect(screen.getByText("-t0")).toBeTruthy();
  });
});

describe("TransportButtons", () => {
  const props = { onPrev: vi.fn(), onToggle: vi.fn(), onNext: vi.fn(), sideSize: 52, playSize: 72, iconSize: 26 };

  it("wires previous, play/pause and next", () => {
    render(<TransportButtons playing={false} {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(props.onPrev).toHaveBeenCalledTimes(1);
    expect(props.onToggle).toHaveBeenCalledTimes(1);
    expect(props.onNext).toHaveBeenCalledTimes(1);
  });

  it("reads Pause while playing", () => {
    render(<TransportButtons playing {...props} />);
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Play" })).toBeNull();
  });

  it("takes the sizes each page asks for", () => {
    render(<TransportButtons playing={false} {...props} sideSize={54} playSize={78} iconSize={30} />);
    const side = screen.getByRole("button", { name: "Previous" });
    const play = screen.getByRole("button", { name: "Play" });
    expect([side.style.width, play.style.width]).toEqual(["54px", "78px"]);
    expect(play.querySelector("svg")!.getAttribute("width")).toBe("30");
  });
});

describe("QueueCard", () => {
  const card = (column: boolean) => {
    render(
      <QueueCard column={column} bottomGap={10} header={<div>head</div>} between={<div>between</div>} listTestId="list">
        <div>rows</div>
      </QueueCard>,
    );
    const root = screen.getByText("head").parentElement as HTMLElement;
    return { root, list: screen.getByTestId("list") };
  };

  it("renders header, the rows between, then the list, in that order", () => {
    const { root } = card(false);
    expect([...root.children].map((c) => c.textContent)).toEqual(["head", "between", "rows"]);
  });

  it("fills its container in the desktop column, so the queue never sets the row height", () => {
    const { root, list } = card(true);
    expect(root.style.position).toBe("absolute");
    expect(parseFloat(root.style.inset)).toBe(0);
    expect(list.style.flexGrow).toBe("1");
    expect(list.style.overflowY).toBe("auto");
  });

  it("sizes to its content on the mobile Queue tab and shrinks to what is left", () => {
    const { root, list } = card(false);
    expect(root.style.position).toBe("");
    expect(root.style.flexShrink).toBe("1");
    expect(parseFloat(root.style.minHeight)).toBe(0);
    expect(root.style.marginBottom).toBe("10px");
    expect(list.style.overflowY).toBe("auto");
    expect(list.style.flex).toBe("");
  });

  it("hands the list ref to the page, for scrolling the playing row into view", () => {
    const ref = { current: null as HTMLDivElement | null };
    render(<QueueCard column bottomGap={0} header={null} listRef={ref}>x</QueueCard>);
    expect(ref.current).toBeInstanceOf(HTMLDivElement);
  });
});
