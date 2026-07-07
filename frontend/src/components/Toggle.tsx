export function Toggle({ on, onChange, disabled = false, label }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button"
      className={"toggle-wrap " + (on ? "on" : "off") + (disabled ? " disabled" : "")}
      onClick={() => { if (!disabled) onChange(!on); }}
      aria-pressed={on}
      aria-label={label}
      aria-disabled={disabled}
      disabled={disabled}
    >
      <span className="toggle-knob" />
    </button>
  );
}
