export type PlayerMode = "run" | "listen";

/** What the Player page hands down to Run or Listen: the switch, drawn inline for
 *  the desktop header (`fill` false) or as a full-width row on a phone (`fill`
 *  true). Absent when the page is mounted without it (the kiosk shell). */
export type ModeSwitchSlot = (fill: boolean) => React.ReactNode;

const MODES: ReadonlyArray<{ mode: PlayerMode; label: string; hint: string }> = [
  { mode: "run", label: "Run", hint: "Lock every track onto your cadence" },
  { mode: "listen", label: "Listen", hint: "Your music at native speed, nothing stretched" },
];

/** The Run | Listen switch at the top of the player page. A plain segmented
 *  control (the design system's `.segmented`), so it reads like the Library's
 *  Tracks | Artists | Albums switcher. `fill` stretches it across the row, which
 *  is how it sits on a phone; on desktop it hugs the page title. */
export default function ModeSwitch({ mode, onChange, fill = false }: {
  mode: PlayerMode;
  onChange: (next: PlayerMode) => void;
  fill?: boolean;
}) {
  return (
    <div
      className="segmented"
      role="tablist"
      aria-label="Player mode"
      data-testid="mode-switch"
      style={fill ? { display: "flex", width: "100%", flexShrink: 0, marginBottom: 12 } : undefined}
    >
      {MODES.map((m) => (
        <button
          key={m.mode}
          type="button"
          role="tab"
          aria-selected={mode === m.mode}
          className={"segmented-btn" + (mode === m.mode ? " active" : "")}
          style={fill ? { flex: 1, minHeight: 34 } : undefined}
          title={m.hint}
          onClick={() => onChange(m.mode)}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}
