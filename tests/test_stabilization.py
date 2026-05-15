from fastapi.testclient import TestClient


def test_memory_defaults_to_chromadb_class():
    from tradingagents.agents.utils.memory import FinancialSituationMemory
    from tradingagents.graph.trading_graph import _resolve_memory_class

    assert _resolve_memory_class({"memory_backend": "chroma"}) is FinancialSituationMemory
    assert _resolve_memory_class({}) is FinancialSituationMemory


def test_health_diagnostics_report_degraded_optional_redis(monkeypatch):
    from server import api_server

    monkeypatch.setattr(
        api_server,
        "get_config",
        lambda: {
            "memory_backend": "chroma",
            "postgres_url": "",
            "redis_url": "redis://localhost:6379",
        },
    )
    monkeypatch.setattr(api_server, "_module_available", lambda name: False)
    monkeypatch.setattr(api_server, "_tcp_reachable_from_url", lambda url: False if url else None)

    diagnostics = api_server._runtime_diagnostics()

    assert diagnostics["memory"]["backend"] == "chroma"
    assert diagnostics["memory"]["postgres_vector_required"] is False
    assert diagnostics["redis"]["package_available"] is False
    assert "redis_streaming_unavailable" in diagnostics["degraded_reasons"]


def test_backtrader_report_normalizes_to_numeric_dashboard_contract():
    from server.api_server import _normalize_bt_report

    report = {
        "session": "COMI.CA",
        "metrics": {
            "Total Return": "1.72%",
            "Benchmark Return": "0.50%",
            "Alpha": "1.22%",
            "Win Rate": "66.67%",
            "Max Drawdown": "-3.10%",
            "Sharpe Ratio": "1.25",
            "Calmar Ratio": "0.55",
            "Total Trades": 3,
            "Total Commissions": "42.50 EGP",
            "Final Portfolio": "1,017,197.11 EGP",
        },
        "trades": [{"date": "2026-01-02", "action": "BUY", "price": 10, "shares": 5}],
        "daily_portfolio": [{"date": "2026-01-02", "portfolio_value": 1_000_000}],
    }

    normalized = _normalize_bt_report(report, session_id="bt_report_COMI.CA_1")
    metrics = normalized["bt"]["metrics"]

    assert normalized["ticker"] == "COMI.CA"
    assert metrics["total_return_pct"] == 1.72
    assert metrics["benchmark_return_pct"] == 0.50
    assert metrics["max_drawdown_pct"] == 3.10
    assert metrics["final_equity"] == 1_017_197.11
    assert normalized["bt"]["daily_portfolio"] == [{"date": "2026-01-02", "equity": 1_000_000.0}]


def test_quick_prediction_llm_failure_is_not_persisted_as_success(monkeypatch):
    import run_egx_prediction
    from server.api_server import app

    def fake_analysis(ticker):
        return {
            "ticker": ticker,
            "llm_error": "LLM unavailable",
            "recommendation": {"signal": "HOLD", "confidence": "LOW"},
        }

    monkeypatch.setattr(run_egx_prediction, "analyze_ticker_for_api", fake_analysis)

    client = TestClient(app)
    response = client.post("/api/test/random-egx", json={"ticker": "COMI.CA"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["llm_error"] == "LLM unavailable"
    assert "session_id" not in body
