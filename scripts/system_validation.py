"""
System Validation & Hardening Test Suite
========================================
Runs consistency, determinism, scenario, risk, and stability checks
across the TradingAgents architecture.
"""
import sys
import os
import time
import json
import logging

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tradingagents.agents.utils.scoring import calculate_unified_score
from tradingagents.agents.managers.risk_manager import run_all_risk_checks
from tradingagents.graph.signal_processing import SignalProcessor
from tradingagents.dataflows.gateway import DataGateway
from tradingagents.dataflows.config import set_config
from unittest.mock import MagicMock

logging.basicConfig(level=logging.ERROR) # Mute verbose logs for neat test output

def title(msg):
    print(f"\n{'=' * 60}")
    print(f"{msg.center(60)}")
    print(f"{'=' * 60}")

def pass_test(msg):
    print(f"[PASS] {msg}")

def fail_test(msg):
    print(f"[FAIL] {msg}")
    
def get_mock_state_bullish():
    return {
        "technical_analysis": {"trend_direction": {"direction": "bullish"}, "signals": {"rsi": {"signal": "oversold"}, "macd": {"signal": "bullish"}}, "confidence_score": 0.8},
        "fundamental_analysis": {"health_assessment": {"profitability": {"status": "healthy"}, "leverage": {"status": "healthy"}, "liquidity": {"status": "healthy"}}, "confidence_score": 0.9},
        "sentiment_analysis": {"sentiment": "bullish", "sentiment_strength": "strong", "confidence_score": 0.85},
        "social_sentiment_analysis": {"sentiment_score": 0.8, "confidence": 0.7}
    }

def get_mock_state_bearish():
    return {
        "technical_analysis": {"trend_direction": {"direction": "bearish"}, "signals": {"rsi": {"signal": "overbought"}, "macd": {"signal": "bearish"}}, "confidence_score": 0.8},
        "fundamental_analysis": {"health_assessment": {"profitability": {"status": "concerning"}, "leverage": {"status": "critical"}}, "confidence_score": 0.9},
        "sentiment_analysis": {"sentiment": "bearish", "sentiment_strength": "strong", "confidence_score": 0.85},
        "social_sentiment_analysis": {"sentiment_score": -0.8, "confidence": 0.7}
    }

def get_mock_state_sideways():
    return {
        "technical_analysis": {"trend_direction": {"direction": "neutral"}, "signals": {"rsi": {"signal": "neutral"}}, "confidence_score": 0.6},
        "fundamental_analysis": {"health_assessment": {"profitability": {"status": "unknown"}}, "confidence_score": 0.4},
        "sentiment_analysis": {"sentiment": "neutral", "confidence_score": 0.5},
        "social_sentiment_analysis": {"sentiment_score": 0.0, "confidence": 0.5}
    }

def get_mock_state_missing_data():
    return {
        "technical_analysis": None,
        "fundamental_analysis": {"confidence_score": 0.2}, # No actual health assessment
        "sentiment_analysis": {},
        "social_sentiment_analysis": None
    }


def test_scenario_outcomes():
    title("1. SCENARIO & SCORING TESTS")
    tests = {
        "Bullish Strategy": (get_mock_state_bullish(), "STRONG_BUY"),
        "Bearish Strategy": (get_mock_state_bearish(), ["STRONG_SELL", "SELL"]),
        "Sideways / Neutral": (get_mock_state_sideways(), "HOLD"),
        "Missing Data Fallback": (get_mock_state_missing_data(), "HOLD")
    }
    
    for name, (state, expected) in tests.items():
        decision, conf, reason, _, _status = calculate_unified_score(state)

        match = False
        if isinstance(expected, list):
            match = decision in expected
        else:
            match = expected in decision

        if match:
            pass_test(f"{name} -> Evaluated accurately as {decision} (Conf: {conf:.2f})")
        else:
            fail_test(f"{name} -> Expected {expected}, got {decision}")


def test_determinism():
    title("2. CONSISTENCY & DETERMINISM TESTS")
    state = get_mock_state_bullish()
    
    first_decision, first_conf, first_reason, _, _first_status = calculate_unified_score(state)
    is_deterministic = True

    for i in range(100):
        d, c, r, _, _s = calculate_unified_score(state)
        if d != first_decision or abs(c - first_conf) > 0.0001 or r != first_reason:
            is_deterministic = False
            break
            
    if is_deterministic:
        pass_test("Ran Scoring Engine 100 times - exact same floats and strings returned (100% Deterministic)")
    else:
        fail_test("Scoring Engine is non-deterministic!")


