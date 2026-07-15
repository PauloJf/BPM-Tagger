import { useState } from "react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

// First-run courtesy prompt: asks (once) whether to send a single anonymous
// install ping. Rendered only when /api/me reports install_ping_ask. Either
// choice is persisted server-side, so it never reappears; dismissing locally
// hides it immediately regardless of the network round-trip.
export default function InstallPingCard() {
  const { installPingAsk, dismissInstallPingAsk } = useAuth();
  const [busy, setBusy] = useState<"yes" | "no" | null>(null);

  if (!installPingAsk) return null;

  const choose = async (consent: boolean) => {
    setBusy(consent ? "yes" : "no");
    try {
      await api.post("/api/settings/install-ping", { consent });
    } catch {
      // Best effort — the prompt is a courtesy, never a blocker.
    } finally {
      dismissInstallPingAsk();
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Anonymous install count"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 200,
        background: "rgba(0, 0, 0, 0.55)",
        display: "grid",
        placeItems: "center",
        padding: 20,
      }}
    >
      <div className="card" style={{ maxWidth: 440, width: "100%", padding: 24 }}>
        <div className="about-section-title" style={{ marginBottom: 10 }}>
          Count this install?
        </div>
        <p style={{ fontSize: 14, lineHeight: 1.7, color: "var(--text)", margin: "0 0 12px" }}>
          Would you let BPM Tagger send <strong>one</strong> anonymous ping so I can
          gauge roughly how many installs are out there? It contains only the app
          version — <strong>no</strong> identifier, no library or usage data, no
          cookies, and IP addresses aren&rsquo;t logged.
        </p>
        <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--muted)", margin: "0 0 18px" }}>
          It fires just once. Say no and nothing is sent — every feature works
          exactly the same either way. You can change your mind later under{" "}
          <strong>About</strong>. The ping is a few lines of auditable open source.
        </p>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button
            className="btn btn-sm btn-ghost"
            disabled={busy !== null}
            onClick={() => choose(false)}
          >
            {busy === "no" ? "Saving…" : "No thanks"}
          </button>
          <button
            className="btn btn-sm btn-primary"
            disabled={busy !== null}
            onClick={() => choose(true)}
          >
            {busy === "yes" ? "Sending…" : "Sure, send it"}
          </button>
        </div>
      </div>
    </div>
  );
}
