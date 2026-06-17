"""P2 tests for the Portfolio Extraction Agent.

All tests use a mocked LLM. The point is not to test model quality in CI; it is
to prove that LLM candidates are normalized, registry-gated, and converted into
the existing confirmation-table contract deterministically.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from tradingagents.portfolio.extraction import PortfolioExtractionAgent
from tradingagents.portfolio.llm_boundary import normalize_user_text, registry_tickers


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, _messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


EN_CASES = [
    ("I own 25% CIB bought at 72 and 10% Fawry at 8.5.", [
        {"name": "CIB", "weight_pct": 25, "avg_cost": 72},
        {"name": "Fawry", "weight_pct": 10, "avg_cost": 8.5},
    ], ["COMI.CA", "FWRY.CA"]),
    ("Telecom Egypt is 300 shares, avg 38.", [{"name": "Telecom Egypt", "shares": 300, "avg_cost": 38}], ["ETEL.CA"]),
    ("TMG is half my account.", [{"name": "TMG", "weight_pct": 50}], ["TMGH.CA"]),
    ("I have 1000 shares in Orascom Construction.", [{"name": "Orascom Construction", "shares": 1000}], ["ORAS.CA"]),
    ("EFG Hermes is 15 percent.", [{"name": "EFG Hermes", "weight_pct": 15}], ["HRHO.CA"]),
    ("Palm Hills 20%, Sodic 10%.", [{"name": "Palm Hills", "weight_pct": 20}, {"name": "Sodic", "weight_pct": 10}], ["PHDC.CA", "OCDI.CA"]),
    ("Edita and Juhayna, 5% each.", [{"name": "Edita", "weight_pct": 5}, {"name": "Juhayna", "weight_pct": 5}], ["EFID.CA", "JUFO.CA"]),
    ("Abu Qir fertilizers 200 shares.", [{"name": "Abu Qir", "shares": 200}], ["ABUK.CA"]),
    ("QNB AlAhli 7%.", [{"name": "QNB AlAhli", "weight_pct": 7}], ["QNBE.CA"]),
    ("Beltone 11% and CI Capital 4%.", [{"name": "Beltone", "weight_pct": 11}, {"name": "CI Capital", "weight_pct": 4}], ["BTFH.CA", "CICH.CA"]),
    ("E-finance 6%.", [{"name": "e-finance", "weight_pct": 6}], ["EFIH.CA"]),
    ("Madinet Masr 9%.", [{"name": "Madinet Masr", "weight_pct": 9}], ["MASR.CA"]),
    ("Rameda 3%.", [{"name": "Rameda", "weight_pct": 3}], ["RMDA.CA"]),
    ("Domty 250 shares.", [{"name": "Domty", "shares": 250}], ["DOMT.CA"]),
    ("I keep 50000 EGP cash and 12% COMI.", [{"ticker": "COMI", "weight_pct": 12}], ["COMI.CA"]),
]

AR_CASES = [
    ("عندي ٢٠٪ في فوري و ١٠٪ في CIB", [{"name": "فوري", "weight_pct": 20}, {"name": "CIB", "weight_pct": 10}], ["FWRY.CA", "COMI.CA"]),
    ("المصرية للاتصالات ٣٠٠ سهم", [{"name": "ETEL", "shares": 300}], ["ETEL.CA"]),
    ("طلعت مصطفى نص المحفظة", [{"name": "TMG", "weight_pct": 50}], ["TMGH.CA"]),
    ("اوراسكوم للانشاء عندي فيها ١٥٪", [{"name": "Orascom", "weight_pct": 15}], ["ORAS.CA"]),
    ("هيرميس ٨٪", [{"name": "EFG", "weight_pct": 8}], ["HRHO.CA"]),
    ("بالم هيلز ٧٪ وسوديك ٦٪", [{"name": "Palm Hills", "weight_pct": 7}, {"name": "Sodic", "weight_pct": 6}], ["PHDC.CA", "OCDI.CA"]),
    ("ايديتا وجهينة خمسة في المية لكل واحد", [{"name": "Edita", "weight_pct": 5}, {"name": "Juhayna", "weight_pct": 5}], ["EFID.CA", "JUFO.CA"]),
    ("ابو قير ٢٠٠ سهم", [{"name": "Abu Qir", "shares": 200}], ["ABUK.CA"]),
    ("كيو ان بي ٩٪", [{"name": "QNB", "weight_pct": 9}], ["QNBE.CA"]),
    ("بلتون ١١٪ وسي اي كابيتال ٤٪", [{"name": "Beltone", "weight_pct": 11}, {"name": "CI Capital", "weight_pct": 4}], ["BTFH.CA", "CICH.CA"]),
    ("اي فاينانس ٦٪", [{"name": "EFINANCE", "weight_pct": 6}], ["EFIH.CA"]),
    ("مدينة مصر ٩٪", [{"name": "MNHD", "weight_pct": 9}], ["MASR.CA"]),
    ("راميدا ٣٪", [{"name": "Rameda", "weight_pct": 3}], ["RMDA.CA"]),
    ("دومتي ٢٥٠ سهم", [{"name": "Domty", "shares": 250}], ["DOMT.CA"]),
    ("معايا ٥٠ الف كاش و ١٢٪ التجاري الدولي", [{"name": "CIB", "weight_pct": 12}], ["COMI.CA"]),
]


@pytest.mark.parametrize("text,holdings,expected", EN_CASES + AR_CASES)
def test_extraction_fixtures_resolve_only_registry_tickers(text, holdings, expected):
    payload = {"cash_egp": 50_000, "holdings": holdings, "unresolved_names": [], "warnings": []}
    result = PortfolioExtractionAgent(llm=FakeLLM(payload)).extract(text)

    assert [h.ticker for h in result.snapshot.holdings] == expected
    assert set(expected) <= registry_tickers()
    assert result.clarification is None
    assert result.block.type == "extracted_portfolio_table"


def test_eastern_numerals_are_normalized_before_prompting():
    assert "20%" in normalize_user_text("عندي ٢٠٪ في فوري")


def test_unlisted_name_becomes_clarification_not_guess():
    payload = {
        "cash_egp": 0,
        "holdings": [{"name": "Mystery Pyramid Coin", "weight_pct": 25}],
        "unresolved_names": [],
        "warnings": [],
    }
    result = PortfolioExtractionAgent(llm=FakeLLM(payload)).extract("I own Mystery Pyramid Coin")

    assert result.snapshot.holdings == []
    assert result.clarification is not None
    assert "ticker:Mystery Pyramid Coin" in result.clarification.missing


def test_weight_sum_above_100_triggers_clarification():
    payload = {
        "holdings": [
            {"name": "CIB", "weight_pct": 80},
            {"name": "Fawry", "weight_pct": 40},
        ]
    }
    result = PortfolioExtractionAgent(llm=FakeLLM(payload)).extract("80% CIB and 40% Fawry")

    assert result.clarification is not None
    assert "weights_sum" in result.clarification.missing
    assert any("above 100%" in w for w in result.block.data.warnings)


def test_shares_price_value_mismatch_is_warning_only():
    payload = {"holdings": [{"name": "CIB", "shares": 100, "avg_cost": 70, "market_value_egp": 9000}]}
    result = PortfolioExtractionAgent(llm=FakeLLM(payload)).extract("100 CIB at 70 worth 9000")

    assert result.snapshot.holdings[0].ticker == "COMI.CA"
    assert any("does not match" in w for w in result.block.data.warnings)


class FailingLLM:
    """Simulates the conversational endpoint 500ing on every attempt."""
    def invoke(self, _messages):
        raise RuntimeError("nvidia 500 — service unavailable")


class FlakyLLM:
    """504s on the first attempt, then succeeds — the real NVIDIA failure mode
    (~1/3 calls 504 per a live probe). Proves invoke_json's retry recovers it."""
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
    def invoke(self, _messages):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Error code: 504 — overloaded")
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


