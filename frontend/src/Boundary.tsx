import { Component, type ReactNode } from "react";

/**
 * A render error unmounts the whole React tree, and what you get is a blank page with
 * nothing to go on — not in the UI, not in the server log, and gone from the console the
 * moment you reload. This turns that into a sentence and a stack.
 *
 * Class component because that is the only thing React gives an error boundary; there is
 * no hook for it.
 */
export class Boundary extends Component<{ children: ReactNode }, { failed: Error | null }> {
  state = { failed: null as Error | null };

  static getDerivedStateFromError(failed: Error) {
    return { failed };
  }

  componentDidCatch(failed: Error) {
    console.error("Mindstash stopped rendering:", failed);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="broke">
        <h1>That broke.</h1>
        <p>
          The page stopped rendering. Reloading usually clears it — during development it is
          most often a hot update that could not be applied.
        </p>
        <pre>{this.state.failed.stack ?? String(this.state.failed)}</pre>
        <button className="primary" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
