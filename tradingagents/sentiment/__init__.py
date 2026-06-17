"""EGX sentiment subsystem — typed contracts, taxonomy, liquidity tiers, config.

This package is the redesigned sentiment layer (Phase 3).
- PR 1: typed contracts, taxonomy, liquidity tiers, config (foundation)
- PR 2: phrase-boundary entity extraction, EGX_MARKET routing fixes
- PR 3: Layer A MarketSentiment aggregator with hard gates
- PR 4: Layer B SectorSentiment aggregator with hard gates + bilingual classifier
- PR 5: Layer C StockSentiment aggregator with per-tier liquidity gates + pre-LLM gate
- PR 6: Layer A0 MacroSentiment aggregator with source-credibility + corroboration gates
- PR 9: surfacing helpers — extract_sentiment_audit_record, format_sentiment_for_api,
        format_sentiment_for_cli, build_sentiment_context_event
"""

from tradingagents.sentiment.macro import MacroDataPoint, compute_macro_sentiment
from tradingagents.sentiment.market import MarketDataPoint, compute_market_sentiment
from tradingagents.sentiment.sector import (
    SectorDataPoint,
    classify_post_to_sector,
    compute_sector_sentiment,
)
from tradingagents.sentiment.stock import StockDataPoint, compute_stock_sentiment
from tradingagents.sentiment.contracts import (
    NO_SIGNAL,
    LayerStatus,
    MacroCategory,
    MacroDirection,
    MacroEvent,
    MacroMagnitude,
    IndexSentiment,
    MacroSentiment,
    MarketRegime,
    MarketSentiment,
    NoSignalReason,
    SentimentContext,
    SectorSentiment,
    SourceCredibility,
    StockSentiment,
    VolatilityMood,
)
from tradingagents.sentiment.liquidity_tiers import LiquidityTier, tier_for
from tradingagents.sentiment.surfacing import (
    build_sentiment_context_event,
    extract_sentiment_audit_record,
    format_sentiment_for_api,
    format_sentiment_for_cli,
)
from tradingagents.sentiment.taxonomy import (
    IndexEnum,
    SectorEnum,
    all_indices,
    members_of_index,
    primary_index,
    sector_aliases_ar,
    ticker_to_indices,
    ticker_to_sector,
    to_fundamentals_sector,
)

__all__ = [
    # Layer A0 — macro sentiment
    "MacroDataPoint",
    "compute_macro_sentiment",
    # Layer A — market sentiment
    "MarketDataPoint",
    "compute_market_sentiment",
    # Layer B — sector sentiment
    "SectorDataPoint",
    "classify_post_to_sector",
    "compute_sector_sentiment",
    # Layer C — stock sentiment
    "StockDataPoint",
    "compute_stock_sentiment",
    # contracts
    "NO_SIGNAL",
    "LayerStatus",
    "LiquidityTier",
    "MacroCategory",
    "MacroDirection",
    "MacroEvent",
    "MacroMagnitude",
    "IndexSentiment",
    "MacroSentiment",
    "MarketRegime",
    "MarketSentiment",
    "NoSignalReason",
    "SectorEnum",
    "SectorSentiment",
    "SentimentContext",
    "SourceCredibility",
    "StockSentiment",
    "VolatilityMood",
    # taxonomy
    "IndexEnum",
    "all_indices",
    "members_of_index",
    "primary_index",
    "sector_aliases_ar",
    "ticker_to_indices",
    "ticker_to_sector",
    "to_fundamentals_sector",
    # liquidity tiers
    "tier_for",
    # PR 9 — surfacing helpers
    "build_sentiment_context_event",
    "extract_sentiment_audit_record",
    "format_sentiment_for_api",
    "format_sentiment_for_cli",
]
