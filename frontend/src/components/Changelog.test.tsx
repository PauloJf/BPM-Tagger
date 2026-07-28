import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ChangelogModal } from "./Changelog";

// A v2.11.0-shaped entry: a bold lead-in, `###` sub-headings, inline code,
// *italics*, a link, and an indented sub-bullet.
const MD = `# Changelog

## v2.11.0 — 2026-07-26

**Playlists grow up, and your library learns to answer "what can I run at 165?"**

### Cadence-ready views

- **New \`/cadence\` page** — uses the *exact* eligibility rule the run queue uses.
  - A nested detail line.
- See the [full changelog](https://example.com/CHANGELOG.md).

### Playlist artwork

- **Cover art in playlist rows.**
`;

const h = vi.hoisted(() => ({ md: "" }));

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: { changelog: h.md }, isLoading: false }),
}));
vi.mock("../lib/api", () => ({ api: { get: vi.fn() } }));

afterEach(cleanup);

/** The modal portals into document.body, so assert against the dialog element
 *  itself rather than RTL's (empty) detached container. */
function renderModal(): HTMLElement {
  render(<ChangelogModal since="2.10.0" onClose={() => {}} />);
  return screen.getByRole("dialog");
}

describe("ChangelogModal — markdown rendering", () => {
  it("renders `###` sub-headings as headings, never as literal text", () => {
    h.md = MD;
    const container = renderModal();

    expect(screen.getByText("Cadence-ready views")).toBeTruthy();
    expect(screen.getByText("Playlist artwork")).toBeTruthy();
    // The bug: the raw marker used to survive into the popup body.
    expect(container.textContent).not.toContain("###");
    expect(container.textContent).not.toContain("#");
  });

  it("renders the inline subset — bold, italic, code and links", () => {
    h.md = MD;
    const container = renderModal();

    expect(container.querySelector("strong")).toBeTruthy();
    expect(container.querySelector("em")?.textContent).toBe("exact");
    expect(container.querySelector("code")?.textContent).toBe("/cadence");
    expect(container.querySelector("a[href='https://example.com/CHANGELOG.md']")).toBeTruthy();
    // No stray emphasis markers left behind.
    expect(container.textContent).not.toContain("*");
  });

  it("nests an indented sub-bullet inside its parent item", () => {
    h.md = MD;
    const container = renderModal();

    const nested = container.querySelector("li ul li");
    expect(nested?.textContent).toBe("A nested detail line.");
  });
});
