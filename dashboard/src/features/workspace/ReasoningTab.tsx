import { Link } from "react-router-dom";
import { Activity, Sparkles } from "lucide-react";
import { Card, CardBody } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { EmptyState } from "../../components/ui/EmptyState";
import { useLatestSessionTrace } from "../../hooks/useSessionTrace";
import { useT } from "../../lib/i18n";
import { ReasoningView } from "./ReasoningView";

export function ReasoningTab({ ticker }: { ticker: string }) {
  const t = useT();
  const { loading, source, session, events } = useLatestSessionTrace(ticker);

  if (loading) {
    return (
      <Card>
        <CardBody>
          <Skeleton className="h-48 w-full rounded-lg" />
        </CardBody>
      </Card>
    );
  }

  if (!session || events.length === 0) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            icon={<Activity className="h-4 w-4" />}
            title={t("reasoning.empty.title")}
            description={t("reasoning.empty.desc")}
            action={
              <Link
                to={`/run?ticker=${encodeURIComponent(ticker)}`}
                className="inline-flex items-center gap-1 text-xs text-brand-400 hover:text-brand-300"
              >
                <Sparkles className="h-3 w-3" aria-hidden />
                {t("reasoning.empty.cta")}
              </Link>
            }
          />
        </CardBody>
      </Card>
    );
  }

  return <ReasoningView session={session} events={events} source={source} />;
}
