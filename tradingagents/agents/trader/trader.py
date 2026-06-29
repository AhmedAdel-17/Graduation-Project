import functools
import time
import json
import re
from tradingagents.dataflows.config import get_config
from tradingagents.graph.node_record import get_recorder, hash_state_slice, hash_string

# =============================================================================
# Institutional Trader Agent for EGX Market
# =============================================================================
# Generates structured EXECUTION PLANS, not just BUY/HOLD/SELL
# Respects EGX constraints: long-only, no leverage, no market orders
# Position sizing is liquidity-adjusted
# =============================================================================

def _fundamentals_quality_note_short(fundamental_analysis: dict) -> str:
    """One-line quality note for the fundamentals section in the trader prompt."""
    if not fundamental_analysis:
        return ""
    qs = fundamental_analysis.get("quality_status", {})
    level = qs.get("level", "")
    if level == "unavailable":
        return "[NO FUNDAMENTALS DATA] "
    if level == "deterministic_only" and qs.get("enrichment_attempted"):
        return "[DEGRADED: enrichment failed, ratios only] "
    if level == "deterministic_only":
        return "[deterministic-only: ratios/flags, no thesis] "
    if level == "partial":
        return "[partial enrichment: some CoT stages failed] "
    return ""


# Execution constraints from EGX institutional best practices
MAX_POSITION_PCT_ADV = 0.10  # Max 10% of Average Daily Volume per day
MAX_PORTFOLIO_SINGLE_STOCK = 0.10  # Max 10% of portfolio in single stock
DAILY_PRICE_LIMIT = 0.10  # EGX ±10% circuit breaker

# Order type constraints
ALLOWED_ORDER_TYPES = ["limit", "limit_ioc", "vwap", "twap"]
FORBIDDEN_ORDER_TYPES = ["market", "market_on_close", "stop_market"]