def test_transient_504_is_retried_and_recovers(monkeypatch):
    """WHY: a single transient 504 must NOT lose the user's portfolio — the retry
    recovers it. This is the exact browser bug. PHASE: live-fix."""
    import tradingagents.portfolio.llm_boundary as lb
    monkeypatch.setattr(lb.time, "sleep", lambda *_a, **_k: None)
    payload = {"cash_egp": 50000, "holdings": [
        {"name": "المصرية للاتصالات", "weight_pct": 20},
        {"name": "اوراسكوم كونستراكشن", "weight_pct": 40}], "unresolved_names": [], "warnings": []}
    llm = FlakyLLM(payload)
    res = PortfolioExtractionAgent(llm=llm).extract("portfolio", language="ar")
    assert llm.calls == 2  # failed once, retried, succeeded
    assert {h.ticker for h in res.snapshot.holdings} == {"ETEL.CA", "ORAS.CA"}
    assert res.snapshot.cash_egp == 50000


def test_llm_total_failure_degrades_gracefully_not_whole_message(monkeypatch):
    """WHY: when the extraction LLM fails after retries, the UI must NOT show
    'Couldn't match: <entire prompt>' with cash 0 (the live bug). It should show
    an empty table + a friendly 'please resend' clarification. PHASE: live-fix."""
    import tradingagents.portfolio.llm_boundary as lb
    monkeypatch.setattr(lb.time, "sleep", lambda *_a, **_k: None)  # no retry delay in CI
    agent = PortfolioExtractionAgent(llm=FailingLLM())
    res = agent.extract("انا محفظتي فيها ٢٠٪ المصرية للاتصالات و قيمتها ١٠٠ الف", language="ar")
    assert res.snapshot.holdings == []
    assert res.snapshot.cash_egp == 0.0
    assert res.block.data.unresolved_names == []            # NOT the raw message
    assert res.clarification is not None
    assert "llm_unavailable" in res.clarification.missing
    assert "ابعتها تاني" in (res.clarification.question or "")  # friendly AR 'resend it' ask


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("EGX_LIVE_LLM") != "1" or not os.getenv("NVIDIA_API_KEY"),
    reason="requires EGX_LIVE_LLM=1 and NVIDIA_API_KEY",
)
def test_live_extraction_smoke_nvidia_gpt_oss():
    agent = PortfolioExtractionAgent()
    result = agent.extract("I own 20% CIB, 10% Fawry, and 50000 EGP cash.")
    assert {"COMI.CA", "FWRY.CA"} <= {h.ticker for h in result.snapshot.holdings}
