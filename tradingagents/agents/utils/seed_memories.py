"""Hand-curated EGX seed memories — bootstraps the agent memory collections
so the first 10–20 trades aren't running with an empty ``past_memory_str``.

Each entry is a dict with the canonical metadata keys used by
``FinancialSituationMemory`` (see ``memory.py``):

    agent_name    : "bull_memory" | "bear_memory" | "trader_memory" |
                    "invest_judge_memory" | "risk_manager_memory"
    ticker        : EGX symbol with ``.CA`` suffix
    memory_type   : "thesis" | "execution" | "risk_decision"
    trade_date    : ISO date string of the original decision
    situation     : free-text snapshot of market/news/fundamentals at that date
    recommendation: lesson learned (used as the past_memory_str in prompts)
    outcome       : JSON-encoded dict with verdict + forward return for retrieval ranking

Tickers covered: COMI.CA (banks), TMGH.CA (real estate), ETEL.CA (telecom),
EAST.CA (industry), HRHO.CA (financial services), ABUK.CA (chemicals).

Notes:
- Outcomes are based on plausible / illustrative EGX scenarios from
  2023-2024, not literal trade records. The point is to give agents
  realistic structural lessons (e.g., "rate-cut tailwind for banks",
  "EGP devaluation hurts importers") — not to backtest the corpus itself.
- All numeric returns are pre-cost. Verdict thresholds: WIN > +1%, LOSS < -1%,
  else NEUTRAL.
- Expansion to EGX-30/-70 + Arabic-language variants is on the roadmap
  (MEMORY.md §K follow-up).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


def _outcome(verdict: str, forward_return: float, horizon: int = 20, action: str = "BUY") -> str:
    """JSON-encode an outcome payload (Chroma metadata values are scalar-only)."""
    return json.dumps(
        {
            "verdict": verdict,
            "forward_return": forward_return,
            "horizon_days": horizon,
            "action": action,
        },
        ensure_ascii=False,
    )


EGX_SEED_MEMORIES: List[Dict[str, Any]] = [
    # ── BULL THESES (good and bad outcomes — both teach) ─────────────────────
    {
        "agent_name": "bull_memory",
        "ticker": "COMI.CA",
        "memory_type": "thesis",
        "trade_date": "2023-11-15",
        "situation": "CIB posts Q3 NIM 7.8%, NPL ratio 3.2%, CAR 21%. CBE policy rate at 19.25%. "
                     "Deposits growing 18% YoY in EGP, FX deposits stable. EGX-30 down 4% YTD.",
        "recommendation": "Egyptian bank earnings benefit from high-rate environment; "
                          "long position justified when NIM > 7%, NPL < 4%, CAR > 18%.",
        "outcome": _outcome("WIN", 0.042, 20, "BUY"),
    },
    {
        "agent_name": "bull_memory",
        "ticker": "COMI.CA",
        "memory_type": "thesis",
        "trade_date": "2024-03-10",
        "situation": "CBE hikes 600bps to 27.25% post-IMF agreement. CIB stock down 8% on cycle "
                     "uncertainty despite consensus EPS upgrade. Deposit beta lagging 30%.",
        "recommendation": "Initial hike-shock sell-offs in CIB are buyable when deposit beta is "
                          "lagging — NIM expansion materializes within 1–2 quarters.",
        "outcome": _outcome("WIN", 0.078, 20, "BUY"),
    },
    {
        "agent_name": "bull_memory",
        "ticker": "TMGH.CA",
        "memory_type": "thesis",
        "trade_date": "2024-02-01",
        "situation": "TMG closes Ras El-Hekma land sale settlement, $24B inflow announced. "
                     "EGP devaluation expected to lift USD-pegged receivables. Construction backlog 4.2 years.",
        "recommendation": "EGP devaluation events are net-positive for TMG when USD-pegged backlog "
                          "exceeds EGP cost base. Sell the news only after stock has +15%.",
        "outcome": _outcome("WIN", 0.115, 20, "BUY"),
    },
    {
        "agent_name": "bull_memory",
        "ticker": "ETEL.CA",
        "memory_type": "thesis",
        "trade_date": "2024-01-20",
        "situation": "Telecom Egypt FY24 capex guide -15% YoY, fiber rollout milestone hit. "
                     "Vodafone EG stake monetization rumours. EGX-30 +6% MoM, telecom rotating in.",
        "recommendation": "ETEL outperforms when EGX-30 rotates defensive and capex peak passes. "
                          "Avoid chasing once dividend yield falls below 9%.",
        "outcome": _outcome("WIN", 0.058, 20, "BUY"),
    },
    {
        "agent_name": "bull_memory",
        "ticker": "ABUK.CA",
        "memory_type": "thesis",
        "trade_date": "2023-09-12",
        "situation": "Ammonia futures +18% on Black Sea disruption. Abou Kir export "
                     "share 65% in USD. Natural gas subsidy renewal confirmed for FY24.",
        "recommendation": "Abou Kir is a USD-revenue/EGP-cost hedge — long bias when ammonia "
                          "spot price > $400/t AND gas subsidy is intact.",
        "outcome": _outcome("WIN", 0.094, 20, "BUY"),
    },
    {
        "agent_name": "bull_memory",
        "ticker": "HRHO.CA",
        "memory_type": "thesis",
        "trade_date": "2023-10-04",
        "situation": "EFG Hermes IB pipeline $1.2B in EGX IPOs queued. Markets fee growth +28% YoY. "
                     "EGX volumes up 35% MoM as IMF program speculation builds.",
        "recommendation": "EFG outperforms when EGX volumes ramp ahead of IMF events — long but "
                          "size at 60% normal because beta to EGX-30 is 1.6.",
        "outcome": _outcome("LOSS", -0.022, 20, "BUY"),
    },

    # ── BEAR THESES ──────────────────────────────────────────────────────────
    {
        "agent_name": "bear_memory",
        "ticker": "TMGH.CA",
        "memory_type": "thesis",
        "trade_date": "2023-08-22",
        "situation": "TMG inventory turnover slowing, off-plan sales -22% YoY. EGP devaluation "
                     "expectations pushing buyers to dollar instruments. Construction inflation +35%.",
        "recommendation": "Egyptian real estate bear thesis triggers when off-plan velocity drops AND "
                          "construction inflation outpaces selling-price increases. Avoid catching falling knife.",
        "outcome": _outcome("WIN", -0.068, 20, "SELL"),
    },
    {
        "agent_name": "bear_memory",
        "ticker": "EAST.CA",
        "memory_type": "thesis",
        "trade_date": "2024-04-08",
        "situation": "Eastern Tobacco loses regulatory pricing flexibility, FRA tariff cap imposed. "
                     "Volume -12% YoY on smuggling surge. Excise rate stable but margin compression underway.",
        "recommendation": "Pricing-power loss in Egyptian regulated consumer staples is a structural "
                          "bear catalyst — exit fully, don't average down.",
        "outcome": _outcome("WIN", -0.041, 20, "SELL"),
    },
    {
        "agent_name": "bear_memory",
        "ticker": "ETEL.CA",
        "memory_type": "thesis",
        "trade_date": "2023-12-15",
        "situation": "Telecom Egypt capex revised up 22% on 5G mandate. Net debt/EBITDA 3.1x, "
                     "above 2.5x covenant. FX exposure 40% of opex, EGP weakening trend.",
        "recommendation": "When ETEL net debt/EBITDA breaches 3.0x with FX exposure, bear bias is "
                          "warranted until refinancing terms are public.",
        "outcome": _outcome("LOSS", 0.018, 20, "SELL"),
    },
    {
        "agent_name": "bear_memory",
        "ticker": "ABUK.CA",
        "memory_type": "thesis",
        "trade_date": "2024-05-22",
        "situation": "Ammonia spot at $260/t (down from $580). European demand soft. "
                     "Government considering raising natural gas industrial tariff by 18%.",
        "recommendation": "Abou Kir bear case fires when ammonia < $300/t AND gas subsidy at risk. "
                          "Tight stop on policy reversal — government often backtracks on industry.",
        "outcome": _outcome("NEUTRAL", -0.005, 20, "SELL"),
    },
    {
        "agent_name": "bear_memory",
        "ticker": "COMI.CA",
        "memory_type": "thesis",
        "trade_date": "2024-09-18",
        "situation": "CBE signals 200bps cut in next meeting. CIB NIM peaked at 8.1%. "
                     "Deposit beta catching up to 75%. Loan growth flat at 4% YoY.",
        "recommendation": "Banks roll over from peak-rate cycle when deposit beta > 70% and NIM "
                          "stops expanding. Short into rate-cut anticipation, cover post-decision.",
        "outcome": _outcome("WIN", -0.038, 20, "SELL"),
    },

    # ── TRADER (execution / sizing) ──────────────────────────────────────────
    {
        "agent_name": "trader_memory",
        "ticker": "COMI.CA",
        "memory_type": "execution",
        "trade_date": "2024-02-15",
        "situation": "COMI ADV 1.2M shares, our target 80k shares (6.7% of ADV). EGX +/-10% "
                     "circuit breaker in effect. Risk approved 0.6× sizing post-veto on aggressive case.",
        "recommendation": "Honor 10% ADV cap and breaker — split into 3 tranches (08:00, 11:00, 14:00 EGT). "
                          "T+2 settlement means cash unblocks only after second business day.",
        "outcome": _outcome("WIN", 0.034, 20, "BUY"),
    },
    {
        "agent_name": "trader_memory",
        "ticker": "TMGH.CA",
        "memory_type": "execution",
        "trade_date": "2024-02-05",
        "situation": "Ras El-Hekma announcement spike, TMG up 9.2% intraday — approaching daily "
                     "circuit breaker. Limit orders at +9.5% mostly unfilled.",
        "recommendation": "Near-breaker days: tighten limit to +/-9.0% and split execution; expect "
                          "post-breaker gap on next session if news flow continues.",
        "outcome": _outcome("WIN", 0.062, 20, "BUY"),
    },
    {
        "agent_name": "trader_memory",
        "ticker": "ETEL.CA",
        "memory_type": "execution",
        "trade_date": "2023-11-08",
        "situation": "ETEL ADV 850k, low-liquidity day (volume 40% below 30d avg). "
                     "Bid-ask 0.18 EGP on a 22 EGP price. Our target 60k shares.",
        "recommendation": "Low-liquidity EGX names: halve target size, use VWAP over 4-hour window. "
                          "Spread cost on small-cap EGX can exceed 0.8% — model it explicitly.",
        "outcome": _outcome("WIN", 0.024, 20, "BUY"),
    },
    {
        "agent_name": "trader_memory",
        "ticker": "EAST.CA",
        "memory_type": "execution",
        "trade_date": "2023-12-05",
        "situation": "Eastern Tobacco low-liquidity, ADV 320k. Trader plan was 50k BUY but "
                     "market depth only 22k at top 5 levels. Confidence 0.65.",
        "recommendation": "Hard rule: target size must not exceed top-5-level depth on EGX small caps. "
                          "Better to underfill than to walk the book and pay 1%+ slippage.",
        "outcome": _outcome("WIN", 0.019, 20, "BUY"),
    },
    {
        "agent_name": "trader_memory",
        "ticker": "HRHO.CA",
        "memory_type": "execution",
        "trade_date": "2024-06-14",
        "situation": "HRHO ATR(14) jumped from 0.32 to 0.71 in 5 days. Earnings in 3 days. "
                     "Bull thesis 0.78 confidence, but high vol makes stop-loss placement difficult.",
        "recommendation": "ATR-based stops on HRHO: 2.0× ATR works in calm regimes (vol < 0.4), "
                          "but use 3.0× ATR pre-earnings to avoid premature stop-out.",
        "outcome": _outcome("LOSS", -0.029, 20, "BUY"),
    },
    {
        "agent_name": "trader_memory",
        "ticker": "ABUK.CA",
        "memory_type": "execution",
        "trade_date": "2024-03-08",
        "situation": "Abou Kir circuit breaker on EGP-flotation day, gapped down -9.8%. "
                     "Pre-flotation BUY trigger from bull thesis. Stop-loss not honored — gap below.",
        "recommendation": "On scheduled regime-change days (devaluation, IMF announcements), "
                          "either fully exit or accept gap risk — stop orders DO NOT execute through breakers.",
        "outcome": _outcome("LOSS", -0.082, 20, "BUY"),
    },

    # ── INVEST JUDGE (research-manager arbitration) ──────────────────────────
    {
        "agent_name": "invest_judge_memory",
        "ticker": "COMI.CA",
        "memory_type": "thesis",
        "trade_date": "2024-02-15",
        "situation": "Bull: rate-cycle tailwind, NIM 7.8%, deposit beta lagging. "
                     "Bear: peak earnings, deposit beta catching up by Q4. Tie on data quality.",
        "recommendation": "Banking debates resolve to bull while NIM is still expanding QoQ. "
                          "Switch to neutral when 2 consecutive flat NIM quarters print.",
        "outcome": _outcome("WIN", 0.034, 20, "BUY"),
    },
    {
        "agent_name": "invest_judge_memory",
        "ticker": "TMGH.CA",
        "memory_type": "thesis",
        "trade_date": "2024-02-04",
        "situation": "Bull: $24B Ras El-Hekma inflow, USD backlog hedge. "
                     "Bear: stock +15% already, news priced in. Bull confidence 0.82, bear 0.68.",
        "recommendation": "Major sovereign-deal catalysts in EGX real estate justify continued bull "
                          "even after +15% — momentum windows typically last 3–4 weeks.",
        "outcome": _outcome("WIN", 0.062, 20, "BUY"),
    },
    {
        "agent_name": "invest_judge_memory",
        "ticker": "EAST.CA",
        "memory_type": "thesis",
        "trade_date": "2024-04-15",
        "situation": "Bull: cheap on PE 4.5×, dividend yield 11%. "
                     "Bear: pricing-power loss, FRA tariff cap, smuggling. Bear confidence 0.85.",
        "recommendation": "When the bear case attacks structural pricing power (regulator move), "
                          "yield/PE valuation is a trap. Side with bear regardless of confidence delta.",
        "outcome": _outcome("WIN", -0.041, 20, "SELL"),
    },
    {
        "agent_name": "invest_judge_memory",
        "ticker": "HRHO.CA",
        "memory_type": "thesis",
        "trade_date": "2024-01-22",
        "situation": "Bull: EGX volume surge, IPO pipeline. Bear: post-IMF risk-on may fade, "
                     "HRHO beta 1.6. Both confidences ~0.70.",
        "recommendation": "Beta-1.6 names: only conviction-bull when EGX-30 trend is unbroken. "
                          "Step down to neutral the moment EGX-30 closes below 20-day SMA.",
        "outcome": _outcome("NEUTRAL", 0.008, 20, "BUY"),
    },
    {
        "agent_name": "invest_judge_memory",
        "ticker": "ETEL.CA",
        "memory_type": "thesis",
        "trade_date": "2023-12-20",
        "situation": "Bull: defensive rotation. Bear: debt covenant pressure, capex up. "
                     "Bear confidence 0.78, well-cited; bull 0.62, more sentiment-based.",
        "recommendation": "When bear is data-citing and bull is narrative-only, side with bear "
                          "even at minor confidence parity.",
        "outcome": _outcome("LOSS", 0.018, 20, "SELL"),
    },
    {
        "agent_name": "invest_judge_memory",
        "ticker": "ABUK.CA",
        "memory_type": "thesis",
        "trade_date": "2023-09-14",
        "situation": "Bull: USD hedge on ammonia exports. Bear: commodity downcycle starting. "
                     "Spot prices ambiguous; bull cites futures curve.",
        "recommendation": "On EGX commodity-linked names, futures curve trumps spot when forward "
                          "20-day signal is needed. Bull wins when curve > spot.",
        "outcome": _outcome("WIN", 0.094, 20, "BUY"),
    },

    # ── RISK MANAGER (deterministic veto + LLM judge interplay) ──────────────
    {
        "agent_name": "risk_manager_memory",
        "ticker": "COMI.CA",
        "memory_type": "risk_decision",
        "trade_date": "2024-02-15",
        "situation": "Trader plan: BUY 120k shares COMI.CA. ADV 1.2M → 10.0% of ADV. "
                     "Single-stock concentration 28% in backtest portfolio. Liquidity normal.",
        "recommendation": "10.0% ADV is AT the cap, not below — risk should approve with size cut to "
                          "9% to leave headroom. Concentration veto disabled in backtest_mode is fine.",
        "outcome": _outcome("WIN", 0.034, 20, "BUY"),
    },
    {
        "agent_name": "risk_manager_memory",
        "ticker": "TMGH.CA",
        "memory_type": "risk_decision",
        "trade_date": "2024-02-04",
        "situation": "Trader plan: BUY at +9.5% limit on circuit-breaker day. Deterministic veto "
                     "triggered: 'order price too close to breaker'. Aggressive risk overrode.",
        "recommendation": "Honor the deterministic breaker check — overriding it costs 30-50% of "
                          "the would-be win in unfilled-spread alone. Defer to next session.",
        "outcome": _outcome("WIN", 0.062, 20, "BUY"),
    },
    {
        "agent_name": "risk_manager_memory",
        "ticker": "ETEL.CA",
        "memory_type": "risk_decision",
        "trade_date": "2023-11-08",
        "situation": "Trader plan: BUY 80k ETEL low-liquidity day. ADV 850k → 9.4% (under cap). "
                     "But intraday volume 40% below 30d avg → effective ADV today ~510k.",
        "recommendation": "Risk should apply intraday-ADV adjustment: when volume <70% of 30d avg, "
                          "use intraday ADV not historical. Cap at 8% of effective ADV.",
        "outcome": _outcome("WIN", 0.024, 20, "BUY"),
    },
    {
        "agent_name": "risk_manager_memory",
        "ticker": "EAST.CA",
        "memory_type": "risk_decision",
        "trade_date": "2024-04-15",
        "situation": "Bear thesis approved; trader plan SELL 100% of position. "
                     "EGX no short selling — only existing long can be sold. Position size: 4.2% of portfolio.",
        "recommendation": "EGX long-only constraint is hard: SELL plan must be capped by current "
                          "position size, never opens a short. Always cross-check trader plan vs positions.",
        "outcome": _outcome("WIN", -0.041, 20, "SELL"),
    },
    {
        "agent_name": "risk_manager_memory",
        "ticker": "HRHO.CA",
        "memory_type": "risk_decision",
        "trade_date": "2024-06-14",
        "situation": "Pre-earnings BUY plan, ATR(14) 2.2× normal. Trader stop-loss at -3.5%. "
                     "Risk score asked: is this stop wide enough for the vol regime? Confidence 0.78.",
        "recommendation": "Pre-earnings stops on EGX volatile names should be ≥3.0× ATR or skip the "
                          "trade. -3.5% on 2.2× ATR is too tight; expect premature stop-out 60%+ of time.",
        "outcome": _outcome("LOSS", -0.029, 20, "BUY"),
    },
    {
        "agent_name": "risk_manager_memory",
        "ticker": "ABUK.CA",
        "memory_type": "risk_decision",
        "trade_date": "2024-03-08",
        "situation": "EGP-flotation announced for tomorrow; trader plan still BUY. Aggressive risk "
                     "voted to hold position; safe risk vetoed. Stop-loss at -5%. Gap risk obvious.",
        "recommendation": "On scheduled regime-change days, exit fully BEFORE the event regardless of "
                          "thesis strength. Stop orders DO NOT save you through circuit-breaker gaps.",
        "outcome": _outcome("LOSS", -0.082, 20, "BUY"),
    },
]


def _compute_valid_after_date(entry: Dict[str, Any]) -> str:
    """Compute valid_after_date for a seed entry.

    Seeds contain outcome/hindsight information (they know what happened after
    trade_date over the horizon). Therefore valid_after_date = trade_date + horizon_days.
    This prevents them from being retrieved for decisions BEFORE the outcome was known.
    """
    from datetime import datetime, timedelta

    trade_date = entry.get("trade_date")
    if not trade_date:
        return "1900-01-01"  # always valid if no date

    # Extract horizon from the outcome JSON
    horizon_days = 20  # default
    outcome_str = entry.get("outcome")
    if outcome_str:
        try:
            outcome = json.loads(outcome_str)
            horizon_days = outcome.get("horizon_days", 20) or 20
        except (json.JSONDecodeError, TypeError):
            pass

    dt = datetime.strptime(trade_date, "%Y-%m-%d")
    return (dt + timedelta(days=horizon_days)).strftime("%Y-%m-%d")


def get_seeds_for_agent(agent_name: str) -> List[Dict[str, Any]]:
    """Return only the seeds destined for a given agent collection.

    Each returned entry includes ``valid_after_date`` computed as
    ``trade_date + horizon_days`` (seeds contain outcome knowledge).
    """
    seeds = [m for m in EGX_SEED_MEMORIES if m.get("agent_name") == agent_name]
    for s in seeds:
        if "valid_after_date" not in s:
            s["valid_after_date"] = _compute_valid_after_date(s)
    return seeds


def all_agent_names() -> List[str]:
    """Distinct agent_name values present in the corpus."""
    return sorted({m["agent_name"] for m in EGX_SEED_MEMORIES})
