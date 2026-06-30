"""
A/B Comparison: Single-Call vs 3-Call Competing-Hypotheses H&P Thesis Mode.

STATUS: SYNTHETIC (no live LLM). API keys are not available in this environment.
The comparison exercises the full pipeline logic with deterministic mock responses
to validate structural differences. Quality evaluation of actual LLM reasoning
requires live API access.

Tickers: COMI.CA (bank), EAST.CA (industrial), FWRY.CA (fintech),
         TMGH.CA (real estate), ABUK.CA (chemicals)
Trade date: 2024-06-01 (fixed across all runs)

Run: python scripts/ab_thesis_comparison.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig
from tradingagents.agents.analysts.fundamentals import pipeline


# ---------------------------------------------------------------------------
# Mock LLM responses per ticker (realistic EGX content)
# ---------------------------------------------------------------------------

# Concept-stage (Stage 2) responses — same for both modes
CONCEPT_RESPONSES = {
    "COMI.CA": {
        "financial_health": "healthy",
        "financial_health_rationale": "ROE 25.3%, NIM expanding, NPLs stable at 3.2%.",
        "key_metrics_discussion": "ROE well above sector median (18%). DuPont decomposition shows margin-driven.",
        "growth_signal": "positive",
        "growth_signal_rationale": "Net income +14% YoY, NIM +35bps.",
        "valuation_read": "fair",
        "valuation_rationale": "P/E 7.2x vs sector 8.5x; slight discount justified by size.",
        "risk_factors": ["FX devaluation pass-through", "Rate cycle peak risk"],
        "standout_signals": ["NIM expansion", "Improving asset quality"],
        "coherence_notes": "none",
        "analyst_note": "none",
    },
    "EAST.CA": {
        "financial_health": "concerning",
        "financial_health_rationale": "Net margin 3.1% compressed from 5.8% prior year. D/E 2.4.",
        "key_metrics_discussion": "Revenue +22% but margins squeezed by input costs.",
        "growth_signal": "mixed",
        "growth_signal_rationale": "Top-line growth but bottom-line flat due to cost pressure.",
        "valuation_read": "cheap",
        "valuation_rationale": "P/E 5.1x, P/B 0.8x — deep value if margins recover.",
        "risk_factors": ["Margin compression", "Working capital strain", "FX input costs"],
        "standout_signals": ["Revenue growth despite macro headwinds"],
        "coherence_notes": "none",
        "analyst_note": "none",
    },
    "FWRY.CA": {
        "financial_health": "healthy",
        "financial_health_rationale": "Net margin 18%, ROE 32%, zero debt.",
        "key_metrics_discussion": "Growth company metrics: high margins, low leverage, fast revenue growth.",
        "growth_signal": "positive",
        "growth_signal_rationale": "Revenue +45% YoY, net income +38%.",
        "valuation_read": "expensive",
        "valuation_rationale": "P/E 28x — growth premium fully priced.",
        "risk_factors": ["Valuation premium", "Competition from banks entering digital payments"],
        "standout_signals": ["Revenue growth acceleration"],
        "coherence_notes": "none",
        "analyst_note": "none",
    },
    "TMGH.CA": {
        "financial_health": "healthy",
        "financial_health_rationale": "Net margin 24%, strong cash position, NAV discount.",
        "key_metrics_discussion": "Land bank provides long-term visibility. Recurring revenue from hospitality.",
        "growth_signal": "positive",
        "growth_signal_rationale": "Pre-sales +30%, delivery schedule on track.",
        "valuation_read": "cheap",
        "valuation_rationale": "Trading at 0.6x NAV; significant discount to replacement cost.",
        "risk_factors": ["Interest rate sensitivity", "Delivery execution risk", "EGP cost inflation"],
        "standout_signals": ["NAV discount", "Pre-sales momentum"],
        "coherence_notes": "none",
        "analyst_note": "none",
    },
    "ABUK.CA": {
        "financial_health": "concerning",
        "financial_health_rationale": "D/E 3.1 elevated for non-financial. Margins volatile.",
        "key_metrics_discussion": "Revenue flat, operating margin declined 200bps. Capacity utilization unknown.",
        "growth_signal": "negative",
        "growth_signal_rationale": "Net income -8% YoY, no clear catalyst for reversal.",
        "valuation_read": "fair",
        "valuation_rationale": "P/E 9x — not cheap given declining earnings.",
        "risk_factors": ["High leverage", "Commodity price exposure", "Subsidy dependency"],
        "standout_signals": [],
        "coherence_notes": "none",
        "analyst_note": "Fertilizer subsidies under review by government.",
    },
}

# Single-call thesis responses
SINGLE_THESIS_RESPONSES = {
    "COMI.CA": {
        "hypothesis": "COMI net income will grow next period driven by NIM expansion.",
        "evidence_for": ["ROE 25.3% improving", "NIM +35bps YoY"],
        "evidence_against": ["D/E 6.2 limits further leverage"],
        "synthesis": "Strong profitability momentum outweighs leverage concerns.",
        "thesis_text": "COMI demonstrates robust earnings momentum driven by NIM expansion and stable asset quality. ROE at 25.3% is well above the banking sector median. The primary risk is rate cycle reversal, but current trajectory supports continued growth.",
        "fundamental_outlook": "bullish",
        "downside_risk_level": "low",
        "earnings_direction": "up",
        "earnings_direction_confidence": 76,
        "earnings_direction_rationale": "NIM expansion + improving ROE directly support earnings growth.",
        "valuation_assessment": "fair_value",
        "valuation_rationale": "P/E 7.2x slight discount to sector.",
        "financial_health": "healthy",
        "key_risks": ["Rate cycle peak", "FX pass-through"],
        "egx_specific_risks": ["EGP devaluation"],
        "invalidation_conditions": ["NIM compression >30bps"],
    },
    "EAST.CA": {
        "hypothesis": "EAST earnings will recover as input cost pressures ease.",
        "evidence_for": ["Revenue +22% shows demand strength"],
        "evidence_against": ["Net margin compressed from 5.8% to 3.1%", "D/E 2.4 elevated"],
        "synthesis": "Revenue growth is real but margin squeeze dominates near-term earnings.",
        "thesis_text": "EAST shows strong top-line momentum (+22% revenue) but severe margin compression undermines bottom-line growth. The key question is whether input costs normalize. Until margins stabilize, earnings direction remains uncertain.",
        "fundamental_outlook": "neutral",
        "downside_risk_level": "moderate",
        "earnings_direction": "flat",
        "earnings_direction_confidence": 52,
        "earnings_direction_rationale": "Revenue growth offset by margin compression; net effect unclear.",
        "valuation_assessment": "undervalued",
        "valuation_rationale": "P/E 5.1x, P/B 0.8x if margins recover.",
        "financial_health": "concerning",
        "key_risks": ["Continued margin compression", "Working capital strain"],
        "egx_specific_risks": ["FX input cost pass-through"],
        "invalidation_conditions": ["Net margin below 2% for 2 consecutive quarters"],
    },
    "FWRY.CA": {
        "hypothesis": "FWRY earnings will continue growing driven by digital payments adoption.",
        "evidence_for": ["Revenue +45%", "Net margin 18%", "Zero debt"],
        "evidence_against": ["P/E 28x leaves no margin of safety"],
        "synthesis": "Exceptional growth metrics but valuation already reflects much of the upside.",
        "thesis_text": "FWRY is a high-quality growth compounder with 45% revenue growth, 18% margins, and zero leverage. However, at 28x P/E, the market has priced in significant future growth. Earnings will likely grow but the risk-reward for new entry is less compelling.",
        "fundamental_outlook": "bullish",
        "downside_risk_level": "moderate",
        "earnings_direction": "up",
        "earnings_direction_confidence": 72,
        "earnings_direction_rationale": "Strong growth trajectory with clean balance sheet.",
        "valuation_assessment": "overvalued",
        "valuation_rationale": "28x P/E prices in multi-year growth.",
        "financial_health": "healthy",
        "key_risks": ["Valuation de-rating", "Competition from banks"],
        "egx_specific_risks": [],
        "invalidation_conditions": ["Revenue growth deceleration below 20%"],
    },
    "TMGH.CA": {
        "hypothesis": "TMGH earnings will grow as pre-sales convert to revenue recognition.",
        "evidence_for": ["Pre-sales +30%", "Net margin 24%", "NAV discount 0.6x"],
        "evidence_against": ["Interest rate sensitivity", "Delivery execution risk"],
        "synthesis": "Strong pre-sales pipeline and NAV discount provide clear value, with execution as the primary risk.",
        "thesis_text": "TMGH trades at a significant 40% discount to NAV with strong pre-sales momentum (+30%). High margins (24%) and a diversified land bank provide long-term earnings visibility. The main risk is delivery execution and rate sensitivity on buyer financing.",
        "fundamental_outlook": "bullish",
        "downside_risk_level": "moderate",
        "earnings_direction": "up",
        "earnings_direction_confidence": 70,
        "earnings_direction_rationale": "Pre-sales conversion + NAV discount support earnings growth.",
        "valuation_assessment": "undervalued",
        "valuation_rationale": "0.6x NAV is a significant discount.",
        "financial_health": "healthy",
        "key_risks": ["Delivery delays", "Rate sensitivity", "Cost inflation"],
        "egx_specific_risks": ["EGP construction cost inflation"],
        "invalidation_conditions": ["Pre-sales decline >20% QoQ"],
    },
    "ABUK.CA": {
        "hypothesis": "ABUK earnings will decline further due to margin pressure and high leverage.",
        "evidence_for": ["Net income -8% YoY", "D/E 3.1 elevated", "Operating margin -200bps"],
        "evidence_against": ["Revenue flat suggests demand not collapsing"],
        "synthesis": "Declining profitability with high leverage creates genuine downside risk.",
        "thesis_text": "ABUK faces a challenging period with declining margins, high leverage (D/E 3.1), and no clear earnings catalyst. The fertilizer subsidy review adds regulatory uncertainty. Revenue stability provides a floor but is insufficient to reverse the earnings decline.",
        "fundamental_outlook": "bearish",
        "downside_risk_level": "high",
        "earnings_direction": "down",
        "earnings_direction_confidence": 65,
        "earnings_direction_rationale": "Declining margins + high leverage + no catalyst = continued pressure.",
        "valuation_assessment": "fair_value",
        "valuation_rationale": "P/E 9x appropriate given declining trajectory.",
        "financial_health": "concerning",
        "key_risks": ["Subsidy removal", "Debt servicing pressure", "Commodity cycle"],
        "egx_specific_risks": ["Government subsidy policy change"],
        "invalidation_conditions": ["Operating margin recovery >200bps"],
    },
}

# 3-call mode responses (per ticker)
HYPOTHESES_RESPONSES = {
    "COMI.CA": {
        "hypotheses": [
            {"id": "H1", "direction": "up", "statement": "COMI net income will grow 10-15% next period driven by NIM expansion and loan book growth.", "rationale": "ROE 25.3% improving, NIM +35bps — momentum supports continued growth."},
            {"id": "H2", "direction": "flat", "statement": "COMI earnings will plateau as the rate cycle peaks and NIM expansion decelerates.", "rationale": "D/E at 6.2 limits further leverage; rate cuts would compress NIM."},
            {"id": "H3", "direction": "down", "statement": "COMI earnings will decline due to asset quality deterioration from EGP devaluation stress.", "rationale": "FX pass-through could trigger NPL spike in retail/SME portfolios."},
        ]
    },
    "EAST.CA": {
        "hypotheses": [
            {"id": "H1", "direction": "up", "statement": "EAST earnings will recover as input cost normalization restores margins to 5%+.", "rationale": "Revenue +22% proves demand; margin is the only headwind."},
            {"id": "H2", "direction": "flat", "statement": "EAST earnings remain flat as revenue growth is fully offset by persistent margin compression.", "rationale": "Net margin at 3.1% with no sign of cost relief."},
            {"id": "H3", "direction": "down", "statement": "EAST earnings will decline as D/E 2.4 triggers debt servicing costs that further erode margins.", "rationale": "High leverage + compressed margins = negative operating leverage risk."},
        ]
    },
    "FWRY.CA": {
        "hypotheses": [
            {"id": "H1", "direction": "up", "statement": "FWRY earnings will grow 30%+ as digital payments TAM expands and market share gains continue.", "rationale": "Revenue +45%, net margin 18%, zero debt — pure growth compounder."},
            {"id": "H2", "direction": "flat", "statement": "FWRY earnings growth will decelerate to single digits as base effects and competition intensify.", "rationale": "P/E 28x implies market expects deceleration; banks entering digital payments."},
            {"id": "H3", "direction": "down", "statement": "FWRY earnings will decline as banks launch competing services and squeeze transaction margins.", "rationale": "Bank competition could compress margins and slow customer acquisition."},
        ]
    },
    "TMGH.CA": {
        "hypotheses": [
            {"id": "H1", "direction": "up", "statement": "TMGH earnings will grow 15-20% as record pre-sales convert to revenue over next 12 months.", "rationale": "Pre-sales +30% with 24% margin; delivery pipeline is visibility."},
            {"id": "H2", "direction": "flat", "statement": "TMGH earnings will stagnate as construction cost inflation erodes margins despite strong pre-sales.", "rationale": "EGP devaluation inflates input costs faster than price escalation clauses."},
            {"id": "H3", "direction": "down", "statement": "TMGH earnings will decline as rising rates suppress new buyer demand and slow pre-sales.", "rationale": "Interest rate sensitivity in real estate + affordability constraints."},
        ]
    },
    "ABUK.CA": {
        "hypotheses": [
            {"id": "H1", "direction": "down", "statement": "ABUK earnings will decline 10-15% as margin pressure continues with no operational catalyst.", "rationale": "Net income -8%, D/E 3.1, operating margin -200bps — deteriorating trend."},
            {"id": "H2", "direction": "flat", "statement": "ABUK earnings stabilize near current levels as cost pass-through partially offsets input pressure.", "rationale": "Revenue flat suggests pricing power exists; margins may find floor."},
            {"id": "H3", "direction": "up", "statement": "ABUK earnings recover if government maintains subsidies and commodity cycle turns favorable.", "rationale": "Fertilizer demand is inelastic; subsidy continuation would protect margins."},
        ]
    },
}

SCORED_RESPONSES = {
    "COMI.CA": {
        "scored_hypotheses": [
            {"id": "H1", "evidence_for": ["ROE 25.3% improving", "NIM +35bps YoY", "NPLs stable at 3.2%"], "evidence_against": ["D/E 6.2 — limited room for further leverage"], "evidence_support_score": 82, "score_rationale": "Multiple data points directly support growth; counter-evidence is weak."},
            {"id": "H2", "evidence_for": ["D/E 6.2 near sector ceiling"], "evidence_against": ["NIM still expanding", "ROE improving — no deceleration signal"], "evidence_support_score": 35, "score_rationale": "Rate peak is theoretical; no data supports deceleration yet."},
            {"id": "H3", "evidence_for": ["FX risk acknowledged"], "evidence_against": ["NPLs stable at 3.2%", "No asset quality deterioration in current data"], "evidence_support_score": 18, "score_rationale": "Pure speculation; zero current-period evidence of stress."},
        ]
    },
    "EAST.CA": {
        "scored_hypotheses": [
            {"id": "H1", "evidence_for": ["Revenue +22% shows demand strength", "P/B 0.8x implies market expects recovery"], "evidence_against": ["Net margin 3.1% — no sign of improvement yet", "No evidence of input cost relief"], "evidence_support_score": 38, "score_rationale": "Recovery is hopeful but no current evidence of margin improvement."},
            {"id": "H2", "evidence_for": ["Net margin compressed from 5.8% to 3.1%", "Revenue +22% but net income flat"], "evidence_against": ["Revenue growth could eventually restore operating leverage"], "evidence_support_score": 62, "score_rationale": "Current data directly supports flat earnings — margin squeeze offsets revenue growth."},
            {"id": "H3", "evidence_for": ["D/E 2.4 elevated", "Operating margin -200bps"], "evidence_against": ["Revenue flat not declining", "No debt servicing distress signal"], "evidence_support_score": 30, "score_rationale": "Leverage is concerning but no active distress; decline requires further deterioration."},
        ]
    },
    "FWRY.CA": {
        "scored_hypotheses": [
            {"id": "H1", "evidence_for": ["Revenue +45% YoY", "Net margin 18%", "Zero debt", "ROE 32%"], "evidence_against": ["P/E 28x — growth already priced in"], "evidence_support_score": 75, "score_rationale": "Exceptional fundamentals directly support continued growth; valuation is a market concern not an earnings concern."},
            {"id": "H2", "evidence_for": ["P/E 28x implies deceleration expected", "Competition from banks noted"], "evidence_against": ["No actual deceleration visible — revenue +45%", "Margins still expanding"], "evidence_support_score": 32, "score_rationale": "Theoretical risk; zero current data shows deceleration."},
            {"id": "H3", "evidence_for": ["Competition from banks entering digital payments"], "evidence_against": ["Margins still 18%", "Revenue accelerating not decelerating", "Zero debt = no financial stress"], "evidence_support_score": 15, "score_rationale": "Speculative downside; contradicted by all current metrics."},
        ]
    },
    "TMGH.CA": {
        "scored_hypotheses": [
            {"id": "H1", "evidence_for": ["Pre-sales +30%", "Net margin 24%", "NAV discount 0.6x"], "evidence_against": ["Delivery execution risk", "EGP cost inflation"], "evidence_support_score": 72, "score_rationale": "Strong pre-sales pipeline provides direct earnings visibility; risks are execution-related not demand-related."},
            {"id": "H2", "evidence_for": ["EGP cost inflation risk noted", "Interest rate sensitivity"], "evidence_against": ["Net margin 24% still healthy", "No evidence of margin decline yet"], "evidence_support_score": 35, "score_rationale": "Cost inflation is a risk but no current margin compression visible."},
            {"id": "H3", "evidence_for": ["Interest rate sensitivity acknowledged"], "evidence_against": ["Pre-sales +30% — demand clearly not suppressed", "Strong cash position"], "evidence_support_score": 20, "score_rationale": "Contradicted by actual pre-sales data; demand is strong."},
        ]
    },
    "ABUK.CA": {
        "scored_hypotheses": [
            {"id": "H1", "evidence_for": ["Net income -8% YoY", "D/E 3.1 elevated", "Operating margin -200bps", "No clear catalyst"], "evidence_against": ["Revenue flat — not collapsing"], "evidence_support_score": 70, "score_rationale": "Multiple metrics directly support continued decline; only counter is revenue stability."},
            {"id": "H2", "evidence_for": ["Revenue flat suggests pricing power exists"], "evidence_against": ["Operating margin declining despite flat revenue", "D/E 3.1 adds pressure"], "evidence_support_score": 40, "score_rationale": "Revenue stability provides a floor but insufficient to reverse margin decline."},
            {"id": "H3", "evidence_for": ["Subsidy mention"], "evidence_against": ["Net income -8%", "Subsidy under review — not guaranteed", "No commodity cycle data available"], "evidence_support_score": 20, "score_rationale": "Recovery requires external events not visible in current data."},
        ]
    },
}

SELECTION_RESPONSES = {
    "COMI.CA": {
        "selected_hypothesis_id": "H1",
        "selection_rationale": "H1 scored 82 vs H2 at 35 and H3 at 18. NIM expansion, improving ROE, and stable NPLs provide overwhelming evidence for continued growth. The 47-point margin over the runner-up justifies high confidence.",
        "thesis_text": "COMI demonstrates strong earnings momentum with ROE at 25.3% (improving), NIM expanding +35bps, and stable asset quality (NPLs 3.2%). The bearish devaluation thesis (H3) is unsupported by current data, and the peak-rate thesis (H2) is premature given no deceleration signal. Primary risk is rate cycle reversal, which would validate H2.",
        "fundamental_outlook": "bullish",
        "downside_risk_level": "low",
        "earnings_direction": "up",
        "earnings_direction_confidence": 78,
        "earnings_direction_rationale": "H1 scored 82 with 47-point margin; NIM expansion + ROE improvement directly support growth.",
        "valuation_assessment": "fair_value",
        "valuation_rationale": "P/E 7.2x slight discount; fair given strong trajectory.",
        "financial_health": "healthy",
        "key_risks": ["Rate cycle peak could validate H2 (stagnation)", "FX stress could validate H3 (asset quality)"],
        "egx_specific_risks": ["EGP devaluation pass-through"],
        "invalidation_conditions": ["NIM compression >30bps (validates H2)", "NPL ratio above 5% (validates H3)"],
    },
    "EAST.CA": {
        "selected_hypothesis_id": "H2",
        "selection_rationale": "H2 scored 62 vs H1 at 38 and H3 at 30. Current data clearly shows revenue growth offset by margin compression. Recovery (H1) lacks evidence of cost improvement. Decline (H3) is possible but no distress signal yet.",
        "thesis_text": "EAST shows a classic growth-without-profit pattern: +22% revenue but net margin compressed from 5.8% to 3.1%. Until input costs normalize, earnings will remain flat despite strong demand. The stock is a potential value trap at P/E 5.1x unless margins recover. Key catalyst to watch: margin stabilization above 4%.",
        "fundamental_outlook": "neutral",
        "downside_risk_level": "moderate",
        "earnings_direction": "flat",
        "earnings_direction_confidence": 58,
        "earnings_direction_rationale": "H2 scored 62 but only 24-point margin over recovery thesis; genuine uncertainty about margin trajectory.",
        "valuation_assessment": "undervalued",
        "valuation_rationale": "P/E 5.1x, P/B 0.8x — cheap if margins recover, but that's the uncertainty.",
        "financial_health": "concerning",
        "key_risks": ["Margin recovery (H1) could make current valuation attractive", "Leverage spiral (H3) if margins deteriorate further"],
        "egx_specific_risks": ["FX input cost pass-through ongoing"],
        "invalidation_conditions": ["Net margin recovery above 5% (validates H1)", "Net margin below 2% for 2Q (validates H3)"],
    },
    "FWRY.CA": {
        "selected_hypothesis_id": "H1",
        "selection_rationale": "H1 scored 75 vs H2 at 32 and H3 at 15. All current metrics support continued growth — revenue +45%, margins 18%, zero debt, ROE 32%. Deceleration (H2) and decline (H3) are not supported by any current data.",
        "thesis_text": "FWRY is a rare EGX growth compounder: 45% revenue growth, 18% net margins, zero leverage, and 32% ROE. The valuation at 28x P/E is demanding but justified by the growth trajectory. Bank competition (H3) is theoretical with no margin impact visible. The key risk is growth deceleration (H2), which would trigger a valuation de-rating.",
        "fundamental_outlook": "bullish",
        "downside_risk_level": "moderate",
        "earnings_direction": "up",
        "earnings_direction_confidence": 72,
        "earnings_direction_rationale": "H1 scored 75 but H2 (deceleration) at 32 is a legitimate medium-term concern. Confidence reflects growth continuation but acknowledges high valuation.",
        "valuation_assessment": "overvalued",
        "valuation_rationale": "28x P/E fully prices in multi-year growth; limited upside from current entry.",
        "financial_health": "healthy",
        "key_risks": ["Growth deceleration below 20% (would validate H2)", "Margin compression from bank competition (H3)"],
        "egx_specific_risks": [],
        "invalidation_conditions": ["Revenue growth below 20% YoY (validates H2)", "Net margin below 12% (validates H3)"],
    },
    "TMGH.CA": {
        "selected_hypothesis_id": "H1",
        "selection_rationale": "H1 scored 72 vs H2 at 35 and H3 at 20. Pre-sales +30% and 24% margins directly support earnings growth. Cost inflation (H2) and demand decline (H3) are not visible in current data.",
        "thesis_text": "TMGH offers compelling value: 40% NAV discount, +30% pre-sales growth, and 24% net margins. The pre-sales pipeline provides 12-18 month earnings visibility. Cost inflation (H2) is a monitoring risk but margins remain healthy. Rate-driven demand weakness (H3) is contradicted by strong current pre-sales.",
        "fundamental_outlook": "bullish",
        "downside_risk_level": "moderate",
        "earnings_direction": "up",
        "earnings_direction_confidence": 68,
        "earnings_direction_rationale": "H1 scored 72 with 37-point margin; pre-sales visibility supports growth. Moderate confidence due to execution risk in delivery.",
        "valuation_assessment": "undervalued",
        "valuation_rationale": "0.6x NAV with strong pre-sales pipeline.",
        "financial_health": "healthy",
        "key_risks": ["Construction cost inflation could erode margins (H2)", "Rate hikes could slow future pre-sales (H3)", "Delivery execution risk"],
        "egx_specific_risks": ["EGP construction cost inflation"],
        "invalidation_conditions": ["Net margin below 18% (validates H2)", "Pre-sales decline >20% QoQ (validates H3)"],
    },
    "ABUK.CA": {
        "selected_hypothesis_id": "H1",
        "selection_rationale": "H1 scored 70 vs H2 at 40 and H3 at 20. Multiple metrics support continued decline: net income -8%, margins -200bps, D/E 3.1 elevated. Stabilization (H2) is possible but not yet evidenced.",
        "thesis_text": "ABUK faces structural headwinds: declining margins (-200bps), high leverage (D/E 3.1), and net income already -8% YoY with no clear catalyst for reversal. Fertilizer subsidy uncertainty adds regulatory risk. Revenue stability provides a floor but is insufficient to reverse the earnings decline. The stock is fairly valued at 9x given the deteriorating trajectory.",
        "fundamental_outlook": "bearish",
        "downside_risk_level": "high",
        "earnings_direction": "down",
        "earnings_direction_confidence": 64,
        "earnings_direction_rationale": "H1 scored 70 with 30-point margin over stabilization thesis. Multiple deterioration signals present. Confidence limited because revenue isn't collapsing (supports H2 floor).",
        "valuation_assessment": "fair_value",
        "valuation_rationale": "P/E 9x appropriate for declining earnings trajectory.",
        "financial_health": "concerning",
        "key_risks": ["Subsidy removal would accelerate decline", "Revenue stabilization could validate H2", "Commodity cycle turn could validate H3"],
        "egx_specific_risks": ["Government subsidy policy review"],
        "invalidation_conditions": ["Operating margin recovery >200bps (validates H2)", "Subsidy confirmed + commodity upturn (validates H3)"],
    },
}


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

TICKERS = ["COMI.CA", "EAST.CA", "FWRY.CA", "TMGH.CA", "ABUK.CA"]
TRADE_DATE = "2024-06-01"


def _make_report(ticker: str) -> FundamentalAnalysisReport:
    """Create a minimal valid report for testing."""
    sector_map = {
        "COMI.CA": "banks", "EAST.CA": "operational", "FWRY.CA": "operational",
        "TMGH.CA": "real_estate", "ABUK.CA": "operational",
    }
    ratios_map = {
        "COMI.CA": {"roe": 0.253, "debt_to_equity": 6.2, "net_margin": 0.32},
        "EAST.CA": {"roe": 0.08, "debt_to_equity": 2.4, "net_margin": 0.031},
        "FWRY.CA": {"roe": 0.32, "debt_to_equity": 0.0, "net_margin": 0.18},
        "TMGH.CA": {"roe": 0.18, "debt_to_equity": 0.8, "net_margin": 0.24},
        "ABUK.CA": {"roe": 0.06, "debt_to_equity": 3.1, "net_margin": 0.04},
    }
    return FundamentalAnalysisReport(
        ticker=ticker,
        analysis_date=TRADE_DATE,
        fiscal_period="FY2023",
        sector=sector_map[ticker],
        ratios=ratios_map[ticker],
        preprocessing={},
        distress_flags=[],
        data_confidence=80,
        signal_coherence=85,
    )


def run_comparison():
    """Run both modes for all tickers and collect results."""
    results_a: Dict[str, FundamentalAnalysisReport] = {}
    results_b: Dict[str, FundamentalAnalysisReport] = {}

    for ticker in TICKERS:
        report = _make_report(ticker)
        sector_cfg = SectorConfig(ticker)

        # --- Mode A: Single-call ---
        single_llm = MagicMock()
        single_llm.invoke.return_value = MagicMock(
            content=json.dumps(SINGLE_THESIS_RESPONSES[ticker])
        )
        quick_llm_a = MagicMock()
        quick_llm_a.invoke.return_value = MagicMock(
            content=json.dumps(CONCEPT_RESPONSES[ticker])
        )

        with patch.object(pipeline, "get_config", return_value={
            "thesis_cot_mode": "single",
            "use_fundamental_memory": False,
        }):
            results_a[ticker] = pipeline.run_cot_pipeline(
                quick_llm=quick_llm_a, deep_llm=single_llm,
                report=report, sector_cfg=sector_cfg,
            )

        # --- Mode B: 3-call ---
        call_count = [0]
        def make_3call_invoke(t):
            count = [0]
            def invoke(messages):
                count[0] += 1
                if count[0] == 1:
                    return MagicMock(content=json.dumps(HYPOTHESES_RESPONSES[t]))
                elif count[0] == 2:
                    return MagicMock(content=json.dumps(SCORED_RESPONSES[t]))
                else:
                    return MagicMock(content=json.dumps(SELECTION_RESPONSES[t]))
            return invoke

        deep_llm_b = MagicMock()
        deep_llm_b.invoke.side_effect = make_3call_invoke(ticker)
        quick_llm_b = MagicMock()
        quick_llm_b.invoke.return_value = MagicMock(
            content=json.dumps(CONCEPT_RESPONSES[ticker])
        )

        with patch.object(pipeline, "get_config", return_value={
            "thesis_cot_mode": "3call",
            "use_fundamental_memory": False,
        }):
            results_b[ticker] = pipeline.run_cot_pipeline(
                quick_llm=quick_llm_b, deep_llm=deep_llm_b,
                report=report, sector_cfg=sector_cfg,
            )

    return results_a, results_b


def print_comparison(results_a, results_b):
    """Print formatted comparison table and analysis."""
    print("=" * 90)
    print("A/B COMPARISON: SINGLE-CALL vs 3-CALL COMPETING-HYPOTHESES H&P")
    print("=" * 90)
    print()
    print("STATUS: SYNTHETIC (mock LLM responses — no live API calls)")
    print("        Quality evaluation requires live LLM; this validates structure only.")
    print(f"TRADE DATE: {TRADE_DATE}")
    print(f"TICKERS: {', '.join(TICKERS)}")
    print()

    # --- Summary Table ---
    print("-" * 90)
    print(f"{'Ticker':<10} {'Mode':<8} {'Direction':<10} {'Conf':<5} {'Outlook':<10} "
          f"{'Risk':<10} {'Health':<12} {'Valuation':<14}")
    print("-" * 90)

    for ticker in TICKERS:
        a = results_a[ticker]
        b = results_b[ticker]
        print(f"{ticker:<10} {'Single':<8} {a.earnings_direction:<10} {a.earnings_direction_confidence:<5} "
              f"{a.fundamental_outlook:<10} {a.downside_risk_level:<10} {a.financial_health:<12} "
              f"{a.valuation_assessment:<14}")
        print(f"{'':10} {'3-Call':<8} {b.earnings_direction:<10} {b.earnings_direction_confidence:<5} "
              f"{b.fundamental_outlook:<10} {b.downside_risk_level:<10} {b.financial_health:<12} "
              f"{b.valuation_assessment:<14}")
        print()

    # --- Direction Agreement ---
    print("-" * 90)
    print("DIRECTION AGREEMENT:")
    agree = 0
    for ticker in TICKERS:
        a_dir = results_a[ticker].earnings_direction
        b_dir = results_b[ticker].earnings_direction
        match = "AGREE" if a_dir == b_dir else "DIFFER"
        if a_dir == b_dir:
            agree += 1
        print(f"  {ticker}: {match} (A={a_dir}, B={b_dir})")
    print(f"  Agreement rate: {agree}/{len(TICKERS)} ({100*agree//len(TICKERS)}%)")
    print()

    # --- Confidence Comparison ---
    print("CONFIDENCE COMPARISON:")
    for ticker in TICKERS:
        a_conf = results_a[ticker].earnings_direction_confidence
        b_conf = results_b[ticker].earnings_direction_confidence
        diff = b_conf - a_conf
        direction = "higher" if diff > 0 else "lower" if diff < 0 else "same"
        print(f"  {ticker}: A={a_conf}, B={b_conf} ({direction:>6}, delta={diff:+d})")
    print()

    # --- 3-Call Audit Trail ---
    print("-" * 90)
    print("3-CALL COMPETING-HYPOTHESES AUDIT TRAIL:")
    print()
    for ticker in TICKERS:
        b = results_b[ticker]
        print(f"  {ticker} — Selected: {b.selected_hypothesis_id}")
        for h in b.competing_hypotheses:
            marker = " >>>" if h["id"] == b.selected_hypothesis_id else "    "
            score = next(
                (s["evidence_support_score"] for s in b.scored_hypotheses if s["id"] == h["id"]), "?"
            )
            print(f"  {marker} {h['id']} [{h['direction']:>5}] (score {score:>2}): {h['statement'][:70]}")
        print()

    # --- Cost Analysis ---
    print("-" * 90)
    print("COST ANALYSIS (LLM calls per ticker):")
    print(f"  Mode A (single): 1 concept + 1 thesis = 2 deep-equivalent calls")
    print(f"  Mode B (3-call):  1 concept + 3 thesis = 4 deep-equivalent calls")
    print(f"  Overhead: +2 calls/ticker (+100% thesis cost, +50% total cost)")
    print(f"  For 5 tickers: A=10 calls, B=20 calls")
    print()

    # --- Qualitative Analysis ---
    print("=" * 90)
    print("QUALITATIVE ANALYSIS (based on mock responses — not live LLM evaluation)")
    print("=" * 90)
    print()
    print("1. GROUNDING:")
    print("   Both modes cite evidence-pack data (ROE, NIM, margins, D/E).")
    print("   3-call mode forces explicit evidence attribution PER hypothesis,")
    print("   making unsupported claims structurally harder to introduce.")
    print()
    print("2. HYPOTHESIS QUALITY:")
    print("   3-call mode generates genuinely different hypotheses (up/flat/down)")
    print("   that represent real alternative scenarios, not filler. Validation")
    print("   enforces direction diversity (at least 2 different directions).")
    print()
    print("3. EVIDENCE WEIGHING:")
    print("   The scoring step provides auditable evidence-quality metrics.")
    print("   Score margins (e.g., COMI H1=82 vs H2=35) directly calibrate")
    print("   confidence — this is structurally better than single-call's")
    print("   unconstrained confidence assignment.")
    print()
    print("4. UNCERTAINTY:")
    print("   3-call mode links confidence to hypothesis competition:")
    print("   - COMI: 78 (47-point margin — high confidence justified)")
    print("   - EAST: 58 (24-point margin — genuinely uncertain)")
    print("   - ABUK: 64 (30-point margin — directionally clear but not extreme)")
    print("   Single-call lacks this mechanical grounding for confidence values.")
    print()
    print("5. DECISION STABILITY:")
    print("   All 5 tickers produce the SAME direction in both modes.")
    print("   This is expected — the same evidence supports the same conclusion.")
    print("   The 3-call mode adds reasoning transparency without changing outcomes.")
    print()
    print("6. KEY STRUCTURAL ADVANTAGE:")
    print("   Rejected hypotheses become explicit key_risks and invalidation_conditions.")
    print("   COMI's key_risks cite 'Rate cycle peak (H2)' and 'FX stress (H3)' —")
    print("   these are not generic risks but specific alternative scenarios the LLM")
    print("   evaluated and rejected on evidence grounds.")
    print()
    print("-" * 90)
    print("RECOMMENDATION:")
    print("-" * 90)
    print()
    print("   The 3-call competing-hypotheses mode provides material structural")
    print("   improvements in auditability, confidence calibration, and risk")
    print("   identification — at the cost of 2 extra LLM calls per ticker (+50%).")
    print()
    print("   VERDICT: Keep as OPTIONAL (thesis_cot_mode='3call').")
    print("   Do NOT make default until validated with live LLM on 10+ tickers.")
    print("   The structural benefits are clear but actual quality improvement")
    print("   requires live evaluation to confirm the LLM produces genuinely")
    print("   diverse hypotheses rather than superficial variants.")
    print()
    print("   NEXT STEPS:")
    print("   1. Run with live API on 5 tickers, manually grade outputs")
    print("   2. Check whether LLM actually produces diverse hypotheses or")
    print("      generates 3 variations of the same bullish thesis")
    print("   3. If live quality is confirmed, promote to default")
    print("   4. Consider async Call 2 (score hypotheses in parallel) for latency")
    print()


if __name__ == "__main__":
    results_a, results_b = run_comparison()
    print_comparison(results_a, results_b)
