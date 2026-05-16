import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/Tabs";
import { useAppStore } from "../../store/appStore";
import { useT } from "../../lib/i18n";
import { WorkspaceHeader } from "./WorkspaceHeader";
import { OverviewTab } from "./OverviewTab";
import { ReasoningTab } from "./ReasoningTab";
import { FundamentalsTab } from "./FundamentalsTab";
import { SentimentTab } from "./SentimentTab";
import { MemoryTab } from "./MemoryTab";
import { HistoryTab } from "./HistoryTab";

const TAB_KEYS = ["overview", "reasoning", "fundamentals", "sentiment", "memory", "history"] as const;
type TabKey = (typeof TAB_KEYS)[number];

export function WorkspacePage() {
  const t = useT();
  const [params, setParams] = useSearchParams();
  const { selectedTicker, setSelectedTicker } = useAppStore();

  // ?ticker= in the URL wins over the store on initial mount so deep-links
  // from /run and /sessions land on the right ticker.
  useEffect(() => {
    const urlTicker = params.get("ticker");
    if (urlTicker && urlTicker !== selectedTicker) {
      setSelectedTicker(urlTicker);
    }
    // Only on mount; subsequent changes flow through onTickerChange below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const initialTab: TabKey = useMemo(() => {
    const raw = params.get("tab");
    return (TAB_KEYS as readonly string[]).includes(raw ?? "")
      ? (raw as TabKey)
      : "overview";
  }, [params]);

  const [tab, setTab] = useState<TabKey>(initialTab);

  const handleTickerChange = (next: string) => {
    setSelectedTicker(next);
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      out.set("ticker", next);
      return out;
    });
  };

  const handleTabChange = (next: string) => {
    setTab(next as TabKey);
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      if (next === "overview") {
        out.delete("tab");
      } else {
        out.set("tab", next);
      }
      return out;
    });
  };

  return (
    <AppShell
      title={t("workspace.title")}
      subtitle={`${selectedTicker} · ${t("workspace.subtitle")}`}
    >
      <div className="flex flex-col gap-5">
        <WorkspaceHeader
          ticker={selectedTicker}
          onTickerChange={handleTickerChange}
        />

        <Tabs value={tab} onValueChange={handleTabChange}>
          <TabsList aria-label={t("workspace.tabs.aria")}>
            <TabsTrigger value="overview">
              {t("workspace.tab.overview")}
            </TabsTrigger>
            <TabsTrigger value="reasoning">
              {t("workspace.tab.reasoning")}
            </TabsTrigger>
            <TabsTrigger value="fundamentals">
              {t("workspace.tab.fundamentals")}
            </TabsTrigger>
            <TabsTrigger value="sentiment">
              {t("workspace.tab.sentiment")}
            </TabsTrigger>
            <TabsTrigger value="memory">
              {t("workspace.tab.memory")}
            </TabsTrigger>
            <TabsTrigger value="history">
              {t("workspace.tab.history")}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="overview">
            <OverviewTab ticker={selectedTicker} />
          </TabsContent>

          <TabsContent value="reasoning">
            <ReasoningTab ticker={selectedTicker} />
          </TabsContent>

          <TabsContent value="fundamentals">
            <FundamentalsTab ticker={selectedTicker} />
          </TabsContent>

          <TabsContent value="sentiment">
            <SentimentTab ticker={selectedTicker} />
          </TabsContent>

          <TabsContent value="memory">
            <MemoryTab ticker={selectedTicker} />
          </TabsContent>

          <TabsContent value="history">
            <HistoryTab ticker={selectedTicker} />
          </TabsContent>
        </Tabs>
      </div>
    </AppShell>
  );
}
