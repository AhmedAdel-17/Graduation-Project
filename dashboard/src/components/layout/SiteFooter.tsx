import { useQuery } from "@tanstack/react-query";
import { Cpu, GitCommit, Brain } from "lucide-react";
import { endpoints } from "../../services/api";
import { useT } from "../../lib/i18n";

const GIT_SHA = (import.meta.env.VITE_GIT_SHA as string | undefined) ?? "dev";
const BUILD_TIME = (import.meta.env.VITE_BUILD_TIME as string | undefined) ?? "";

export function SiteFooter() {
  const t = useT();
  const { data } = useQuery({
    queryKey: ["config"],
    queryFn: () => endpoints.config(),
    staleTime: 60_000,
    retry: 0,
  });

  const cfg = data?.config ?? {};
  const deep = String(cfg.deep_think_llm ?? cfg.deep_think_model ?? "—");
  const quick = String(cfg.quick_think_llm ?? cfg.quick_think_model ?? "—");
  const rlOn = Boolean(cfg.rl_meta_policy_enabled);

  const shaShort = GIT_SHA.slice(0, 7);

  return (
    <footer className="border-t border-line bg-ink-900/40 px-4 lg:px-8 py-2.5">
      <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-x-6 gap-y-1.5 text-[10px] text-fg-subtle">
        <p className="truncate">{t("footer.disclaimer.short")}</p>
        <div className="flex items-center gap-4 num">
          <span className="inline-flex items-center gap-1.5" title="LLM tiers">
            <Cpu className="h-3 w-3" aria-hidden />
            <span className="uppercase tracking-wider">{t("footer.model.label")}</span>
            <span className="text-fg-muted">deep:{deep}</span>
            <span className="text-fg-subtle">·</span>
            <span className="text-fg-muted">quick:{quick}</span>
          </span>
          <span className="inline-flex items-center gap-1.5" title="RL meta-policy">
            <Brain className="h-3 w-3" aria-hidden />
            <span className="uppercase tracking-wider">{t("footer.rl.label")}</span>
            <span className={rlOn ? "text-brand-400" : "text-fg-muted"}>
              {rlOn ? t("footer.rl.on") : t("footer.rl.off")}
            </span>
          </span>
          <span className="inline-flex items-center gap-1.5" title={BUILD_TIME || "build"}>
            <GitCommit className="h-3 w-3" aria-hidden />
            <span className="uppercase tracking-wider">{t("footer.build.label")}</span>
            <span className="text-fg-muted">{shaShort}</span>
          </span>
        </div>
      </div>
    </footer>
  );
}
