import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { ApiError } from "../lib/api";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [lockedOut, setLockedOut] = useState(false);
  const [busy, setBusy] = useState(false);
  const [shake, setShake] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(password);
      navigate("/tracks", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setLockedOut(true);
        setError("Too many attempts. Try again in a few minutes.");
      } else {
        setError("Wrong password.");
      }
      setShake(true);
      setTimeout(() => setShake(false), 500);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 20 }}>
      <div
        className="card"
        style={{ width: "100%", maxWidth: 360, animation: shake ? "shake-x 0.4s" : "fade-in 0.3s ease" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
          <div className="nav-logo-tile" style={{ width: 40, height: 40, borderRadius: 11 }}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
              <rect x="3" y="10" width="2.4" height="4" rx="1" fill="white" opacity="0.7" />
              <rect x="7" y="6" width="2.4" height="12" rx="1" fill="white" opacity="0.9" />
              <rect x="11" y="3" width="2.4" height="18" rx="1" fill="white" />
              <rect x="15" y="6" width="2.4" height="12" rx="1" fill="white" opacity="0.9" />
              <rect x="19" y="10" width="2.4" height="4" rx="1" fill="white" opacity="0.7" />
            </svg>
          </div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 16, letterSpacing: "-0.01em" }}>BPM Tagger</div>
            <div className="nav-logo-sub">for navidrome</div>
          </div>
        </div>

        <form onSubmit={submit}>
          <label
            style={{ display: "block", fontSize: 12, color: "var(--muted)", marginBottom: 6, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.1em" }}
          >
            Password
          </label>
          <input
            type="password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy || lockedOut}
            style={{ width: "100%", marginBottom: 14 }}
          />
          {error && (
            <div className="flash error" style={{ marginBottom: 14 }}>
              {error}
            </div>
          )}
          <button type="submit" className="btn btn-primary btn-lg" style={{ width: "100%" }} disabled={busy || lockedOut}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
