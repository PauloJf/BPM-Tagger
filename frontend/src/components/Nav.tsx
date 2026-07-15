import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useScan, type ScanState } from "../hooks/useScan";
import type { Progress } from "../lib/types";
import { useGrabberStatus } from "../hooks/useGrabberStatus";
import { applyTheme, type Theme } from "../lib/theme";
import BpmMark from "./BpmMark";

/* 15px stroke icons, one per nav destination. */
const ic = { width: 15, height: 15, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" } as const;
const IconLibrary = () => (
  <svg {...ic}><path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" /></svg>
);
const IconPlaylists = () => (
  <svg {...ic}><path d="M3 6h11M3 11h11M3 16h6" /><path d="M18 8v8" /><circle cx="15.5" cy="16" r="2.5" /></svg>
);
const IconReview = () => (
  <svg {...ic}><path d="M22 12h-4l-3 8L9 4l-3 8H2" /></svg>
);
const IconRun = () => (
  <svg {...ic}><circle cx="12" cy="14" r="7" /><path d="M12 14v-4M9 2h6M12 2v3" /></svg>
);
const IconDuplicates = () => (
  <svg {...ic}><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>
);
const IconAddMusic = () => (
  <svg {...ic}><path d="M9 17V5l10-1.5V11" /><circle cx="6.5" cy="17" r="2.5" /><path d="M18 15v6M15 18h6" /></svg>
);
const IconSuggestions = () => (
  <svg {...ic}><path d="M12 3l1.9 4.6L18.5 9.5 13.9 11.4 12 16l-1.9-4.6L5.5 9.5 10.1 7.6z" /><path d="M18 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" /></svg>
);
const IconQueue = () => (
  <svg {...ic}><path d="M12 3v12M6 11l6 6 6-6" /><path d="M4 21h16" /></svg>
);
const IconInbox = () => (
  <svg {...ic}><path d="M22 13h-5l-2 3h-6l-2-3H2" /><path d="M5.5 5h13L22 13v5a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-5z" /></svg>
);
const IconStats = () => (
  <svg {...ic}><path d="M4 21v-8M10 21V4M16 21v-12M22 21v-5" /></svg>
);
const IconSettings = () => (
  <svg {...ic}><path d="M5 21v-7M5 10V3M12 21v-9M12 8V3M19 21v-5M19 12V3" /><path d="M2 14h6M9 8h6M16 16h6" /></svg>
);
const IconAbout = () => (
  <svg {...ic}><circle cx="12" cy="12" r="9" /><path d="M12 8h.01M12 12v5" /></svg>
);
const IconLogout = () => (
  <svg {...ic}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5M21 12H9" /></svg>
);

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
    <button className="btn btn-bare btn-sm" style={{ padding: 6 }} onClick={toggle} aria-label="Toggle theme" title="Toggle theme">
      {icon}
    </button>
  );
}

interface NavItem { to: string; label: string; match: string[]; icon: () => JSX.Element; badge?: "review" | "inbox" }
interface NavSection { label: string; items: NavItem[] }

/** Grouped navigation: tagging, grabber and app chrome each get a section. */
function buildSections(grabberEnabled: boolean): NavSection[] {
  const library: NavItem[] = [
    { to: "/tracks", label: "Library", icon: IconLibrary, match: ["/tracks", "/artists", "/albums", "/artist", "/album", "/track"] },
    { to: "/run", label: "Run", icon: IconRun, match: ["/run"] },
  ];
  if (grabberEnabled) library.push({ to: "/playlists", label: "Playlists", icon: IconPlaylists, match: ["/playlists", "/playlist"] });

  const sections: NavSection[] = [
    { label: "Library", items: library },
    {
      label: "Tagging",
      items: [
        { to: "/review", label: "BPM Review", icon: IconReview, match: ["/review"], badge: "review" },
        { to: "/duplicates", label: "Duplicates", icon: IconDuplicates, match: ["/duplicates", "/compare"] },
      ],
    },
  ];
  if (grabberEnabled) {
    sections.push({
      label: "Grabber",
      items: [
        { to: "/search", label: "Add Music", icon: IconAddMusic, match: ["/search"] },
        { to: "/suggestions", label: "Suggestions", icon: IconSuggestions, match: ["/suggestions"] },
        { to: "/queue", label: "Queue", icon: IconQueue, match: ["/queue"] },
        { to: "/inbox", label: "Inbox", icon: IconInbox, match: ["/inbox"], badge: "inbox" },
      ],
    });
  }
  sections.push({
    label: "System",
    items: [
      { to: "/stats", label: "Stats", icon: IconStats, match: ["/stats"] },
      { to: "/settings", label: "Settings", icon: IconSettings, match: ["/settings"] },
      { to: "/about", label: "About", icon: IconAbout, match: ["/about"] },
    ],
  });
  return sections;
}

