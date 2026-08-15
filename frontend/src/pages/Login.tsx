import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { ApiError } from "../lib/api";
import BpmMark from "../components/BpmMark";

// Sine-envelope heights for the 40 decorative bars (matches the Jinja original).
const BAR_HEIGHTS = [
  14, 18, 26, 36, 46, 58, 70, 80, 88, 96, 100, 100, 96, 88, 80, 70, 58, 46, 36, 26,
  18, 14, 10, 16, 28, 44, 60, 74, 86, 94, 98, 94, 86, 74, 60, 44, 28, 16, 10, 12,
];

const Logo = () => (
  <div className="login-logo">
    <div className="login-logo-wrap">
      <div className="tile">
        <BpmMark size={27} />
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
  // The page a signed-out visit was bounced from (App passes it in state) — a
  // session expiry mid-anything returns there instead of dumping on /tracks.
  // The player shell collapses any admin path to its own home, so this is safe
  // for every role; /tracks stays the fallback for a direct login-page visit.
  const from = (useLocation().state as { from?: string } | null)?.from;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState(false);
  const [lockedOut, setLockedOut] = useState(false);
  const [busy, setBusy] = useState(false);
  const [shake, setShake] = useState(false);
  // Admin 2FA: the server answers the password step with "totp_required" when a
  // code is needed. We then show a code field and resubmit password + code.
  const [totpStep, setTotpStep] = useState(false);
  const [totp, setTotp] = useState("");
  const [codeError, setCodeError] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(false);
    setCodeError(false);
    try {
      await login(password, username, totpStep ? totp : undefined);
      navigate(from && from !== "/login" ? from : "/tracks", { replace: true });
    } catch (err) {
      const code = err instanceof ApiError ? err.message : "";
      if (err instanceof ApiError && err.status === 429) {
        setLockedOut(true);
      } else if (code === "totp_required") {
        // Password was right; move to the code step (keep the password in state).
        setTotpStep(true);
      } else if (code === "totp_invalid") {
        setCodeError(true);
        setShake(true);
        setTimeout(() => setShake(false), 400);
        setTotp("");
      } else {
        // Wrong password (or anything else): drop back to the password step.
        setTotpStep(false);
        setTotp("");
        setError(true);
        setShake(true);
        setTimeout(() => setShake(false), 400);
        setPassword("");
      }
    } finally {
      setBusy(false);
    }
  }

  function backToPassword() {
    setTotpStep(false);
    setTotp("");
    setCodeError(false);
    setError(false);
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
          ) : totpStep ? (
            <>
              <div className="login-card-title">Two-factor</div>
              <div className="login-card-sub">Enter the 6-digit code from your authenticator app.</div>
              <form onSubmit={submit}>
                <div className="login-field">
                  <label className="login-label" htmlFor="login-totp">
                    Authentication code
                  </label>
                  <div className="login-input-wrap">
                    <input
                      id="login-totp"
                      className={"login-input" + (codeError ? " error" : "")}
                      type="text"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      value={totp}
                      onChange={(e) => setTotp(e.target.value.replace(/[^0-9a-zA-Z-]/g, ""))}
                      autoFocus
                      placeholder="123 456"
                      maxLength={14}
                      disabled={busy}
                    />
                  </div>
                  {codeError && (
                    <div className="login-error">
                      <WarnIcon />
                      Invalid code
                    </div>
                  )}
                  <div style={{ marginTop: 8, fontSize: 12, color: "var(--muted)" }}>
                    Lost your device? Enter a <strong>recovery code</strong> instead.
                  </div>
                </div>
                <button className="login-submit" type="submit" disabled={busy}>
                  {busy ? "Verifying…" : "Verify"}
                </button>
                <button
                  type="button"
                  onClick={backToPassword}
                  disabled={busy}
                  style={{ marginTop: 10, width: "100%", background: "none", border: "none", color: "var(--muted)", fontSize: 12, cursor: "pointer" }}
                >
                  ← Back
                </button>
              </form>
            </>
          ) : (
            <>
              <div className="login-card-title">Sign in</div>
              <div className="login-card-sub">Admin &amp; guest: password only. Player user: username + password.</div>
              <form onSubmit={submit}>
                <div className="login-field">
                  <label className="login-label" htmlFor="login-user">
                    Username <span style={{ color: "var(--muted)", fontWeight: 400 }}>(player users only)</span>
                  </label>
                  <div className="login-input-wrap">
                    <input
                      id="login-user"
                      className="login-input"
                      type="text"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      autoComplete="username"
                      placeholder="Leave blank for admin"
                      disabled={busy}
                    />
                  </div>
                </div>
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
