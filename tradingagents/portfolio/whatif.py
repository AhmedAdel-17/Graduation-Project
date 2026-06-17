"""Natural-language what-if interpreter for Portfolio Assistant scenarios."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, TypeAdapter

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.llm_boundary import (
    build_boundary_llm,
    invoke_json,
    normalize_user_text,
    resolve_egx_symbol,
)

logger = logging.getLogger("tradingagents.portfolio.whatif")

WHATIF_PROMPT = """\
Convert a portfolio hypothetical into a ScenarioPatch.

Return ONLY JSON:
{
  "ops": [
    {"op": "ADD_CASH", "amount_egp": number},
    {"op": "REMOVE_CASH", "amount_egp": number},
    {"op": "CLOSE_POSITION", "ticker": "raw name or ticker"},
    {"op": "SCALE_POSITION", "ticker": "raw name or ticker", "factor": number},
    {"op": "SET_POSITION_WEIGHT", "ticker": "raw name or ticker", "weight_pct": number},
    {"op": "EXCLUDE_SECTOR", "sector": "BANKS"},
    {"op": "EXCLUDE_TICKER", "ticker": "raw name or ticker"},
    {"op": "OVERRIDE_POLICY", "field": "risk_tolerance|objective|horizon|income_preference|max_position_pct|min_cash_egp", "value": "new value"},
    {"op": "TARGET_RISK_DELTA", "vol_delta_pct": number}
  ],
  "reference": "active" | "baseline",
  "label": "short scenario label",
  "clarification": "question if ambiguous, else empty"
}

For "reduce risk by 20%" use {"op":"TARGET_RISK_DELTA","vol_delta_pct":-20}.
Do not invent tickers. Leave ambiguous requests as clarification with no ops.
"""


class WhatIfResult(BaseModel):
    """Interpreter output for P3 scenario handlers."""

    model_config = ConfigDict(extra="forbid")

    patch: Optional[s.ScenarioPatch] = None
    clarification: Optional[s.ClarificationEvent] = None


class WhatIfInterpreter:
    """LLM boundary adapter producing typed ``ScenarioPatch`` objects."""

    def __init__(self, llm: Optional[Any] = None, config: Optional[dict[str, Any]] = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self.llm = llm if llm is not None else build_boundary_llm(self.config)

    def interpret(
        self,
        text: str,
        workspace_digest: s.WorkspaceDigest | str | None = None,
        *,
        language: str = "auto",
    ) -> WhatIfResult:
        cleaned = normalize_user_text(text)
        digest = workspace_digest.model_dump(mode="json") if isinstance(workspace_digest, s.WorkspaceDigest) else workspace_digest
        try:
            payload = invoke_json(
                self.llm,
                WHATIF_PROMPT,
                f"WORKSPACE DIGEST:\n{digest or 'none'}\n\nUSER MESSAGE:\n{cleaned}",
                agent_name="portfolio.whatif",
            )
        except Exception as exc:
            logger.warning("WhatIf LLM failed, trying fallback parser: %s", exc)
            payload = self._fallback_payload(cleaned)
        return self._from_payload(payload, source_text=cleaned, language=language)

    def _from_payload(self, payload: dict[str, Any], *, source_text: str, language: str) -> WhatIfResult:
        clarification = str(payload.get("clarification") or "").strip()
        raw_ops = payload.get("ops") or []
        if clarification and not raw_ops:
            return WhatIfResult(clarification=s.ClarificationEvent(question=clarification, missing=["scenario_patch"]))

        ops: list[Any] = []
        missing: list[str] = []
        for raw in raw_ops:
            if not isinstance(raw, dict):
                continue
            op_payload = dict(raw)
            op = str(op_payload.get("op") or "")
            if op in {"CLOSE_POSITION", "SCALE_POSITION", "SET_POSITION_WEIGHT", "EXCLUDE_TICKER"}:
                resolved = resolve_egx_symbol(str(op_payload.get("ticker") or ""), context=source_text)
                if not resolved:
                    missing.append(f"ticker:{op_payload.get('ticker') or ''}")
                    continue
                op_payload["ticker"] = resolved
            if op == "OVERRIDE_POLICY" and not self._valid_policy_override(op_payload):
                missing.append(f"policy:{op_payload.get('field') or ''}")
                continue
            try:
                ops.append(TypeAdapter(s.ScenarioOp).validate_python(op_payload))
            except Exception as exc:
                logger.info("WhatIfInterpreter rejected op %s: %s", op_payload, exc)
                missing.append(op or "op")

        if missing or not ops:
            question = "Please clarify the hypothetical change."
            if language == "ar":
                question = "وضح التغيير الافتراضي المطلوب من فضلك."
            return WhatIfResult(clarification=s.ClarificationEvent(question=question, missing=missing or ["scenario_patch"]))

        patch = s.ScenarioPatch(
            ops=ops,
            reference=payload.get("reference") if payload.get("reference") in {"active", "baseline"} else "active",
            label=str(payload.get("label") or self._label_for_ops(ops)),
        )
        return WhatIfResult(patch=patch)

    @staticmethod
    def _valid_policy_override(op_payload: dict[str, Any]) -> bool:
        field = str(op_payload.get("field") or "")
        if field not in s.InvestmentPolicy.model_fields:
            return False
        try:
            base = s.InvestmentPolicy.default_policy().model_dump()
            base[field] = op_payload.get("value")
            s.InvestmentPolicy(**base)
            return True
        except Exception:
            return False

    @staticmethod
    def _label_for_ops(ops: list[Any]) -> str:
        first = ops[0]
        if isinstance(first, s.ClosePositionOp):
            return f"Sell all {first.ticker}"
        if isinstance(first, s.AddCashOp):
            return f"Add {first.amount_egp:.0f} EGP"
        if isinstance(first, s.TargetRiskDeltaOp):
            return f"Risk {first.vol_delta_pct:+.0f}%"
        return "Scenario"

    @staticmethod
    def _fallback_payload(text: str) -> dict[str, Any]:
        low = text.lower()
        risk = re.search(r"(?:risk|volatility|مخاطرة).{0,12}(-?\d+(?:\.\d+)?)\s*%", low)
        if risk:
            value = float(risk.group(1))
            if "reduce" in low or "less" in low or "قل" in text or "خفض" in text:
                value = -abs(value)
            return {"ops": [{"op": "TARGET_RISK_DELTA", "vol_delta_pct": value}], "reference": "active", "label": f"Risk {value:+.0f}%"}

        cash = re.search(r"(?:add|deposit|زود|اضف|ضيف).{0,12}(\d+(?:,\d{3})*(?:\.\d+)?)", low)
        if cash:
            return {"ops": [{"op": "ADD_CASH", "amount_egp": float(cash.group(1).replace(",", ""))}], "reference": "active", "label": "Add cash"}

        return {"ops": [], "clarification": "Please clarify the scenario change."}


__all__ = ["WHATIF_PROMPT", "WhatIfInterpreter", "WhatIfResult"]