function isActivePath(pathname: string, match: string[]): boolean {
  return match.some((p) => pathname === p || pathname.startsWith(p + "/"));
}

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
      <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: mobile ? 13 : 12, flexShrink: 0 }} title={info.label}>
        <Dot color={info.color} pulsing={info.pulsing} />
        <span className="scan-label" style={{ color: info.labelColor, fontWeight: 500 }}>{info.label}</span>
      </span>
      {state === "idle" && (
        <button className={btnClass} style={{ color: "var(--ok-fg)" }} onClick={() => act("start")} title="Start scan">
          ▶<span className="btn-label"> Start scan</span>
        </button>
      )}
      {state === "analysing" && (
        <>
          <button className={btnClass} onClick={() => act("pause")} title="Pause scan">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="4" width="4" height="16" rx="1" />
              <rect x="14" y="4" width="4" height="16" rx="1" />
            </svg>
            {mobile && " Pause"}
          </button>
          <button className={mobile ? "btn btn-danger btn-sm" : "btn btn-bare btn-sm"} style={mobile ? {} : { color: "var(--err-fg)" }} onClick={() => act("stop")} title="Stop scan">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
              <rect x="5" y="5" width="14" height="14" rx="2" />
            </svg>
            {mobile && " Stop"}
          </button>
        </>
      )}
      {state === "paused" && (
        <button className={btnClass} style={{ color: "var(--ok-fg)" }} onClick={() => act("resume")} title="Resume scan">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
            <polygon points="6,4 20,12 6,20" />
          </svg>
          {mobile && " Resume"}
        </button>
      )}
    </>
  );
}

/** Desktop sidebar footer: the scan status promoted to a small block — state +
 *  live count on top, a thin progress bar while analysing, full-width controls. */
function SidebarScan({ state, act, progress }: { state: ScanState; act: (a: "start" | "pause" | "resume" | "stop") => void; progress: Progress | null }) {
  const info = stateColor(state);
  const scanning = state === "analysing" && !!progress && progress.total > 0;
  const pct = scanning ? Math.round((progress!.completed / progress!.total) * 100) : 0;
  return (
    <div className="sidebar-scan">
      <div className="sidebar-scan-head">
        <Dot color={info.color} pulsing={info.pulsing} />
        <span className="scan-label" style={{ color: info.labelColor, fontWeight: 500, fontSize: 12 }}>{info.label}</span>
        {scanning && <span className="sidebar-scan-count">{progress!.completed} / {progress!.total}</span>}
      </div>
      {scanning && (
        <div className="sidebar-scan-track"><div className="sidebar-scan-fill" style={{ width: `${pct}%` }} /></div>
      )}
      <div className="sidebar-scan-btns">
        {state === "idle" && (
          <button className="btn btn-ghost btn-sm" style={{ color: "var(--ok-fg)" }} onClick={() => act("start")} title="Start scan">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><polygon points="6,4 20,12 6,20" /></svg>
            <span className="btn-label"> Start scan</span>
          </button>
        )}
        {state === "analysing" && (
          <>
            <button className="btn btn-ghost btn-sm" onClick={() => act("pause")} title="Pause scan">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></svg>
              <span className="btn-label"> Pause</span>
            </button>
            <button className="btn btn-danger btn-sm" onClick={() => act("stop")} title="Stop scan">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2" /></svg>
              <span className="btn-label"> Stop</span>
            </button>
          </>
        )}
        {state === "paused" && (
          <button className="btn btn-ghost btn-sm" style={{ color: "var(--ok-fg)" }} onClick={() => act("resume")} title="Resume scan">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><polygon points="6,4 20,12 6,20" /></svg>
            <span className="btn-label"> Resume</span>
          </button>
        )}
        {state === "stopping" && <span style={{ fontSize: 12, color: "var(--err-fg)" }}>Stopping…</span>}
      </div>
    </div>
  );
}

