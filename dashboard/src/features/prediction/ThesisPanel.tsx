import type { Recommendation } from "../../services/api/types";
import { Card, CardBody, CardHeader, CardTitle } from "../../components/ui/Card";
import { TrendingUp, TrendingDown, Scale } from "lucide-react";

function Block({
  title,
  icon,
  color,
  content,
}: {
  title: string;
  icon: React.ReactNode;
  color: string;
  content?: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-ink-900/40 p-4">
      <div
        className={`flex items-center gap-2 text-xs font-semibold uppercase tracking-wider ${color}`}
      >
        {icon}
        {title}
      </div>
      <p className="mt-2 text-sm text-fg/90 leading-relaxed whitespace-pre-wrap">
        {content?.trim() || "No analysis available."}
      </p>
    </div>
  );
}

export function ThesisPanel({ rec }: { rec: Recommendation }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Investment Thesis</CardTitle>
      </CardHeader>
      <CardBody className="space-y-3">
        <div className="grid md:grid-cols-3 gap-3">
          <Block
            title="Bull Case"
            icon={<TrendingUp className="h-3.5 w-3.5" />}
            color="text-up"
            content={rec.bull_case}
          />
          <Block
            title="Bear Case"
            icon={<TrendingDown className="h-3.5 w-3.5" />}
            color="text-down"
            content={rec.bear_case}
          />
          <Block
            title="Neutral / Judge"
            icon={<Scale className="h-3.5 w-3.5" />}
            color="text-accent"
            content={rec.neutral_case}
          />
        </div>

        {(rec.rationale || rec.recommendation) && (
          <div className="rounded-lg border border-line bg-ink-900/40 p-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-fg-muted">
              Rationale & Plan
            </p>
            {rec.rationale && (
              <p className="mt-2 text-sm text-fg/90 leading-relaxed whitespace-pre-wrap">
                {rec.rationale}
              </p>
            )}
            {rec.recommendation && (
              <p className="mt-2 text-sm text-fg/90 leading-relaxed whitespace-pre-wrap">
                {rec.recommendation}
              </p>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
