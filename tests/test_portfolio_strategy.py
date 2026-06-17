"""P2 tests for the Portfolio Strategy Agent."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.strategy import PortfolioStrategyAgent


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, _messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


CASES = [
    ("Make this the safest possible portfolio.", {
        "objective": "capital_preservation", "risk_tolerance": "very_low", "horizon": "1_3y",
        "source_spans": {"objective": "safest", "risk_tolerance": "safest"},
    }, s.Objective.CAPITAL_PRESERVATION, s.RiskTolerance.VERY_LOW, s.Horizon.Y1_3),
    ("I want max growth and I can tolerate big swings.", {
        "objective": "aggressive_growth", "risk_tolerance": "very_high", "horizon": "gt_3y",
        "source_spans": {"objective": "max growth", "risk_tolerance": "big swings", "horizon": "long term"},
    }, s.Objective.AGGRESSIVE_GROWTH, s.RiskTolerance.VERY_HIGH, s.Horizon.GT_3Y),
    ("I need money for marriage in six months.", {
        "objective": "capital_preservation", "risk_tolerance": "low", "horizon": "lt_6m",
        "source_spans": {"objective": "need money", "risk_tolerance": "need money", "horizon": "six months"},
    }, s.Objective.CAPITAL_PRESERVATION, s.RiskTolerance.LOW, s.Horizon.LT_6M),
    ("I prefer passive income and dividends.", {
        "objective": "income", "risk_tolerance": "medium", "horizon": "1_3y", "income_preference": True,
        "source_spans": {"objective": "passive income", "income_preference": "dividends"},
    }, s.Objective.INCOME, s.RiskTolerance.MEDIUM, s.Horizon.Y1_3),
    ("High risk is fine, avoid CIB.", {
        "objective": "growth", "risk_tolerance": "high", "horizon": "1_3y", "excluded_tickers": ["CIB"],
        "source_spans": {"risk_tolerance": "High risk", "excluded_tickers": "avoid CIB"},
    }, s.Objective.GROWTH, s.RiskTolerance.HIGH, s.Horizon.Y1_3),
    ("عايزها آمنة جدا.", {
        "objective": "capital_preservation", "risk_tolerance": "very_low", "horizon": "1_3y",
        "source_spans": {"objective": "آمنة جدا", "risk_tolerance": "آمنة جدا"},
    }, s.Objective.CAPITAL_PRESERVATION, s.RiskTolerance.VERY_LOW, s.Horizon.Y1_3),
    ("عايز أعلى نمو ومش فارقة معايا المخاطرة.", {
        "objective": "aggressive_growth", "risk_tolerance": "very_high", "horizon": "gt_3y",
        "source_spans": {"objective": "أعلى نمو", "risk_tolerance": "مش فارقة معايا المخاطرة"},
    }, s.Objective.AGGRESSIVE_GROWTH, s.RiskTolerance.VERY_HIGH, s.Horizon.GT_3Y),
    ("الجواز بعد ٦ شهور فخليها محافظة.", {
        "objective": "capital_preservation", "risk_tolerance": "low", "horizon": "lt_6m",
        "source_spans": {"objective": "خليها محافظة", "horizon": "٦ شهور"},
    }, s.Objective.CAPITAL_PRESERVATION, s.RiskTolerance.LOW, s.Horizon.LT_6M),
    ("مهم عندي دخل سلبي وتوزيعات.", {
        "objective": "income", "risk_tolerance": "medium", "horizon": "1_3y", "income_preference": True,
        "source_spans": {"objective": "دخل سلبي", "income_preference": "توزيعات"},
    }, s.Objective.INCOME, s.RiskTolerance.MEDIUM, s.Horizon.Y1_3),
    ("موافق على مخاطرة عالية بس ابعد عن التجاري الدولي.", {
        "objective": "growth", "risk_tolerance": "high", "horizon": "1_3y", "excluded_tickers": ["CIB"],
        "source_spans": {"risk_tolerance": "مخاطرة عالية", "excluded_tickers": "ابعد عن التجاري الدولي"},
    }, s.Objective.GROWTH, s.RiskTolerance.HIGH, s.Horizon.Y1_3),
]


@pytest.mark.parametrize("text,payload,objective,risk,horizon", CASES)
def test_strategy_examples_en_and_ar(text, payload, objective, risk, horizon):
    agent = PortfolioStrategyAgent(llm=FakeLLM(payload))
    policy = agent.infer_policy(text, version=3)

    assert policy.objective == objective
    assert policy.risk_tolerance == risk
    assert policy.horizon == horizon
    assert policy.version == 3
    assert policy.confirmed_by_user is False
    if "excluded_tickers" in payload:
        assert policy.excluded_tickers == ["COMI.CA"]


def test_strategy_defaults_unmentioned_fields_are_marked_inferred():
    policy = PortfolioStrategyAgent(llm=FakeLLM({
        "risk_tolerance": "low",
        "source_spans": {"risk_tolerance": "safer"},
    })).infer_policy("make it safer")

    assert policy.risk_tolerance == s.RiskTolerance.LOW
    assert set(policy.inferred_fields) == {"objective", "horizon", "income_preference"}


def test_strategy_rejects_unresolved_excluded_ticker():
    policy = PortfolioStrategyAgent(llm=FakeLLM({
        "risk_tolerance": "high",
        "excluded_tickers": ["Totally Unknown"],
        "source_spans": {"risk_tolerance": "high"},
    })).infer_policy("high risk but avoid Totally Unknown")

    assert policy.excluded_tickers == []
