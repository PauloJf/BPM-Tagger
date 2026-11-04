import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import PlayerUsers from "../PlayerUsers";
import { SaveButton, fieldLabel, readers, useSaveSection, useSettings, type Saved } from "./shared";

/** Settings → People & security tab: admin sign-in, two-factor, login protection, people. */

export function AdminSignInCard() {
  const qc = useQueryClient();
  const { cfg } = useSettings();
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [pwSaved, setPwSaved] = useState<Saved>("");
  const [pwErr, setPwErr] = useState("");
  // Optional admin login username — an identifier so password managers have a
  // user field to store. Blank = the historical password-only login.
  const [adminUser, setAdminUser] = useState("");
  const [adminUserSaved, setAdminUserSaved] = useState<Saved>("");
  const [adminUserErr, setAdminUserErr] = useState("");
  useEffect(() => {
    if (!cfg) return;
    const { s } = readers(cfg);
    setAdminUser(s("ui_username"));
  }, [cfg]);

  async function saveAdminUsername(e: React.FormEvent) {
    e.preventDefault();
    setAdminUserErr("");
    setAdminUserSaved("saving");
    try {
      await api.post("/api/settings/username", { ui_username: adminUser.trim() });
      setAdminUserSaved("ok");
      qc.invalidateQueries({ queryKey: ["settings"] });
      setTimeout(() => setAdminUserSaved(""), 1800);
    } catch (err) {
      setAdminUserSaved("err");
      setAdminUserErr(err instanceof Error ? err.message : "Failed");
    }
  }

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwErr("");
    setPwSaved("saving");
    try {
      await api.post("/api/settings/password", { current_password: pw.current, new_password: pw.next, confirm_password: pw.confirm });
      setPwSaved("ok");
      setPw({ current: "", next: "", confirm: "" });
      setTimeout(() => setPwSaved(""), 1800);
    } catch (err) {
      setPwSaved("err");
      setPwErr(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <>
      {/* Admin sign-in: optional username + password */}
      <div id="sec-password" className="settings-card card">
        <div className="settings-card-header">
          <h2>Admin sign-in</h2>
          <p>Name the admin account and change its password.</p>
        </div>
        <form onSubmit={saveAdminUsername}>
          <div className="settings-fields" style={{ marginBottom: 18 }}>
            <div className="field-row">
              {fieldLabel("Username", "Optional. Set one so a password manager has a user field to save. Signing in with a blank username keeps working either way, so this can't lock you out.")}
              <input type="text" autoComplete="username" value={adminUser} placeholder="(none — password only)"
                     onChange={(e) => setAdminUser(e.target.value)} style={{ maxWidth: 340, width: "100%" }} />
            </div>
            {adminUserErr && <div style={{ color: "var(--err-fg)", fontSize: 12 }}>{adminUserErr}</div>}
            <div>
              <SaveButton state={adminUserSaved} label={adminUser.trim() ? "Save username" : "Clear username"} />
            </div>
          </div>
        </form>
        <form onSubmit={changePassword}>
          <div className="settings-fields">
            <div className="field-row">
              {fieldLabel("Current password")}
              <input type="password" required autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} style={{ maxWidth: 340, width: "100%" }} />
            </div>
            <div className="field-row">
              {fieldLabel("New password")}
              <input type="password" required autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} style={{ maxWidth: 340, width: "100%" }} />
            </div>
            <div className="field-row">
              {fieldLabel("Confirm new password")}
              <input type="password" required autoComplete="new-password" value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} style={{ maxWidth: 340, width: "100%" }} />
            </div>
            {pwErr && <div style={{ color: "var(--err-fg)", fontSize: 12 }}>{pwErr}</div>}
            <div>
              <SaveButton state={pwSaved} label="Update Password" />
            </div>
          </div>
        </form>
      </div>
    </>
  );
}

