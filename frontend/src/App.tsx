import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./lib/auth";
import Nav from "./components/Nav";
import PlayerBar from "./components/PlayerBar";
import Login from "./pages/Login";
import Tracks from "./pages/Tracks";
import TrackDetail from "./pages/TrackDetail";
import TrackCompare from "./pages/TrackCompare";
import Artist from "./pages/Artist";
import Album from "./pages/Album";
import Playlists from "./pages/Playlists";
import PlaylistDetail from "./pages/PlaylistDetail";
import Queue from "./pages/Queue";
import Inbox from "./pages/Inbox";
import Search from "./pages/Search";
import Review from "./pages/Review";
import Stats from "./pages/Stats";
import Settings from "./pages/Settings";
import About from "./pages/About";

function Layout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <Nav />
      <div className="container page-enter">{children}</div>
      <PlayerBar />
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
  const { ready, authenticated } = useAuth();
  const location = useLocation();

  if (!ready) return <FullScreenLoader />;

  if (!authenticated) {
    // Any route while logged out → Login (preserve intended path via state).
    if (location.pathname === "/login") return <Login />;
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  // Authenticated: /login redirects into the app.
  if (location.pathname === "/login") return <Navigate to="/tracks" replace />;

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/tracks" replace />} />
        <Route path="/tracks" element={<Tracks />} />
        <Route path="/track" element={<TrackDetail />} />
        <Route path="/compare" element={<TrackCompare />} />
        <Route path="/artist" element={<Artist />} />
        <Route path="/album" element={<Album />} />
        <Route path="/playlists" element={<Playlists />} />
        <Route path="/playlist" element={<PlaylistDetail />} />
        <Route path="/queue" element={<Queue />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/inbox/:id" element={<Inbox />} />
        <Route path="/search" element={<Search />} />
        <Route path="/review" element={<Review />} />
        <Route path="/stats" element={<Stats />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/about" element={<About />} />
        <Route path="*" element={<Navigate to="/tracks" replace />} />
      </Routes>
    </Layout>
  );
}