def test_risk_manager():
    title("3. RISK MANAGER CONSTRAINTS TESTS")
    
    # Context
    port_val = 1_000_000
    adv = 100_000
    price = 100.0
    
    # 1. OK Trade
    inner_ok = {"decision": "BUY", "position_sizing": {"target_shares": 500, "portfolio_allocation": "5%"}, "exit_logic": {"stop_loss": {"price": 98}}}
    appr, viols = run_all_risk_checks(inner_ok, port_val, adv, price, False)
    if appr: pass_test("Valid EGX execution plan approved safely.")
    else: fail_test(f"Valid plan rejected: {[v.rule_name for v in viols]}")
    
    # 2. Too Large Position (15% portfolio when 10% is max)
    inner_huge = {"decision": "BUY", "position_sizing": {"target_shares": 1500, "portfolio_allocation": "15%"}, "exit_logic": {"stop_loss": {"price": 98}}}
    appr, viols = run_all_risk_checks(inner_huge, port_val, adv, price, False)
    if not appr and any(v.rule_name == "MAX_SINGLE_STOCK_EXPOSURE" for v in viols): pass_test("Excessive Portfolio allocation intercepted properly.")
    else: fail_test(f"Excessive Portfolio logic bypassed risk manager! Got: {[v.rule_name for v in viols]}")
    
    # 3. Missing Stop Loss
    inner_nosl = {"decision": "BUY", "position_sizing": {"target_shares": 500, "portfolio_allocation": "5%"}, "exit_logic": {}}
    appr, viols = run_all_risk_checks(inner_nosl, port_val, adv, price, False)
    if not appr and any(v.rule_name == "STOP_LOSS_MISSING" for v in viols): pass_test("Missing Stop-Loss trigger intercepted properly.")
    else: fail_test("Missing Stop-Loss bypassed risk manager!")

    # 4. Low Liquidity Trap
    appr, viols = run_all_risk_checks(inner_ok, port_val, avg_daily_volume=5000, current_price=price, low_liquidity=True)
    if not appr and any(v.rule_name == "INSUFFICIENT_LIQUIDITY" for v in viols): pass_test("Insufficient Low Liquidity filter enacted correctly.")
    else: fail_test(f"Low Liquidity trap failed! Got: {[v.rule_name for v in viols]}")


def test_signal_processing():
    title("4. SIGNAL ENGINE OFF-RAMPING")
    # Using a fake object to fake out LLM behavior 
    class FakeLLM:
        def invoke(self, messages):
            class Resp: content = "Should NOT be called"
            return Resp()
            
    proc = SignalProcessor(FakeLLM())
    tests = {
        "STRONG_BUY": "STRONG_BUY",
        "SELL": "SELL",
        "Some random text VETO because of SL": "HOLD (VETOED)",
        "HOLD \n VETO": "HOLD (VETOED)"
    }
    for t_in, expected in tests.items():
        res = proc.process_signal(t_in)
        if res == expected: pass_test(f"Signal bypassed LLM resolving perfectly to -> {expected}")
        else: fail_test(f"Signal extraction failed. Got {res}")


def test_data_cache_performance():
    title("5. DATA GATEWAY CACHE STRESS TEST")
    from tradingagents.dataflows.config import get_config
    gateway = DataGateway(get_config())
    
    start_t = time.time()
    for _ in range(50):
        gateway.fetch_stock_data("COMI.CA", start_date="2023-01-01", end_date="2023-01-07")
    end_t = time.time()
    
    total_ms = (end_t - start_t) * 1000
    if total_ms < 1000: # 50 queries should take almost 0ms if DiskCache returns instantly
        pass_test(f"DiskCache returned 50 recursive sequential OHLCV blocks instantly in {total_ms:.2f}ms")
    else:
        fail_test(f"DiskCache is excessively slow! Took {total_ms:.2f}ms")

if __name__ == "__main__":
    set_config({"target_market": "EGX"})
    test_scenario_outcomes()
    test_determinism()
    test_risk_manager()
    test_signal_processing()
    test_data_cache_performance()
    
    print("\n------------------------------------------------------------")
    print("All unit, determinism, scenario, and stress tests dispatched.")
    print("------------------------------------------------------------")
