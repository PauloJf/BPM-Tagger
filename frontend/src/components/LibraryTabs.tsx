import { Link, useLocation } from "react-router-dom";

const TABS = [
  { to: "/tracks", label: "Tracks" },
  { to: "/artists", label: "Artists" },
  { to: "/albums", label: "Albums" },
];

/** The tempo filter follows you between the views that can use it. */
const TEMPO_KEYS = ["bpm", "bpm_tol", "bpm_cadence"];
const TEMPO_VIEWS = ["/tracks", "/artists"];

/** Segmented Tracks | Artists | Albums switcher shown on the Library views. */
export default function LibraryTabs() {
  const { pathname, search } = useLocation();
  const current = new URLSearchParams(search);
  const carried = new URLSearchParams();
  for (const k of TEMPO_KEYS) {
    const v = current.get(k);
    if (v) carried.set(k, v);
  }
  const qs = carried.toString();
  const href = (to: string) => (qs && TEMPO_VIEWS.includes(to) && TEMPO_VIEWS.includes(pathname) ? `${to}?${qs}` : to);
  return (
    <div className="segmented" aria-label="Library view">
      {TABS.map((t) => (
        <Link key={t.to} to={href(t.to)} className={"segmented-btn" + (pathname === t.to ? " active" : "")}>
          {t.label}
        </Link>
      ))}
    </div>
  );
}
