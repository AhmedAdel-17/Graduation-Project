"""Sentiment surfacing — pure functions for audit-trail extraction and formatting.

PR 9: Surfacing + audit trail.

Used by:
  - `trading_graph._log_state()` — adds sentiment block to the eval-results JSON
  - `server/api_server.py`        — writes SENTIMENT_CONTEXT JSONL event
  - `cli/main.py`                 — renders Sentiment Context panel in Rich TUI

All functions are pure (no I/O, no LLM calls) and always succeed — missing
keys yield sensible defaults and never raise.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("tradingagents.sentiment.surfacing")

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_NO_SIGNAL_LAYER_C_MARKERS = (
    "no_signal",
    "insufficient",
)


def _is_no_signal_layer_c(layer_c_status: Optional[str]) -> bool:
    """Return True when Layer C emitted NO_SIGNAL (case-insensitive)."""
    if not layer_c_status:
        return True  # unknown → treat conservatively as no-signal
    s = layer_c_status.lower()
    return any(m in s for m in _NO_SIGNAL_LAYER_C_MARKERS)


# ──────────────────────────────────────────────────────────────────────────────
# Core extractor
# ──────────────────────────────────────────────────────────────────────────────

def extract_sentiment_audit_record(final_state: Dict[str, Any]) -> Dict[str, Any]:
    """Extract a structured sentiment audit record from the final graph state.

    Returns a flat dict suitable for JSONL audit-event logging.
    Keys:

        layer_c_status          str | None   — "NO_SIGNAL: …" or "SIGNAL"
        overall_status          str | None   — "OK" | "INSUFFICIENT_DATA" | unknown
        is_no_signal            bool         — True when Layer C produced NO_SIGNAL
        confidence_multiplier   float | None
        position_size_multiplier float | None
        blend_audit             str | None   — human-readable blend provenance
        llm_narrative           str | None   — LLM-produced social narrative
        cited_post_ids          list | None  — post IDs cited by LLM
        sentiment_report_snippet str | None  — first 200 chars of raw report
        macro_direction         str | None   — RISK_OFF / RISK_ON / NEUTRAL
        market_regime           str | None   — EUPHORIA / GREED / NEUTRAL / FEAR / PANIC
        sector_tilt             str | None   — sector name or "NO_SIGNAL"

    Always succeeds; missing keys → None.
    """
    _raw_blend = final_state.get("sentiment_blend_result")
    blend: Dict[str, Any] = _raw_blend if isinstance(_raw_blend, dict) else {}
    conf_scores: Dict[str, Any] = final_state.get("confidence_scores") or {}
    social: Dict[str, Any] = final_state.get("social_sentiment_analysis") or {}
    sentiment_report: str = final_state.get("sentiment_report") or ""

    layer_c_status: Optional[str] = social.get("layer_c_status")
    overall_status: Optional[str] = conf_scores.get("overall_status")
    is_no_signal: bool = _is_no_signal_layer_c(layer_c_status)

    # Blend modifiers (may come from blend_result dict or social analysis dict)
    blend_result_inner: Dict[str, Any] = blend.get("blend_result") or blend
    conf_mult: Optional[float] = _safe_float(blend_result_inner.get("confidence_multiplier"))
    size_mult: Optional[float] = _safe_float(blend_result_inner.get("position_size_multiplier"))
    blend_audit: Optional[str] = (
        blend_result_inner.get("audit")
        or blend.get("audit")
        or None
    )

    # Macro / market / sector from blend audit string if structured data absent
    macro_direction: Optional[str] = _extract_key_from_audit(blend_audit, "macro")
    market_regime: Optional[str] = _extract_key_from_audit(blend_audit, "market")
    sector_tilt: Optional[str] = _extract_key_from_audit(blend_audit, "sector")

    snippet = sentiment_report[:200].rstrip() if sentiment_report else None

    return {
        "layer_c_status": layer_c_status,
        "overall_status": overall_status,
        "is_no_signal": is_no_signal,
        "confidence_multiplier": conf_mult,
        "position_size_multiplier": size_mult,
        "blend_audit": blend_audit,
        "llm_narrative": social.get("llm_narrative"),
        "cited_post_ids": social.get("cited_post_ids"),
        "sentiment_report_snippet": snippet,
        "macro_direction": macro_direction,
        "market_regime": market_regime,
        "sector_tilt": sector_tilt,
    }


# ──────────────────────────────────────────────────────────────────────────────
# API formatter
# ──────────────────────────────────────────────────────────────────────────────

def format_sentiment_for_api(final_state: Dict[str, Any]) -> Dict[str, Any]:
    """Return a frontend-friendly dict for inclusion in the API response body.

    Shape:
    {
        "layer_c_status":  str | None,
        "overall_status":  str | None,
        "is_no_signal":    bool,
        "blend_modifiers": {
            "confidence":    float,
            "position_size": float,
            "audit":         str | None,
        } | None,  # None when no signal or modifiers absent
        "narrative":       str | None,
        "cited_post_ids":  list | None,
        "macro_direction": str | None,
        "market_regime":   str | None,
        "sector_tilt":     str | None,
    }
    """
    rec = extract_sentiment_audit_record(final_state)

    blend_modifiers: Optional[Dict[str, Any]] = None
    if (
        not rec["is_no_signal"]
        and rec["confidence_multiplier"] is not None
        and rec["position_size_multiplier"] is not None
    ):
        blend_modifiers = {
            "confidence": rec["confidence_multiplier"],
            "position_size": rec["position_size_multiplier"],
            "audit": rec["blend_audit"],
        }

    return {
        "layer_c_status": rec["layer_c_status"],
        "overall_status": rec["overall_status"],
        "is_no_signal": rec["is_no_signal"],
        "blend_modifiers": blend_modifiers,
        "narrative": rec["llm_narrative"],
        "cited_post_ids": rec["cited_post_ids"],
        "macro_direction": rec["macro_direction"],
        "market_regime": rec["market_regime"],
        "sector_tilt": rec["sector_tilt"],
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI formatter
# ──────────────────────────────────────────────────────────────────────────────

def format_sentiment_for_cli(final_state: Dict[str, Any]) -> str:
    """Return a human-readable multi-line string for the Rich TUI Sentiment panel.

    Compact enough to fit a single panel without scrolling for the common case.
    """
    rec = extract_sentiment_audit_record(final_state)

    layer_c = rec["layer_c_status"] or "unknown"
    overall = rec["overall_status"] or "unknown"
    conf_mult = rec["confidence_multiplier"]
    size_mult = rec["position_size_multiplier"]
    audit = rec["blend_audit"] or "—"
    narrative = rec["llm_narrative"]
    macro = rec["macro_direction"]
    regime = rec["market_regime"]
    sector = rec["sector_tilt"]

    lines: List[str] = []

    # Status line
    lines.append(f"Layer C: {layer_c}   Overall: {overall}")

    # Context modifiers
    if macro or regime or sector:
        ctx_parts: List[str] = []
        if macro:
            ctx_parts.append(f"macro={macro}")
        if regime:
            ctx_parts.append(f"market={regime}")
        if sector:
            ctx_parts.append(f"sector={sector}")
        lines.append("Context: " + "  |  ".join(ctx_parts))

    # Blend modifiers
    if conf_mult is not None and size_mult is not None:
        lines.append(
            f"Blend:   confidence×{conf_mult:.2f}   position-size×{size_mult:.2f}"
        )
        lines.append(f"Audit:   {audit}")
    else:
        lines.append("Blend:   no modifiers applied (pass-through)")

    # Narrative snippet
    if narrative:
        snippet = narrative[:300].rstrip()
        if len(narrative) > 300:
            snippet += "…"
        lines.append("")
        lines.append("Narrative:")
        lines.append(snippet)

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# JSONL event builder
# ──────────────────────────────────────────────────────────────────────────────

def build_sentiment_context_event(
    session_id: str,
    ticker: str,
    trade_date: str,
    final_state: Dict[str, Any],
    logged_at: str,
) -> Dict[str, Any]:
    """Build a SENTIMENT_CONTEXT JSONL event dict.

    Suitable for appending to `audit_logs/{ticker}/audit_log.jsonl` alongside
    SESSION_START and QUICK_ANALYSIS_RESULT events.
    """
    rec = extract_sentiment_audit_record(final_state)
    return {
        "event": "SENTIMENT_CONTEXT",
        "_session_id": session_id,
        "agent_name": "SentimentBlender",
        "ticker": ticker,
        "trade_date": trade_date,
        "_logged_at": logged_at,
        # Structured fields
        "layer_c_status": rec["layer_c_status"],
        "overall_status": rec["overall_status"],
        "is_no_signal": rec["is_no_signal"],
        "confidence_multiplier": rec["confidence_multiplier"],
        "position_size_multiplier": rec["position_size_multiplier"],
        "blend_audit": rec["blend_audit"],
        "macro_direction": rec["macro_direction"],
        "market_regime": rec["market_regime"],
        "sector_tilt": rec["sector_tilt"],
        "llm_narrative": rec["llm_narrative"],
        "cited_post_ids": rec["cited_post_ids"],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> Optional[float]:
    """Coerce v to float, return None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_key_from_audit(audit: Optional[str], key: str) -> Optional[str]:
    """Pull a value from an audit string like 'macro=RISK_OFF|market=FEAR|...'."""
    if not audit:
        return None
    for part in audit.replace(",", "|").split("|"):
        part = part.strip()
        if part.lower().startswith(key + "="):
            return part.split("=", 1)[1].strip()
    return None
