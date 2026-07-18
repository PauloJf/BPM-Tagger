import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props { children: ReactNode }
interface State { error: Error | null }

/** Catches render errors anywhere below it so one broken page can't white-screen
 *  the whole app — important for the installed PWA, where a crash mid-run would
 *  otherwise leave a blank screen with no way back. Shows a minimal fallback with
 *  a reload button; styling leans on the design-system CSS vars so it works in
 *  either theme. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface it for debugging; no telemetry is sent.
    console.error("Unhandled render error:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: 24, textAlign: "center", color: "var(--text)" }}>
        <div style={{ maxWidth: 420 }}>
          <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Something went wrong</div>
          <p style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.5, marginBottom: 16 }}>
            The page hit an unexpected error. Reloading usually clears it. If it keeps
            happening, note what you were doing and report it.
          </p>
          <button className="btn btn-primary" onClick={() => window.location.reload()}>Reload</button>
        </div>
      </div>
    );
  }
}
