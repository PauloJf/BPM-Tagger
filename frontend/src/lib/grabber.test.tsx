import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { GrabberRequestControl, type GrabberRequestState, type GrabberStatus } from "./grabber";

afterEach(cleanup);

const on: GrabberStatus = { configured: true, public_url: "https://grab.example", can_request: true };
const st = (over: Partial<GrabberRequestState>): GrabberRequestState => ({
  source_ref: "pt:1", request_id: 1, status: "pending", needs_choice: false, path: null, error: null, active: true, ...over,
});

describe("GrabberRequestControl", () => {
  it("offers Request when nothing was asked yet", () => {
    const onRequest = vi.fn();
    render(<GrabberRequestControl status={on} onRequest={onRequest} label="Song" />);
    fireEvent.click(screen.getByRole("button", { name: "Request Song from Music Grabber" }));
    expect(onRequest).toHaveBeenCalledOnce();
  });

  it("shows the state instead of a button while in flight", () => {
    render(<GrabberRequestControl status={on} state={st({ status: "downloading" })} onRequest={() => {}} />);
    expect(screen.getByText(/downloading/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("links a request that needs a choice to Music Grabber's Inbox", () => {
    render(<GrabberRequestControl status={on} state={st({ status: "awaiting_user", needs_choice: true })} onRequest={() => {}} />);
    expect(screen.getByRole("link").getAttribute("href")).toBe("https://grab.example/inbox");
  });

  it("lets a failed request be retried", () => {
    render(<GrabberRequestControl status={on} state={st({ status: "failed", active: false, error: "no match" })} onRequest={() => {}} />);
    expect(screen.getByText(/request failed/)).toBeTruthy();
    expect(screen.getByRole("button").textContent).toBe("Retry");
  });

  it("renders nothing for an account that may not request and has no state to show", () => {
    const { container } = render(
      <GrabberRequestControl status={{ ...on, can_request: false }} onRequest={() => {}} />);
    expect(container.innerHTML).toBe("");
  });
});
