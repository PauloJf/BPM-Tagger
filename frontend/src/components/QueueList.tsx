import { useState } from "react";
import { usePlayer } from "../lib/player";
import { Cover } from "./Artwork";

/** The playback queue rows — drag-to-reorder, jump, move, remove — extracted
 *  from PlayerBar's queue drawer so the Listen page can embed the same list
 *  outside a drawer. Owns only the transient drag state; everything else comes
 *  from the player context. The host supplies the scrolling container's sizing
 *  via its own wrapper; `fontClass` carries PlayerBar's drawer font stepping
 *  (unused by hosts without one). */
export default function QueueList({ fontClass = "" }: { fontClass?: string }) {
  const { orderedQueue, orderPos, jumpTo, removeAt, moveAt, reorderTo } = usePlayer();
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null);

  return (
    <div className={("player-queue-list " + fontClass).trim()}>
      {orderedQueue.map((t, i) => (
        <div
          key={`${t.path}-${i}`}
          className={"player-queue-row"
            + (i === orderPos ? " current" : "")
            + (dragIdx === i ? " dragging" : "")
            + (dragOverIdx === i && dragIdx !== null && dragIdx !== i ? " drag-over" : "")}
          draggable
          onDragStart={(e) => { setDragIdx(i); e.dataTransfer.effectAllowed = "move"; }}
          onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; if (dragOverIdx !== i) setDragOverIdx(i); }}
          onDrop={(e) => { e.preventDefault(); if (dragIdx !== null) reorderTo(dragIdx, i); setDragIdx(null); setDragOverIdx(null); }}
          onDragEnd={() => { setDragIdx(null); setDragOverIdx(null); }}
        >
          <span className="player-queue-grip" aria-hidden title="Drag to reorder">⠿</span>
          {!t.ephemeral && <Cover path={t.path} size={30} />}
          <button className="player-queue-title" title={t.title} onClick={() => jumpTo(i)}>
            {i === orderPos && <span style={{ color: "var(--accent-2)", marginRight: 6 }}>▶</span>}
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</span>
            {t.artist && <span style={{ color: "var(--muted)" }}> · {t.artist}</span>}
          </button>
          {t.bpm != null && <span className="player-queue-bpm" title={`${Math.round(t.bpm)} BPM`}>{Math.round(t.bpm)}</span>}
          <div className="player-queue-actions">
            <button className="btn btn-bare btn-sm" disabled={i === 0} onClick={() => moveAt(i, -1)} aria-label="Move up" title="Move up">↑</button>
            <button className="btn btn-bare btn-sm" disabled={i === orderedQueue.length - 1} onClick={() => moveAt(i, 1)} aria-label="Move down" title="Move down">↓</button>
            <button className="btn btn-bare btn-sm" onClick={() => removeAt(i)} aria-label="Remove" title="Remove from queue">✕</button>
          </div>
        </div>
      ))}
    </div>
  );
}
