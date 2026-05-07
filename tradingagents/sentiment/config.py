"""Config-driven thresholds for the redesigned sentiment subsystem.

Every value in this module is PROVISIONAL until calibrated against a 30-day
rolling window of real EGX social data. They are config-driven so the
calibration script (PR 10) can rewrite them without touching pipeline code.

Honest-abstention design principle: every gate is a HARD binary admit/refuse.
Failing a gate emits NO_SIGNAL with a structured `NoSignalReason`. There are
no soft multipliers, no neutral defaults, no "low confidence with direction".
"""
from __future__ import annotations

from typing import Final

from tradingagents.sentiment.liquidity_tiers import LiquidityTier

# ---- Layer A — Market sentiment hard gates -----------------------------------
MARKET_THRESHOLDS: Final[dict[str, float | int]] = {
    "min_total_posts": 50,
    "min_distinct_sources": 2,
    "min_recent_24h_share": 0.30,
}

# ---- Layer B — Sector sentiment hard gates -----------------------------------
SECTOR_THRESHOLDS: Final[dict[str, float | int]] = {
    "min_sector_posts": 10,
    "min_distinct_days": 3,
    "min_aggregate_entity_confidence": 0.70,
}

# ---- Layer C — Stock sentiment hard gates per liquidity tier -----------------
# Per-tier (n_strong_mentions, n_distinct_authors, n_distinct_sources).
# A "strong" mention is entity_confidence >= MIN_STRONG_ENTITY_CONFIDENCE
# (cashtag, .CA suffix, full company name, or full multi-word alias matched
# as a phrase — not a substring).
STOCK_THRESHOLDS_BY_TIER: Final[dict[LiquidityTier, dict[str, int | float]]] = {
    LiquidityTier.MEGA: {
        "n_strong_mentions": 8,
        "n_distinct_authors": 5,
        "n_distinct_sources": 3,
    },
    LiquidityTier.MID: {
        "n_strong_mentions": 5,
        "n_distinct_authors": 3,
        "n_distinct_sources": 2,
    },
    LiquidityTier.SMALL: {
        "n_strong_mentions": 3,
        "n_distinct_authors": 2,
        "n_distinct_sources": 2,
    },
}

MIN_STRONG_ENTITY_CONFIDENCE: Final[float] = 0.85
MIN_RECENT_72H_SHARE: Final[float] = 0.60

# ---- Layer A0 — Macro half-lives (hours) by event category -------------------
MACRO_HALF_LIVES_HOURS: Final[dict[str, int]] = {
    "RATE_DECISION": 120,
    "EGP_DEVALUATION": 240,
    "IMF_PROGRAM": 168,
    "INFLATION_PRINT": 72,
    "TAX_REGULATION": 168,
    "GEOPOLITICAL": 48,
    "COMMODITY_SHOCK": 72,
}

MACRO_GATES: Final[dict[str, int]] = {
    "min_corroborating_sources": 2,
    "corroboration_window_hours": 48,
}

# ---- Macro source credibility ladder (approved Phase 2) ----------------------
# Domains/identifiers normalized to lowercase, no scheme, no www.
MACRO_SOURCES_TIER1: Final[frozenset[str]] = frozenset(
    {
        "cbe.org.eg",          # Central Bank of Egypt
        "mof.gov.eg",          # Ministry of Finance
        "fra.gov.eg",          # Financial Regulatory Authority
        "reuters.com",
        "bloomberg.com",
        "mubasher.info",       # Mubasher official
        "egx.com.eg",          # EGX official announcements
    }
)
MACRO_SOURCES_TIER2: Final[frozenset[str]] = frozenset(
    {
        "enterprise.press",
        "almalnews.com",       # Al Mal
        "dailynewsegypt.com",
        "alborsaanews.com",    # Al Borsa News
    }
)

# ---- Layer E — Final blending: confidence multipliers ------------------------
# Sentiment NEVER flips direction. It can only modulate confidence and the
# trader's proposed position size. The risk manager's deterministic veto is
# untouched; sentiment can tighten limits via these multipliers, never loosen
# them.
LAYER_E_CONFIDENCE_MULTIPLIERS: Final[dict[str, dict[str, float]]] = {
    "market": {
        "PANIC": 0.70,
        "FEAR": 0.85,
        "NEUTRAL": 1.00,
        "NO_SIGNAL": 1.00,
        "GREED": 0.90,
        "EUPHORIA": 0.70,
    },
    "macro": {
        "RISK_OFF": 0.80,
        "RISK_ON": 0.95,
        "NEUTRAL": 1.00,
        "NO_SIGNAL": 1.00,
    },
}

LAYER_E_POSITION_SIZE_MULTIPLIERS: Final[dict[str, float]] = {
    "PANIC": 0.50,
    "FEAR": 0.75,
    "NEUTRAL": 1.00,
    "NO_SIGNAL": 1.00,
    "GREED": 0.90,
    "EUPHORIA": 0.60,
}

SECTOR_TILT_MAX_CONFIDENCE_SHIFT: Final[float] = 0.10

# ---- Quorum rule -------------------------------------------------------------
# Final unified decision requires at least this many non-NO_SIGNAL analysts.
# Falls below → overall_status = INSUFFICIENT_DATA, defer.
MIN_ANALYST_QUORUM: Final[int] = 2
