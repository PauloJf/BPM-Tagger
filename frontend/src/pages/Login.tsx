import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { ApiError } from "../lib/api";

// Sine-envelope heights for the 40 decorative bars (matches the Jinja original).
const BAR_HEIGHTS = [
  14, 18, 26, 36, 46, 58, 70, 80, 88, 96, 100, 100, 96, 88, 80, 70, 58, 46, 36, 26,
  18, 14, 10, 16, 28, 44, 60, 74, 86, 94, 98, 94, 86, 74, 60, 44, 28, 16, 10, 12,
];

const Logo = () => (
  <div className="login-logo">
    <div className="login-logo-wrap">
      <div className="tile">
        <svg width="27" height="27" viewBox="0 0 24 24" fill="none">
          <rect x="3" y="10" width="2.4" height="4" rx="1" fill="white" opacity="0.7" />
          <rect x="7" y="6" width="2.4" height="12" rx="1" fill="white" opacity="0.9" />
          <rect x="11" y="3" width="2.4" height="18" rx="1" fill="white" />
          <rect x="15" y="6" width="2.4" height="12" rx="1" fill="white" opacity="0.9" />
          <rect x="19" y="10" width="2.4" height="4" rx="1" fill="white" opacity="0.7" />
        </svg>
      </div>
      <div className="login-logo-text">
        <span className="login-logo-title">BPM Tagger</span>
        <span className="login-logo-sub">for navidrome</span>
      </div>
    </div>
  </div>
);

const WarnIcon = ({ size = 11 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3 L22 20 H2 Z" />
    <path d="M12 10 V14" />
    <circle cx="12" cy="17" r="0.5" fill="currentColor" />
  </svg>
);

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState(false);
  const [lockedOut, setLockedOut] = useState(false);
  const [busy, setBusy] = useState(false);
  const [shake, setShake] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(false);
    try {
      await login(password);
      navigate("/tracks", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setLockedOut(true);
      } else {
        setError(true);
        setShake(true);
        setTimeout(() => setShake(false), 400);
        setPassword("");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-glow" />
      <div className="login-bars">
        {BAR_HEIGHTS.map((h, i) => (
          <div
            key={i}
            className="login-bar"
            style={{ height: h, animation: `scan-bar 2.4s ease-in-out ${Number(((i * 0.08) % 2).toFixed(2))}s infinite` }}
          />
        ))}
      </div>

      <div className="login-card-wrap">
        <Logo />
        <div className={"login-card" + (shake ? " shake" : "")}>
          {lockedOut ? (
            <div style={{ textAlign: "center", padding: "12px 0" }}>
              <div className="lockout-icon">
                <WarnIcon size={20} />
              </div>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>Too many attempts</div>
              <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5 }}>
                This IP is locked out for <span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>5 minutes</span>.
                <br />
                Wait and try again.
              </div>
            </div>
          ) : (
            <>
              <div className="login-card-title">Sign in</div>
              <div className="login-card-sub">Enter the UI password to continue</div>
              <form onSubmit={submit}>
                <div className="login-field">
                  <label className="login-label" htmlFor="login-pw">
                    Password
                  </label>
                  <div className="login-input-wrap">
                    <input
                      id="login-pw"
                      className={"login-input" + (error ? " error" : "")}
                      type={show ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      autoFocus
                      autoComplete="current-password"
                      placeholder="••••••••"
                      disabled={busy}
                    />
                    <button type="button" className="login-eye" onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"}>
                      {show ? (
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                          <line x1="1" y1="1" x2="23" y2="23" />
                        </svg>
                      ) : (
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                          <path d="M2 12 C5 6 8 5 12 5 S19 6 22 12 C19 18 16 19 12 19 S5 18 2 12 Z" />
                          <circle cx="12" cy="12" r="3" />
                        </svg>
                      )}
                    </button>
                  </div>
                  {error && (
                    <div className="login-error">
                      <WarnIcon />
                      Incorrect password
                    </div>
                  )}
                </div>
                <button className="login-submit" type="submit" disabled={busy}>
                  {busy ? "Signing in…" : "Sign in"}
                  {!busy && (
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M5 12 H19 M13 6 L19 12 L13 18" />
                    </svg>
                  )}
                </button>
              </form>
            </>
          )}
        </div>
        <div className="login-footer">
          BPM Tagger · <span style={{ color: "var(--accent-2)" }}>healthy</span>
        </div>
      </div>
    </div>
  );
}