def calculate_position_limits(
    avg_daily_volume: float,
    current_price: float,
    portfolio_value: float,
    low_liquidity: bool = False,
    max_position_pct_override: float | None = None,
) -> dict:
    """
    Calculate position sizing limits based on EGX liquidity constraints.

    In backtest_mode the single-stock portfolio cap is lifted to 100% so that
    single-ticker backtests are not artificially capped at 10% of capital.

    When *max_position_pct_override* is provided (from investor profile),
    the effective cap is ``min(override, system_cap)`` — the profile can
    only be MORE restrictive than the system default, never less.
    """
    config = get_config()
    backtest_mode = config.get("backtest_mode", False)

    # Max shares per day based on ADV
    adv_limit_pct = MAX_POSITION_PCT_ADV * 0.5 if low_liquidity else MAX_POSITION_PCT_ADV
    max_shares_per_day = int(avg_daily_volume * adv_limit_pct)

    # In backtest mode allow the full portfolio; otherwise enforce the 10% cap
    system_cap = 1.0 if backtest_mode else MAX_PORTFOLIO_SINGLE_STOCK
    if max_position_pct_override is not None:
        effective_single_stock_pct = min(max_position_pct_override, system_cap)
    else:
        effective_single_stock_pct = system_cap
    max_position_value = portfolio_value * effective_single_stock_pct
    max_shares_portfolio = int(max_position_value / current_price) if current_price > 0 else 0

    # Use the more restrictive limit
    max_shares = min(max_shares_per_day, max_shares_portfolio)

    # Execution days needed for a full position
    days_to_accumulate = max(1, max_shares_portfolio // max_shares_per_day) if max_shares_per_day > 0 else 999

    return {
        "max_shares_per_day": max_shares_per_day,
        "max_shares_total": max_shares_portfolio,
        "days_to_full_position": days_to_accumulate,
        "adv_constraint_pct": adv_limit_pct,
        "portfolio_constraint_pct": effective_single_stock_pct,
        "low_liquidity_adjustment": low_liquidity
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
        trade_date = state.get("trade_date")
        past_memories = memory.get_memories(
            curr_situation,
            n_matches=2,
            where=memory_where,
            min_similarity=memory_threshold,
            as_of_date=str(trade_date) if trade_date else None,
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
        ic = state.get("investor_context") or {}
        avg_daily_volume = state.get("avg_daily_volume") or 100000
        current_price = state.get("current_price") or 50.0
        portfolio_value = state.get("portfolio_value") or 10000000
        position_limits = calculate_position_limits(
            avg_daily_volume=avg_daily_volume,
            current_price=current_price,
            portfolio_value=portfolio_value,
            low_liquidity=low_liquidity,
            max_position_pct_override=ic.get("max_position_pct"),
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
- **Max shares per day** (ADV-based): {position_limits['max_shares_per_day']:,} shares
- **Max shares total** (portfolio cap): {position_limits['max_shares_total']:,} shares
- **ADV constraint**: {position_limits['adv_constraint_pct']:.0%} of ADV per day
- **Portfolio allocation cap**: {position_limits['portfolio_constraint_pct']:.0%} of portfolio
- **Days to accumulate full position**: ~{position_limits['days_to_full_position']} trading days
- **Liquidity adjustment applied**: {"YES — 50% reduction for low liquidity" if low_liquidity else "No — normal liquidity"}

### Order Type Constraints
- ✅ ALLOWED: Limit orders, Limit IOC, VWAP, TWAP
- ❌ FORBIDDEN: Market orders, Market-on-Close, Stop Market
- Always use limit orders to control execution price
- For large positions, use VWAP/TWAP over multiple days

### Liquidity Status for {ticker}
{"⚠️ LOW LIQUIDITY - Limits already halved above" if low_liquidity else "Normal liquidity - standard limits apply"}
"""


        # ── Investor profile constraints (when running for a specific user) ──
        investor_section = ""
        if ic:
            max_pos = ic.get("max_position_pct", 0.10)
            investor_section = (
                "## Investor Constraints\n"
                f"- Risk tolerance: {ic.get('risk_tolerance', 'moderate')}\n"
                f"- Horizon: {ic.get('investment_horizon', 'medium_term').replace('_', ' ')}\n"
                f"- Max single-stock allocation: {max_pos:.0%} of portfolio\n"
                f"- Capital: {ic.get('capital_size', 10_000_000):,.0f} EGP\n"
                f"- Style: {ic.get('trading_style', 'position')}\n"
                f"- Sector exclusions: {', '.join(ic.get('sector_exclusions', [])) or 'none'}\n\n"
                f"Position sizing MUST respect the {max_pos:.0%} allocation cap.\n"
                "For conservative investors, use the lower end of position ranges.\n"
                "This is a RECOMMENDATION — the investor reviews before any action."
            )

        # Macro overlay: deterministic EGX macro context.
        try:
            from tradingagents.dataflows.macro_provider import format_macro_context_for_prompt
            macro_section = format_macro_context_for_prompt(state.get("macro_context"))
        except Exception:
            macro_section = ""

        prompt_context = f"""You are an Institutional Trader generating a detailed EXECUTION PLAN for {company_name}.

{macro_section}

{egx_constraints}
{investor_section}
{position_info}
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
- Fundamental Analysis: {_fundamentals_quality_note_short(fundamental_analysis)}{json.dumps(fundamental_analysis, indent=2) if fundamental_analysis else fundamentals_report[:500]}
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

        # ── Recording: capture input state and prompt ────────────────────
        recorder = get_recorder(state)
        _input_keys = [
            "company_of_interest", "investment_plan", "market_report",
            "sentiment_report", "news_report", "fundamentals_report",
            "technical_analysis", "fundamental_analysis", "sentiment_analysis",
            "investment_debate_state", "low_liquidity", "current_position",
            "avg_daily_volume", "current_price", "portfolio_value", "macro_context",
        ]
        _input_hash = hash_state_slice(state, _input_keys) if recorder else ""
        _prompt_full = json.dumps(messages, ensure_ascii=False)
        _prompt_hash = hash_string(_prompt_full) if recorder else ""

        t0 = time.monotonic()
        result = llm.invoke(messages)
        _elapsed_ms = (time.monotonic() - t0) * 1000

        # Try to extract structured execution plan
        execution_plan = None
        _record_status = "success"
        _fallback_source = None
        try:
            json_match = re.search(r'```json\s*(.*?)\s*```', result.content, re.DOTALL)
            if json_match:
                execution_plan = json.loads(json_match.group(1))
        except (json.JSONDecodeError, AttributeError):
            # If JSON parsing fails, create minimal plan
            _record_status = "fallback"
            _fallback_source = "json_parse_failure"
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

        # Build return dict
        _return = {
            "messages": [result],
            "trader_investment_plan": result.content,
            "execution_plan": execution_plan,
            "sender": name,
        }

        # ── Recording: write record ──────────────────────────────────────
        if recorder:
            _signal = None
            if execution_plan and isinstance(execution_plan, dict):
                ep = execution_plan.get("execution_plan", execution_plan)
                _signal = ep.get("decision")
            recorder.record(
                node_name="trader",
                trade_date=state.get("trade_date", ""),
                input_state_keys=_input_keys,
                input_hash=_input_hash,
                prompt_hash=_prompt_hash,
                prompt_text=_prompt_full,
                raw_output=result.content,
                state_update=_return,
                state_update_keys=["messages", "trader_investment_plan", "execution_plan", "sender"],
                signal=_signal,
                wall_clock_ms=_elapsed_ms,
                status=_record_status,
                fallback_source=_fallback_source,
            )

        return _return

    return functools.partial(trader_node, name="Institutional_Trader")

