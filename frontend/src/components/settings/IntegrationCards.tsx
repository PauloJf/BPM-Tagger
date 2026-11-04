import { useEffect, useState } from "react";
import { Toggle } from "../Toggle";
import MusicGrabberSettings from "../MusicGrabberSettings";
import SubsonicSettings from "../SubsonicSettings";
import { api } from "../../lib/api";
import { SaveButton, fieldLabel, readers, useSaveSection, useSettings, useTestConn, type Saved } from "./shared";

/** Settings → Integrations tab: ntfy, Navidrome, Music Grabber, Subsonic API. */

export function NotificationsCard() {
  const { cfg } = useSettings();
  const saveSection = useSaveSection();
  const { testMsg, testConn } = useTestConn();
  const [ntfy, setNtfy] = useState({ url: "", topic: "", batch: 10, interval: 300, notifyReview: true });
  const [ntfySaved, setNtfySaved] = useState<Saved>("");
  useEffect(() => {
    if (!cfg) return;
    const { s, n, b } = readers(cfg);
    setNtfy({ url: s("ntfy_url"), topic: s("ntfy_topic"), batch: n("ntfy_batch_size", 10), interval: n("ntfy_min_interval", 300), notifyReview: b("ntfy_notify_review", true) });
  }, [cfg]);

  return (
    <>
      {/* Notifications */}
      <div id="sec-ntfy" className="settings-card card">
        <div className="settings-card-header">
          <h2>Notifications (ntfy)</h2>
          <p>Push notifications for scan progress and review counts.</p>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/ntfy", { ntfy_url: ntfy.url, ntfy_topic: ntfy.topic, ntfy_batch_size: ntfy.batch, ntfy_min_interval: ntfy.interval, ntfy_notify_review: ntfy.notifyReview }, setNtfySaved); }}>
          <div className="settings-fields">
            <div className="field-row">
              {fieldLabel("ntfy server URL")}
              <input type="text" value={ntfy.url} onChange={(e) => setNtfy({ ...ntfy, url: e.target.value })} placeholder="https://ntfy.sh" style={{ maxWidth: 340, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
            </div>
            <div className="field-row">
              {fieldLabel("ntfy topic")}
              <input type="text" value={ntfy.topic} onChange={(e) => setNtfy({ ...ntfy, topic: e.target.value })} placeholder="my-bpm-alerts" style={{ maxWidth: 340, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
            </div>
            <div className="field-row">
              {fieldLabel("Batch size", "tracks per notification")}
              <input type="number" min={1} max={100} value={ntfy.batch} onChange={(e) => setNtfy({ ...ntfy, batch: +e.target.value })} style={{ width: 90 }} />
            </div>
            <div className="field-row">
              {fieldLabel("Min interval (s)", "between batch notifications")}
              <input type="number" min={10} max={86400} value={ntfy.interval} onChange={(e) => setNtfy({ ...ntfy, interval: +e.target.value })} style={{ width: 100 }} />
            </div>
            <div className="field-row">
              {fieldLabel("Notify on review", 'include "N need review" in scan summary')}
              <Toggle on={ntfy.notifyReview} onChange={(v) => setNtfy({ ...ntfy, notifyReview: v })} label="Notify on review" />
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <SaveButton state={ntfySaved} label="Save Notification Settings" />
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => testConn("ntfy", "/api/settings/test-ntfy", { ntfy_url: ntfy.url, ntfy_topic: ntfy.topic })}>Test</button>
              {testMsg.ntfy && <span style={{ fontSize: 12, color: testMsg.ntfy.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{testMsg.ntfy.text}</span>}
            </div>
          </div>
        </form>
      </div>
    </>
  );
}

export function NavidromeCard() {
  const { cfg } = useSettings();
  const saveSection = useSaveSection();
  const { testMsg, testConn } = useTestConn();
  const [nav, setNav] = useState({ url: "", user: "", pass: "", starSync: false, scrobble: false, ratingSync: false });
  const [navSaved, setNavSaved] = useState<Saved>("");
  const [syncInterval, setSyncInterval] = useState(0);
  const [syncIntSaved, setSyncIntSaved] = useState<Saved>("");
  const [starSyncMsg, setStarSyncMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [starSyncing, setStarSyncing] = useState(false);
  const [ratingSyncing, setRatingSyncing] = useState(false);
  const [ratingSyncMsg, setRatingSyncMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [playPullMsg, setPlayPullMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [playPulling, setPlayPulling] = useState(false);
  useEffect(() => {
    if (!cfg) return;
    const { s, n, b } = readers(cfg);
    setNav({ url: s("navidrome_url"), user: s("navidrome_user"), pass: s("navidrome_pass"), starSync: b("navidrome_star_sync", false), scrobble: b("navidrome_scrobble", false), ratingSync: b("navidrome_sync_ratings", false) });
    setSyncInterval(n("sync_interval_minutes", 0));
  }, [cfg]);

  async function pullPlayCounts() {
    setPlayPulling(true);
    setPlayPullMsg(null);
    try {
      const r = await api.post<{ ok: boolean; error?: string; remote_songs?: number;
                                 matched?: number; unmatched_remote?: number }>(
        "/api/settings/sync-play-counts");
      if (r.ok) {
        const bits = [`${r.matched ?? 0} of ${r.remote_songs ?? 0} songs matched`];
        if (r.unmatched_remote) bits.push(`${r.unmatched_remote} remote-only`);
        setPlayPullMsg({ ok: true, text: `Play counts pulled — ${bits.join(", ")}` });
      } else {
        setPlayPullMsg({ ok: false, text: r.error || "Pull failed" });
      }
    } catch (e) {
      setPlayPullMsg({ ok: false, text: e instanceof Error ? e.message : "Pull failed" });
    } finally {
      setPlayPulling(false);
    }
  }

  async function syncStars() {
    setStarSyncing(true);
    setStarSyncMsg(null);
    try {
      const r = await api.post<{ ok: boolean; error?: string; pushed?: number; pulled?: number;
                                 conflicts?: number; unmatched_remote?: number; failed?: number }>(
        "/api/settings/sync-stars");
      if (r.ok) {
        const bits = [`pulled ${r.pulled ?? 0}`, `pushed ${r.pushed ?? 0}`];
        if (r.conflicts) bits.push(`${r.conflicts} conflicts`);
        if (r.unmatched_remote) bits.push(`${r.unmatched_remote} remote stars unmatched`);
        if (r.failed) bits.push(`${r.failed} failed (will retry next sync)`);
        setStarSyncMsg({ ok: !r.failed, text: `Stars synced — ${bits.join(", ")}` });
      } else {
        setStarSyncMsg({ ok: false, text: r.error || "Sync failed" });
      }
    } catch (e) {
      setStarSyncMsg({ ok: false, text: e instanceof Error ? e.message : "Sync failed" });
    } finally {
      setStarSyncing(false);
    }
  }

  async function syncRatings() {
    setRatingSyncing(true);
    setRatingSyncMsg(null);
    try {
      const r = await api.post<{ ok: boolean; error?: string; remote_songs?: number; matched?: number;
                                 pulled?: number; pushed?: number; conflicts?: number; errors?: number }>(
        "/api/settings/sync-ratings");
      if (r.ok) {
        const bits = [`${r.matched ?? 0} of ${r.remote_songs ?? 0} songs matched`, `pulled ${r.pulled ?? 0}`, `pushed ${r.pushed ?? 0}`];
        if (r.conflicts) bits.push(`${r.conflicts} conflicts`);
        if (r.errors) bits.push(`${r.errors} errors`);
        setRatingSyncMsg({ ok: !r.errors, text: `Ratings synced — ${bits.join(", ")}` });
      } else {
        setRatingSyncMsg({ ok: false, text: r.error || "Sync failed" });
      }
    } catch (e) {
      setRatingSyncMsg({ ok: false, text: e instanceof Error ? e.message : "Sync failed" });
    } finally {
      setRatingSyncing(false);
    }
  }

  return (
    <>
      {/* Navidrome */}
      <div id="sec-navidrome" className="settings-card card">
        <div className="settings-card-header">
          <h2>Navidrome Integration</h2>
          <p>Trigger a library rescan after every scan pass, keep stars in sync both ways, scrobble plays, and pull play counts.</p>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); saveSection("/api/settings/navidrome", { navidrome_url: nav.url, navidrome_user: nav.user, navidrome_pass: nav.pass, navidrome_star_sync: nav.starSync, navidrome_scrobble: nav.scrobble, navidrome_sync_ratings: nav.ratingSync }, setNavSaved); }}>
          <div className="settings-fields">
            <div className="field-row">
              {fieldLabel("Navidrome URL")}
              <input type="text" value={nav.url} onChange={(e) => setNav({ ...nav, url: e.target.value })} placeholder="http://navidrome:4533" style={{ maxWidth: 340, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
            </div>
            <div className="field-row">
              {fieldLabel("Username")}
              <input type="text" value={nav.user} onChange={(e) => setNav({ ...nav, user: e.target.value })} autoComplete="off" style={{ maxWidth: 280, width: "100%", fontFamily: "var(--mono)", fontSize: 12 }} />
            </div>
            <div className="field-row">
              {fieldLabel("Password")}
              <input type="password" value={nav.pass} onChange={(e) => setNav({ ...nav, pass: e.target.value })} autoComplete="off" style={{ maxWidth: 280, width: "100%" }} />
            </div>
            <div className="field-row">
              {fieldLabel("Two-way star sync", nav.ratingSync
                ? "Rating sync is on, so this just mirrors the admin's derived star (rating ≥ 4) out to Navidrome — push-only while rating sync is enabled. Manual for now: save, then use Sync stars now."
                : "Reconcile the app's starred tracks with Navidrome's favourites in both directions — stars set here push out, stars set in Navidrome pull in. Manual for now: save, then use Sync stars now.")}
              <Toggle on={nav.starSync} onChange={(v) => setNav({ ...nav, starSync: v })} label="Enable star sync" />
            </div>
            <div className="field-row">
              {fieldLabel("Two-way rating sync", "Reconcile the admin's 1-5 star ratings with Navidrome's own rating in both directions. Admin ratings only — player ratings never sync. Manual for now: save, then use Sync ratings now.")}
              <Toggle on={nav.ratingSync} onChange={(v) => setNav({ ...nav, ratingSync: v })} label="Enable rating sync" />
            </div>
            <div className="field-row">
              {fieldLabel("Scrobble plays", "Report tracks played in the built-in player (Run mode included) to Navidrome once they pass the halfway mark — play counts and 'last played' stay accurate, and Navidrome forwards to Last.fm/ListenBrainz if you've connected them there.")}
              <Toggle on={nav.scrobble} onChange={(v) => setNav({ ...nav, scrobble: v })} label="Scrobble plays" />
            </div>
            <div className="field-row" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
              {fieldLabel("Background sync", "Minutes between automatic sync passes for Navidrome playlists, stars, and play counts. 0 = off (use the manual buttons only). Applies on the next restart; runs only in watch mode.")}
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="number" min={0} max={1440} step={5} value={syncInterval}
                  onChange={(e) => setSyncInterval(Math.max(0, Math.min(1440, Number(e.target.value) || 0)))}
                  style={{ width: 90, fontFamily: "var(--mono)", textAlign: "center" }}
                />
                <span style={{ color: "var(--muted)", fontSize: 13 }}>min</span>
                <button
                  type="button" className="btn btn-ghost btn-sm"
                  onClick={() => saveSection("/api/settings/sync-interval", { sync_interval_minutes: syncInterval }, setSyncIntSaved)}
                >
                  {syncIntSaved === "saving" ? "Saving…" : syncIntSaved === "ok" ? "Saved ✓" : syncIntSaved === "err" ? "Error" : "Save interval"}
                </button>
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <SaveButton state={navSaved} label="Save Navidrome Settings" />
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => testConn("nav", "/api/settings/test-navidrome", { navidrome_url: nav.url, navidrome_user: nav.user, navidrome_pass: nav.pass })}>Test</button>
              {testMsg.nav && <span style={{ fontSize: 12, color: testMsg.nav.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{testMsg.nav.text}</span>}
              {nav.starSync && (
                <button type="button" className="btn btn-soft btn-sm" disabled={starSyncing || !nav.url || !nav.user} onClick={syncStars}>
                  {starSyncing ? "Syncing…" : "Sync stars now"}
                </button>
              )}
              {starSyncMsg && <span style={{ fontSize: 12, color: starSyncMsg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{starSyncMsg.text}</span>}
              {nav.ratingSync && (
                <button type="button" className="btn btn-soft btn-sm" disabled={ratingSyncing || !nav.url || !nav.user} onClick={syncRatings}>
                  {ratingSyncing ? "Syncing…" : "Sync ratings now"}
                </button>
              )}
              {ratingSyncMsg && <span style={{ fontSize: 12, color: ratingSyncMsg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{ratingSyncMsg.text}</span>}
              <button type="button" className="btn btn-soft btn-sm" disabled={playPulling || !nav.url || !nav.user} onClick={pullPlayCounts}>
                {playPulling ? "Pulling…" : "Pull play counts"}
              </button>
              {playPullMsg && <span style={{ fontSize: 12, color: playPullMsg.ok ? "var(--ok-fg)" : "var(--err-fg)" }}>{playPullMsg.text}</span>}
            </div>
          </div>
        </form>
      </div>
    </>
  );
}

export function GrabberCard() {

  return (
    <>
      {/* Music Grabber link (optional) */}
      <div id="sec-grabber" className="settings-card card">
        <div className="settings-card-header">
          <h2>Music Grabber</h2>
          <p>Optional. Connect a Music Grabber (a separate app) to request missing tracks from a playlist's Missing rows and from the Related panel. New files arrive through the music folder like any other.</p>
        </div>
        <MusicGrabberSettings />
      </div>
    </>
  );
}

export function SubsonicCard() {

  return (
    <>
      {/* Subsonic API (optional) */}
      <div id="sec-subsonic" className="settings-card card">
        <div className="settings-card-header">
          <h2>Subsonic API</h2>
          <p>Optional. Lets Subsonic apps browse, stream, star and scrobble your library straight from BPM Tagger, with detected BPM included. Uses its own credentials, never your web password.</p>
        </div>
        <SubsonicSettings />
      </div>
    </>
  );
}
