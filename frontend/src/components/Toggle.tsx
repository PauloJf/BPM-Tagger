export function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button type="button" className={"toggle-wrap " + (on ? "on" : "off")} onClick={() => onChange(!on)} aria-pressed={on}>
      <span className="toggle-knob" />
    </button>
  );
}
