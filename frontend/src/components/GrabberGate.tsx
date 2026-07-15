import type { ReactNode } from "react";
import PageHeader from "./PageHeader";
import { useGrabberStatus } from "../hooks/useGrabberStatus";

/**
 * Gates a grabber page. While the grabber is disabled it renders a uniform
 * PageHeader + "disabled" card; otherwise it renders the page body. Every
 * grabber page (Queue / Search / Suggestions / Inbox / Playlists) repeated
 * this exact early-return, so it lives here once.
 *
 * Note: the loading state (`status.data` still undefined) falls through to
 * `children`, matching the old behavior — pages guard their own queries with
 * `enabled: status.data?.enabled === true` and show their own loading UI.
 */
export default function GrabberGate({
  title,
  subtitle,
  disabledMessage = "The grabber is disabled.",
  children,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  disabledMessage?: ReactNode;
  children: ReactNode;
}) {
  const status = useGrabberStatus();
  if (status.data && !status.data.enabled) {
    return (
      <>
        <PageHeader title={title} subtitle={subtitle} />
        <div className="card" style={{ color: "var(--muted)", fontSize: 14 }}>{disabledMessage}</div>
      </>
    );
  }
  return <>{children}</>;
}
