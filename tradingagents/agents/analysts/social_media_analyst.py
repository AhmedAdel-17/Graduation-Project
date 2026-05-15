"""
Social Media Sentiment Analyst for EGX — Phase 3 (PR 8)
=========================================================
Phase 3 redesign: LLM is an explainer only. Directional numbers come from
the deterministic sentiment pipeline (Layers A0/A/B/C). The LLM writes a
``narrative`` (2-3 sentences) + ``cited_post_ids`` list. No ``sentiment_score``,
``direction``, or ``confidence`` fields are accepted from the LLM output.

Pipeline:
  1. Layer C pre-LLM gate (PR 5): if prefetched StockDataPoints fail hard gates
     → skip LLM, write NO_SIGNAL template, write pass-through blend result.
  2. LLM explainer: receives deterministic layer results as context; produces
     narrative + cited_post_ids only.
  3. Blend computation: ``blend_sentiment()`` called with available aggregated
     layer objects (or all-None pass-through when no structured data is present).
  4. ``sentiment_blend_result`` written to state for propagator + trader.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.social_media_tools import (
    get_social_media_posts,
    get_social_sentiment,
)
from tradingagents.dataflows.config import get_config
from tradingagents.agents.utils.input_sanitizer import sanitize_external_text, wrap_external_content

logger = logging.getLogger("tradingagents.social_media_analyst")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_NO_SIGNAL_TEMPLATE = "Social sentiment: insufficient data — excluded."

# Instruction injected into LLM prompts: LLM must not emit directional scores.
_LLM_ROLE_INSTRUCTION = (
    "IMPORTANT — YOUR ROLE IS EXPLAINER ONLY:\n"
    "Directional scores (sentiment_score, confidence, direction) are computed by a "
    "deterministic pipeline OUTSIDE of your analysis. Your job is to describe in plain "
    "language what the social data says — NOT to assign a score. Do not include "
    "any numeric sentiment scores or directional labels in your JSON output.\n"
    "Your JSON block must contain exactly two fields:\n"
    '  "narrative": "<2-3 sentences describing key themes and retail investor tone>",\n'
    '  "cited_post_ids": ["id1", "id2", ...]  // empty list if no post IDs available\n'
    "Do not add any other fields."
)


# ---------------------------------------------------------------------------
# Layer C pre-LLM gate (carried over from PR 5, unchanged)
# ---------------------------------------------------------------------------

def _try_layer_c_gate(ticker: str, datapoints_raw: List[Dict], trade_date: str) -> bool:
    """Return True when Layer C emits NO_SIGNAL (LLM should be skipped).

    Any exception causes False so the LLM path is taken as safe fallback.
    """
    try:
        from tradingagents.sentiment.contracts import LayerStatus
        from tradingagents.sentiment.stock import StockDataPoint, compute_stock_sentiment

        try:
            reference_time = datetime.strptime(trade_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            reference_time = datetime.now(timezone.utc)

        points = [
            StockDataPoint(
                timestamp=str(d.get("timestamp", "")),
                platform=str(d.get("platform", "")),
                author=str(d.get("author", "")),
                sentiment_score=float(d.get("sentiment_score", 0.0)),
                weight=float(d.get("weight", 1.0)),
                entity_confidence=float(d.get("entity_confidence", 0.0)),
                is_spam_promo=bool(d.get("is_spam_promo", False)),
            )
            for d in datapoints_raw
        ]
        result = compute_stock_sentiment(ticker, points, reference_time)
        if result.status == LayerStatus.NO_SIGNAL:
            logger.info(
                "[SocialAnalyst][%s] Layer C NO_SIGNAL — LLM skipped. %s",
                ticker,
                result.reason.to_log_str() if result.reason else "",
            )
            return True
        return False
    except Exception as exc:
        logger.debug(
            "[SocialAnalyst][%s] Layer C gate check failed, proceeding with LLM: %s",
            ticker,
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Blend computation
# ---------------------------------------------------------------------------

def _compute_blend_result(state: dict, ticker: str) -> Dict[str, Any]:
    """Compute the Layer E blend and return it as a serialisable dict.

    Tries to extract structured MarketSentiment / MacroSentiment / SectorSentiment
    from ``state["prefetched_social_sentiment"]`` if it is a JSON string containing
    pre-computed aggregated results (v2 pipeline format). Falls back to a
    pass-through blend (conf×1.0, size×1.0) when no structured data is available.

    The returned dict has keys: confidence_multiplier, position_size_multiplier, audit.
    """
    from tradingagents.agents.utils.scoring import blend_sentiment

    market_obj = None
    sector_obj = None
    macro_obj = None

    prefetched = state.get("prefetched_social_sentiment") or ""
    if prefetched and isinstance(prefetched, str):
        try:
            data = json.loads(prefetched)
            if isinstance(data, dict):
                market_obj = _try_build_market_sentiment(data.get("market_sentiment") or data.get("market"))
                macro_obj  = _try_build_macro_sentiment(data.get("macro_sentiment") or data.get("macro"))
                sector_obj = _try_build_sector_sentiment(
                    data.get("sector_sentiment") or data.get("sector"), ticker
                )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    blend = blend_sentiment(macro=macro_obj, market=market_obj, sector=sector_obj)
    return {
        "confidence_multiplier": blend.confidence_multiplier,
        "position_size_multiplier": blend.position_size_multiplier,
        "audit": blend.audit,
    }


def _try_build_market_sentiment(raw: Any):
    """Reconstruct a MarketSentiment from a dict if structurally valid; else None."""
    if not isinstance(raw, dict):
        return None
    try:
        from tradingagents.sentiment.contracts import (
            LayerStatus,
            MarketRegime,
            MarketSentiment,
            NoSignalReason,
            VolatilityMood,
        )
        status_str = str(raw.get("status", "NO_SIGNAL")).upper()
        if "NO_SIGNAL" in status_str or "INSUFFICIENT" in status_str:
            return MarketSentiment(
                status=LayerStatus.NO_SIGNAL,
                confidence=0.0,
                reason=NoSignalReason(
                    gate_failed="prefetch.market",
                    human_readable="market NO_SIGNAL from prefetched data",
                    metrics={},
                ),
                regime=MarketRegime.NO_SIGNAL,
                volatility_mood=VolatilityMood.NO_SIGNAL,
            )
        score_val = raw.get("score")
        if score_val is None:
            return None
        regime_str = str(raw.get("regime", "NEUTRAL")).upper()
        regime = MarketRegime[regime_str] if regime_str in MarketRegime.__members__ else MarketRegime.NEUTRAL
        return MarketSentiment(
            status=LayerStatus.SIGNAL,
            score=float(score_val),
            confidence=float(raw.get("confidence", 0.5)),
            regime=regime,
            volatility_mood=VolatilityMood.NO_SIGNAL,
            n_posts=int(raw.get("n_posts", 0)),
            n_distinct_sources=int(raw.get("n_distinct_sources", 0)),
        )
    except Exception:
        return None


def _try_build_sector_sentiment(raw: Any, ticker: str):
    """Reconstruct a SectorSentiment; else None."""
    if not isinstance(raw, dict):
        return None
    try:
        from tradingagents.sentiment.contracts import (
            LayerStatus,
            NoSignalReason,
            SectorSentiment,
        )
        from tradingagents.sentiment.taxonomy import ticker_to_sector

        status_str = str(raw.get("status", "NO_SIGNAL")).upper()
        sector_name = str(raw.get("sector") or ticker_to_sector(ticker).value)
        if "NO_SIGNAL" in status_str or "INSUFFICIENT" in status_str:
            return SectorSentiment(
                status=LayerStatus.NO_SIGNAL,
                confidence=0.0,
                reason=NoSignalReason(
                    gate_failed="prefetch.sector",
                    human_readable="sector NO_SIGNAL from prefetched data",
                    metrics={},
                ),
                sector=sector_name,
            )
        score_val = raw.get("score")
        if score_val is None:
            return None
        return SectorSentiment(
            status=LayerStatus.SIGNAL,
            score=float(score_val),
            confidence=float(raw.get("confidence", 0.5)),
            sector=sector_name,
            n_posts=int(raw.get("n_posts", 0)),
            n_distinct_days=int(raw.get("n_distinct_days", 0)),
        )
    except Exception:
        return None


def _try_build_macro_sentiment(raw: Any):
    """Reconstruct a MacroSentiment; else None."""
    if not isinstance(raw, dict):
        return None
    try:
        from tradingagents.sentiment.contracts import (
            MacroDirection,
            MacroSentiment,
            NoSignalReason,
        )
        direction_str = str(raw.get("composite_regime", "NO_SIGNAL")).upper()
        direction = (
            MacroDirection[direction_str]
            if direction_str in MacroDirection.__members__
            else MacroDirection.NO_SIGNAL
        )
        if direction == MacroDirection.NO_SIGNAL:
            return MacroSentiment(
                composite_regime=MacroDirection.NO_SIGNAL,
                reason=NoSignalReason(
                    gate_failed="prefetch.macro",
                    human_readable="macro NO_SIGNAL from prefetched data",
                    metrics={},
                ),
            )
        return MacroSentiment(composite_regime=direction, active_events=[])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# LLM narrative extraction
# ---------------------------------------------------------------------------

def _extract_narrative(text: str) -> Dict[str, Any]:
    """Extract the narrative + cited_post_ids from LLM response.

    Accepts a ```json ... ``` block or falls back to plain text extraction.
    Never returns directional fields — those are stripped even if the LLM
    included them (old prompt shape or model non-compliance).
    """
    # Strategy 1: fenced JSON block
    json_match = re.search(r"```json\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if json_match:
        try:
            raw = json.loads(json_match.group(1))
            if isinstance(raw, dict):
                return {
                    "narrative": str(raw.get("narrative", "")).strip(),
                    "cited_post_ids": list(raw.get("cited_post_ids", [])),
                }
        except json.JSONDecodeError:
            pass

    # Strategy 2: first non-empty paragraph as narrative
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    narrative = paragraphs[0] if paragraphs else text[:300].strip()
    return {"narrative": narrative, "cited_post_ids": []}


# ---------------------------------------------------------------------------
# Report text builder (shown to bull/bear researchers)
# ---------------------------------------------------------------------------

def _build_sentiment_report(ticker: str, narrative: str, blend_result: Dict) -> str:
    """Build the human-readable ``sentiment_report`` string stored in state.

    This is what bull_researcher and bear_researcher read. It surfaces:
    - The LLM narrative (or NO_SIGNAL notice)
    - The blend modifiers (confidence × and position-size ×) without a direction label
    """
    conf_mult = blend_result.get("confidence_multiplier", 1.0)
    size_mult = blend_result.get("position_size_multiplier", 1.0)
    blend_note = (
        f"Blend modifiers: confidence×{conf_mult:.2f}, position-size×{size_mult:.2f}"
    )
    if not narrative or narrative == _NO_SIGNAL_TEMPLATE:
        return (
            f"[Social sentiment for {ticker}]\n"
            f"Status: EXCLUDED — insufficient data to compute sentiment signal.\n"
            f"{blend_note} (pass-through — no sentiment data)"
        )
    return (
        f"[Social sentiment for {ticker}]\n"
        f"{narrative}\n\n"
        f"{blend_note}\n"
        f"Note: The above modifiers affect execution sizing only. "
        f"Social sentiment does NOT alter the directional investment thesis."
    )


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def create_social_media_analyst(llm):
    """Create the Social Media Sentiment Analyst agent for EGX.

    Phase 3 (PR 8) pipeline:
      1. Layer C pre-LLM gate — skip LLM when stock-level data fails hard gates
      2. LLM explainer — produce narrative + cited_post_ids only (no directional score)
      3. Blend computation — call blend_sentiment() with available aggregated layer data
      4. Write sentiment_blend_result dict to state
    """

    def social_media_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        # ── Phase 3 (PR 5): Layer C pre-LLM gate ─────────────────────────────
        stock_datapoints_raw = state.get("prefetched_stock_datapoints")
        if stock_datapoints_raw:
            if _try_layer_c_gate(ticker, stock_datapoints_raw, current_date):
                blend_result = _compute_blend_result(state, ticker)
                return {
                    "social_messages": [],
                    "sentiment_report": _NO_SIGNAL_TEMPLATE,
                    "social_sentiment_analysis": json.dumps(
                        {
                            "ticker": ticker,
                            "layer_c_status": "NO_SIGNAL",
                            "llm_narrative": "",
                            "cited_post_ids": [],
                            "blend_result": blend_result,
                        },
                        ensure_ascii=False,
                    ),
                    "sentiment_blend_result": blend_result,
                }

        tools = [get_social_sentiment, get_social_media_posts]

        # ── Phase 2c / LLM explainer path ─────────────────────────────────────
        _raw_sentiment = sanitize_external_text(state.get("prefetched_social_sentiment", ""))
        prefetched_sentiment = wrap_external_content(_raw_sentiment, "social sentiment") if _raw_sentiment else ""
        _raw_posts = sanitize_external_text(state.get("prefetched_social_posts", ""))
        prefetched_posts = wrap_external_content(_raw_posts, "social posts") if _raw_posts else ""
        has_prefetched = bool(prefetched_sentiment or prefetched_posts)

        if has_prefetched:
            prefetch_prompt = (
                f"You are a Social Media Analyst for the Egyptian Stock Exchange (EGX). "
                f"You specialize in bilingual Arabic (Egyptian dialect) + English content.\n\n"
                f"## Pre-Fetched Social Data\n"
                f"The following data has already been retrieved.\n\n"
                f"### Aggregated Sentiment Summary\n"
                f"{prefetched_sentiment or 'No structured sentiment data available.'}\n\n"
                f"### Social Media Posts\n"
                f"{prefetched_posts or 'No posts retrieved.'}\n\n"
                f"## {_LLM_ROLE_INSTRUCTION}\n\n"
                f"Analyze the above data and produce only the JSON block shown.\n"
                f"Your narrative must describe: (1) key retail discussion themes, "
                f"(2) tone and intensity, (3) any Arabic-language signals or dialects noted. "
                f"Do NOT include a direction score or confidence estimate.\n\n"
                f"```json\n"
                f'{{\n'
                f'    "narrative": "<2-3 sentences describing what retail investors are saying>",\n'
                f'    "cited_post_ids": []\n'
                f"}}\n"
                f"```\n\n"
                f"Current date: {current_date} | Ticker: {ticker}"
            )
            result = llm.invoke(prefetch_prompt, temperature=0, seed=42)
        else:
            system_message = (
                "You are a Social Media Analyst for the Egyptian Stock Exchange (EGX) "
                "specializing in bilingual Arabic (Egyptian dialect) + English content.\n\n"
                "Use the get_social_sentiment tool to retrieve aggregated sentiment scores. "
                "Use the get_social_media_posts tool to retrieve actual posts for qualitative context.\n\n"
                f"{_LLM_ROLE_INSTRUCTION}\n\n"
                "After fetching data, produce a JSON block with exactly:\n"
                '  "narrative": "<2-3 sentences on key themes, tone, Arabic signals>",\n'
                '  "cited_post_ids": ["id1", ...]  // post IDs if available\n\n'
                "Do NOT include sentiment_score, direction, confidence, or any other field."
            )

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a helpful AI assistant collaborating with other assistants."
                        " Use the provided tools to progress towards answering the question."
                        " If you are unable to fully answer, that's OK; another assistant with different tools"
                        " will help where you left off. Execute what you can to make progress."
                        " You have access to the following tools: {tool_names}.\n{system_message}"
                        "\nFor your reference, the current date is {current_date}. "
                        "The current EGX company we want to analyze is {ticker}.",
                    ),
                    MessagesPlaceholder(variable_name="messages"),
                ]
            )

            prompt = prompt.partial(system_message=system_message)
            prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
            prompt = prompt.partial(current_date=current_date)
            prompt = prompt.partial(ticker=ticker)

            chain = prompt | llm.bind_tools(tools).bind(temperature=0, seed=42)
            result = chain.invoke(
                state.get("social_messages") or [("human", ticker)]
            )

        report_text = ""
        social_analysis: Dict[str, Any] = {}

        if getattr(result, "tool_calls", None) and len(result.tool_calls) > 0:
            # Tool-calling round still in flight; return partial so LangGraph continues
            return {"social_messages": [result]}

        report_text = result.content if hasattr(result, "content") else str(result)
        extracted = _extract_narrative(report_text)

        # ── Blend computation ─────────────────────────────────────────────
        blend_result = _compute_blend_result(state, ticker)

        # ── Assemble social_sentiment_analysis ────────────────────────────
        social_analysis = {
            "ticker": ticker,
            "layer_c_status": "not_evaluated",
            "llm_narrative": extracted["narrative"],
            "cited_post_ids": extracted["cited_post_ids"],
            "blend_result": blend_result,
        }

        sentiment_report = _build_sentiment_report(ticker, extracted["narrative"], blend_result)

        return {
            "social_messages": [result],
            "sentiment_report": sentiment_report,
            "social_sentiment_analysis": json.dumps(
                social_analysis, ensure_ascii=False, default=str
            ),
            "sentiment_blend_result": blend_result,
        }

    return social_media_analyst_node
