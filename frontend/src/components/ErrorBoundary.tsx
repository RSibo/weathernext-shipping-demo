import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error?: Error;
}

/** React has no hook for render errors; a class is still the only way. Shows a calm recovery card
 * instead of a blank page if something unexpected throws while the audience is watching. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = {};

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Unhandled render error", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex h-dvh items-center justify-center p-6">
        <div className="glass max-w-sm space-y-4 rounded-panel p-6 text-center">
          <p className="text-base font-semibold text-ink-1">Something went wrong.</p>
          <p className="text-sm text-ink-3">Reloading brings the demo back to its starting state.</p>
          <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}
