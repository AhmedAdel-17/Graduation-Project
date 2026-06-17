"""Advisor Narrator for Portfolio Assistant.

The narrator explains computed analytics/proposals/scenario diffs. It never
computes metrics or edits actions; all numeric JSON is injected as ground truth
and chart blocks are provided by deterministic code.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.llm_boundary import build_boundary_llm, invoke_json

logger = logging.getLogger("tradingagents.portfolio.narrator")

NARRATOR_PROMPT = """\
You are the Portfolio Assistant narrator for an EGX decision-support tool. You are
given deterministic analytics/proposal/diff JSON as ground truth.

Write a concise, friendly explanation in the user's language (English or Egyptian
Arabic) that ALWAYS covers, in this order:

1. WHY — the main driver of the proposal, in 1–3 sentences. Inspect
   proposal_json.actions[].rationale plus the before/after weights, HHI and vol.
   Each action's rationale already states its driver: an evidence-based view
   (value = low P/E, quality = ROE, or momentum), an agent signal, or pure
   portfolio construction (concentration / diversification / cash). Decide the
   dominant theme and name the single biggest move concretely (e.g. "trimming
   ETEL.CA from 43% to 15%").
   ONE plain-language sentence on method: the proposal starts from a market-cap
   equilibrium (what a neutral EGX investor would hold), then tilts toward names
   with supporting evidence using a Black-Litterman blend, all inside the user's
   risk-profile limits. Do NOT use jargon beyond "market equilibrium" and the
   factor names; keep it readable for a retail investor.
   If no name has an evidence view (all construction-driven), say plainly that the
   trades reduce concentration / diversify under the user's risk profile, NOT a
   market call on any stock.
2. WHAT — only the notable trades, very briefly. Do NOT restate the whole table
   (the UI already renders every row); one or two sentences max.
3. NEXT STEPS — a short numbered plan the user can act on:
   (a) review the proposal; (b) if you accept it, Adopt it to turn it into a
   share-level plan to take to your broker; (c) place SELLs before BUYs because
   EGX settles T+2; (d) note the estimated cost and that EGX is long-only with a
   ±10% daily price limit; (e) re-run when fresh signals or prices arrive.
   If proposal_json has expected_return_view_annual / expected_vol_after, add a
   one-line OUTLOOK: the plan's model-view expected annual return and volatility,
   explicitly a model estimate (not a forecast).

Rules:
- Explain ONLY the provided JSON. Never calculate, change, or invent numbers.
- Say "proposal", never "order" — this is decision-support for a human to review,
  not execution.
- If expected returns appear, call them model views, not forecasts.
- Return ONLY JSON: {"text": "...", "block_refs": ["block type names in display order"]}.
"""


class NarrationResult(BaseModel):
    """Text plus ordered references to already-built chat blocks."""

    model_config = ConfigDict(extra="forbid")

    text: str = ""
    blocks: list[s.ChatBlock] = Field(default_factory=list)
    block_refs: list[str] = Field(default_factory=list)


class AdvisorNarrator:
    """LLM boundary adapter for bilingual explanation."""

    def __init__(self, llm: Optional[Any] = None, config: Optional[dict[str, Any]] = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self.llm = llm if llm is not None else build_boundary_llm(self.config)

    def narrate(
        self,
        *,
        analytics: Any = None,
        proposal: Any = None,
        diff: Any = None,
        blocks: Optional[list[s.ChatBlock]] = None,
        language: str = "en",
    ) -> NarrationResult:
        """Explain deterministic inputs and return provided blocks in chosen order."""
        supplied_blocks = blocks or []
        payload = {
            "language": language,
            "analytics_json": _jsonable(analytics),
            "proposal_json": _jsonable(proposal),
            "diff_json": _jsonable(diff),
            "available_blocks": [getattr(block, "type", "") for block in supplied_blocks],
        }
        lang_word = "Egyptian Arabic" if str(language).lower().startswith("ar") else "English"
        instruction = (
            f"Write your ENTIRE answer — including the WHY / WHAT / NEXT STEPS section "
            f"headers — in {lang_word}. Do not mix languages.\n\n"
        )
        try:
            raw = invoke_json(
                self.llm,
                NARRATOR_PROMPT,
                instruction + json.dumps(payload, ensure_ascii=False, sort_keys=True),
                agent_name="portfolio.narrator",
            )
        except Exception as exc:
            logger.warning("Narrator LLM failed, using deterministic fallback: %s", exc)
            raw = {
                "text": _deterministic_narration(proposal, diff, language),
                "block_refs": [getattr(block, "type", "") for block in supplied_blocks],
            }

        text = str(raw.get("text") or "").strip()
        refs = [str(ref) for ref in raw.get("block_refs", []) if str(ref).strip()]
        ordered = _order_blocks(supplied_blocks, refs)
        return NarrationResult(text=text, blocks=ordered, block_refs=[b.type for b in ordered])


def _deterministic_narration(proposal: Any, diff: Any, language: str) -> str:
    """Reasons + next-steps the user can act on, built from the proposal without an
    LLM. Used when the narrator LLM is unavailable so the human still gets the WHY
    and the plan, not a bare "here is the proposal". Bilingual (EN / Egyptian AR)."""
    ar = str(language).lower().startswith("ar")
    pj = _jsonable(proposal) or {}
    actions = pj.get("actions") or []

    # --- strongest evidence view (if any) for a grounded one-liner ----------
    audit = pj.get("inputs_audit") or {}
    views = audit.get("views") or {}
    method = ""
    strongest = None
    if views:
        cand = max(views.items(), key=lambda kv: abs(float(kv[1].get("score", 0.0))))
        if abs(float(cand[1].get("score", 0.0))) >= 0.15:
            strongest = cand
    if audit.get("prior_source") == "market_cap_equilibrium" or strongest is not None:
        if ar:
            method = (" المنهجية: نبدأ من توزيع السوق (حسب القيمة السوقية) وبنميل ناحية الأسهم اللي "
                      "وراها أدلة (القيمة/الجودة/الزخم) باستخدام نموذج بلاك-ليترمان، في حدود مخاطرك.")
            if strongest is not None:
                t, d = strongest
                lean = "إيجابية" if float(d["score"]) > 0 else "سلبية"
                method += f" أقوى إشارة: {t} (نظرة {lean}، ثقة {float(d.get('confidence',0)):.0%})."
        else:
            method = (" Method: we start from the market-cap equilibrium and tilt toward names with "
                      "supporting evidence (value / quality / momentum) via a Black-Litterman blend, "
                      "within your risk limits.")
            if strongest is not None:
                t, d = strongest
                lean = "bullish" if float(d["score"]) > 0 else "bearish"
                method += f" Strongest view: {t} ({lean}, confidence {float(d.get('confidence',0)):.0%})."

    # --- WHY: name the single biggest weight move ---------------------------
    why = ""
    if actions:
        def _move(a: dict) -> float:
            return abs(float(a.get("target_weight_pct", 0.0)) - float(a.get("current_weight_pct", 0.0)))
        top = max(actions, key=_move)
        sells = sum(1 for a in actions if a.get("side") == "SELL")
        buys = sum(1 for a in actions if a.get("side") == "BUY")
        t = top.get("ticker", "")
        cw, tw = float(top.get("current_weight_pct", 0.0)), float(top.get("target_weight_pct", 0.0))
        if ar:
            why = (f"الاقتراح بيعيد توزيع المحفظة: أكبر حركة هي {t} من {cw:.0f}% لـ {tw:.0f}% "
                   f"({sells} بيع و{buys} شراء)، الهدف تقليل التركّز وتوزيع المخاطر حسب ملفك الاستثماري، "
                   f"مش بناءً على توصية اتجاهية لسهم معيّن.")
        else:
            why = (f"This proposal rebalances your book: the biggest move is {t} from {cw:.0f}% to "
                   f"{tw:.0f}% ({sells} sell(s), {buys} buy(s)). It's driven by reducing concentration "
                   f"and diversifying under your risk profile — not by a directional call on any stock.")
    else:
        why = ("المحفظة متوافقة بالفعل مع سياستك — مفيش صفقات مقترحة."
               if ar else
               "Your portfolio already matches your policy — no trades are proposed.")

    # --- expectation: the plan's model-view outlook (return + vol) ----------
    exp_ret = pj.get("expected_return_view_annual")
    exp_vol = pj.get("expected_vol_after")
    expectation = ""
    if exp_ret is not None and exp_vol:
        if ar:
            expectation = (f" التوقّع (نظرة نموذجية، مش تنبؤ): عائد سنوي ≈ {float(exp_ret)*100:.1f}% "
                           f"عند تذبذب ≈ {float(exp_vol)*100:.1f}%.")
        else:
            expectation = (f" Outlook (a model view, not a forecast): ~{float(exp_ret)*100:.1f}%/yr "
                           f"expected return at ~{float(exp_vol)*100:.1f}% volatility.")

    why = (why + method + expectation).strip()

    if not actions:
        return why

    cost = float(pj.get("est_total_cost_egp", 0.0) or 0.0)
    turnover = float(pj.get("est_turnover_pct", 0.0) or 0.0)
    if ar:
        steps = (
            "الخطوات الجاية:\n"
            "1) راجع الصفقات في الجدول.\n"
            "2) لو موافق، اضغط Adopt عشان يتحوّل لخطة بعدد الأسهم تاخدها لشركة السمسرة.\n"
            "3) نفّذ البيع قبل الشراء (التسوية T+2 في البورصة المصرية).\n"
            f"4) خد بالك إن التكلفة التقديرية ≈ {cost:,.0f} ج.م ومعدل الدوران ≈ {turnover:.1f}%، "
            "والسوق long-only بحد يومي ±10%.\n"
            "5) أعِد التحسين لما تتحدّث الإشارات أو الأسعار.\n"
            "ده اقتراح للمراجعة — مش أمر تنفيذ.")
    else:
        steps = (
            "Next steps:\n"
            "1) Review the trades in the table.\n"
            "2) If you accept, click Adopt to turn it into a share-level plan for your broker.\n"
            "3) Place sells before buys (EGX settles T+2).\n"
            f"4) Note the estimated cost ≈ EGP {cost:,.0f} and turnover ≈ {turnover:.1f}%; "
            "EGX is long-only with a ±10% daily limit.\n"
            "5) Re-run the optimization when fresh signals or prices arrive.\n"
            "This is a proposal for review — not an order.")
    return f"{why}\n\n{steps}"


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _order_blocks(blocks: list[s.ChatBlock], refs: list[str]) -> list[s.ChatBlock]:
    by_type: dict[str, list[s.ChatBlock]] = {}
    for block in blocks:
        by_type.setdefault(block.type, []).append(block)
    ordered: list[s.ChatBlock] = []
    for ref in refs:
        if by_type.get(ref):
            ordered.append(by_type[ref].pop(0))
    used = {id(block) for block in ordered}
    ordered.extend(block for block in blocks if id(block) not in used)
    return ordered


__all__ = ["AdvisorNarrator", "NARRATOR_PROMPT", "NarrationResult"]
