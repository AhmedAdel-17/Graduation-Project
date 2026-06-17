"""Portfolio extraction adapter.

Turns free-form English/Egyptian-Arabic portfolio descriptions into an editable
confirmation table. The LLM proposes candidate rows; deterministic code resolves
tickers against ``SYMBOL_REGISTRY`` and validates arithmetic before anything can
reach analytics or optimization.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.llm_boundary import (
    build_boundary_llm,
    invoke_json,
    normalize_user_text,
    resolve_egx_symbol,
    unresolved_question,
)

logger = logging.getLogger("tradingagents.portfolio.extraction")

EXTRACTION_PROMPT = """\
Extract a user's EGX portfolio from English, Arabic, or mixed text.

Return ONLY JSON:
{
  "cash_egp": number | null,
  "total_value_egp": number | null,
  "holdings": [
    {
      "name": "raw issuer name or ticker",
      "ticker": "optional raw ticker if stated",
      "shares": number | null,
      "avg_cost": number | null,
      "weight_pct": number | null,
      "market_value_egp": number | null
    }
  ],
  "unresolved_names": ["names you could not map"],
  "warnings": ["short human-readable validation warnings"]
}

total_value_egp is the user's stated total invested portfolio value EXCLUDING
separate cash (e.g. "my portfolio is worth 50k and I have 20k cash" → total_value_egp=50000,
cash_egp=20000). Do not invent missing prices, shares, tickers, or percentages.
Preserve names exactly enough that deterministic registry resolution can verify them.
"""


class ExtractionResult(BaseModel):
    """Structured result consumed by P3 confirmation-gate handlers."""

    model_config = ConfigDict(extra="forbid")

    snapshot: s.PortfolioSnapshot
    block: s.ExtractedPortfolioTableBlock
    clarification: Optional[s.ClarificationEvent] = None


class PortfolioExtractionAgent:
    """LLM-assisted, registry-gated portfolio extractor."""

    def __init__(self, llm: Optional[Any] = None, config: Optional[dict[str, Any]] = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self.llm = llm if llm is not None else build_boundary_llm(self.config)

    def extract(self, text: str, *, conversation_id: Optional[str] = None, language: str = "auto") -> ExtractionResult:
        """Extract holdings/cash and build a confirmation-table block."""
        cleaned = normalize_user_text(text)
        try:
            payload = invoke_json(
                self.llm,
                EXTRACTION_PROMPT,
                f"USER PORTFOLIO MESSAGE:\n{cleaned}",
                agent_name="portfolio.extraction",
            )
        except Exception as exc:
            # Total LLM failure (all retries exhausted). Do NOT dump the whole
            # message as an "unresolved ticker" — that produced the catastrophic
            # "Couldn't match: <entire prompt>" card with cash 0. Degrade to a
            # friendly retry ask instead.
            logger.warning("Extraction LLM failed after retries: %s", exc)
            return self._llm_failure_result(conversation_id=conversation_id, language=language)

        return self._from_candidates(payload, cleaned, conversation_id=conversation_id, language=language)

    def _llm_failure_result(
        self, *, conversation_id: Optional[str], language: str,
    ) -> "ExtractionResult":
        """Graceful degradation when the extraction LLM is unreachable: an empty
        snapshot + a bilingual 'please resend' clarification (never the raw text)."""
        snapshot = s.PortfolioSnapshot(
            conversation_id=conversation_id, cash_egp=0.0, holdings=[], confirmed_by_user=False)
        block = s.ExtractedPortfolioTableBlock(data=s.ExtractedPortfolioTableData(
            holdings=[], cash_egp=0.0, total_value_egp=None, unresolved_names=[],
            warnings=["llm_unavailable"]))
        q = ("معلش، حصلت مشكلة مؤقتة وأنا بقرأ محفظتك. ابعتها تاني من فضلك — "
             "مثلاً: '٢٠٪ المصرية للاتصالات، ٤٠٪ اوراسكوم كونستراكشن، القيمة ١٠٠ ألف، كاش ٥٠ ألف'."
             if language == "ar" else
             "Sorry — I had a temporary problem reading your portfolio. Please resend it, "
             "e.g. '20% Telecom Egypt, 40% Orascom Construction, value 100k, cash 50k'.")
        clarification = s.ClarificationEvent(question=q, missing=["llm_unavailable"])
        return ExtractionResult(snapshot=snapshot, block=block, clarification=clarification)

    def _from_candidates(
        self,
        payload: dict[str, Any],
        source_text: str,
        *,
        conversation_id: Optional[str],
        language: str,
    ) -> ExtractionResult:
        holdings: list[s.PortfolioHolding] = []
        unresolved: list[str] = [str(n) for n in payload.get("unresolved_names", []) if str(n).strip()]
        warnings: list[str] = [str(w) for w in payload.get("warnings", []) if str(w).strip()]

        for raw in payload.get("holdings", []) or []:
            if not isinstance(raw, dict):
                continue
            raw_name = str(raw.get("name") or raw.get("ticker") or "").strip()
            symbol = self._resolve_candidate(raw, source_text)
            if symbol is None:
                if raw_name:
                    unresolved.append(raw_name)
                continue

            shares = _optional_float(raw.get("shares"))
            avg_cost = _optional_float(raw.get("avg_cost") or raw.get("price") or raw.get("buy_price"))
            weight_pct = _optional_float(raw.get("weight_pct") or raw.get("percent") or raw.get("pct"))
            market_value = _optional_float(raw.get("market_value_egp") or raw.get("value_egp") or raw.get("value"))

            if shares is not None and avg_cost is not None and market_value is not None:
                expected = shares * avg_cost
                if expected > 0 and abs(expected - market_value) / expected > 0.03:
                    warnings.append(
                        f"{symbol}: shares x price ({expected:.2f}) does not match stated value ({market_value:.2f})."
                    )

            try:
                holdings.append(s.PortfolioHolding(
                    ticker=symbol,
                    shares=shares,
                    avg_cost=avg_cost,
                    weight_pct=weight_pct,
                    source=s.HoldingSource.EXTRACTED,
                    name_raw=raw_name or symbol,
                ))
            except Exception as exc:
                warnings.append(f"{raw_name or symbol}: invalid numeric fields ({exc}).")

        weights = [h.weight_pct for h in holdings if h.weight_pct is not None]
        if weights and sum(weights) > 100.0 + 1e-9:
            warnings.append(f"Stated portfolio weights sum to {sum(weights):.1f}%, above 100%.")

        cash = _optional_float(payload.get("cash_egp"))
        total_value = _optional_float(payload.get("total_value_egp") or payload.get("portfolio_value_egp"))
        if total_value is not None and total_value <= 0:
            total_value = None
        snapshot = s.PortfolioSnapshot(
            conversation_id=conversation_id,
            cash_egp=max(cash or 0.0, 0.0),
            total_value_egp=total_value,
            holdings=holdings,
            confirmed_by_user=False,
        )
        data = s.ExtractedPortfolioTableData(
            holdings=holdings,
            cash_egp=snapshot.cash_egp,
            total_value_egp=total_value,
            unresolved_names=list(dict.fromkeys(unresolved)),
            warnings=list(dict.fromkeys(warnings)),
        )
        block = s.ExtractedPortfolioTableBlock(data=data)
        clarification = self.build_clarification(block, language=language)
        return ExtractionResult(snapshot=snapshot, block=block, clarification=clarification)

    @staticmethod
    def _resolve_candidate(raw: dict[str, Any], source_text: str) -> Optional[str]:
        for key in ("ticker", "symbol", "name"):
            value = raw.get(key)
            if value:
                resolved = resolve_egx_symbol(str(value), context=source_text)
                if resolved:
                    return resolved
        return None

    @staticmethod
    def build_clarification(
        block: s.ExtractedPortfolioTableBlock,
        *,
        language: str = "auto",
    ) -> Optional[s.ClarificationEvent]:
        """Ask only for issues the deterministic validators cannot safely infer."""
        missing: list[str] = []
        names = list(block.data.unresolved_names)
        for name in names:
            missing.append(f"ticker:{name}")
        if sum((h.weight_pct or 0.0) for h in block.data.holdings) > 100.0 + 1e-9:
            missing.append("weights_sum")
        if not missing:
            return None
        question = unresolved_question(names, language="ar" if language == "ar" else "en")
        if "weights_sum" in missing:
            question = (question + " " if question else "") + "Please correct the percentages so they sum to 100% or less."
        return s.ClarificationEvent(question=question, missing=missing)


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        out = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


__all__ = ["EXTRACTION_PROMPT", "ExtractionResult", "PortfolioExtractionAgent"]