export function TwoFactorCard() {
  const qc = useQueryClient();
  const { cfg } = useSettings();
  const [totpSetup, setTotpSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [totpErr, setTotpErr] = useState("");
  const [totpBusy, setTotpBusy] = useState(false);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [totpDisablePw, setTotpDisablePw] = useState("");

  async function startTotpSetup() {
    setTotpErr("");
    setTotpBusy(true);
    try {
      const r = await api.post<{ secret: string; otpauth_uri: string }>("/api/settings/totp/setup", {});
      setTotpSetup({ secret: r.secret, uri: r.otpauth_uri });
      setTotpCode("");
    } catch (err) {
      setTotpErr(err instanceof Error ? err.message : "Failed");
    } finally {
      setTotpBusy(false);
    }
  }

  async function confirmTotp(e: React.FormEvent) {
    e.preventDefault();
    setTotpErr("");
    setTotpBusy(true);
    try {
      const r = await api.post<{ recovery_codes: string[] }>("/api/settings/totp/confirm", { code: totpCode });
      setRecoveryCodes(r.recovery_codes);
      setTotpSetup(null);
      setTotpCode("");
      qc.invalidateQueries({ queryKey: ["settings"] });
    } catch (err) {
      setTotpErr(err instanceof Error ? err.message : "Failed");
    } finally {
      setTotpBusy(false);
    }
  }

  async function disableTotp(e: React.FormEvent) {
    e.preventDefault();
    setTotpErr("");
    setTotpBusy(true);
    try {
      await api.post("/api/settings/totp/disable", { password: totpDisablePw });
      setTotpDisablePw("");
      setRecoveryCodes(null);
      qc.invalidateQueries({ queryKey: ["settings"] });
    } catch (err) {
      setTotpErr(err instanceof Error ? err.message : "Failed");
    } finally {
      setTotpBusy(false);
    }
  }

  return (
    <>
      {/* Admin two-factor (TOTP) */}
      {(() => {
        const enabled = cfg?.totp_enabled === true;
        const rawRemaining = cfg?.totp_recovery_remaining;
        const remaining = typeof rawRemaining === "number" ? rawRemaining : null;
        return (
          <div id="sec-2fa" className="settings-card card">
            <div className="settings-card-header">
              <h2>Two-factor authentication</h2>
              <p>
                Require a 6-digit code from an authenticator app (Google Authenticator, Authy, 1Password…)
                on top of the admin password. Applies to the <strong>admin</strong> login only.
              </p>
            </div>
            <div style={{ fontSize: 12, fontFamily: "var(--mono)", color: enabled ? "var(--ok-fg)" : "var(--muted)", marginBottom: 12 }}>
              {enabled ? "● Two-factor is enabled" : "○ Two-factor is off"}
              {enabled && remaining !== null && (
                <span style={{ color: remaining <= 2 ? "var(--err-fg)" : "var(--muted)" }}>
                  {"  ·  "}{remaining} recovery code{remaining === 1 ? "" : "s"} left
                </span>
              )}
            </div>

            {/* One-time recovery codes, shown once right after enabling. */}
            {recoveryCodes && (
              <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 12, marginBottom: 12, background: "var(--surface)" }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Save your recovery codes</div>
                <p style={{ fontSize: 12, color: "var(--muted)", margin: "0 0 8px", lineHeight: 1.5 }}>
                  Each works once if you lose your authenticator. This is the <strong>only</strong> time they're shown.
                </p>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, fontFamily: "var(--mono)", fontSize: 13 }}>
                  {recoveryCodes.map((c) => <span key={c}>{c}</span>)}
                </div>
                <button type="button" className="btn" style={{ marginTop: 10 }} onClick={() => setRecoveryCodes(null)}>
                  I've saved them
                </button>
              </div>
            )}

            {!enabled && !totpSetup && (
              <button type="button" className="btn btn-primary" disabled={totpBusy} onClick={startTotpSetup}>
                {totpBusy ? "Starting…" : "Enable two-factor"}
              </button>
            )}

            {!enabled && totpSetup && (
              <form onSubmit={confirmTotp}>
                <p style={{ fontSize: 13, margin: "0 0 8px", lineHeight: 1.5 }}>
                  Scan this in your authenticator app, or enter the key by hand, then type the current code to confirm.
                </p>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>Secret key</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 15, letterSpacing: 1, wordBreak: "break-all", padding: "8px 10px", border: "1px solid var(--border)", borderRadius: 6, marginBottom: 4 }}>
                  {totpSetup.secret}
                </div>
                <div style={{ fontSize: 11, color: "var(--muted)", wordBreak: "break-all", marginBottom: 12 }}>{totpSetup.uri}</div>
                <div className="settings-fields">
                  <div className="field-row">
                    {fieldLabel("Code from app")}
                    <input type="text" inputMode="numeric" autoComplete="one-time-code" value={totpCode}
                           onChange={(e) => setTotpCode(e.target.value.replace(/[^0-9]/g, ""))}
                           maxLength={6} placeholder="123456" style={{ maxWidth: 160, width: "100%", fontFamily: "var(--mono)" }} />
                  </div>
                  {totpErr && <div style={{ color: "var(--err-fg)", fontSize: 12 }}>{totpErr}</div>}
                  <div style={{ display: "flex", gap: 8 }}>
                    <button type="submit" className="btn btn-primary" disabled={totpBusy || totpCode.length !== 6}>
                      {totpBusy ? "Verifying…" : "Confirm & enable"}
                    </button>
                    <button type="button" className="btn" disabled={totpBusy} onClick={() => { setTotpSetup(null); setTotpErr(""); }}>
                      Cancel
                    </button>
                  </div>
                </div>
              </form>
            )}

            {enabled && (
              <form onSubmit={disableTotp} style={{ marginTop: 4 }}>
                <div className="settings-fields">
                  <div className="field-row">
                    {fieldLabel("Confirm password to disable")}
                    <input type="password" required autoComplete="current-password" value={totpDisablePw}
                           onChange={(e) => setTotpDisablePw(e.target.value)} style={{ maxWidth: 340, width: "100%" }} />
                  </div>
                  {totpErr && <div style={{ color: "var(--err-fg)", fontSize: 12 }}>{totpErr}</div>}
                  <div>
                    <button type="submit" className="btn" disabled={totpBusy}>
                      {totpBusy ? "Disabling…" : "Disable two-factor"}
                    </button>
                  </div>
                </div>
              </form>
            )}
          </div>
        );
      })()}
    </>
  );
}

