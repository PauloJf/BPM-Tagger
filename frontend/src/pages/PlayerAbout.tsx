import { useAuth } from "../lib/auth";
import { useTitle } from "../hooks/useTitle";

/** About page for the locked-down player ("Run mode") role. Written for the
 *  person on a run — not the admin — so it explains what Run mode does and how
 *  to drive it, and stays clear of the library/tagging internals the player
 *  can't reach. Self-contained: it only reads `version` from /api/me (the one
 *  endpoint players are allowed), never the admin-only version/grabber APIs. */

const HOW: [string, string][] = [
  ["Target BPM", "The big number is your goal cadence — steps per minute. Set it with the presets, the ±1 / ±5 buttons, or tap it in."],
  ["Presets", "Four one-tap cadences (Warmup, Easy, Steady, Tempo). Pick one to jump straight to that BPM."],
  ["Tempo lock", "With the lock on, every track is time-stretched onto your target cadence — pitch preserved, so nothing sounds chipmunked. Tap the lock to hear tracks at their native speed instead."],
  ["Octave fold", "A 75 BPM track can carry a 150 cadence (every half-beat) — the queue folds tempos by ×½ / ×2 to find more matches for your target."],
  ["The queue", "Start run builds a queue of tracks whose (folded) tempo fits your target. It refills itself as you go, so a run never falls silent."],
  ["Star & dislike", "Star the tracks you love — they're preferred next time a queue is built. Dislike one and it's skipped now and never queued again."],
];

const CONTROLS: [string, string][] = [
  ["Play / pause", "The big centre button, or your headset's play/pause."],
  ["Skip", "Previous / next on either side of play; lock-screen and headset skip work too."],
  ["Scrub", "Tap or drag on the waveform to move within the current track."],
  ["Lock screen", "Media controls appear on your phone's lock screen and headphones (via the Media Session API) so you needn't wake the app mid-run."],
  ["Install as an app", "Add BPM Tagger to your home screen for a full-screen, app-like run (requires the site served over HTTPS)."],
];

export default function PlayerAbout() {
  useTitle("About");
  const { version, logout } = useAuth();

  return (
    <>
      <div style={{ marginBottom: 18 }}>
        <div className="section-label" style={{ marginBottom: 10 }}>
          <span>About</span>
          {version && <span className="section-hint">v{version}</span>}
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.02em", marginBottom: 6 }}>
          Run mode
          <span style={{ fontSize: 13, fontWeight: 400, color: "var(--muted)", fontFamily: "var(--mono)", marginLeft: 8 }}>BPM Tagger</span>
        </h1>
        <p style={{ fontSize: 14, color: "var(--muted)", lineHeight: 1.65, margin: 0, maxWidth: 640 }}>
          A tempo-matched music player for running. Pick a cadence and every track
          is locked onto it — pitch preserved — so the beat lands under your feet
          instead of fighting them. Set a target, hit <strong style={{ color: "var(--text)", fontWeight: 600 }}>Start run</strong>,
          and go.
        </p>
      </div>

      <div className="about-grid">
        <div className="card">
          <div className="about-section-title">How it works</div>
          {HOW.map(([name, label]) => (
            <div key={name} style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 3 }}>{name}</div>
              <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.6 }}>{label}</div>
            </div>
          ))}
        </div>

        <div className="card">
          <div className="about-section-title">Controls</div>
          {CONTROLS.map(([name, label]) => (
            <div key={name} style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 3 }}>{name}</div>
              <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.6 }}>{label}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="card" style={{ marginTop: 18, borderLeft: "3px solid var(--accent)", paddingLeft: 24 }}>
        <div className="about-section-title">You're signed in as a player</div>
        <p style={{ fontSize: 14, lineHeight: 1.7, color: "var(--text)", margin: 0, maxWidth: 640 }}>
          This is a limited view — just the run player. Browsing the library,
          editing tags, downloading, and settings all live behind the main
          password and aren't reachable from here. That keeps a shared phone or a
          dedicated running device safe to hand around.
        </p>
      </div>

      <div style={{ marginTop: 18, padding: "14px 0", display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", borderTop: "1px solid var(--border)", fontFamily: "var(--mono)", fontSize: 11, color: "var(--muted)" }}>
        <span>BPM Tagger{version ? ` · v${version}` : ""} · Run mode</span>
        <span style={{ color: "var(--border)" }}>·</span>
        <button
          className="btn btn-bare btn-sm"
          style={{ padding: 0, color: "var(--err-fg)", fontFamily: "var(--mono)", fontSize: 11 }}
          onClick={() => logout()}
        >
          Sign out
        </button>
      </div>
    </>
  );
}
