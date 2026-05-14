"""Tests for tradingagents.db.backtest_writer (PR 6).

Covers:
- Number / percent / date parsing helpers handle the string shapes produced
  by ``BacktestingEngine._calculate_metrics``.
- ``_build_trade_rows`` maps the in-memory trade dict to the row tuple
  expected by the ``backtest_trades`` table, including portfolio_value
  lookup from the daily_portfolio side-input.
- ``write_backtest_run`` short-circuits on missing Postgres / bad dates.
- ``write_backtest_run`` executes the expected INSERT with parsed metrics
  under a mocked cursor.
- ``write_backtest_trades`` bulk-inserts via execute_values.
- All failure modes (bad dates, broken cursor) return False / 0 without
  raising into the caller.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.db import backtest_writer as bw


# ─── parsers ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1.72%", 1.72),
        ("-3.10%", -3.10),
        ("0%", 0.0),
        ("0", 0.0),
        (1.72, 1.72),
        (None, None),
        ("N/A", None),
        ("garbage-letters-only", None),
        ("", None),
    ],
)
def test_parse_percent(value, expected):
    if expected is None:
        assert bw._parse_percent(value) is None
    else:
        assert bw._parse_percent(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1.25", 1.25),
        ("1,017,197.11 EGP", 1017197.11),
        ("42.50 EGP", 42.50),
        (42, 42.0),
        (None, None),
        ("N/A", None),
        ("", None),
    ],
)
def test_parse_float(value, expected):
    if expected is None:
        assert bw._parse_float(value) is None
    else:
        assert bw._parse_float(value) == pytest.approx(expected)


def test_parse_int_truncates_floats():
    assert bw._parse_int("12") == 12
    assert bw._parse_int(3.7) == 3
    assert bw._parse_int(None) is None
    assert bw._parse_int("N/A") is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2024-01-15", date(2024, 1, 15)),
        ("2024-01-15T10:00:00", date(2024, 1, 15)),
        (date(2024, 1, 15), date(2024, 1, 15)),
        (None, None),
        ("not-a-date", None),
    ],
)
def test_coerce_date(value, expected):
    assert bw._coerce_date(value) == expected


# ─── _build_trade_rows ────────────────────────────────────────────────────────


def _sample_trade(**overrides) -> dict:
    base = {
        "date": "2024-01-15",
        "ticker": "COMI.CA",
        "action": "BUY",
        "split": "full",
        "shares": 1000,
        "close_price": 95.0,
        "exec_price": 95.10,
        "value": 95100.00,
        "commission": 179.74,
        "realized_pnl": 0.0,
        "confidence": 0.72,
        "reasoning": "Bullish CIB thesis on rate cut.",
    }
    base.update(overrides)
    return base


def test_build_trade_rows_maps_canonical_fields():
    trades = [_sample_trade()]
    daily = [{"date": "2024-01-15", "portfolio_value": 1_050_000}]

    rows = bw._build_trade_rows(
        run_id="r-1", trades=trades, daily_portfolio=daily
    )
    assert len(rows) == 1
    row = rows[0]
    # (run_id, trade_date, action, shares, price_egp, value_egp,
    #  commission_egp, portfolio_value, signal, confidence, notes)
    assert row[0] == "r-1"
    assert row[1] == date(2024, 1, 15)
    assert row[2] == "BUY"
    assert row[3] == pytest.approx(1000.0)
    assert row[4] == pytest.approx(95.10)  # exec_price preferred over close_price
    assert row[5] == pytest.approx(95100.00)
    assert row[6] == pytest.approx(179.74)
    assert row[7] == pytest.approx(1_050_000.0)  # from daily_portfolio lookup
    assert row[8] == "BUY"  # signal mirrors action
    assert row[9] == pytest.approx(0.72)
    assert "Bullish CIB thesis" in row[10]


def test_build_trade_rows_falls_back_to_close_price_when_exec_price_missing():
    trades = [_sample_trade(exec_price=None)]
    rows = bw._build_trade_rows(run_id="r-2", trades=trades, daily_portfolio=None)
    assert rows[0][4] == pytest.approx(95.0)


def test_build_trade_rows_no_daily_portfolio_yields_null_pv():
    trades = [_sample_trade()]
    rows = bw._build_trade_rows(run_id="r-3", trades=trades, daily_portfolio=None)
    assert rows[0][7] is None


def test_build_trade_rows_skips_malformed_dates():
    trades = [_sample_trade(date="not-a-date"), _sample_trade()]
    rows = bw._build_trade_rows(run_id="r-4", trades=trades, daily_portfolio=None)
    assert len(rows) == 1  # the bad one is dropped


def test_build_trade_rows_truncates_long_reasoning():
    long_reason = "x" * 5000
    trades = [_sample_trade(reasoning=long_reason)]
    rows = bw._build_trade_rows(run_id="r-5", trades=trades, daily_portfolio=None)
    notes = rows[0][10]
    assert len(notes) <= 2001  # 2000 + ellipsis
    assert notes.endswith("…")


# ─── write_backtest_run ───────────────────────────────────────────────────────


_SAMPLE_METRICS = {
    "Total Return":      "1.72%",
    "Benchmark Return":  "0.50%",
    "Alpha":             "1.22%",
    "Buy&Hold Return":   "1.50%",
    "Strategy Alpha":    "0.22%",
    "Win Rate":          "66.67%",
    "Max Drawdown":      "-3.10%",
    "Sharpe Ratio":      "1.25",
    "Calmar Ratio":      "0.55",
    "Total Trades":      3,
    "Total Commissions": "42.50 EGP",
    "Final Portfolio":   "1,017,197.11 EGP",
}


def test_write_backtest_run_noop_when_postgres_unavailable(monkeypatch):
    monkeypatch.setattr(bw, "is_postgres_available", lambda: False)
    cursor_mock = MagicMock()
    monkeypatch.setattr(bw, "db_cursor", cursor_mock)

    ok = bw.write_backtest_run(
        run_id="r-1", ticker="COMI.CA", strategy="llm",
        start_date="2024-01-01", end_date="2024-03-01",
        metrics=_SAMPLE_METRICS, config={},
    )
    assert ok is False
    cursor_mock.assert_not_called()


def test_write_backtest_run_executes_insert_with_parsed_metrics(monkeypatch):
    monkeypatch.setattr(bw, "is_postgres_available", lambda: True)

    fake_cursor = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(bw, "db_cursor", lambda **kw: fake_cm)

    ok = bw.write_backtest_run(
        run_id="r-7", ticker="COMI.CA", strategy="llm",
        start_date="2024-01-01", end_date="2024-03-01",
        metrics=_SAMPLE_METRICS, config={"target_market": "EGX"},
    )
    assert ok is True
    fake_cursor.execute.assert_called_once()
    sql, params = fake_cursor.execute.call_args[0]

    assert "INSERT INTO backtest_runs" in sql
    # Column order from the writer:
    #   (run_id, ticker, strategy, start_date, end_date,
    #    total_return_pct, benchmark_return_pct, alpha_pct,
    #    sharpe_ratio, calmar_ratio, max_drawdown_pct, win_rate_pct,
    #    total_trades, total_commissions, final_portfolio_egp, metrics)
    assert params[0] == "r-7"
    assert params[1] == "COMI.CA"
    assert params[2] == "llm"
    assert params[3] == date(2024, 1, 1)
    assert params[4] == date(2024, 3, 1)
    assert params[5] == pytest.approx(1.72)   # total_return_pct
    assert params[6] == pytest.approx(0.50)   # benchmark_return_pct
    assert params[7] == pytest.approx(1.22)   # alpha_pct
    assert params[8] == pytest.approx(1.25)   # sharpe_ratio
    assert params[9] == pytest.approx(0.55)   # calmar_ratio
    assert params[10] == pytest.approx(-3.10)  # max_drawdown_pct
    assert params[11] == pytest.approx(66.67)  # win_rate_pct
    assert params[12] == 3                    # total_trades
    assert params[13] == pytest.approx(42.50)  # total_commissions
    assert params[14] == pytest.approx(1_017_197.11)  # final_portfolio_egp


def test_write_backtest_run_rejects_bad_dates(monkeypatch):
    monkeypatch.setattr(bw, "is_postgres_available", lambda: True)
    cursor_mock = MagicMock()
    monkeypatch.setattr(bw, "db_cursor", cursor_mock)

    ok = bw.write_backtest_run(
        run_id="r-bad", ticker="COMI.CA", strategy="llm",
        start_date="not-a-date", end_date="2024-03-01",
        metrics=_SAMPLE_METRICS,
    )
    assert ok is False
    cursor_mock.assert_not_called()


def test_write_backtest_run_swallows_cursor_exception(monkeypatch):
    monkeypatch.setattr(bw, "is_postgres_available", lambda: True)

    class _BrokenCursor:
        def execute(self, *a, **k):
            raise RuntimeError("table missing")

    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=_BrokenCursor())
    fake_cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(bw, "db_cursor", lambda **kw: fake_cm)

    ok = bw.write_backtest_run(
        run_id="r-explode", ticker="COMI.CA", strategy="llm",
        start_date="2024-01-01", end_date="2024-03-01",
        metrics=_SAMPLE_METRICS,
    )
    assert ok is False


# ─── write_backtest_trades ────────────────────────────────────────────────────


def test_write_backtest_trades_noop_when_postgres_unavailable(monkeypatch):
    monkeypatch.setattr(bw, "is_postgres_available", lambda: False)
    cursor_mock = MagicMock()
    monkeypatch.setattr(bw, "db_cursor", cursor_mock)

    n = bw.write_backtest_trades(
        run_id="r-x", trades=[_sample_trade()], daily_portfolio=None
    )
    assert n == 0
    cursor_mock.assert_not_called()


def test_write_backtest_trades_uses_execute_values(monkeypatch):
    monkeypatch.setattr(bw, "is_postgres_available", lambda: True)

    fake_cursor = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(bw, "db_cursor", lambda **kw: fake_cm)

    trades = [_sample_trade(date=f"2024-01-{i:02d}") for i in range(1, 6)]
    daily = [{"date": t["date"], "portfolio_value": 1_000_000 + i * 1000}
             for i, t in enumerate(trades)]

    execute_values_mock = MagicMock()
    with patch("psycopg2.extras.execute_values", execute_values_mock):
        n = bw.write_backtest_trades(
            run_id="r-bulk", trades=trades, daily_portfolio=daily
        )

    assert n == 5
    execute_values_mock.assert_called_once()
    args, _ = execute_values_mock.call_args
    assert args[0] is fake_cursor
    assert "INSERT INTO backtest_trades" in args[1]
    assert len(args[2]) == 5
    for row in args[2]:
        assert row[0] == "r-bulk"


def test_write_backtest_trades_empty_returns_zero(monkeypatch):
    monkeypatch.setattr(bw, "is_postgres_available", lambda: True)
    cursor_mock = MagicMock()
    monkeypatch.setattr(bw, "db_cursor", cursor_mock)

    n = bw.write_backtest_trades(run_id="r-empty", trades=[], daily_portfolio=None)
    assert n == 0
    cursor_mock.assert_not_called()


def test_write_backtest_trades_swallows_cursor_exception(monkeypatch):
    monkeypatch.setattr(bw, "is_postgres_available", lambda: True)

    fake_cursor = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(bw, "db_cursor", lambda **kw: fake_cm)

    def _raise(*a, **k):
        raise RuntimeError("FK violation")

    with patch("psycopg2.extras.execute_values", _raise):
        n = bw.write_backtest_trades(
            run_id="r-explode", trades=[_sample_trade()], daily_portfolio=None
        )
    assert n == 0


# ─── _config_snapshot ─────────────────────────────────────────────────────────


def test_config_snapshot_filters_canonical_keys():
    cfg = {
        "llm_provider": "openai",
        "deep_think_llm": "deepseek-chat",
        "quick_think_llm": "deepseek-chat",
        "backend_url": "https://api.deepseek.com",
        "target_market": "EGX",
        "backtest_mode": True,
        "max_position_pct_adv": 0.10,
        # noise that should NOT be retained
        "secret_key": "hunter2",
        "huge_blob": [1] * 1_000_000,
    }
    snap = bw._config_snapshot(cfg)
    assert snap["llm_provider"] == "openai"
    assert snap["target_market"] == "EGX"
    assert "secret_key" not in snap
    assert "huge_blob" not in snap


def test_config_snapshot_handles_none_or_empty():
    """Both None and an empty dict short-circuit to None — there's nothing to
    snapshot, and we'd rather store SQL NULL than an empty JSON blob."""
    assert bw._config_snapshot(None) is None
    assert bw._config_snapshot({}) is None
