from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
import re
from typing import Dict, Any, Optional, List
from tradingagents.agents.utils.agent_utils import (
    get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_egx_fundamentals, get_egx_income, get_egx_balance, get_egx_ratios
)
from tradingagents.dataflows.config import get_config
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import ToolNode

# =============================================================================
# Fundamental Analyst ("Accountant") for EGX Market
# =============================================================================
# Analyzes Egyptian company financials from CSV-based data
# Assumes Egyptian accounting disclosures, NOT SEC-style
# Outputs structured assessment with confidence adjusted for data quality
# =============================================================================

# Confidence adjustment factors
DATA_COMPLETENESS_WEIGHT = 0.30  # 30% of confidence from data availability
RECENCY_WEIGHT = 0.20  # 20% penalty for stale data (>12 months old)
DISCLOSURE_QUALITY_WEIGHT = 0.15  # 15% for disclosure quality

# Financial health thresholds (EGX-calibrated)
EGX_HEALTH_THRESHOLDS = {
    "debt_to_equity": {
        "healthy": 1.0,      # D/E < 1.0 is healthy for EGX
        "concerning": 2.0,   # D/E 1.0-2.0 is concerning
        "critical": 3.0,     # D/E > 2.0 is critical
    },
    "current_ratio": {
        "healthy": 1.5,      # Current ratio > 1.5 is healthy
        "concerning": 1.0,   # 1.0-1.5 is concerning
        "critical": 0.8,     # < 0.8 is critical
    },
    "net_margin": {
        "healthy": 0.10,     # > 10% net margin is healthy
        "concerning": 0.05,  # 5-10% is concerning
        "critical": 0.0,     # < 5% or negative is critical
    }
}


def calculate_data_completeness_score(fundamentals_data: Dict[str, Any]) -> float:
    """
    Calculate data completeness score (0.0 to 1.0) based on available fields.
    """
    if not fundamentals_data:
        return 0.0
    
    # Check data completeness from the summary
    completeness = fundamentals_data.get("data_completeness", {})
    if completeness:
        return completeness.get("completeness_ratio", 0.0)
    
    # Fallback: count available statements
    available = 0
    total = 3
    
    if fundamentals_data.get("income_statement", {}).get("data"):
        available += 1
    if fundamentals_data.get("balance_sheet", {}).get("data"):
        available += 1
    if fundamentals_data.get("key_ratios", {}).get("ratios"):
        available += 1
    
    return available / total


