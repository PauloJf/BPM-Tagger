import { useState } from "react";
import { usePlayer, type PlayerTrack } from "../lib/player";

const PlayIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style={{ marginRight: 4 }}><polygon points="6,4 20,12 6,20" /></svg>
);
const ShuffleIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 4 }}>
    <path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5" />
  </svg>
);
const AddIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
);

/** Play / Shuffle / Add-to-queue action trio for a whole list of tracks — an
 *  artist's, an album's, a filtered library view's. Play and Shuffle REPLACE
 *  the current queue (player.playQueue); Add to queue APPENDS to it
 *  (player.enqueueMany) without disturbing what's already playing — the one
 *  Playlist/Cadence pages already had that Artist/Album/Tracks didn't. */
export function QueueActions({
  tracks, getTracks, empty, label = "", disabledTitle,
}: {
  /** The tracks to act on, when already loaded (Artist/Album/Playlist/Cadence). */
  tracks?: PlayerTrack[];
  /** Or fetch the full set lazily at click time — e.g. the Tracks page queues
   *  everything matching the current filter, not just the loaded page. Wins
   *  over `tracks` when both are given. */
  getTracks?: () => Promise<PlayerTrack[]>;
  /** Explicit "nothing to queue" flag for the `getTracks` case, where the
   *  count is already known (e.g. from the loaded page) without fetching.
   *  Defaults to `tracks.length === 0` when using `tracks` directly. */
  empty?: boolean;
  /** Appended to the Play/Shuffle labels, e.g. " playlist". */
  label?: string;
  /** Tooltip shown on all three buttons while there's nothing to queue. */
  disabledTitle?: string;
}) {
  const player = usePlayer();
  const [loading, setLoading] = useState(false);
  const knownEmpty = empty ?? (getTracks ? false : (tracks?.length ?? 0) === 0);
  const disabled = loading || knownEmpty;

  async function run(action: (list: PlayerTrack[]) => void) {
    if (getTracks) {
      setLoading(true);
      try {
        const list = await getTracks();
        if (list.length) action(list);
      } finally {
        setLoading(false);
      }
    } else if (tracks?.length) {
      action(tracks);
    }
  }

  return (
    <>
      <button
        className="btn btn-primary btn-sm"
        disabled={disabled}
        title={disabled ? disabledTitle : undefined}
        onClick={() => run((list) => player.playQueue(list, 0, { shuffle: false }))}
      >
        <PlayIcon />Play{label}
      </button>
      <button
        className="btn btn-ghost btn-sm"
        disabled={disabled}
        title={disabled ? disabledTitle : undefined}
        onClick={() => run((list) => player.playQueue(list, 0, { shuffle: true }))}
      >
        <ShuffleIcon />Shuffle{label}
      </button>
      <button
        className="btn btn-bare btn-sm"
        disabled={disabled}
        title={disabled ? disabledTitle : "Append to the current queue without replacing it"}
        onClick={() => run((list) => player.enqueueMany(list))}
      >
        <AddIcon />Add to queue
      </button>
    </>
  );
}
