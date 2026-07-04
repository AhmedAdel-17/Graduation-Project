import { useEffect, useState } from "react";
import { Loader2, Newspaper, ExternalLink } from "lucide-react";
import { api } from "../../services/api/client";

interface NewsArticle {
  title: string;
  summary: string;
  source: string;
  published_at: string;
  url: string;
  language: string;
}

interface NewsData {
  articles: NewsArticle[];
}

export function MarketNewsFeed() {
  const [data, setData] = useState<NewsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchNews() {
      try {
        setLoading(true);
        const res = await api.get<{ status: string; data: NewsData }>("/market/news?days=3");
        if (res.status === "ok") {
          setData(res.data);
        } else {
          setError("Failed to load news data");
        }
      } catch (err) {
        setError("Error fetching market news");
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    fetchNews();
  }, []);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center p-10 card grain">
        <Loader2 className="h-6 w-6 animate-spin text-stone-400 mb-3" />
        <span className="text-stone-500 text-[13px]">Fetching market news...</span>
      </div>
    );
  }

  if (error || !data || data.articles.length === 0) {
    return null;
  }

  const formatDate = (dateString: string) => {
    if (!dateString) return "";
    try {
      const d = new Date(dateString);
      return new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "numeric",
      }).format(d);
    } catch {
      return dateString;
    }
  };

  return (
    <div className="card overflow-hidden grain anim-fade-up" style={{ animationDelay: "200ms" }}>
      <div className="border-b border-stone-200 p-4 dark:border-[var(--hairline)] flex items-center gap-2">
        <Newspaper className="h-4 w-4 text-stone-500" />
        <h3 className="text-[15px] font-semibold text-ink">
          Latest Market News
        </h3>
      </div>
      
      <div className="divide-y divide-stone-100 dark:divide-[var(--hairline)] max-h-[400px] overflow-y-auto">
        {data.articles.map((article, i) => (
          <div key={i} className="p-4 hover:bg-stone-50/50 transition-colors">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[10px] uppercase font-bold tracking-wider text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">
                    {article.source}
                  </span>
                  <span className="text-[11px] text-stone-400">
                    {formatDate(article.published_at)}
                  </span>
                </div>
                <h4 className="text-[14px] font-medium text-ink leading-snug mb-1" dir={article.language === "ar" ? "rtl" : "ltr"}>
                  {article.title}
                </h4>
                {article.summary && (
                  <p className="text-[12.5px] text-stone-500 line-clamp-2 leading-relaxed" dir={article.language === "ar" ? "rtl" : "ltr"}>
                    {article.summary}
                  </p>
                )}
              </div>
              {article.url && (
                <a 
                  href={article.url} 
                  target="_blank" 
                  rel="noreferrer"
                  className="shrink-0 text-stone-400 hover:text-ink transition-colors p-1"
                >
                  <ExternalLink className="h-4 w-4" />
                </a>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