def assess_financial_health(fundamentals_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Assess financial health based on available EGX fundamental data.
    Returns health scores and flags for each category.
    """
    health = {
        "overall": "unknown",
        "profitability": {"status": "unknown", "details": []},
        "leverage": {"status": "unknown", "details": []},
        "liquidity": {"status": "unknown", "details": []},
        "flags": []
    }
    
    # Extract ratios if available
    ratios = fundamentals_data.get("key_ratios", {}).get("ratios", {})
    income = fundamentals_data.get("income_statement", {}).get("data", {})
    balance = fundamentals_data.get("balance_sheet", {}).get("data", {})
    
    # Profitability assessment
    net_margin = ratios.get("net_margin")
    roe = ratios.get("roe")
    
    if net_margin is not None:
        if net_margin >= EGX_HEALTH_THRESHOLDS["net_margin"]["healthy"]:
            health["profitability"]["status"] = "healthy"
            health["profitability"]["details"].append(f"Net margin {net_margin:.1%} is strong")
        elif net_margin >= EGX_HEALTH_THRESHOLDS["net_margin"]["concerning"]:
            health["profitability"]["status"] = "concerning"
            health["profitability"]["details"].append(f"Net margin {net_margin:.1%} is weak")
        else:
            health["profitability"]["status"] = "critical"
            health["profitability"]["details"].append(f"Net margin {net_margin:.1%} is critical")
            health["flags"].append("LOW_PROFITABILITY")
            
    # Leverage assessment
    de_ratio = ratios.get("debt_to_equity")
    
    if de_ratio is not None:
        if de_ratio <= EGX_HEALTH_THRESHOLDS["debt_to_equity"]["healthy"]:
            health["leverage"]["status"] = "healthy"
            health["leverage"]["details"].append(f"D/E ratio {de_ratio:.2f} is conservative")
        elif de_ratio <= EGX_HEALTH_THRESHOLDS["debt_to_equity"]["concerning"]:
            health["leverage"]["status"] = "concerning"
            health["leverage"]["details"].append(f"D/E ratio {de_ratio:.2f} is elevated")
        else:
            health["leverage"]["status"] = "critical"
            health["leverage"]["details"].append(f"D/E ratio {de_ratio:.2f} is highly leveraged")
            health["flags"].append("HIGH_LEVERAGE")
            
    # Liquidity assessment
    current_ratio = ratios.get("current_ratio")
    
    if current_ratio is not None:
        if current_ratio >= EGX_HEALTH_THRESHOLDS["current_ratio"]["healthy"]:
            health["liquidity"]["status"] = "healthy"
            health["liquidity"]["details"].append(f"Current ratio {current_ratio:.2f} indicates good liquidity")
        elif current_ratio >= EGX_HEALTH_THRESHOLDS["current_ratio"]["concerning"]:
            health["liquidity"]["status"] = "concerning"
            health["liquidity"]["details"].append(f"Current ratio {current_ratio:.2f} is tight")
        else:
            health["liquidity"]["status"] = "critical"
            health["liquidity"]["details"].append(f"Current ratio {current_ratio:.2f} indicates liquidity crisis")
            health["flags"].append("LOW_LIQUIDITY")
            
    # Overall assessment logic
    flags_count = len(health["flags"])
    if flags_count >= 2:
        health["overall"] = "critical"
    elif flags_count == 1:
        health["overall"] = "concerning"
    else:
        # If we have enough data and no flags, it's healthy
        if net_margin is not None and de_ratio is not None:
            health["overall"] = "healthy"
        else:
            health["overall"] = "insufficient_data"
            
    return health


def determine_valuation_gap(fundamentals_data: Dict[str, Any], current_price: float = None) -> Dict[str, Any]:
    """
    Determine valuation gap/range based on available metrics.
    """
    valuation = {
        "fair_value_range": {"low": None, "mid": None, "high": None},
        "current_valuation": "unknown",
        "methods_used": [],
        "notes": []
    }
    
    ratios = fundamentals_data.get("key_ratios", {}).get("ratios", {})
    
    pe = ratios.get("pe_ratio")
    eps = ratios.get("eps")
    book_value = ratios.get("book_value_per_share")
    
    estimates = []
    
    # Method 1: PE Ratio (Standard for EGX)
    # Target PE ranges for EGX (simplified)
    TARGET_PE_LOW = 6.0
    TARGET_PE_HIGH = 12.0
    
    if eps and eps > 0:
        low_fair = eps * TARGET_PE_LOW
        high_fair = eps * TARGET_PE_HIGH
        mid_fair = (low_fair + high_fair) / 2
        
        estimates.append({
            "method": "PE_Ratio",
            "low": low_fair,
            "mid": mid_fair,
            "high": high_fair
        })
        valuation["methods_used"].append("PE_Ratio")
        valuation["notes"].append(f"Based on EPS {eps:.2f} and target PE {TARGET_PE_LOW}-{TARGET_PE_HIGH}x")
        
    # Method 2: Price to Book
    if book_value and book_value > 0:
        low_fair = book_value * 0.8
        high_fair = book_value * 2.0
        mid_fair = book_value * 1.3
        
        estimates.append({
            "method": "Price_to_Book",
            "low": low_fair,
            "mid": mid_fair,
            "high": high_fair
        })
        valuation["methods_used"].append("Price_to_Book")
        valuation["notes"].append(f"Book value: {book_value:.2f} EGP/share")
    
    # Aggregate ranges
    if estimates:
        valuation["fair_value_range"]["low"] = round(min(e["low"] for e in estimates), 2)
        valuation["fair_value_range"]["high"] = round(max(e["high"] for e in estimates), 2)
        valuation["fair_value_range"]["mid"] = round(sum(e["mid"] for e in estimates) / len(estimates), 2)
        
        # Assess current valuation if PE is known
        if pe is not None:
            if pe < 8:
                valuation["current_valuation"] = "potentially_undervalued"
            elif pe > 15:
                valuation["current_valuation"] = "potentially_overvalued"
            else:
                valuation["current_valuation"] = "fairly_valued"
    else:
        valuation["notes"].append("Insufficient data for valuation - no PE or Book Value available")
    
    return valuation


def identify_key_risks(fundamentals_data: Dict[str, Any], health: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Identify key risks based on fundamental analysis.
    """
    risks = []
    
    # Add risks from health flags
    for flag in health.get("flags", []):
        if flag == "HIGH_LEVERAGE":
            risks.append({
                "category": "Financial",
                "risk": "High debt levels",
                "severity": "high",
                "description": "Elevated debt-to-equity ratio increases financial risk and interest burden"
            })
        elif flag == "LOW_LIQUIDITY":
            risks.append({
                "category": "Financial",
                "risk": "Liquidity constraints",
                "severity": "high",
                "description": "Low current ratio may indicate difficulty meeting short-term obligations"
            })
        elif flag == "LOW_PROFITABILITY":
            risks.append({
                "category": "Operational",
                "risk": "Weak profitability",
                "severity": "medium",
                "description": "Low margins may indicate pricing pressure or cost inefficiencies"
            })
    
    # Data quality risks
    completeness = fundamentals_data.get("data_completeness", {})
    if completeness.get("completeness_ratio", 1.0) < 0.7:
        risks.append({
            "category": "Data Quality",
            "risk": "Incomplete financial data",
            "severity": "medium",
            "description": "Missing financial statements limit analysis accuracy"
        })
    
    # EGX-specific risks
    risks.append({
        "category": "Market",
        "risk": "EGX market structure",
        "severity": "low",
        "description": "Limited liquidity, daily price limits (±10%), no short selling"
    })
    
    risks.append({
        "category": "Currency",
        "risk": "EGP currency exposure",
        "severity": "medium",
        "description": "Egyptian Pound volatility affects real returns for foreign investors"
    })
    
    return risks


def calculate_confidence_score(
    data_completeness: float,
    health_assessment: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Calculate overall confidence score adjusted for data quality.
    """
    # Base confidence from data completeness
    base_confidence = data_completeness * DATA_COMPLETENESS_WEIGHT * 100
    
    # Add confidence for each assessed dimension
    dimensions_assessed = 0
    for dim in ["profitability", "leverage", "liquidity"]:
        if health_assessment.get(dim, {}).get("status") != "unknown":
            dimensions_assessed += 1
            base_confidence += 20  # Each dimension adds up to 20%
    
    # Cap at 100
    confidence = min(100, base_confidence)
    
    # Determine confidence level
    if confidence >= 70:
        level = "high"
    elif confidence >= 50:
        level = "moderate"
    elif confidence >= 30:
        level = "low"
    else:
        level = "very_low"
    
    adjustments = []
    
    if data_completeness < 0.5:
        adjustments.append(f"Data completeness penalty: only {data_completeness:.0%} of statements available")
    
    if dimensions_assessed < 2:
        adjustments.append(f"Limited assessment: only {dimensions_assessed}/3 dimensions could be evaluated")
    
def create_fundamentals_analyst(llm):
    """
    Create the Fundamental Analyst ("Accountant") agent.
    
    This agent:
    - Analyzes company financials using configured tools (yfinance by default)
    - Outputs structured assessment with valuation RANGES
    - Identifies key risks
    - Adjusts confidence based on data quality
    """

    def fundamentals_analyst_node(state):
        """
        Fundamental Analyst ("Accountant") agent node.
        
        Analyzes company financials using configured tools (yfinance by default for EGX).
        Outputs structured assessment with valuation ranges and risk analysis.
        """
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        
        # Get config to determine market
        config = get_config()
        target_market = config.get("target_market", "US")
        
        # Select appropriate tools
        if target_market == "EGX":
            tools = [
                get_egx_fundamentals,
                get_egx_income,
                get_egx_balance,
                get_egx_ratios,
            ]
        else:
            tools = [
                get_fundamentals,
                get_income_statement,
                get_balance_sheet,
                get_cashflow,
            ]
        
        # System message
        system_message = f"""You are a Fundamental Analyst ("Accountant") specializing in {target_market} companies.
        
        ## Your Role
        Analyze company financial statements and provide a structured fundamental assessment. Your analysis must be suitable for institutional investment decisions.
        
        ## Available Data Sources
        - Generic Fundamental Data (mapped to Yahoo Finance or other providers)
        - Income Statement, Balance Sheet, Cash Flow, and Key Ratios
        
        ## Market Considerations ({target_market})
        - For EGX companies, values are in EGP.
        - Be aware of potential data gaps for smaller cap stocks.
        
        ## CRITICAL RULES
        1. Provide VALUATION RANGES, not point estimates (e.g., "fair value: 45-55 {config.get('trading_currency', 'USD')}")
        2. If data is incomplete, REDUCE your confidence score explicitly
        3. Always identify key risks specific to the company
        4. Focus on:
        - Profitability (Margins, ROE)
        - Financial Health (Leverage, Liquidity)
        - Valuation (PE, P/B vs sector)
        - Growth (Revenue/Earnings trends)
        5. Once you have received the data from your tools, DO NOT attempt to call any other tools like 'calculate_fundamental_analysis'. Output your final unstructured report and the structured JSON object directly in your text response.
        
        ## Required Output Format
        You must output a JSON object with the following structure:
        {{{{
            "financial_health": "string (Strong/Moderate/Weak)",
            "valuation_gap": "string (Undervalued/Fair/Overvalued)",
            "fair_value_range": "string (e.g., 45-55)",
            "key_risks": ["risk1", "risk2"],
            "confidence_score": "number (0-100)",
            "data_completeness": "number (0-100)",
            "reasoning": "string (brief summary)"
        }}}}
        """
        
        # Initialize LLM
        # We use the passed LLM or create a new one if needed, but here we ignore the passed LLM 
        # to ensure we use the deep_thinking_llm from config if we want, or just use the passed one.
        # The setup.py passes quick_thinking_llm. Let's use the passed llm for consistency with other agents.
        # But wait, previous implementation ignored the passed llm and created a new one? 
        # No, the previous implementation used the passed llm: "chain = prompt | llm.bind_tools(tools)"
        # So we should use the `llm` argument from the outer scope.

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_message),
            MessagesPlaceholder(variable_name="messages"),
        ])
        
        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({
            "messages": state.get("fundamentals_messages") or [("human", ticker)]
        })
        
        report = ""
        structured_analysis = None
        
        # If the LLM returned a tool call, we simply return the message. 
        # The graph controls the loop (ConditionalLogic -> tools_fundamentals -> back here).
        if len(result.tool_calls) > 0:
            return {"fundamentals_messages": [result]}

        # If no tool calls, it means we have the final report
        report = result.content
        
        # Try to extract JSON from the report
        try:
            # Look for JSON block
            import re
            json_match = re.search(r'\{.*\}', report, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                structured_analysis = json.loads(json_str)
        except Exception as e:
            print(f"Error parsing JSON from analyst report: {e}")
            
        # Fallback if structure missing
        if not structured_analysis:
            structured_analysis = {
                "financial_health": "Unknown",
                "valuation_gap": "Unknown",
                "fair_value_range": "N/A",
                "key_risks": ["Analysis failed or data missing"],
                "confidence_score": 0,
                "data_completeness": 0,
                "reasoning": "Model did not provide structured output"
            }

        return {
            "fundamentals_messages": [result],
            "fundamentals_report": report,
            "fundamental_analysis": structured_analysis,
        }

    return fundamentals_analyst_node


# =============================================================================
# Deterministic Fundamentals Analyst (replaces LLM calls with existing functions)
# =============================================================================

def create_deterministic_fundamentals_analyst():
    """
    Create a deterministic Fundamental Analyst node that calls EGX data tools
    directly and applies the existing assess/valuation/risk functions — no LLM call.

    Saves ~2 LLM calls and ~4,000-6,000 tokens per run compared to the
    LLM-based `create_fundamentals_analyst`.

    The output state schema is identical to `create_fundamentals_analyst` so
    downstream agents require no changes.
    """
    from tradingagents.dataflows.local import (
        get_egx_fundamentals_summary,
        get_egx_income_statement,
        get_egx_balance_sheet,
        get_egx_key_ratios,
    )

    def deterministic_fundamentals_analyst_node(state):
        trade_date = state["trade_date"]
        ticker = state["company_of_interest"]

        # ── 1. Fetch all EGX fundamental data directly ─────────────────────────
        fundamentals_data = get_egx_fundamentals_summary(ticker, trade_date)
        income_data = get_egx_income_statement(ticker, "annual", trade_date)
        balance_data = get_egx_balance_sheet(ticker, "annual", trade_date)
        ratios_data = get_egx_key_ratios(ticker, trade_date)

        # Merge into combined dict (same schema as `assess_financial_health` expects)
        combined = {
            "income_statement": income_data if income_data else {},
            "balance_sheet": balance_data if balance_data else {},
            "key_ratios": ratios_data if ratios_data else {},
            "data_completeness": fundamentals_data.get("data_completeness", {}),
        }

        # ── 2. Run existing deterministic pipeline ─────────────────────────────
        data_completeness_score = calculate_data_completeness_score(combined)
        health = assess_financial_health(combined)
        valuation = determine_valuation_gap(combined)
        risks = identify_key_risks(combined, health)
        confidence_info = calculate_confidence_score(data_completeness_score, health)

        # ── 3. Build structured output (same schema as LLM output) ─────────────
        # Map internal health/valuation labels to the LLM output format
        health_map = {"healthy": "Strong", "concerning": "Moderate", "critical": "Weak"}
        financial_health_str = health_map.get(health.get("overall", "unknown"), "Unknown")

        val_status = valuation.get("current_valuation", "unknown")
        val_map = {
            "potentially_undervalued": "Undervalued",
            "fairly_valued": "Fair",
            "potentially_overvalued": "Overvalued",
        }
        valuation_gap_str = val_map.get(val_status, "Unknown")

        fvr = valuation.get("fair_value_range", {})
        fair_value_range_str = (
            f"{fvr.get('low', 'N/A')}-{fvr.get('high', 'N/A')}"
            if fvr.get("low") is not None and fvr.get("high") is not None
            else "N/A"
        )

        structured_analysis = {
            "financial_health": financial_health_str,
            "valuation_gap": valuation_gap_str,
            "fair_value_range": fair_value_range_str,
            "key_risks": [r.get("risk", str(r)) for r in risks],
            "confidence_score": confidence_info.get("score", 0) if isinstance(confidence_info, dict) else confidence_info,
            "data_completeness": round(data_completeness_score * 100),
            "reasoning": (
                f"Health: {health.get('overall', 'unknown')} "
                f"| Valuation: {val_status} "
                f"| Risks: {len(risks)} identified"
            ),
            # Extended fields for downstream agents
            "health_detail": health,
            "valuation_detail": valuation,
            "risks_detail": risks,
        }

        # Compact text report for backward compatibility
        report = (
            f"Deterministic Fundamental Analysis for {ticker} on {trade_date}:\n"
            f"Financial Health: {financial_health_str}\n"
            f"Valuation: {valuation_gap_str} | Fair Value Range: {fair_value_range_str}\n"
            f"Key Risks: {', '.join(r.get('risk', '') for r in risks[:3])}\n"
            f"Confidence: {structured_analysis['confidence_score']}/100 | "
            f"Data Completeness: {structured_analysis['data_completeness']}%"
        )

        return {
            "fundamentals_report": report,
            "fundamental_analysis": structured_analysis,
            # Clear per-analyst message channel (no messages were added)
            "fundamentals_messages": [],
        }

    return deterministic_fundamentals_analyst_node
