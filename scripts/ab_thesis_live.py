"""
Live A/B Comparison: Single-Call vs 3-Call Competing-Hypotheses H&P.

Makes real DeepSeek API calls for both thesis modes on a diverse EGX sample.
Requires DEEPSEEK_API_KEY in .env.

Usage: python scripts/ab_thesis_live.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Setup
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from langchain_openai import ChatOpenAI

from tradingagents.agents.analysts.fundamentals.schemas import FundamentalAnalysisReport
from tradingagents.agents.analysts.fundamentals.sector_config import SectorConfig
from tradingagents.agents.analysts.fundamentals import pipeline
from tradingagents.agents.analysts.fundamentals.data_cot import build_evidence_pack
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TICKERS = ["COMI.CA", "EAST.CA", "FWRY.CA", "TMGH.CA", "ABUK.CA"]
TRADE_DATE = "2024-06-01"

# LLM setup (same as TradingAgentsGraph uses)
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
if not API_KEY:
    print("ERROR: DEEPSEEK_API_KEY not found in environment. Set it in .env")
    sys.exit(1)

deep_llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    temperature=0,
    seed=42,
    api_key=API_KEY,
)

quick_llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    temperature=0,
    seed=42,
    api_key=API_KEY,
)


# ---------------------------------------------------------------------------
# Build minimal reports from available data
# ---------------------------------------------------------------------------

def _make_report(ticker: str) -> FundamentalAnalysisReport:
    """Create a report with realistic ratios for the test tickers."""
    sector_map = {
        "COMI.CA": "banks", "EAST.CA": "operational", "FWRY.CA": "operational",
        "TMGH.CA": "real_estate", "ABUK.CA": "operational",
    }
    # Realistic EGX ratios (FY2023 estimates)
    ratios_map = {
        "COMI.CA": {"roe": 0.253, "roa": 0.028, "debt_to_equity": 6.2,
                    "net_margin": 0.32, "pe_ratio": 7.2, "pb_ratio": 1.6,
                    "eps": 13.2, "current_ratio": None},
        "EAST.CA": {"roe": 0.08, "roa": 0.03, "debt_to_equity": 2.4,
                    "net_margin": 0.031, "pe_ratio": 5.1, "pb_ratio": 0.8,
                    "eps": 2.1, "current_ratio": 1.1},
        "FWRY.CA": {"roe": 0.32, "roa": 0.22, "debt_to_equity": 0.0,
                    "net_margin": 0.18, "pe_ratio": 28.0, "pb_ratio": 8.5,
                    "eps": 3.8, "current_ratio": 2.5},
        "TMGH.CA": {"roe": 0.18, "roa": 0.09, "debt_to_equity": 0.8,
                    "net_margin": 0.24, "pe_ratio": 6.5, "pb_ratio": 0.6,
                    "eps": 5.1, "current_ratio": 1.8},
        "ABUK.CA": {"roe": 0.06, "roa": 0.02, "debt_to_equity": 3.1,
                    "net_margin": 0.04, "pe_ratio": 9.0, "pb_ratio": 0.9,
                    "eps": 1.2, "current_ratio": 0.9},
    }
    preprocessing_map = {
        "COMI.CA": {"revenue_growth_yoy": 0.18, "net_income_growth_yoy": 0.14},
        "EAST.CA": {"revenue_growth_yoy": 0.22, "net_income_growth_yoy": -0.02},
        "FWRY.CA": {"revenue_growth_yoy": 0.45, "net_income_growth_yoy": 0.38},
        "TMGH.CA": {"revenue_growth_yoy": 0.25, "net_income_growth_yoy": 0.20},
        "ABUK.CA": {"revenue_growth_yoy": 0.0, "net_income_growth_yoy": -0.08},
    }
    directions_map = {
        "COMI.CA": {"roe": "improving", "net_margin": "improving"},
        "EAST.CA": {"roe": "deteriorating", "net_margin": "deteriorating"},
        "FWRY.CA": {"roe": "improving", "net_margin": "improving"},
        "TMGH.CA": {"roe": "improving", "net_margin": "stable"},
        "ABUK.CA": {"roe": "deteriorating", "net_margin": "deteriorating"},
    }

    return FundamentalAnalysisReport(
        ticker=ticker,
        analysis_date=TRADE_DATE,
        fiscal_period="FY2023",
        sector=sector_map[ticker],
        ratios=ratios_map[ticker],
        preprocessing={
            **preprocessing_map[ticker],
            "directions": directions_map[ticker],
        },
        distress_flags=[],
        data_confidence=80,
        signal_coherence=85,
    )


# ---------------------------------------------------------------------------
# Run both modes
# ---------------------------------------------------------------------------

def run_single_ticker(ticker: str, mode: str) -> FundamentalAnalysisReport:
    """Run one ticker in the specified mode."""
    report = _make_report(ticker)
    sector_cfg = SectorConfig(ticker)

    with patch.object(pipeline, "get_config", return_value={
        "thesis_cot_mode": mode,
        "use_fundamental_memory": False,
    }):
        result = pipeline.run_cot_pipeline(
            quick_llm=quick_llm,
            deep_llm=deep_llm,
            report=report,
            sector_cfg=sector_cfg,
        )
    return result


def main():
    print("=" * 95)
    print("LIVE A/B COMPARISON: Single-Call vs 3-Call Competing-Hypotheses H&P")
    print("=" * 95)
    print(f"LLM: deepseek-chat | temperature=0 | seed=42")
    print(f"Trade date: {TRADE_DATE}")
    print(f"Tickers: {', '.join(TICKERS)}")
    print()

    results: Dict[str, Dict[str, FundamentalAnalysisReport]] = {}
    timings: Dict[str, Dict[str, float]] = {}

    for ticker in TICKERS:
        results[ticker] = {}
        timings[ticker] = {}

        for mode in ("single", "3call"):
            print(f"  Running {ticker} [{mode}]...", end=" ", flush=True)
            t0 = time.time()
            try:
                result = run_single_ticker(ticker, mode)
                elapsed = time.time() - t0
                results[ticker][mode] = result
                timings[ticker][mode] = elapsed
                print(f"done ({elapsed:.1f}s) — raw={result.raw_earnings_direction}, "
                      f"conf={result.raw_earnings_direction_confidence}")
            except Exception as e:
                elapsed = time.time() - t0
                print(f"FAILED ({elapsed:.1f}s): {e}")
                timings[ticker][mode] = elapsed

    # --- Print comparison ---
    print()
    print("-" * 95)
    print(f"{'Ticker':<10} {'Mode':<8} {'Raw Dir':<9} {'Cal Dir':<9} {'Raw Conf':<10} "
          f"{'Outlook':<10} {'Risk':<10} {'Time(s)':<8}")
    print("-" * 95)

    for ticker in TICKERS:
        for mode in ("single", "3call"):
            if mode not in results[ticker]:
                continue
            r = results[ticker][mode]
            label = "Single" if mode == "single" else "3-Call"
            t = timings[ticker][mode]
            print(f"{ticker:<10} {label:<8} {r.raw_earnings_direction:<9} "
                  f"{r.earnings_direction:<9} {r.raw_earnings_direction_confidence:<10} "
                  f"{r.fundamental_outlook:<10} {r.downside_risk_level:<10} {t:<8.1f}")
        print()

    # --- Direction agreement ---
    print("-" * 95)
    print("RAW DIRECTION AGREEMENT:")
    agree = 0
    for ticker in TICKERS:
        if "single" in results[ticker] and "3call" in results[ticker]:
            a = results[ticker]["single"].raw_earnings_direction
            b = results[ticker]["3call"].raw_earnings_direction
            match = "AGREE" if a == b else "DIFFER"
            if a == b:
                agree += 1
            print(f"  {ticker}: {match} (single={a}, 3call={b})")
    print(f"  Agreement: {agree}/{len(TICKERS)}")
    print()

    # --- 3-Call audit trail ---
    print("-" * 95)
    print("3-CALL COMPETING-HYPOTHESES AUDIT TRAIL:")
    print()
    for ticker in TICKERS:
        if "3call" not in results[ticker]:
            continue
        r = results[ticker]["3call"]
        print(f"  {ticker} — Selected: {r.selected_hypothesis_id}")
        for h in r.competing_hypotheses:
            marker = " >>>" if h.get("id") == r.selected_hypothesis_id else "    "
            score = "?"
            for s in r.scored_hypotheses:
                if s.get("id") == h.get("id"):
                    score = s.get("evidence_support_score", "?")
                    break
            stmt = h.get("statement", "")[:72]
            print(f"  {marker} {h.get('id', '?')} [{h.get('direction', '?'):>5}] "
                  f"(score {score:>3}): {stmt}")
        print()

    # --- Thesis text comparison ---
    print("-" * 95)
    print("THESIS TEXT COMPARISON (first 200 chars):")
    print()
    for ticker in TICKERS:
        for mode in ("single", "3call"):
            if mode not in results[ticker]:
                continue
            r = results[ticker][mode]
            label = "Single" if mode == "single" else "3-Call"
            text = r.thesis_text[:200] if r.thesis_text else "(empty)"
            print(f"  {ticker} [{label}]: {text}")
        print()

    # --- Cost ---
    print("-" * 95)
    print("TIMING & COST:")
    total_single = sum(timings[t].get("single", 0) for t in TICKERS)
    total_3call = sum(timings[t].get("3call", 0) for t in TICKERS)
    print(f"  Total single-mode: {total_single:.1f}s ({len(TICKERS)} tickers × 2 calls)")
    print(f"  Total 3-call mode: {total_3call:.1f}s ({len(TICKERS)} tickers × 4 calls)")
    print(f"  Overhead: {total_3call - total_single:.1f}s (+{100*(total_3call-total_single)/max(total_single,0.1):.0f}%)")
    print()

    # Save full results
    output_path = _PROJECT_ROOT / "scripts" / "ab_thesis_live_results.json"
    serializable = {}
    for ticker in TICKERS:
        serializable[ticker] = {}
        for mode in ("single", "3call"):
            if mode in results[ticker]:
                r = results[ticker][mode]
                serializable[ticker][mode] = {
                    "raw_earnings_direction": r.raw_earnings_direction,
                    "earnings_direction": r.earnings_direction,
                    "raw_earnings_direction_confidence": r.raw_earnings_direction_confidence,
                    "earnings_direction_confidence": r.earnings_direction_confidence,
                    "fundamental_outlook": r.fundamental_outlook,
                    "downside_risk_level": r.downside_risk_level,
                    "financial_health": r.financial_health,
                    "valuation_assessment": r.valuation_assessment,
                    "thesis_text": r.thesis_text,
                    "key_risks": r.key_risks,
                    "competing_hypotheses": r.competing_hypotheses,
                    "scored_hypotheses": r.scored_hypotheses,
                    "selected_hypothesis_id": r.selected_hypothesis_id,
                    "time_seconds": timings[ticker][mode],
                }
    with open(output_path, "w") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)
    print(f"Full results saved to: {output_path}")


if __name__ == "__main__":
    main()
