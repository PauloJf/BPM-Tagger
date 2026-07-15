import type { ReactNode } from "react";

/**
 * Centered "nothing here" card — the ✓ success/empty panel shared by
 * Duplicates, Inbox, and Review. Pass a different `icon` for non-success
 * empties; `message` is usually `loading ? "Loading…" : "…"`.
 */
export default function EmptyState({ icon = "✓", message }: { icon?: ReactNode; message: ReactNode }) {
  return (
    <div style={{ padding: "60px 20px", textAlign: "center", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 14 }}>
      <div style={{ fontSize: 32, marginBottom: 12, color: "var(--ok-fg)" }}>{icon}</div>
      <div style={{ fontSize: 14, color: "var(--muted)" }}>{message}</div>
    </div>
  );
}
