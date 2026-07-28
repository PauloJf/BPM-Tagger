import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./lib/auth";
import Nav, { PlayerNav, PlayerMobileBar } from "./components/Nav";
import PlayerBar from "./components/PlayerBar";
import InstallPingCard from "./components/InstallPingCard";
import WhatsNew from "./components/WhatsNew";
import Login from "./pages/Login";
import Tracks from "./pages/Tracks";
import TrackDetail from "./pages/TrackDetail";
import TrackCompare from "./pages/TrackCompare";
import Duplicates from "./pages/Duplicates";
import Artist from "./pages/Artist";
import Artists from "./pages/Artists";
import Album from "./pages/Album";
import Albums from "./pages/Albums";
import Playlists from "./pages/Playlists";
import PlaylistDetail from "./pages/PlaylistDetail";
import Queue from "./pages/Queue";
import Inbox from "./pages/Inbox";
import Search from "./pages/Search";
import Suggestions from "./pages/Suggestions";
import Review from "./pages/Review";
import Run from "./pages/Run";
import Listen from "./pages/Listen";
import Cadence from "./pages/Cadence";
import Stats from "./pages/Stats";
import Settings from "./pages/Settings";
import About from "./pages/About";
import PlayerAbout from "./pages/PlayerAbout";

function Layout({ children }: { children: React.ReactNode }) {
  // Run and Listen carry their own full-screen transport — the global bar
  // would duplicate it and steal vertical space on phones. Both also take the
  // container--run treatment (tight padding, no player-bar reserve) that their
  // one-screen mobile layout is budgeted against.
  const pathname = useLocation().pathname;
  const ownTransport = pathname === "/run" || pathname === "/listen";
  return (
    <>
      <Nav />
      <div className="app-main">
        <div className={"container page-enter" + (ownTransport ? " container--run" : "")}>{children}</div>
      </div>
      {!ownTransport && <PlayerBar />}
      <InstallPingCard />
      <WhatsNew />
    </>
  );
}

// Locked-down shell for the run-only ("player") role: only the kiosk pages are
// routable — the backend independently refuses any endpoint those pages don't
// need. Which pages, and where the kiosk lands, is the admin's listen-mode
// setting (from /api/me):
//   off     → Run + About (the original kiosk)
//   on      → + Listen, landing stays /run
//   default → + Listen, landing is /listen
//   only    → Listen + About, /run itself redirects (pure jukebox)
// A slim PlayerNav fills the sidebar slot on desktop; below 1100px each page
// renders its own compact top bar. The global player bar stays off (Run and
// Listen carry their own transport).
function PlayerLayout() {
  const { listenMode } = useAuth();
  const pathname = useLocation().pathname;
  const ownTransport = pathname === "/run" || pathname === "/listen";
  const hasListen = listenMode !== "off";
  const hasRun = listenMode !== "only";
  const home = listenMode === "default" || listenMode === "only" ? "/listen" : "/run";
  return (
    <>
      <PlayerNav />
      {/* Player kiosk top bar, lifted out of the pages so it's a single sticky
          bar shared by the kiosk pages — consistent offset (no shift when
          switching pages) and it stays pinned while the page scrolls. Hidden
          ≥1101px, where the PlayerNav sidebar takes over. */}
      <PlayerMobileBar />
      <div className="app-main">
        <div className={"container page-enter" + (ownTransport ? " container--run" : "")}>
          <Routes>
            {hasRun && <Route path="/run" element={<Run />} />}
            {hasListen && <Route path="/listen" element={<Listen />} />}
            <Route path="/about" element={<PlayerAbout />} />
            <Route path="*" element={<Navigate to={home} replace />} />
          </Routes>
        </div>
      </div>
    </>
  );
}

function FullScreenLoader() {
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", color: "var(--muted)" }}>
      <span style={{ fontFamily: "var(--mono)", fontSize: 13, animation: "pulse 1.2s ease-in-out infinite" }}>
        Loading…
      </span>
    </div>
  );
}

export default function App() {
  const { ready, authenticated, role } = useAuth();
  const location = useLocation();

  if (!ready) return <FullScreenLoader />;

  if (!authenticated) {
    // Any route while logged out → Login (preserve intended path via state).
    if (location.pathname === "/login") return <Login />;
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  // Run-only role: every route collapses to the player shell. The client router
  // never mounts the other pages, and the backend blocks their APIs regardless.
  if (role === "player") return <PlayerLayout />;

  // Authenticated: /login redirects into the app.
  if (location.pathname === "/login") return <Navigate to="/tracks" replace />;

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/tracks" replace />} />
        <Route path="/tracks" element={<Tracks />} />
        <Route path="/track" element={<TrackDetail />} />
        <Route path="/compare" element={<TrackCompare />} />
        <Route path="/duplicates" element={<Duplicates />} />
        <Route path="/artists" element={<Artists />} />
        <Route path="/artist" element={<Artist />} />
        <Route path="/albums" element={<Albums />} />
        <Route path="/album" element={<Album />} />
        <Route path="/playlists" element={<Playlists />} />
        <Route path="/playlist" element={<PlaylistDetail />} />
        <Route path="/queue" element={<Queue />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/inbox/:id" element={<Inbox />} />
        <Route path="/search" element={<Search />} />
        <Route path="/suggestions" element={<Suggestions />} />
        <Route path="/review" element={<Review />} />
        <Route path="/run" element={<Run />} />
        <Route path="/listen" element={<Listen />} />
        {/* Cadence is admin-side only: it lives outside the player Routes block
            above (which bounces everything to /run), matching the default-deny
            allowlist that blocks its endpoints server-side. */}
        <Route path="/cadence" element={<Cadence />} />
        <Route path="/stats" element={<Stats />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/about" element={<About />} />
        <Route path="*" element={<Navigate to="/tracks" replace />} />
      </Routes>
    </Layout>
  );
}
