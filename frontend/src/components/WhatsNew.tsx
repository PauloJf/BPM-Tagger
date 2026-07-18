import { useEffect, useRef, useState } from "react";
import { useAuth } from "../lib/auth";
import { usePlayer } from "../lib/player";
import { ChangelogModal, cmpVersion } from "./Changelog";

const KEY = "bpm.lastSeenVersion";

/** Decide what to do on boot, given the current version and the last one this
 *  device saw. Pure + exported for tests.
 *  - "seed": remember the version, show nothing (fresh install or same/older).
 *  - "show": a genuine update — show the popup.
 *  - "skip": not applicable now (not admin, no version, or defer past an active run). */
export function decideWhatsNew(o: {
  role: string | null; version: string; lastSeen: string | null; playing: boolean;
}): "seed" | "show" | "skip" {
  if (o.role !== "admin" || !o.version) return "skip";
  if (!o.lastSeen) return "seed";
  if (cmpVersion(o.version, o.lastSeen) <= 0) return "seed"; // same or downgrade
  if (o.playing) return "skip";                              // don't interrupt a run — retry next boot
  return "show";
}

/** Admin-only "What's new" popup: opens once per device after an update (never
 *  on a fresh install, never over an actively-playing run, never for a player
 *  kiosk — it's only mounted in the admin layout). */
export default function WhatsNew() {
  const { role, version } = useAuth();
  const { playing } = usePlayer();
  const [open, setOpen] = useState(false);
  const [since, setSince] = useState<string | undefined>(undefined);
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    const lastSeen = localStorage.getItem(KEY);
    const decision = decideWhatsNew({ role, version, lastSeen, playing });
    if (decision === "seed") {
      handled.current = true;
      localStorage.setItem(KEY, version);
    } else if (decision === "show") {
      handled.current = true;
      setSince(lastSeen ?? undefined);
      setOpen(true);
    }
    // "skip": leave it for a later run of this effect (e.g. once playback stops).
  }, [role, version, playing]);

  const close = () => {
    setOpen(false);
    if (version) localStorage.setItem(KEY, version);
  };

  if (!open) return null;
  return <ChangelogModal since={since} onClose={close} />;
}
