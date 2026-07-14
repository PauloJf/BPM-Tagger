import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useTitle } from "../hooks/useTitle";
import { useGrabberStatus } from "../hooks/useGrabberStatus";

const STACK: [string, string][] = [
  ["Flask + Waitress", "web server"],
  ["SQLite (WAL)", "database"],
  ["DeepRhythm", "primary BPM detector"],
  ["Essentia", "secondary BPM detector"],
  ["librosa", "confidence + fallback"],
  ["Mutagen", "tag writing"],
  ["Watchdog", "filesystem watcher"],
];

export default function About() {
  useTitle("About");
  const { version } = useAuth();
  const grabber = useGrabberStatus();
  const ytdlp = grabber.data?.versions?.yt_dlp;
  const versionQ = useQuery({
    queryKey: ["version-check"],
    queryFn: () => api.get<{ latest?: string }>("/api/version/check"),
    retry: false,
  });

  const latest = versionQ.data?.latest?.replace(/^v/, "");
  let badge: { text: string; color: string; bold?: boolean } | null = null;
  if (latest) {
    badge = latest === version ? { text: "· up to date", color: "var(--ok-fg)" } : { text: `· v${latest} available`, color: "var(--warn-fg)", bold: true };
  }

  return (
    <>
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", alignItems: "stretch", marginBottom: 18 }}>
        <div style={{ flex: "1 1 320px", minWidth: 0 }}>
          <div className="section-label" style={{ marginBottom: 10 }}>
            <span>About</span>
            <span className="section-hint">v{version}</span>
          </div>
          <h1 style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.02em", marginBottom: 6 }}>
            BPM Tagger
            <span style={{ fontSize: 13, fontWeight: 400, color: "var(--muted)", fontFamily: "var(--mono)", marginLeft: 8 }}>for Navidrome</span>
          </h1>
          <p style={{ fontSize: 14, color: "var(--muted)", lineHeight: 1.65, margin: 0 }}>
            A self-hosted tool that scans your music library and writes accurate BPM tags directly into your audio files — so Navidrome (and any other player) always shows the right tempo without third-party services or manual work.
          </p>
        </div>

        <div className="card" style={{ flex: "1 1 320px", minWidth: 0, borderLeft: "3px solid var(--accent)", paddingLeft: 24 }}>
          <div className="about-section-title">Why this exists</div>
          <p style={{ fontSize: 14, lineHeight: 1.7, color: "var(--text)", margin: 0 }}>
            Built out of a personal necessity: keeping a running playlist where every song sits at exactly the right cadence. Wrong BPMs meant either fighting the beat or constantly editing playlists by hand. BPM Tagger automates the whole thing — scan once, tag forever, and let Navidrome surface the right tracks for any target cadence.
          </p>
        </div>
      </div>

      <div className="about-grid">
        <div className="card">
          <div className="about-section-title">Authors</div>
          <div className="author-row">
            <div className="author-avatar" style={{ background: "linear-gradient(135deg, var(--accent-2), var(--accent))" }}>P</div>
            <div>
              <div className="author-name">Paulo Fernandes</div>
              <div className="author-role">
                Creator &amp; maintainer ·{" "}
                <a href="https://github.com/paulojf" target="_blank" rel="noopener" style={{ color: "var(--accent-2)", textDecoration: "none" }}>
                  @paulojf
                </a>
              </div>
            </div>
          </div>
          <div className="author-row">
            <div className="author-avatar" style={{ background: "linear-gradient(135deg, oklch(0.55 0.18 160), oklch(0.45 0.15 200))" }}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2 C6.5 2 2 6.5 2 12 s4.5 10 10 10 10-4.5 10-10 S17.5 2 12 2" />
                <path d="M8 9 Q12 6 16 9 Q14 14 12 16 Q10 14 8 9 Z" />
              </svg>
            </div>
            <div>
              <div className="author-name">Claude</div>
              <div className="author-role">
                Co-author &amp; pair programmer ·{" "}
                <a href="https://claude.ai" target="_blank" rel="noopener" style={{ color: "var(--accent-2)", textDecoration: "none" }}>
                  claude.ai
                </a>
              </div>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="about-section-title">Built with</div>
          {STACK.map(([name, label]) => (
            <div className="stack-row" key={name}>
              <span>{name}</span>
              <span className="stack-label">{label}</span>
            </div>
          ))}
          {ytdlp && (
            <div className="stack-row">
              <span>yt-dlp <span style={{ color: "var(--muted)", fontFamily: "var(--mono)", fontSize: 11 }}>{ytdlp}</span></span>
              <span className="stack-label">download fallback</span>
            </div>
          )}
        </div>
      </div>

      <div style={{ marginTop: 18, padding: "14px 0", display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", borderTop: "1px solid var(--border)", fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)" }}>
        <span>BPM Tagger · v{version}</span>
        {badge && <span style={{ color: badge.color, fontWeight: badge.bold ? 600 : undefined }}>{badge.text}</span>}
        <span style={{ color: "var(--border)" }}>·</span>
        <a href="https://github.com/paulojf/bpm-tagger" target="_blank" rel="noopener" style={{ color: "var(--accent-2)", textDecoration: "none" }}>
          github.com/paulojf/bpm-tagger
        </a>
        <span style={{ color: "var(--border)" }}>·</span>
        <a href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noopener" style={{ color: "inherit", textDecoration: "none" }}>AGPLv3 licence</a>
      </div>
    </>
  );
}
