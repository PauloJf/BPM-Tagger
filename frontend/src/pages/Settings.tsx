import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useTitle } from "../hooks/useTitle";
import PageHeader from "../components/PageHeader";
import { useSettings } from "../components/settings/shared";
import { AppearanceCard, ArtworkCard, LyricsCard } from "../components/settings/GeneralCards";
import { AdminSignInCard, LoginProtectionCard, PeopleCard, TwoFactorCard } from "../components/settings/PeopleCards";
import { IsrcCard, LibraryCard, ModeCard, ScanCard } from "../components/settings/LibraryCards";
import { PlaybackCard, RatingsCard, RunCard } from "../components/settings/PlaybackCards";
import { GrabberCard, NavidromeCard, NotificationsCard, SubsonicCard } from "../components/settings/IntegrationCards";
import { DeletedCard, RestartCard, TrashCard, VersionCard } from "../components/settings/MaintenanceCards";

/** Settings grouped into tabs (General, People & security, Library & scanning,
 *  Playback & Run, Integrations, Maintenance), sections listed in page order.
 *  Every card is its own component under components/settings/. Each card keeps its #sec- anchor;
 *  a link to a card opens the tab that holds it. */
export const SETTINGS_GROUPS: { id: string; label: string; sections: [string, string][] }[] = [
  { id: "general", label: "General", sections: [
    ["sec-appearance", "Appearance"], ["sec-artwork", "Artwork"], ["sec-lyrics", "Lyrics"],
  ] },
  { id: "people", label: "People & security", sections: [
    ["sec-password", "Admin sign-in"], ["sec-2fa", "Two-factor"],
    ["sec-login-protection", "Login protection"], ["sec-run-access", "People & player access"],
  ] },
  { id: "library", label: "Library & scanning", sections: [
    ["sec-library", "Library"], ["sec-scan", "Scan Behavior"], ["sec-mode", "Operating Mode"], ["sec-isrc", "ISRC"],
  ] },
  { id: "playback", label: "Playback & Run", sections: [
    ["sec-ratings", "Ratings & picking"], ["sec-playback", "Playback"], ["sec-run", "Run Mode"],
  ] },
  { id: "integrations", label: "Integrations", sections: [
    ["sec-ntfy", "Notifications"], ["sec-navidrome", "Navidrome"], ["sec-grabber", "Music Grabber"],
    ["sec-subsonic", "Subsonic API"],
  ] },
  { id: "maintenance", label: "Maintenance", sections: [
    ["sec-trash", "Trash"], ["sec-deleted", "Deleted Tracks"], ["sec-version", "Version"], ["sec-restart", "Restart"],
  ] },
];

/** The tab to open: ?tab=<id>, or the tab holding a #sec- anchor, else General. */
export function settingsTabFor(search: string, hash: string): string {
  const want = new URLSearchParams(search).get("tab");
  if (want && SETTINGS_GROUPS.some((g) => g.id === want)) return want;
  const anchor = hash.replace(/^#/, "");
  return SETTINGS_GROUPS.find((g) => g.sections.some(([id]) => id === anchor))?.id ?? "general";
}

export default function Settings() {
  useTitle("Settings");
  const location = useLocation();
  const navigate = useNavigate();
  const { isLoading } = useSettings();
  const [tab, setTab] = useState(() => settingsTabFor(location.search, location.hash));
  // A #sec- link (sidebar, or from elsewhere) opens the tab holding that card.
  useEffect(() => {
    if (!location.hash) return;
    setTab(settingsTabFor(location.search, location.hash));
    // The card only shows once its tab has rendered; scroll to it then.
    const id = location.hash.slice(1);
    const t = window.setTimeout(() => document.getElementById(id)?.scrollIntoView?.({ block: "start" }), 0);
    return () => window.clearTimeout(t);
  }, [location.search, location.hash]);
  const openTab = (id: string) => {
    setTab(id);
    navigate({ search: `?tab=${id}`, hash: "" }, { replace: true });
    window.scrollTo({ top: 0 });
  };

  if (isLoading) return <p style={{ color: "var(--muted)" }}>Loading settings…</p>;

  const activeIds = (SETTINGS_GROUPS.find((g) => g.id === tab) ?? SETTINGS_GROUPS[0]).sections.map(([id]) => id);


  return (
    <>
      <PageHeader
        title="Settings"
        subtitle={<>Change behaviour at runtime — saved to <span style={{ fontFamily: "var(--mono)", color: "var(--text)" }}>settings.json</span>.</>}
      />

      <div className="settings-layout">
        <nav className="settings-sidebar" aria-label="Settings sections">
          {SETTINGS_GROUPS.map((g) => (
            <div key={g.id} className="settings-group">
              <button type="button" className={"settings-group-btn" + (g.id === tab ? " active" : "")}
                aria-current={g.id === tab ? "page" : undefined} onClick={() => openTab(g.id)}>
                {g.label}
              </button>
              {g.id === tab && g.sections.length > 1 && g.sections.map(([id, label]) => (
                <a key={id} href={`#${id}`} className="settings-sub">{label}</a>
              ))}
            </div>
          ))}
        </nav>

        <div className="settings-body">
          {/* Only the active tab's cards show; the rest stay mounted (their
              state survives switching tabs). */}
          <style>{`.settings-body > .settings-card:not(${activeIds.map((i) => "#" + i).join(", ")}) { display: none; }`}</style>
          <AppearanceCard />
          <LibraryCard />
          <AdminSignInCard />
          <TwoFactorCard />
          <LoginProtectionCard />
          <PeopleCard />
          <NotificationsCard />
          <ScanCard />
          <ModeCard />
          <NavidromeCard />
          <RatingsCard />
          <GrabberCard />
          <SubsonicCard />
          <PlaybackCard />
          <RunCard />
          <ArtworkCard />
          <LyricsCard />
          <IsrcCard />
          <TrashCard />
          <DeletedCard />
          <VersionCard />
          <RestartCard />
        </div>
      </div>
    </>
  );
}
