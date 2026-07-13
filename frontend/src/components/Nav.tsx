import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useScan, type ScanState } from "../hooks/useScan";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import { applyTheme, type Theme } from "../lib/theme";

function ThemeToggle({ mobile }: { mobile?: boolean }) {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme as Theme) || "dark");
  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
  };
  const icon = theme === "dark" ? (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  ) : (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z" />
    </svg>
  );
  if (mobile) {
    return (
      <button className="nav-mobile-link" style={{ width: "100%", border: "none", background: "none", cursor: "pointer", textAlign: "left" }} onClick={toggle}>
        {icon}
        {theme === "dark" ? "Light mode" : "Dark mode"}
      </button>
    );
  }
  return (
    <button className="nav-item btn-bare nav-desktop-only" style={{ border: "none", background: "none" }} onClick={toggle} aria-label="Toggle theme" title="Toggle theme">
      {icon}
    </button>
  );
}


interface NavItem { to: string; label: string; badge?: boolean; badgeCount?: number }

const BASE_LINKS: NavItem[] = [
  { to: "/tracks", label: "Library" },
  { to: "/review", label: "Review", badge: true },
  { to: "/duplicates", label: "Duplicates" },
  { to: "/stats", label: "Stats" },
  { to: "/settings", label: "Settings" },
  { to: "/about", label: "About" },
];

function stateColor(s: ScanState): { color: string; pulsing: boolean; label: string; labelColor: string } {
  switch (s) {
    case "analysing":
      return { color: "var(--accent)", pulsing: true, label: "Analyzing", labelColor: "var(--accent-2)" };
    case "paused":
      return { color: "var(--warn-fg)", pulsing: false, label: "Paused", labelColor: "var(--warn-fg)" };
    case "stopping":
      return { color: "var(--err-fg)", pulsing: true, label: "Stopping…", labelColor: "var(--err-fg)" };
    default:
      return { color: "var(--muted)", pulsing: false, label: "Idle", labelColor: "var(--muted)" };
  }
}

function Dot({ color, pulsing }: { color: string; pulsing: boolean }) {
  return (
    <span
      className={"scan-dot" + (pulsing ? " pulsing" : "")}
      style={{ background: color, boxShadow: `0 0 0 3px ${color}33` }}
    />
  );
}

function ScanControls({ state, act, mobile }: { state: ScanState; act: (a: "start" | "pause" | "resume" | "stop") => void; mobile?: boolean }) {
  const info = stateColor(state);
  const btnClass = mobile ? "btn btn-ghost btn-sm" : "btn btn-bare btn-sm";
  return (
    <>
      <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: mobile ? 13 : 12, flexShrink: 0 }}>
        <Dot color={info.color} pulsing={info.pulsing} />
        <span style={{ color: info.labelColor, fontWeight: 500 }}>{info.label}</span>
      </span>
      {state === "idle" && (
        <button className={btnClass} style={{ color: "var(--ok-fg)" }} onClick={() => act("start")}>
          ▶ Start scan
        </button>
      )}
      {state === "analysing" && (
        <>
          <button className={btnClass} onClick={() => act("pause")}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="4" width="4" height="16" rx="1" />
              <rect x="14" y="4" width="4" height="16" rx="1" />
            </svg>
            {mobile && " Pause"}
          </button>
          <button className={mobile ? "btn btn-danger btn-sm" : "btn btn-bare btn-sm"} style={mobile ? {} : { color: "var(--err-fg)" }} onClick={() => act("stop")}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
              <rect x="5" y="5" width="14" height="14" rx="2" />
            </svg>
            {mobile && " Stop"}
          </button>
        </>
      )}
      {state === "paused" && (
        <button className={btnClass} style={{ color: "var(--ok-fg)" }} onClick={() => act("resume")}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
            <polygon points="6,4 20,12 6,20" />
          </svg>
          {mobile && " Resume"}
        </button>
      )}
    </>
  );
}

