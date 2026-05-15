import functools
import time
import json
import logging
import re
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.macro_provider import format_macro_context_for_prompt
from tradingagents.agents.utils.llm_failover import safe_invoke

logger = logging.getLogger("tradingagents.trader")

# =============================================================================
# Institutional Trader Agent for EGX Market
# =============================================================================
# Generates structured EXECUTION PLANS, not just BUY/HOLD/SELL
# Respects EGX constraints: long-only, no leverage, no market orders
# Position sizing is liquidity-adjusted
# =============================================================================

# Execution constraints from EGX institutional best practices
MAX_POSITION_PCT_ADV = 0.10        # Max 10% of Average Daily Volume per day
MAX_PORTFOLIO_SINGLE_STOCK = 0.10  # Max 10% of portfolio in single stock (live mode)
MAX_LOSS_PER_TRADE_PCT = 0.02      # Max 2% portfolio loss per trade — Elder/Tharp.
                                   # ENFORCED EVEN IN BACKTEST MODE (Risk Scorer Clause 13).
DAILY_PRICE_LIMIT = 0.10           # EGX ±10% circuit breaker

# Default stop-loss assumption used to pre-compute the loss-limit position size.
# A 5% stop is a conservative midpoint between ATR-based stops (typically 4–8%)
# and the EGX daily price band (10%). The Trader can choose a different stop in
# its plan, but the Risk Scorer will re-validate against the 2% loss limit.
DEFAULT_STOP_DISTANCE_PCT = 0.05

# Order type constraints
ALLOWED_ORDER_TYPES = ["limit", "limit_ioc", "vwap", "twap"]
FORBIDDEN_ORDER_TYPES = ["market", "market_on_close", "stop_market"]


