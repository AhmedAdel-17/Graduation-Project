"""Test news analyst with FinBERT/CAMeLBERT/XLM-R on FWRY.CA."""
import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from tradingagents.dataflows.config import set_config
from tradingagents.default_config import DEFAULT_CONFIG
set_config({**DEFAULT_CONFIG, "target_market": "EGX"})

from langchain_openai import ChatOpenAI
import os

api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
assert api_key, "No API key found in .env"
llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=api_key,
    temperature=0,
    seed=42,
)

# ── Step 1: Pre-fetch real news from all 12 sources ──────────────────────
from tradingagents.graph.prefetch import DataPrefetcher

print("=== Pre-fetching real news from all 12 sources ===")
pf = DataPrefetcher(config={**DEFAULT_CONFIG, "target_market": "EGX"})
prefetched = pf.fetch_all("FWRY.CA", "2025-01-15")
print(f'Company news chars: {len(prefetched.get("prefetched_company_news", "") or "")}')
print(f'Market news chars:  {len(prefetched.get("prefetched_market_news", "") or "")}')
print()

# ── Step 2: Run news analyst with transformers + LLM ─────────────────────
from tradingagents.agents.analysts.news_analyst import create_news_analyst

node = create_news_analyst(llm)
state = {
    "company_of_interest": "FWRY.CA",
    "trade_date": "2025-01-15",
    "news_messages": [],
    **prefetched,
}
print("=== Running news analyst (LLM + Transformers) ===")
r = node(state)

print()
print("=== STRUCTURED SENTIMENT ANALYSIS ===")
sa = r.get("sentiment_analysis", {})
print(f'Sentiment:           {sa.get("sentiment")} ({sa.get("sentiment_strength")})')
print(f'LLM confidence:      {sa.get("confidence_score")}')
print(f'Confidence adj:      {sa.get("confidence_adjustments", [])}')

print()
print("=== TRANSFORMER SENTIMENT (NEW) ===")
ts = sa.get("transformer_sentiment", {})
print(f'Score:        {ts.get("score")}')
print(f'Label:        {ts.get("label")}')
print(f'Confidence:   {ts.get("confidence")}')
print(f'Model status: {ts.get("model_status")}')

print()
print("=== PER-HEADLINE SCORES (top 8) ===")
for h in (ts.get("per_headline_scores") or [])[:8]:
    print(
        f'  [{(h.get("model","?"))[:18]:18}] '
        f'{(h.get("label","?"))[:8]:8} '
        f'(score={h.get("score",0):+.2f}, conf={h.get("confidence",0):.2f}) '
        f'{(h.get("headline","") or "")[:65]}'
    )

print()
print("=== COMBINED SENTIMENT (LLM + Transformer blend) ===")
cs = sa.get("combined_sentiment", {})
print(f'Score:      {cs.get("score")}')
print(f'Label:      {cs.get("label")}')
print(f'Confidence: {cs.get("confidence")}')

print()
print("=== NEWS COVERAGE ===")
nc = sa.get("news_coverage", {})
print(f'Total articles: {nc.get("total_articles")}')
print(f'Sources count:  {nc.get("sources_count")}')
print(f'Languages:      {nc.get("languages")}')
