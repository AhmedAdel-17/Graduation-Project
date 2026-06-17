"""Portfolio Strategy Agent.

Infers a qualitative ``InvestmentPolicy`` from bilingual user preferences. This
is intentionally separate from ``policy_compiler.py``: the LLM understands goals,
while deterministic code converts policy into optimizer parameters.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.llm_boundary import (
    build_boundary_llm,
    invoke_json,
    normalize_user_text,
    resolve_egx_symbol,
)

logger = logging.getLogger("tradingagents.portfolio.strategy")

STRATEGY_PROMPT = """\
Infer an EGX investor policy from the user's message.

Return ONLY JSON matching this shape:
{
  "objective": "capital_preservation" | "income" | "balanced" | "growth" | "aggressive_growth" | null,
  "risk_tolerance": "very_low" | "low" | "medium" | "high" | "very_high" | null,
  "horizon": "lt_6m" | "6_12m" | "1_3y" | "gt_3y" | null,
  "income_preference": true | false | null,
  "excluded_sectors": ["BANKS", ...],
  "excluded_tickers": ["raw names or tickers"],
  "max_position_pct": number | null,
  "min_cash_egp": number | null,
  "goal_target_amount_egp": number | null,
  "goal_horizon_months": number | null,
  "monthly_contribution_egp": number | null,
  "notes": "short note",
  "source_spans": {"field": "exact user quote"}
}

Use null when the user did not address a field. Do not invent exclusions.
Fill the goal_* fields only when the user states a concrete target (e.g. "grow my
50000 to 80000 in 2 years" → goal_target_amount_egp=80000, goal_horizon_months=24;
"I can add 2000 a month" → monthly_contribution_egp=2000). Convert years to months.
"""

_QUAL_FIELDS = ("objective", "risk_tolerance", "horizon", "income_preference")


class PortfolioStrategyAgent:
    """LLM boundary adapter that emits ``InvestmentPolicy``."""

    def __init__(self, llm: Optional[Any] = None, config: Optional[dict[str, Any]] = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self.llm = llm if llm is not None else build_boundary_llm(self.config)

    def infer_policy(
        self,
        text: str,
        *,
        current_policy: Optional[s.InvestmentPolicy] = None,
        version: Optional[int] = None,
    ) -> s.InvestmentPolicy:
        """Infer a new policy draft. Confirmation is left to P3."""
        cleaned = normalize_user_text(text)
        base = current_policy.model_copy(deep=True) if current_policy else s.InvestmentPolicy.default_policy()
        try:
            payload = invoke_json(
                self.llm,
                STRATEGY_PROMPT,
                f"CURRENT POLICY:\n{base.model_dump(mode='json')}\n\nUSER MESSAGE:\n{cleaned}",
                agent_name="portfolio.strategy",
            )
        except Exception as exc:
            logger.warning("Strategy LLM failed, returning conservative default: %s", exc)
            fallback = base.model_copy(update={"confirmed_by_user": False})
            fallback.inferred_fields = sorted(set(fallback.inferred_fields) | set(_QUAL_FIELDS))
            return fallback

        return self._policy_from_payload(payload, base=base, version=version)

    def _policy_from_payload(
        self,
        payload: dict[str, Any],
        *,
        base: s.InvestmentPolicy,
        version: Optional[int],
    ) -> s.InvestmentPolicy:
        data = base.model_dump(mode="python")
        source_spans = dict(data.get("source_spans") or {})

        for field in _QUAL_FIELDS:
            if field in payload and payload[field] is not None:
                data[field] = payload[field]
                if payload.get("source_spans", {}).get(field):
                    source_spans[field] = str(payload["source_spans"][field])

        for field in ("excluded_sectors", "max_position_pct", "min_cash_egp", "notes",
                      "goal_target_amount_egp", "goal_horizon_months", "monthly_contribution_egp"):
            if field in payload and payload[field] not in (None, ""):
                data[field] = payload[field]
                if payload.get("source_spans", {}).get(field):
                    source_spans[field] = str(payload["source_spans"][field])

        excluded_tickers: list[str] = []
        for raw in payload.get("excluded_tickers") or []:
            resolved = resolve_egx_symbol(str(raw))
            if resolved:
                excluded_tickers.append(resolved)
            else:
                logger.info("StrategyAgent: unresolved excluded ticker/name: %s", raw)
        if excluded_tickers:
            data["excluded_tickers"] = sorted(set(excluded_tickers))
            if payload.get("source_spans", {}).get("excluded_tickers"):
                source_spans["excluded_tickers"] = str(payload["source_spans"]["excluded_tickers"])

        data["source_spans"] = source_spans
        data["inferred_fields"] = [
            field for field in _QUAL_FIELDS
            if not source_spans.get(field)
        ]
        data["confirmed_by_user"] = False
        data["version"] = int(version if version is not None else base.version + 1)

        try:
            return s.InvestmentPolicy(**data)
        except Exception as exc:
            logger.warning("StrategyAgent: policy validation failed, using fallback: %s", exc)
            fallback = base.model_copy(deep=True)
            fallback.version = int(version if version is not None else base.version + 1)
            fallback.confirmed_by_user = False
            return fallback


__all__ = ["PortfolioStrategyAgent", "STRATEGY_PROMPT"]