def calculate_position_limits(
    avg_daily_volume: float,
    current_price: float,
    portfolio_value: float,
    low_liquidity: bool = False,
    stop_distance_pct: float = DEFAULT_STOP_DISTANCE_PCT,
) -> dict:
    """
    Calculate position sizing limits — three independent caps, take the MIN.

    1. ADV cap         — max 10% of average daily volume per day
    2. Portfolio cap   — max 10% of portfolio in one stock (lifted to 100% in backtest)
    3. Loss-limit cap  — max 2% of portfolio at risk per trade (Clause 13).
                         This is the BINDING institutional rule and is ENFORCED
                         even in backtest_mode (Elder 1993; Tharp 2008).

    The Trader was previously sizing positions ignoring the loss-limit cap,
    which caused the deterministic Risk Scorer to veto every BUY plan.
    """
    config = get_config()
    backtest_mode = config.get("backtest_mode", False)

    # ── Cap 1: ADV-based daily execution limit ──
    adv_limit_pct = MAX_POSITION_PCT_ADV * 0.5 if low_liquidity else MAX_POSITION_PCT_ADV
    max_shares_per_day = int(avg_daily_volume * adv_limit_pct)

    # ── Cap 2: Single-stock portfolio concentration ──
    # In backtest mode allow up to 100% concentration (single-ticker testing).
    # In live mode enforce the standard 10% UCITS-style cap.
    effective_single_stock_pct = 1.0 if backtest_mode else MAX_PORTFOLIO_SINGLE_STOCK
    max_position_value = portfolio_value * effective_single_stock_pct
    max_shares_portfolio = int(max_position_value / current_price) if current_price > 0 else 0

    # ── Cap 3: Loss-limit (2% portfolio risk per trade) ──
    # ALWAYS enforced — Risk Scorer will veto any plan that exceeds this.
    # Formula: max_shares × (entry - stop) ≤ portfolio × 2%
    # Rearranged: max_shares = (portfolio × 2%) / (entry - stop)
    max_loss_per_trade_egp = portfolio_value * MAX_LOSS_PER_TRADE_PCT
    loss_per_share_egp = current_price * stop_distance_pct  # default 5% stop
    max_shares_loss_limit = (
        int(max_loss_per_trade_egp / loss_per_share_egp)
        if loss_per_share_egp > 0 else 0
    )

    # ── Binding cap = MIN of all three ──
    max_shares = min(max_shares_per_day, max_shares_portfolio, max_shares_loss_limit)

    # Determine which cap is binding so the prompt can explain it
    if max_shares == max_shares_loss_limit:
        binding_cap = "loss_limit_2pct"
    elif max_shares == max_shares_per_day:
        binding_cap = "adv_10pct"
    else:
        binding_cap = "portfolio_concentration"

    # Execution days needed for the binding-cap position
    days_to_accumulate = (
        max(1, max_shares // max_shares_per_day)
        if max_shares_per_day > 0 and max_shares > 0 else 1
    )

    return {
        "max_shares_per_day":         max_shares_per_day,
        "max_shares_total":           max_shares_portfolio,
        "max_shares_loss_limit":      max_shares_loss_limit,
        "max_shares_binding":         max_shares,           # ← this is the real cap
        "binding_cap":                binding_cap,
        "max_loss_per_trade_egp":     max_loss_per_trade_egp,
        "max_loss_per_trade_pct":     MAX_LOSS_PER_TRADE_PCT,
        "assumed_stop_distance_pct":  stop_distance_pct,
        "days_to_full_position":      days_to_accumulate,
        "adv_constraint_pct":         adv_limit_pct,
        "portfolio_constraint_pct":   effective_single_stock_pct,
        "low_liquidity_adjustment":   low_liquidity,
    }


def create_trader(llm, memory):
    """
    Create the Institutional Trader agent for EGX.
    
    This agent:
    - Generates structured EXECUTION PLANS (not just BUY/HOLD/SELL)
    - Respects EGX constraints (long-only, no leverage, no market orders)
    - Calculates liquidity-adjusted position sizes
    - Specifies entry logic, exit logic, and risk controls
    """

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        investment_plan = state.get("investment_plan", "")
        market_research_report = state.get("market_report", "")
        sentiment_report = state.get("sentiment_report", "")
        news_report = state.get("news_report", "")
        fundamentals_report = state.get("fundamentals_report", "")
        
        # Get structured analyses
        technical_analysis = state.get("technical_analysis", {})
        fundamental_analysis = state.get("fundamental_analysis", {})
        sentiment_analysis = state.get("sentiment_analysis", {})
        
        # Get debate theses
        investment_debate = state.get("investment_debate_state", {})
        bull_thesis = investment_debate.get("bull_thesis", {})
        bear_thesis = investment_debate.get("bear_thesis", {})
        
        # Get EGX-specific info
        config = get_config()
        target_market = config.get("target_market", "US")
        is_egx = target_market == "EGX"
        max_portfolio_pct = 0.25 if config.get("backtest_mode", False) else MAX_PORTFOLIO_SINGLE_STOCK
        
        low_liquidity = state.get("low_liquidity", False)
        ticker = state.get("company_of_interest", "")

        current_position = state.get("current_position", {})
        if current_position.get("shares", 0) > 0:
            position_info = f"""
## Current Position
- Shares held: {current_position['shares']:,}
- Average cost: {current_position.get('avg_cost', 0):.2f} EGP
- Market value: {current_position.get('market_value', 0):,.2f} EGP
- Unrealised P&L: {current_position.get('unrealised_pnl', 0):,.2f} EGP
- SELL is available to close this position
"""
        else:
            position_info = """
## Current Position
- NO shares held — portfolio is 100% cash
- SELL is NOT available (EGX: no short selling)
- Only BUY or HOLD are actionable
"""

        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        memory_where = {"ticker": ticker} if ticker else None
        memory_threshold = float(config.get("memory_min_similarity", 0.30))
        past_memories = memory.get_memories(
            curr_situation,
            n_matches=2,
            where=memory_where,
            min_similarity=memory_threshold,
        )

        past_memory_str = ""
        if past_memories:
            for i, rec in enumerate(past_memories, 1):
                past_memory_str += rec["recommendation"] + "\n\n"
        else:
            past_memory_str = "No past memories found."

        # ── Phase 2f: Pre-compute position limits ────────────────────────────
        # Inject validated, deterministic limits into the prompt so the LLM
        # reasons about execution strategy with correct arithmetic — not
        # reinventing position sizing from first principles.
        avg_daily_volume = state.get("avg_daily_volume") or 100000
        current_price = state.get("current_price") or 50.0
        portfolio_value = state.get("portfolio_value") or 10000000
        position_limits = calculate_position_limits(
            avg_daily_volume=avg_daily_volume,
            current_price=current_price,
            portfolio_value=portfolio_value,
            low_liquidity=low_liquidity,
        )

        # EGX-specific constraints prompt
        egx_constraints = ""
        if is_egx:
            egx_constraints = f"""
## EGX TRADING CONSTRAINTS (MANDATORY)

### Market Structure
- **Market**: Egyptian Exchange (EGX)
- **Currency**: Egyptian Pound (EGP)
- **Daily Price Limit**: ±10% (circuit breaker - trading halts at limit)
- **Trading Hours**: 10:00-14:30 Cairo Time (4.5 hours only)
- **Settlement**: T+2

### Pre-Computed Position Limits (use these exact figures — do NOT override)

🛑 **HARDEST CAP — LOSS LIMIT (Clause 13, ALWAYS ENFORCED)**
- **Max loss per trade**: {position_limits['max_loss_per_trade_egp']:,.0f} EGP ({position_limits['max_loss_per_trade_pct']:.0%} of portfolio)
- **Max shares assuming a {position_limits['assumed_stop_distance_pct']:.0%} stop**: **{position_limits['max_shares_loss_limit']:,} shares**
- **Loss per share at that stop**: {current_price * position_limits['assumed_stop_distance_pct']:.2f} EGP

📐 **Other caps**
- Max shares per day (ADV 10%):       {position_limits['max_shares_per_day']:,} shares
- Max shares total (portfolio cap):   {position_limits['max_shares_total']:,} shares
- Liquidity adjustment:               {"YES — 50% reduction for low liquidity" if low_liquidity else "No — normal liquidity"}

✅ **BINDING CAP (use this number, NOT the others)**
- **Final max shares**: **{position_limits['max_shares_binding']:,} shares**
- **Binding constraint**: `{position_limits['binding_cap']}`
- **Days to accumulate**: ~{position_limits['days_to_full_position']} trading days

🧮 **POSITION SIZING FORMULA — apply this to your final plan**
```
loss_per_share        = entry_price - stop_loss_price
max_loss_egp          = portfolio_value × 2%
max_target_shares     = max_loss_egp / loss_per_share
```
**If your stop is wider than {position_limits['assumed_stop_distance_pct']:.0%}, the binding cap shrinks — recompute and use the smaller number.**
**The Risk Scorer will VETO any plan where shares × (entry − stop) > 2% of portfolio.**

### Order Type Constraints
- ✅ ALLOWED: Limit orders, Limit IOC, VWAP, TWAP
- ❌ FORBIDDEN: Market orders, Market-on-Close, Stop Market
- Always use limit orders to control execution price
- For large positions, use VWAP/TWAP over multiple days

### Liquidity Status for {ticker}
{"⚠️ LOW LIQUIDITY - Limits already halved above" if low_liquidity else "Normal liquidity - standard limits apply"}
"""


        # ── Macro context (NEW) ────────────────────────────────────────────
        # Inject the live macro environment so the Trader's risk appetite
        # tracks the actual rate / FX regime instead of inferring from
        # potentially-stale rate references in the upstream investment_plan.
        # In a high real-rate environment (cash yields > equity earnings yield)
        # the Trader should be cautious; in a low-rate environment it can take
        # more risk — but ALWAYS within the EGX hard constraints (no leverage,
        # no margin, no shorts).
        macro_section = format_macro_context_for_prompt(state.get("macro_context"))

        prompt_context = f"""You are an Institutional Trader generating a detailed EXECUTION PLAN for {company_name}.

{egx_constraints}
{position_info}

{macro_section}

## TRADER GUIDANCE ON MACRO
- The CBE policy rate above is the AUTHORITATIVE current rate. Do NOT cite a different rate from the analyst reports.
- Compare equity earnings yield to the T-bill yield. If equity earnings yield < T-bill yield, prefer smaller positions or HOLD.
- If FX trend is "depreciating", reduce position sizes (USD-denominated input costs hurt margins).
- NEVER use leverage, margin, or borrowed capital. EGX is 100% cash only — even when rates are low, do NOT use any "margin" indicator.

## Your Task
Based on the investment thesis and analyst reports, create a comprehensive execution plan that includes:
1. **Decision**: BUY, HOLD, or SELL (remember: no short selling, only reducing/exiting positions)
2. **Position Sizing**: Liquidity-adjusted, respecting ADV constraints
3. **Entry Logic**: Specific price levels and order types
4. **Exit Logic**: Take-profit and stop-loss levels
5. **Risk Controls**: How to manage position risk

## Investment Thesis from Research Team
{investment_plan}

## Bull Thesis Summary
{json.dumps(bull_thesis, indent=2) if bull_thesis else "Not available"}

## Bear Thesis Summary
{json.dumps(bear_thesis, indent=2) if bear_thesis else "Not available"}

## Analyst Reports Summary
- Technical Analysis: {json.dumps(technical_analysis, indent=2) if technical_analysis else market_research_report[:500]}
- Fundamental Analysis: {json.dumps(fundamental_analysis, indent=2) if fundamental_analysis else fundamentals_report[:500]}
- Sentiment Analysis: {json.dumps(sentiment_analysis, indent=2) if sentiment_analysis else sentiment_report[:500]}

## Lessons from Past Trades
{past_memory_str}

## REQUIRED OUTPUT FORMAT
You MUST end your analysis with a structured execution plan in this exact JSON format:

```json
{{
    "execution_plan": {{
        "symbol": "{ticker}",
        "market": "EGX",
        "currency": "EGP",
        "decision": "BUY|HOLD|SELL",
        "conviction": "high|moderate|low",
        "position_sizing": {{
            "target_shares": number,
            "max_shares_per_day": number,
            "execution_days": number,
            "pct_of_adv_per_day": "percentage",
            "portfolio_allocation": "target % of portfolio"
        }},
        "entry_logic": {{
            "order_type": "limit|vwap|twap",
            "entry_zone": {{
                "limit_price": number,
                "price_range_low": number,
                "price_range_high": number
            }},
            "timing": "description of when to enter",
            "conditions": ["condition 1 to trigger entry", "condition 2"]
        }},
        "exit_logic": {{
            "take_profit": {{
                "target_1": {{"price": number, "pct_of_position": percentage}},
                "target_2": {{"price": number, "pct_of_position": percentage}},
                "target_3": {{"price": number, "pct_of_position": percentage}}
            }},
            "stop_loss": {{
                "price": number,
                "type": "mental_stop|limit_order",
                "note": "No market stop orders - use limit"
            }},
            "time_stop": "exit if thesis doesn't play out by [date/timeframe]"
        }},
        "risk_controls": {{
            "max_loss_per_trade": "amount or percentage",
            "max_daily_execution": "shares or % of ADV",
            "price_limit_risk": "plan if stock hits ±10% limit",
            "liquidity_exit_plan": "how to exit if volume dries up"
        }},
        "invalidation_triggers": [
            "condition 1 that cancels the trade",
            "condition 2"
        ]
    }},
    "final_recommendation": "FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**"
}}
```

Provide your detailed analysis first, then conclude with the structured execution plan."""

        messages = [
            {
                "role": "system",
                "content": f"""You are an Institutional Trader for {"EGX (Egyptian Exchange)" if is_egx else "financial markets"}. 
Your job is to translate investment theses into actionable, risk-controlled EXECUTION PLANS.

Key responsibilities:
1. Generate specific, executable trade plans (not vague recommendations)
2. Always respect position limits and order type constraints
3. Account for liquidity in position sizing
4. No market orders - always use limit orders
5. {"LONG-ONLY: No short selling" if is_egx else ""}
6. {"NO LEVERAGE: 100% cash positions only" if is_egx else ""}

Always conclude with: FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**"""
            },
            {
                "role": "user",
                "content": prompt_context
            }
        ]

        _fallback_trader_msg = type("_FallbackMsg", (), {
            "content": (
                'Trader unable to generate execution plan — LLM call failed.\n'
                'Defaulting to HOLD with no entry plan to protect capital.\n'
                '```json\n'
                '{\n'
                '  "execution_plan": {\n'
                f'    "symbol": "{ticker}",\n'
                '    "market": "EGX",\n'
                '    "currency": "EGP",\n'
                '    "decision": "HOLD",\n'
                '    "conviction": "low",\n'
                '    "position_sizing": {"target_shares": 0, "portfolio_allocation": "0%"},\n'
                '    "error": "LLM unavailable - fallback HOLD"\n'
                '  },\n'
                '  "final_recommendation": "FINAL TRANSACTION PROPOSAL: **HOLD**"\n'
                '}\n'
                '```'
            )
        })()
        result = safe_invoke(
            llm, messages,
            fallback=_fallback_trader_msg,
            agent_name="Trader",
        )

        # Try to extract structured execution plan
        execution_plan = None
        try:
            json_match = re.search(r'```json\s*(.*?)\s*```', result.content, re.DOTALL)
            if json_match:
                execution_plan = json.loads(json_match.group(1))
        except (json.JSONDecodeError, AttributeError):
            # If JSON parsing fails, create minimal plan
            execution_plan = {
                "execution_plan": {
                    "symbol": ticker,
                    "market": "EGX" if is_egx else "US",
                    "decision": "HOLD",
                    "conviction": "low",
                    "error": "Failed to parse execution plan from LLM response"
                },
                "final_recommendation": "FINAL TRANSACTION PROPOSAL: **HOLD**"
            }

        return {
            "messages": [result],
            "trader_investment_plan": result.content,
            "execution_plan": execution_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Institutional_Trader")

