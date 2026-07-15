import type { ReactNode } from "react";

/**
 * Uniform page header used by every top-level page.
 *
 * Standardizes what was drifting across pages:
 *  - title: <h1> 28 / 600 / -0.02em (the majority already used this; empty and
 *    disabled states had dropped the letter-spacing and the subtitle)
 *  - subtitle: 13px muted line (optional)
 *  - tabs: optional slot for <LibraryTabs /> on the library views
 *  - actions: right-aligned primary/secondary buttons — ALWAYS top-right, so
 *    "Play all", "Retry all failed", "Start run", etc. live in one place
 *
 * Search / filter / pagination controls do NOT go here — put them in a separate
 * toolbar row directly below <PageHeader>, so page identity stays uncluttered.
 */
export default function PageHeader({
  title,
  subtitle,
  tabs,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  tabs?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-end",
        gap: 16,
        marginBottom: 22,
        flexWrap: "wrap",
      }}
    >
      <div style={{ minWidth: 0 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, letterSpacing: "-0.02em", margin: subtitle ? "0 0 4px" : 0 }}>
          {title}
        </h1>
        {subtitle != null && (
          <p style={{ fontSize: 13, color: "var(--muted)", margin: 0 }}>{subtitle}</p>
        )}
      </div>
      {tabs}
      {actions != null && (
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {actions}
        </div>
      )}
    </div>
  );
}
