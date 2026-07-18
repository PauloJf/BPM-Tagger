import { useEffect, useMemo, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

// ── Parsing (pure, exported for tests) ──────────────────────────────────────
export interface ChangeSection { version: string; date: string; body: string }

/** Split CHANGELOG.md into per-version sections (newest first, as authored). */
export function parseChangelog(md: string): ChangeSection[] {
  const out: ChangeSection[] = [];
  let cur: ChangeSection | null = null;
  for (const line of (md || "").split("\n")) {
    const m = line.match(/^##\s+v?(\d+\.\d+\.\d+)\s*(?:—|-)?\s*(.*)$/);
    if (m) {
      if (cur) out.push(cur);
      cur = { version: m[1], date: m[2].trim(), body: "" };
    } else if (cur) {
      cur.body += line + "\n";
    }
  }
  if (cur) out.push(cur);
  return out;
}

/** Numeric semver-ish compare on "X.Y.Z". */
export function cmpVersion(a: string, b: string): number {
  const pa = a.split(".").map(Number);
  const pb = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) - (pb[i] || 0);
  }
  return 0;
}

/** Sections strictly newer than `since` (all of them when `since` is unset). */
export function sectionsSince(sections: ChangeSection[], since?: string): ChangeSection[] {
  if (!since) return sections;
  return sections.filter((s) => cmpVersion(s.version, since) > 0);
}

// ── Safe inline rendering (no dangerouslySetInnerHTML) ──────────────────────
const codeStyle: React.CSSProperties = {
  fontFamily: "var(--mono)", fontSize: "0.9em", padding: "1px 5px",
  borderRadius: 5, background: "var(--chip-bg)", border: "1px solid var(--border)",
};

/** Render the CHANGELOG's inline subset — **bold**, `code`, [text](url) — into
 *  React nodes. Links are restricted to http(s) so no `javascript:` sneaks in. */
function renderInline(text: string, key: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g;
  let last = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[2] !== undefined) nodes.push(<strong key={`${key}-${i}`}>{m[2]}</strong>);
    else if (m[3] !== undefined) nodes.push(<code key={`${key}-${i}`} style={codeStyle}>{m[3]}</code>);
    else if (m[4] !== undefined) {
      const safe = /^https?:\/\//i.test(m[5]) ? m[5] : "#";
      nodes.push(
        <a key={`${key}-${i}`} href={safe} target="_blank" rel="noopener noreferrer"
           style={{ color: "var(--accent-2)" }}>{m[4]}</a>,
      );
    }
    last = re.lastIndex;
    i++;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** Render a version section body: `- ` lines become a bulleted list; other
 *  non-empty lines become paragraphs (sub-headings in older entries). */
function renderBody(body: string, key: string): ReactNode {
  const out: ReactNode[] = [];
  let bullets: ReactNode[] = [];
  const flush = () => {
    if (bullets.length) {
      out.push(<ul key={`${key}-ul-${out.length}`} style={{ margin: "4px 0", paddingLeft: 18 }}>{bullets}</ul>);
      bullets = [];
    }
  };
  body.split("\n").forEach((line, idx) => {
    const t = line.trim();
    if (t.startsWith("- ")) {
      bullets.push(<li key={`${key}-li-${idx}`} style={{ marginBottom: 6, lineHeight: 1.5 }}>{renderInline(t.slice(2), `${key}-i${idx}`)}</li>);
    } else if (t) {
      flush();
      out.push(<p key={`${key}-p-${idx}`} style={{ margin: "8px 0", lineHeight: 1.5 }}>{renderInline(t, `${key}-p${idx}`)}</p>);
    }
  });
  flush();
  return out;
}

// ── Modal ───────────────────────────────────────────────────────────────────
export function ChangelogModal({ since, onClose }: { since?: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["changelog"],
    queryFn: () => api.get<{ changelog: string }>("/api/changelog"),
    staleTime: Infinity,
  });
  const sections = useMemo(
    () => sectionsSince(parseChangelog(data?.changelog || ""), since).slice(0, 15),
    [data, since],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div
      role="dialog" aria-modal="true" aria-label="What's new" onClick={onClose}
      style={{ position: "fixed", inset: 0, zIndex: 200, background: "rgba(0,0,0,0.55)", display: "grid", placeItems: "center", padding: 16 }}
    >
      <div
        className="card" onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 520, width: "100%", maxHeight: "85dvh", display: "flex", flexDirection: "column", padding: 0 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "14px 18px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
          <span style={{ fontWeight: 600, fontSize: 15 }}>{since ? "What's new" : "Changelog"}</span>
          <button className="btn btn-bare btn-sm" style={{ marginLeft: "auto", padding: 6 }} onClick={onClose} aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M1 1 L13 13 M13 1 L1 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
          </button>
        </div>
        <div style={{ overflowY: "auto", padding: "8px 20px 16px", fontSize: 13, color: "var(--text)" }}>
          {isLoading && <p style={{ color: "var(--muted)" }}>Loading…</p>}
          {!isLoading && sections.length === 0 && (
            <p style={{ color: "var(--muted)" }}>You're up to date.</p>
          )}
          {sections.map((s) => (
            <section key={s.version} style={{ marginTop: 12 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 2 }}>
                <span style={{ fontFamily: "var(--mono)", fontWeight: 600, color: "var(--accent-2)" }}>v{s.version}</span>
                {s.date && <span style={{ fontSize: 11, color: "var(--muted)" }}>{s.date}</span>}
              </div>
              {renderBody(s.body, s.version)}
            </section>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 18px", borderTop: "1px solid var(--border)", flexShrink: 0 }}>
          <a href="https://github.com/paulojf/bpm-tagger/blob/main/CHANGELOG.md" target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: "var(--accent-2)" }}>Full changelog ↗</a>
          <button className="btn btn-primary btn-sm" style={{ marginLeft: "auto" }} onClick={onClose}>Done</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
