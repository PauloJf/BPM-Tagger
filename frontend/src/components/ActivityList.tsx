import type { ActivityItem } from "../lib/types";

/** Background work that isn't the scan: one row per running job, with a bar when it
 *  knows its total. Renders nothing when idle, so the sidebar stays quiet. */
export default function ActivityList({ items }: { items: ActivityItem[] }) {
  if (items.length === 0) return null;
  return (
    <div className="sidebar-activity" role="status" aria-label="Background activity">
      {items.map((it) => {
        const known = it.total != null && it.total > 0 && it.done != null;
        const pct = known ? Math.min(100, Math.round((it.done! / it.total!) * 100)) : 0;
        return (
          <div key={it.id} className="sidebar-activity-item" data-testid={`activity-${it.id}`}>
            <div className="sidebar-activity-head">
              <span className="scan-dot pulsing" style={{ background: "var(--accent)", boxShadow: "0 0 0 3px var(--accent)33" }} />
              <span className="sidebar-activity-label">{it.label}</span>
              {known && <span className="sidebar-scan-count">{it.done} / {it.total}</span>}
            </div>
            {known && (
              <div className="sidebar-scan-track"><div className="sidebar-scan-fill" style={{ width: `${pct}%` }} /></div>
            )}
          </div>
        );
      })}
    </div>
  );
}
