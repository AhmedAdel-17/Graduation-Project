import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCw } from "lucide-react";
import { t } from "../lib/i18n";

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<
  { children: ReactNode; fallback?: (err: Error, reset: () => void) => ReactNode },
  State
> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return (
      <div
        role="alert"
        className="m-6 max-w-xl mx-auto surface rounded-xl px-5 py-6 shadow-card"
      >
        <div className="flex items-center gap-2 text-down">
          <AlertTriangle className="h-4 w-4" aria-hidden />
          <h2 className="text-sm font-semibold">{t("common.error")}</h2>
        </div>
        <p className="text-xs text-fg-muted mt-2 font-mono whitespace-pre-wrap break-all">
          {error.message}
        </p>
        <button
          type="button"
          onClick={this.reset}
          className="mt-4 inline-flex items-center gap-2 h-8 px-3 rounded-md bg-ink-700 hover:bg-ink-600 text-xs text-fg border border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
        >
          <RotateCw className="h-3 w-3" aria-hidden />
          {t("common.retry")}
        </button>
      </div>
    );
  }
}
