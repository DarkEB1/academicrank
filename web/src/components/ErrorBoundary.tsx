import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { Button } from './ui/Button';

type Props = {
  children: ReactNode;
  /** Named so the message can say which part of the app failed. */
  section: string;
  onReset?: () => void;
};

type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // No telemetry service in this app; the console is the honest destination.
    console.error(`[Provenance] ${this.props.section} crashed`, error, info.componentStack);
  }

  private reset = (): void => {
    this.setState({ error: null });
    this.props.onReset?.();
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div
        role="alert"
        className="mx-auto my-10 max-w-measure rounded-sm border border-critical/30 bg-critical/5 px-5 py-6"
      >
        <h2 className="font-serif text-lg text-ink">{this.props.section} failed to render</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-muted">
          This is a bug in the interface, not a statement about your data. The rest of the
          application is still usable.
        </p>
        <pre className="mt-3 max-h-40 overflow-auto rounded-sm border border-rule bg-surface p-3 font-mono text-2xs leading-relaxed text-ink-muted">
          {error.name}: {error.message}
        </pre>
        <div className="mt-4 flex gap-2">
          <Button onClick={this.reset} variant="primary" size="sm">
            Try again
          </Button>
          <Button onClick={() => window.location.reload()} size="sm">
            Reload the page
          </Button>
        </div>
      </div>
    );
  }
}