export default function Nav() {
  const { reviewCount, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const panelRef = useRef<HTMLDivElement>(null);
  const { state, act } = useScan();
  const dot = stateColor(state);
  const grabber = useGrabberStatus();

  // Insert Playlists + Queue + Inbox after Library when the grabber is enabled.
  const links: NavItem[] = [...BASE_LINKS];
  if (grabber.data?.enabled) {
    links.splice(1, 0,
      { to: "/playlists", label: "Playlists" },
      { to: "/search", label: "Search" },
      { to: "/queue", label: "Queue" },
      { to: "/inbox", label: "Inbox", badgeCount: grabber.data.inbox_count || 0 });
  }

  // Close the mobile panel on navigation.
  useEffect(() => setOpen(false), [location.pathname]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!panelRef.current) return;
      const t = e.target as Element;
      if (!t.closest("#nav-mobile-panel") && !t.closest("#nav-hamburger")) setOpen(false);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [open]);

  return (
    <nav className="app-nav">
      <NavLink to="/tracks" className="nav-logo">
        <div className="nav-logo-tile">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none">
            <rect x="3" y="10" width="2.4" height="4" rx="1" fill="white" opacity="0.7" />
            <rect x="7" y="6" width="2.4" height="12" rx="1" fill="white" opacity="0.9" />
            <rect x="11" y="3" width="2.4" height="18" rx="1" fill="white" />
            <rect x="15" y="6" width="2.4" height="12" rx="1" fill="white" opacity="0.9" />
            <rect x="19" y="10" width="2.4" height="4" rx="1" fill="white" opacity="0.7" />
          </svg>
        </div>
        <div className="nav-logo-text">
          <span className="nav-logo-title">BPM Tagger</span>
          <span className="nav-logo-sub">for navidrome</span>
        </div>
      </NavLink>

      <div className="nav-divider nav-desktop-only" />

      {links.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          className={({ isActive }) => "nav-item nav-desktop-only" + (isActive ? " active" : "")}
        >
          {l.label}
          {l.badge && reviewCount > 0 && <span className="nav-badge">{reviewCount}</span>}
          {l.badgeCount ? <span className="nav-badge">{l.badgeCount}</span> : null}
        </NavLink>
      ))}

      <span className="nav-spacer nav-desktop-only" />

      <span className="scan-controls nav-desktop-only" style={{ marginRight: 8 }}>
        <ScanControls state={state} act={act} />
      </span>

      <ThemeToggle />

      <button
        className="nav-item btn-bare nav-desktop-only"
        style={{ color: "var(--err-fg)", border: "none", background: "none" }}
        onClick={() => logout()}
      >
        Logout
      </button>

      {/* Mobile: status dot + hamburger */}
      <span
        className="scan-dot nav-scan-dot-mobile"
        style={{ background: dot.color, marginLeft: "auto", marginRight: 10, boxShadow: `0 0 0 3px ${dot.color}33` }}
      />
      <button
        id="nav-hamburger"
        className="nav-hamburger"
        aria-label="Toggle menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M1 1 L13 13 M13 1 L1 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        ) : (
          <svg width="18" height="13" viewBox="0 0 18 13" fill="none">
            <rect y="0" width="18" height="2" rx="1" fill="currentColor" />
            <rect y="5.5" width="18" height="2" rx="1" fill="currentColor" />
            <rect y="11" width="18" height="2" rx="1" fill="currentColor" />
          </svg>
        )}
      </button>

      <div
        id="nav-mobile-panel"
        ref={panelRef}
        className={"nav-mobile-panel" + (open ? " open" : "")}
        role="navigation"
        aria-label="Mobile navigation"
      >
        {links.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            className={({ isActive }) => "nav-mobile-link" + (isActive ? " active" : "")}
          >
            {l.label}
            {l.badge && reviewCount > 0 && <span className="nav-badge">{reviewCount}</span>}
          </NavLink>
        ))}
        <div className="nav-mobile-sep" />
        <div style={{ padding: "6px 10px", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <ScanControls state={state} act={act} mobile />
        </div>
        <div className="nav-mobile-sep" />
        <ThemeToggle mobile />
        <button
          className="nav-mobile-link"
          style={{ width: "100%", color: "var(--err-fg)", border: "none", background: "none", cursor: "pointer", textAlign: "left" }}
          onClick={() => logout()}
        >
          Logout
        </button>
      </div>
    </nav>
  );
}