function Logo() {
  return (
    <NavLink to="/tracks" className="nav-logo">
      <div className="nav-logo-tile">
        <BpmMark size={17} />
      </div>
      <div className="nav-logo-text">
        <span className="nav-logo-title">BPM Tagger</span>
        <span className="nav-logo-sub">for navidrome</span>
      </div>
    </NavLink>
  );
}

const COLLAPSE_KEY = "bpmtagger.sidebarCollapsed";

export default function Nav() {
  const { reviewCount, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === "1");
  const location = useLocation();
  const panelRef = useRef<HTMLDivElement>(null);
  const { state, act, progress } = useScan();
  const dot = stateColor(state);
  const grabber = useGrabberStatus();

  const sections = buildSections(!!grabber.data?.enabled);
  const inboxCount = grabber.data?.inbox_count || 0;
  const badgeCount = (item: NavItem) =>
    item.badge === "review" ? reviewCount : item.badge === "inbox" ? inboxCount : 0;

  // The collapsed state lives on <body> so .app-main and .player-bar (outside
  // this component) can follow the sidebar width via --sidebar-w.
  useEffect(() => {
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
    return () => document.body.classList.remove("sidebar-collapsed");
  }, [collapsed]);

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
    <>
      {/* Desktop sidebar */}
      <aside className="app-sidebar">
        <Logo />
        {sections.map((s) => (
          <div key={s.label} className="sidebar-section">
            <div className="sidebar-section-label">{s.label}</div>
            {s.items.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                title={l.label}
                className={"sidebar-link" + (isActivePath(location.pathname, l.match) ? " active" : "")}
              >
                <l.icon />
                <span className="sidebar-link-label">{l.label}</span>
                {badgeCount(l) > 0 && <span className="nav-badge">{badgeCount(l)}</span>}
              </NavLink>
            ))}
          </div>
        ))}
        <button
          className="sidebar-collapse-btn"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
        >
          {collapsed ? (
            <svg {...ic}><path d="M6 17l5-5-5-5M13 17l5-5-5-5" /></svg>
          ) : (
            <svg {...ic}><path d="M11 17l-5-5 5-5M18 17l-5-5 5-5" /></svg>
          )}
        </button>
        <div className="sidebar-footer">
          <SidebarScan state={state} act={act} progress={progress} />
          <div className="sidebar-footer-row">
            <ThemeToggle />
            <button
              className="btn btn-bare btn-sm"
              style={{ color: "var(--err-fg)" }}
              onClick={() => logout()}
              title="Logout"
            >
              <IconLogout />
              <span className="btn-label"> Logout</span>
            </button>
          </div>
        </div>
      </aside>

      {/* Mobile top bar + panel */}
      <nav className="app-nav">
        <Logo />
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
          {sections.map((s) => (
            <div key={s.label}>
              <div className="nav-mobile-section">{s.label}</div>
              {s.items.map((l) => (
                <NavLink
                  key={l.to}
                  to={l.to}
                  className={"nav-mobile-link" + (isActivePath(location.pathname, l.match) ? " active" : "")}
                >
                  <l.icon />
                  {l.label}
                  {badgeCount(l) > 0 && <span className="nav-badge">{badgeCount(l)}</span>}
                </NavLink>
              ))}
            </div>
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
            <IconLogout />
            Logout
          </button>
        </div>
      </nav>
    </>
  );
}
