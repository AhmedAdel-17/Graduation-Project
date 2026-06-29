"""Regression tests for historical EGX news temporal safety."""

import json


def test_company_news_skips_live_aggregator_for_historical_backtest(monkeypatch):
    """Historical backtests must not pull wall-clock RSS/Google/NewsAPI data."""
    from tradingagents.agents.utils import news_data_tools as tools

    def fail_live(*_args, **_kwargs):
        raise AssertionError("live news aggregator should be skipped")

    def fake_local(ticker, curr_date, look_back_days):
        return {
            "ticker": ticker,
            "articles": [{"headline": "historical local article", "date": curr_date}],
            "total_articles": 1,
            "look_back_days": look_back_days,
        }

    monkeypatch.setattr(tools, "_fetch_live_news", fail_live)
    monkeypatch.setattr(tools, "get_egx_news_combined", fake_local)

    payload = tools.get_egx_company_news.invoke({
        "ticker": "COMI.CA",
        "curr_date": "2024-01-02",
        "look_back_days": 7,
    })
    data = json.loads(payload)

    assert data["ticker"] == "COMI.CA"
    assert data["articles"][0]["headline"] == "historical local article"


def test_market_news_skips_live_aggregator_for_historical_backtest(monkeypatch):
    """Market-wide EGX news should also avoid live sources in backtests."""
    from tradingagents.agents.utils import news_data_tools as tools

    def fail_live(*_args, **_kwargs):
        raise AssertionError("live news aggregator should be skipped")

    def fake_csv(ticker, curr_date, look_back_days):
        return {
            "ticker": ticker,
            "articles": [{"headline": "historical market article", "date": curr_date}],
            "total_articles": 1,
            "look_back_days": look_back_days,
        }

    monkeypatch.setattr(tools, "_fetch_live_news", fail_live)
    monkeypatch.setattr(tools, "get_egx_news_from_csv", fake_csv)

    payload = tools.get_egx_market_news.invoke({
        "curr_date": "2024-01-02",
        "look_back_days": 7,
    })
    data = json.loads(payload)

    assert data["ticker"] == "ALL"
    assert data["articles"][0]["headline"] == "historical market article"