export function LoginProtectionCard() {
  const { cfg } = useSettings();
  const [resetBusy, setResetBusy] = useState(false);
  const [resetMsg, setResetMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function resetLockouts() {
    setResetBusy(true);
    setResetMsg(null);
    try {
      await api.post("/api/settings/reset-lockouts", {});
      setResetMsg({ ok: true, text: "Login lockouts cleared." });
    } catch (err) {
      setResetMsg({ ok: false, text: err instanceof Error ? err.message : "Failed" });
    } finally {
      setResetBusy(false);
    }
  }

  return (
    <>
      {/* Login protection — read-only thresholds + a reset-lockouts action */}
      {(() => {
        const n = (k: string, d: number) => {
          const v = cfg?.[k];
          return typeof v === "number" ? v : d;
        };
        const rows: [string, string][] = [
          ["Per IP", `${n("ui_max_login_attempts", 5)} attempts / 60s → ${n("ui_lockout_seconds", 300)}s lockout`],
          ["Per account", `${n("ui_account_max_login_attempts", 15)} attempts / 60s → ${n("ui_lockout_seconds", 300)}s lockout`],
          ["Global", `${n("ui_global_max_login_attempts", 50)} attempts / 60s → ${n("ui_global_lockout_seconds", 60)}s cooldown`],
        ];
        return (
          <div id="sec-login-protection" className="settings-card card">
            <div className="settings-card-header">
              <h2>Login protection</h2>
              <p>
                Failed logins are throttled at three levels to slow brute-force attacks. These are
                security defaults — tune them with the <code>UI_*_LOGIN_ATTEMPTS</code> environment
                variables (see the README); they're shown here read-only.
              </p>
            </div>
            <div className="settings-fields">
              {rows.map(([label, val]) => (
                <div key={label} className="field-row">
                  {fieldLabel(label)}
                  <span style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--muted)" }}>{val}</span>
                </div>
              ))}
              <div style={{ marginTop: 4 }}>
                <button type="button" className="btn" disabled={resetBusy} onClick={resetLockouts}>
                  {resetBusy ? "Clearing…" : "Reset login lockouts"}
                </button>
                <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6, lineHeight: 1.5 }}>
                  Clears active lockouts and attempt counters now — e.g. if someone tripped the limit and you're
                  still signed in. Changes no threshold.
                </div>
                {resetMsg && (
                  <div style={{ fontSize: 12, marginTop: 6, color: resetMsg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>
                    {resetMsg.text}
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })()}
    </>
  );
}

export function PeopleCard() {
  const qc = useQueryClient();
  const { cfg } = useSettings();
  const saveSection = useSaveSection();
  const [runPw, setRunPw] = useState({ next: "", confirm: "" });
  const [runPwSaved, setRunPwSaved] = useState<Saved>("");
  const [runPwErr, setRunPwErr] = useState("");
  const [convertName, setConvertName] = useState("guest");
  const [convertMsg, setConvertMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [runSessionDays, setRunSessionDays] = useState(30);
  const [runSessSaved, setRunSessSaved] = useState<Saved>("");
  const [listenModeCfg, setListenModeCfg] = useState("off");
  const [listenModeSaved, setListenModeSaved] = useState<Saved>("");
  useEffect(() => {
    if (!cfg) return;
    const { s, n } = readers(cfg);
    setRunSessionDays(n("run_session_days", 30));
    setListenModeCfg(s("player_listen_mode", "off") || "off");
  }, [cfg]);

  async function setRunPassword(e: React.FormEvent) {
    e.preventDefault();
    setRunPwErr("");
    setRunPwSaved("saving");
    try {
      await api.post("/api/settings/run-password", { new_password: runPw.next, confirm_password: runPw.confirm });
      setRunPwSaved("ok");
      setRunPw({ next: "", confirm: "" });
      qc.invalidateQueries({ queryKey: ["settings"] });
      setTimeout(() => setRunPwSaved(""), 1800);
    } catch (err) {
      setRunPwSaved("err");
      setRunPwErr(err instanceof Error ? err.message : "Failed");
    }
  }

  async function disableRunPassword() {
    setRunPwErr("");
    try {
      await api.post("/api/settings/run-password", { disable: true });
      setRunPw({ next: "", confirm: "" });
      qc.invalidateQueries({ queryKey: ["settings"] });
    } catch (err) {
      setRunPwErr(err instanceof Error ? err.message : "Failed");
    }
  }

  async function convertGuest(e: React.FormEvent) {
    e.preventDefault();
    setConvertMsg(null);
    try {
      const r = await api.post<{ username: string }>("/api/settings/run-password/convert", { username: convertName });
      setConvertMsg({ text: `Done: sign in as “${r.username}” with the old guest password.`, ok: true });
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["players"] });
    } catch (err) {
      setConvertMsg({ text: err instanceof Error ? err.message : "Failed", ok: false });
    }
  }

  async function saveRunSession(e: React.FormEvent) {
    e.preventDefault();
    setRunSessSaved("saving");
    try {
      await api.post("/api/settings/run-session", { run_session_days: runSessionDays });
      setRunSessSaved("ok");
      qc.invalidateQueries({ queryKey: ["settings"] });
      setTimeout(() => setRunSessSaved(""), 1800);
    } catch {
      setRunSessSaved("err");
    }
  }

  return (
    <>
      {/* Player access — the shared Guest login + named Player users */}
      {(() => {
        const runEnabled = cfg?.run_password_hash === "********" || cfg?.run_password === "********";
        return (
          <div id="sec-run-access" className="settings-card card">
            <div className="settings-card-header">
              <h2>People &amp; player access</h2>
              <p>
                Named accounts for everyone besides you: <strong>players</strong> get the locked-down Run/Listen
                kiosk, <strong>members</strong> get the app without the admin tools. Each has its own ratings and
                runs. Leave the list empty to keep the single admin login.
              </p>
            </div>
            {runEnabled ? (
              <div style={{ border: "1px solid var(--warn-fg)", borderRadius: 10, padding: 12, marginBottom: 16 }}>
                <h3 style={{ fontSize: 14, margin: "0 0 4px" }}>Shared guest login <span className="badge" style={{ fontSize: 10, padding: "1px 7px", color: "var(--warn-fg)" }}>retiring</span></h3>
                <p style={{ fontSize: 12, color: "var(--muted)", margin: "0 0 12px", lineHeight: 1.5 }}>
                  One password with no name, shared by everyone who uses it: their ratings and runs all land
                  in one “guest” bucket. It will be removed in a future release. Convert it to a named player
                  with the whole library: people then sign in with that username and the <strong>same
                  password</strong>. (If it comes from <code>RUN_PASSWORD</code>, remove that variable afterwards.)
                </p>
                <form onSubmit={convertGuest} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <input type="text" value={convertName} onChange={(e) => setConvertName(e.target.value)}
                    aria-label="Username for the converted guest" style={{ maxWidth: 200, fontSize: 13 }} />
                  <button type="submit" className="btn btn-primary btn-sm">Convert to a named player</button>
                  {convertMsg && <span role="status" style={{ fontSize: 12, color: convertMsg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{convertMsg.text}</span>}
                </form>
                <form onSubmit={setRunPassword} style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                  <div className="settings-fields">
                    <div className="field-row">
                      {fieldLabel("New guest password")}
                      <input type="password" required autoComplete="new-password" value={runPw.next} onChange={(e) => setRunPw({ ...runPw, next: e.target.value })} style={{ maxWidth: 340, width: "100%" }} />
                    </div>
                    <div className="field-row">
                      {fieldLabel("Confirm password")}
                      <input type="password" required autoComplete="new-password" value={runPw.confirm} onChange={(e) => setRunPw({ ...runPw, confirm: e.target.value })} style={{ maxWidth: 340, width: "100%" }} />
                    </div>
                    {runPwErr && <div style={{ color: "var(--err-fg)", fontSize: 12 }}>{runPwErr}</div>}
                    <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                      <SaveButton state={runPwSaved} label="Change guest password" ghost />
                      <button type="button" className="btn btn-sm btn-ghost" onClick={disableRunPassword}>
                        Turn the guest login off
                      </button>
                    </div>
                  </div>
                </form>
              </div>
            ) : null}
            <form onSubmit={saveRunSession}>
              <div className="settings-fields">
                <div className="field-row">
                  {fieldLabel("Players stay signed in for", "days — the run kiosk rarely re-asks (sliding; each use extends it)")}
                  <input
                    type="number" min={1} max={365} value={runSessionDays}
                    onChange={(e) => setRunSessionDays(Math.max(1, Math.min(365, Number(e.target.value) || 1)))}
                    style={{ maxWidth: 120, width: "100%" }}
                  />
                </div>
                <div>
                  <SaveButton state={runSessSaved} label="Save session length" ghost />
                </div>
              </div>
            </form>
            {/* Kiosk Listen mode: what player-role logins get besides Run.
                This is the default for the Guest login and every Player user;
                a Player user can override it on its own row below. The backend
                gates /api/listen/queue on the same resolved value. */}
            <div style={{ marginTop: 16, borderTop: "1px solid var(--border)", paddingTop: 16 }}>
              <h3 style={{ fontSize: 14, margin: "0 0 4px" }}>Music player (Listen)</h3>
              <p style={{ fontSize: 12, color: "var(--muted)", margin: "0 0 12px", lineHeight: 1.5 }}>
                Give player logins a regular music player next to the tempo-locked Run page — their
                playlists, played at native speed. Your own login always has the Listen page. This is
                the default for every player login; each Player user below can override it.
              </p>
              <div className="settings-fields">
                <div className="field-row">
                  {fieldLabel("Player logins get")}
                  <select
                    value={listenModeCfg}
                    onChange={(e) => {
                      const v = e.target.value;
                      setListenModeCfg(v);
                      saveSection("/api/settings/listen-mode", { player_listen_mode: v }, setListenModeSaved);
                    }}
                    aria-label="Kiosk listen mode"
                    style={{ fontSize: 13, padding: "6px 10px", borderRadius: 10, background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text)", maxWidth: 340 }}
                  >
                    <option value="off">Run only (no music player)</option>
                    <option value="on">Run + Listen — lands on Run</option>
                    <option value="default">Run + Listen — lands on Listen</option>
                    <option value="only">Listen only (pure jukebox, no Run)</option>
                  </select>
                </div>
                <div style={{ minHeight: 16, fontSize: 12, color: listenModeSaved === "err" ? "var(--err-fg)" : "var(--ok-fg)" }}>
                  {listenModeSaved === "saving" ? "Saving…" : listenModeSaved === "ok" ? "Saved — applies on the kiosk's next page load." : listenModeSaved === "err" ? "Could not save." : ""}
                </div>
              </div>
            </div>
            <PlayerUsers globalListenMode={listenModeCfg} />
          </div>
        );
      })()}
    </>
  );
}
