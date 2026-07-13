import { Link, useLocation } from "react-router-dom";

const TABS = [
  { to: "/tracks", label: "Tracks" },
  { to: "/artists", label: "Artists" },
  { to: "/albums", label: "Albums" },
];

/** Segmented Tracks | Artists | Albums switcher shown on the Library views. */
export default function LibraryTabs() {
  const { pathname } = useLocation();
  return (
    <div className="segmented" aria-label="Library view">
      {TABS.map((t) => (
        <Link key={t.to} to={t.to} className={"segmented-btn" + (pathname === t.to ? " active" : "")}>
          {t.label}
        </Link>
      ))}
    </div>
  );
}
