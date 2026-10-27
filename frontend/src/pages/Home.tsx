import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useScan } from "../hooks/useScan";
import { useTitle } from "../hooks/useTitle";
import PageHeader from "../components/PageHeader";

/** Same payload (and query key) the Playlists cadence strip reads, so the two
 *  share one cached fetch. */
interface Readiness {
  presets: Array<{ name: string; bpm: number }>;
  stretch_limit_pct: number;
  library: Record<string, number>;
}

interface StatsSummary {
  summary: { total: number; done: number; decode_problems: number };
}

const plural = (n: number, one: string, many: string) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

interface AttentionItem { key: string; title: string; sub: string; to: string }

const ic = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;
const IconPulse = () => <svg {...ic}><path d="M22 12h-4l-3 8L9 4l-3 8H2" /></svg>;
const IconWarn = () => <svg {...ic}><path d="M12 8v5M12 16h.01" /><path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>;
const IconScan = () => <svg {...ic}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>;

/** The runner's front door: pick a pace and go, with whatever needs attention
 *  underneath. Built only from endpoints that already exist. */
export default function Home() {
  useTitle("Home");
  const { reviewCount } = useAuth();
  const { state, act, progress } = useScan();

  // Per-preset runnable counts. Failure is silent (retry off): the page is still
  // useful without the cards, and Run itself works without them.
  const readinessQ = useQuery({
    queryKey: ["run-readiness"],
    queryFn: () => api.get<Readiness>("/api/run/readiness"),
    staleTime: 60_000,
    retry: false,
  });
  const statsQ = useQuery({
    queryKey: ["stats", "home"],
    queryFn: () => api.get<StatsSummary>("/api/stats"),
    staleTime: 30_000,
    retry: false,
  });

  const readiness = readinessQ.data;
  const summary = statsQ.data?.summary;
  const analysed = summary?.done ?? 0;
  const scanning = state === "analysing" || state === "paused" || state === "watching";

  // Nothing analysed yet: the cards would all read "0 ready", so offer the one
  // thing that helps. Only once the numbers are in, so it never flashes up.
  const firstRun = !!summary && analysed === 0;

  const attention: AttentionItem[] = [];
  if (reviewCount > 0) {
    attention.push({
      key: "review",
      title: `${plural(reviewCount, "track needs", "tracks need")} a BPM check`,
      sub: "Detectors disagree or a file couldn't be analysed.",
      to: "/review",
    });
  }
  if (summary && summary.decode_problems > 0) {
    attention.push({
      key: "problems",
      title: `${plural(summary.decode_problems, "file had", "files had")} decode problems`,
      sub: "Part of the audio wouldn't decode. Show them in the library.",
      to: "/tracks?filter=problems",
    });
  }
  if (state === "analysing" || state === "paused") {
    const p = progress;
    attention.push({
      key: "scan",
      title: state === "paused" ? "Scan paused" : "Analyzing your library",
      sub: p && p.total > 0 ? `${p.completed.toLocaleString()} of ${p.total.toLocaleString()} tracks` : "Working…",
      to: "/tracks",
    });
  }

  return (
    <>
      <PageHeader
        title="Home"
        subtitle={summary && analysed > 0
          ? `Pick a pace and go. ${plural(analysed, "track", "tracks")} analysed.`
          : "Pick a pace and go."}
      />

      {firstRun ? (
        <section className="home-cta" aria-label="Scan your library">
          <div className="home-cta-title">Scan your library</div>
          <p className="home-cta-text">
            Nothing has been analysed yet. Once your tracks have a tempo, you can pick a pace here and start a run.
          </p>
          {scanning ? (
            <span className="home-cta-note">
              {progress && progress.total > 0
                ? `Analyzing: ${progress.completed.toLocaleString()} of ${progress.total.toLocaleString()} tracks`
                : "Analyzing your library…"}
            </span>
          ) : (
            <button type="button" className="btn btn-primary" onClick={() => act("start")}>
              Start scan
            </button>
          )}
        </section>
      ) : (
        <div className="home-grid">
          <section aria-label="Start a run" className="home-main">
            <div className="home-section-head">
              <h2>Start a run</h2>
              <span>Starred tracks come first</span>
            </div>
            {readiness ? (
              <div className="home-presets">
                {readiness.presets.map((p) => {
                  const ready = readiness.library[String(p.bpm)] ?? 0;
                  const pct = analysed > 0 ? Math.min(100, Math.round((ready / analysed) * 100)) : 0;
                  return (
                    <Link
                      key={p.bpm}
                      to={`/run?bpm=${p.bpm}`}
                      className="home-preset"
                      title={`${ready} library tracks runnable at ${p.name} (${p.bpm} BPM) within ±${readiness.stretch_limit_pct.toFixed(1)}%`}
                    >
                      <span className="home-preset-name">{p.name}</span>
                      <span className="home-preset-bpm">{p.bpm}<small>steps/min</small></span>
                      <span className="home-preset-ready"><b>{ready.toLocaleString()}</b> ready</span>
                      <span className="home-bar" aria-hidden="true"><span style={{ width: `${pct}%` }} /></span>
                    </Link>
                  );
                })}
                <Link to="/run" className="home-preset home-preset--custom">
                  <span className="home-preset-name">Custom</span>
                  <span className="home-preset-bpm">&mdash;</span>
                  <span className="home-preset-ready">Pick any cadence</span>
                </Link>
              </div>
            ) : readinessQ.isError ? (
              <p className="home-note">
                Couldn't load the run presets. <Link to="/run">Open Run</Link> to pick a cadence there.
              </p>
            ) : null}
          </section>

          {attention.length > 0 && (
            <section aria-label="Needs attention" className="home-side">
              <div className="home-attn">
                <div className="home-section-head">
                  <h2>Needs attention</h2>
                  <span className="home-attn-count">{attention.length}</span>
                </div>
                {attention.map((a) => (
                  <Link key={a.key} to={a.to} className="home-attn-row">
                    <span className="home-attn-icon">
                      {a.key === "review" ? <IconPulse /> : a.key === "scan" ? <IconScan /> : <IconWarn />}
                    </span>
                    <span className="home-attn-text">
                      <span className="home-attn-title">{a.title}</span>
                      <span className="home-attn-sub">{a.sub}</span>
                    </span>
                    <span aria-hidden="true" className="home-attn-go">&rarr;</span>
                  </Link>
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </>
  );
}
