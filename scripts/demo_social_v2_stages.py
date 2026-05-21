"""Per-stage demo runner for tradingagents/dataflows/social_v2.

Runs each pipeline stage and prints a compact summary of the data flowing
between stages. Use this to inspect what the agent actually sees end-to-end.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Iterable, List

# Force UTF-8 on Windows consoles so Arabic prints don't blow up.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("demo")

from tradingagents.dataflows.social_v2 import pipeline as v2_pipeline
from tradingagents.dataflows.social_v2 import post_store
from tradingagents.dataflows.social_v2.signal_adapter import fetch_v2_signal
from tradingagents.dataflows.social_v2.aggregator import split_outputs


def banner(title: str) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n  {title}\n{bar}")


def short(text: str, n: int = 140) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def show_posts(posts: Iterable, label: str, n: int = 8) -> None:
    posts = list(posts)
    print(f"\n[{label}] count = {len(posts)}")
    for i, p in enumerate(posts[:n], 1):
        print(
            f"  {i:2d}. [{p.platform}/{p.source[:30]:30s}] "
            f"eng={p.engagement:>4} ts={p.timestamp[:19]:19s}"
        )
        print(f"      {short(p.text)}")


def show_enriched(enriched: List[dict], label: str, n: int = 6) -> None:
    print(f"\n[{label}] count = {len(enriched)}")
    for i, r in enumerate(enriched[:n], 1):
        symbols = [m.symbol for m in r.get("mentions", [])]
        intents = r["intent"].get("intents", [])
        content_lbl = r["content"]["label"]
        sentiment = r.get("sentiment", {})
        vader = r.get("sentiment_vader", {})
        gate = r.get("quality_gate", {})
        print(
            f"  {i:2d}. [{r['post'].platform}] "
            f"symbols={symbols or '-'} intents={intents} content={content_lbl} "
            f"gate={gate.get('bucket', '?')}"
        )
        if sentiment:
            print(
                f"      sentiment: {sentiment.get('label'):8s} "
                f"score={float(sentiment.get('score', 0)):+.3f} "
                f"conf={float(sentiment.get('confidence', 0)):.2f} "
                f"model={sentiment.get('model_used')}"
            )
        if vader:
            print(
                f"      vader    : {vader.get('label'):8s} "
                f"score={float(vader.get('score', 0)):+.3f}"
            )
        print(f"      text     : {short(r['post'].text, 120)}")


def main() -> int:
    banner("ENVIRONMENT")
    print(f"  APIFY_API_TOKEN  : {'set' if os.getenv('APIFY_API_TOKEN') else 'MISSING'}")
    print(f"  POSTGRES_URL     : {'set' if os.getenv('POSTGRES_URL') else 'MISSING'}")
    print(f"  EGX_FB_MAX_POST_AGE_DAYS: {os.getenv('EGX_FB_MAX_POST_AGE_DAYS', '3')} (default)")

    t0 = time.time()

    # ---- Stage 1: SCRAPE ----
    banner("STAGE 1 — SCRAPE  (Facebook Apify + Reddit)")
    raw, source_counts = v2_pipeline.stage_scrape(
        fb_per_group=150, reddit_per_query=10, reddit_max=100
    )
    print(f"\n  source counts: {source_counts}")
    show_posts(raw, "scraped (post URL+text dedup)", n=10)

    # ---- Stage 2: RELEVANCE ----
    banner("STAGE 2 — RELEVANCE  (Layer-0 EGX gate)")
    relevant = v2_pipeline.stage_relevance(raw)
    print(
        f"\n  precision = {100.0 * len(relevant) / max(1, len(raw)):.1f}%  "
        f"({len(relevant)} / {len(raw)} kept)"
    )
    show_posts(relevant, "relevant", n=8)

    # ---- Stage 3: ENRICH (entities / intent / content-type) ----
    banner("STAGE 3 — ENRICH  (entities + intent + content-type)")
    pre_gate = v2_pipeline.stage_enrich(relevant)

    n_with_symbol = sum(1 for r in pre_gate if r["mentions"])
    n_with_intent = sum(
        1 for r in pre_gate if r["intent"].get("intents") not in (None, [], ["NONE"])
    )
    print(f"\n  posts_with_symbol_mention : {n_with_symbol} / {len(pre_gate)}")
    print(f"  posts_with_trading_intent : {n_with_intent} / {len(pre_gate)}")
    content_counts: dict[str, int] = {}
    for r in pre_gate:
        lbl = r["content"]["label"]
        content_counts[lbl] = content_counts.get(lbl, 0) + 1
    print(f"  content-type breakdown    : {content_counts}")
    show_enriched(pre_gate, "enriched (sample)", n=6)

    # ---- Stage 4: QUALITY GATE ----
    banner("STAGE 4 — QUALITY GATE")
    kept = v2_pipeline.stage_quality_gate(pre_gate)
    buckets: dict[str, int] = {}
    for r in kept:
        b = r.get("quality_gate", {}).get("bucket", "?")
        buckets[b] = buckets.get(b, 0) + 1
    print(f"\n  kept buckets: {buckets}  ({len(kept)} / {len(pre_gate)} kept)")

    # ---- Stage 5: SENTIMENT ----
    banner("STAGE 5 — SENTIMENT  (FinBERT / CAMeLBERT / XLM-R + VADER baseline)")
    print("  NOTE: first run downloads transformer models (multi-GB, several minutes).")
    enriched = v2_pipeline.stage_sentiment(kept)

    if enriched:
        models_used: dict[str, int] = {}
        for r in enriched:
            m = r.get("sentiment", {}).get("model_used", "unknown")
            models_used[m] = models_used.get(m, 0) + 1
        print(f"\n  model usage: {models_used}")
        disagreements = sum(
            1
            for r in enriched
            if r.get("sentiment", {}).get("label")
            != r.get("sentiment_vader", {}).get("label")
        )
        print(f"  EGX-vs-VADER disagreements: {disagreements} / {len(enriched)}")
    show_enriched(enriched, "sentiment-scored (sample)", n=6)

    # ---- Stage 6: AGGREGATE ----
    banner("STAGE 6 — AGGREGATE  (per-stock + EGX_MARKET weighted signal)")
    per_symbol = v2_pipeline.stage_aggregate(enriched)
    output = split_outputs(
        per_symbol,
        total_posts=len(relevant),
        used_posts=len(enriched),
        source_counts=source_counts,
    )
    market = output["market_sentiment"]
    print(
        f"\n  MARKET signal: {market['label'].upper():8s}  "
        f"score={market['weighted_sentiment']:+.3f}  "
        f"conf={market['confidence']:.2f}  n={market['n']}"
    )
    per_stock = output["per_stock_sentiment"]
    print(f"\n  per-stock signals kept (n>=3 mentions): {len(per_stock)}")
    for card in output["top_stocks"][:10]:
        print(
            f"    {card['symbol']:6s}  mentions={card['mentions']:3d}  "
            f"score={card['score']:+.3f}  conf={card['confidence']:.2f}  "
            f"-> {card['sentiment'].upper()}"
        )

    # ---- Stage 7: ARCHIVE ----
    banner("STAGE 7 — ARCHIVE  (Postgres social_v2_posts)")
    n_archived = post_store.archive(enriched)
    print(f"  rows submitted: {len(enriched)}, accepted (after ON CONFLICT): {n_archived}")

    # ---- Agent-facing signal adapter ----
    banner("AGENT-FACING OUTPUT  (fetch_v2_signal for COMI.CA)")
    payload = fetch_v2_signal("COMI.CA", time.strftime("%Y-%m-%d"), use_cache=False)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str)[:3500])

    # ---- Round-trip through the agent's contract builders ----
    banner("AGENT CONTRACT ROUND-TRIP  (typed Pydantic objects)")
    from tradingagents.agents.analysts.social_media_analyst import (
        _try_build_market_sentiment,
        _try_build_sector_sentiment,
        _try_build_macro_sentiment,
    )
    from tradingagents.agents.utils.scoring import blend_sentiment

    m_obj = _try_build_market_sentiment(payload["market_sentiment"])
    s_obj = _try_build_sector_sentiment(payload["sector_sentiment"], "COMI.CA")
    x_obj = _try_build_macro_sentiment(payload["macro_sentiment"])
    print(f"  market: status={m_obj and m_obj.status.value}  regime={m_obj and m_obj.regime.value}")
    print(f"  sector: status={s_obj and s_obj.status.value}  sector={s_obj and s_obj.sector}")
    print(f"  macro : {x_obj and x_obj.composite_regime.value}")
    blend = blend_sentiment(macro=x_obj, market=m_obj, sector=s_obj)
    print(f"\n  → blend confidence multiplier : {blend.confidence_multiplier:.3f}")
    print(f"  → blend position-size multiplier: {blend.position_size_multiplier:.3f}")
    print(f"  → audit: {blend.audit}")

    banner(f"DONE in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
