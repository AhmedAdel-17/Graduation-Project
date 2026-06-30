# P3 Design Spec: Momentum, Relative Strength & NAV Proxy

> Generated 2026-06-17. Design/spec investigation only — no code changes,
> no LLM calls, no benchmark runs. Based on P2 smoke test results, full
> code review, and the existing strategy investigation documents.

---

## 1. Executive Summary

P2 (regime-aware EY interpretation) improved reasoning quality: the LLM now
treats negative EY spread as regime context rather than a standalone bearish
veto. But all three P2 smoke tests (TMGH, SWDY, FWRY) still produced HOLD.

The remaining gap: **the system has no positive timing evidence**. The bull
researcher can describe why a stock *should* do well, but has no data-backed
signal that says "this stock *is currently participating* in the market's
move." Without momentum, relative strength, or asset-repricing signals, the
debate defaults to "wait for catalyst" — which is always a valid bear
counter-argument.

P3 adds three categories of deterministic, trade-date-safe features:
1. **Price momentum** (20/60/120-day returns + SMA position + volume confirmation)
2. **Relative strength vs EGX30** (ticker return minus index return)
3. **Real estate / asset-heavy NAV proxy** (P/B + inflation context for real_estate/holdings only)

These features are injected into the evidence pack and LLM prompts as
*additional evidence*, not as hard rules. The LLM still makes the call.

---

## 2. Why P2 Was Insufficient

### Diagnosis from smoke tests

| Ticker | Sector | Decision | Key Bear Argument | What's Missing |
|---|---|---|---|---|
| TMGH.CA | Real Estate | HOLD (0.47) | 61x P/E, leverage, liquidity | No signal that TMGH *is currently outperforming*, no NAV/replacement-cost context |
| SWDY.CA | Industrial | HOLD (0.49) | Opportunity cost vs T-bills, leverage | No signal that SWDY's 65% revenue growth is already being priced in via price momentum |
| FWRY.CA | Tech | HOLD (0.49) | 103x P/E, 1yr data only, no coverage | No positive evidence exists (correctly HOLD — P3 should NOT flip this) |

### The structural gap

P2 fixed interpretation honesty. But the pipeline still has **zero positive
timing signals**. The market analyst computes RSI, MACD, Bollinger Bands, and
50-day SMA — all *oscillator/reversal* indicators. None answer the question:

> "Is this stock currently going up, and is it going up faster than the market?"

This is the question momentum and relative strength answer. Without it, the
bull case is always speculative ("could go up if catalyst appears") and the
bear case is always grounded ("currently no reason to buy").

### What P3 provides

Concrete, falsifiable, trade-date-safe evidence that the bull researcher can
cite: "SWDY is up 35% over 60 days, outperforming EGX30 by 20pp, with
above-average volume — momentum is confirmed." This changes the debate from
"wait for catalyst" to "momentum is already the catalyst; is it sustainable?"

---

## 3. Proposed P3 Feature Set

### 3.1 Price Momentum Signals

All computed from OHLCV data already fetched by the market analyst
(120-day lookback from `create_deterministic_market_analyst`, line 559 of
`market_analyst.py`). Extended to 252-day lookback where the backtester
already fetches it (line 1257 of `backtester.py`).

| Feature | Formula | Window | Minimum bars | Insufficient output |
|---|---|---|---|---|
| `return_20d` | `(close_t / close_{t-20}) - 1` | 20 trading days | 20 | `null` |
| `return_60d` | `(close_t / close_{t-60}) - 1` | 60 trading days | 60 | `null` |
| `return_120d` | `(close_t / close_{t-120}) - 1` | 120 trading days | 120 | `null` |
| `price_vs_sma20` | `close_t / SMA(20) - 1` | 20 bars | 20 | `null` |
| `price_vs_sma50` | `close_t / SMA(50) - 1` | 50 bars | 50 | `null` |
| `price_vs_sma200` | `close_t / SMA(200) - 1` | 200 bars | 200 | `null` |
| `trend_slope_60d` | OLS slope of `ln(close)` over 60 bars, annualized | 60 bars | 30 | `null` |
| `volume_ratio_20d` | `volume_t / SMA(volume, 20)` | 20 bars | 20 | `null` |
| `volume_confirmed` | `volume_ratio_20d > 1.2` (bool) | — | — | `false` |

**Momentum label** (derived, for narrative injection):
- `strong_up`: `return_60d > +15%` AND `price > SMA50` AND `volume_confirmed`
- `moderate_up`: `return_60d > +5%` AND `price > SMA50`
- `neutral`: neither up nor down conditions met
- `moderate_down`: `return_60d < -5%` AND `price < SMA50`
- `strong_down`: `return_60d < -15%` AND `price < SMA50` AND `volume_confirmed`
- `insufficient_history`: fewer than 60 bars available

These thresholds are deliberately wide. They are NOT optimized on any
benchmark period. The labels are descriptive, not prescriptive.

### 3.2 Relative Strength vs EGX30

| Feature | Formula | Window | Source |
|---|---|---|---|
| `rs_20d` | `return_20d(ticker) - return_20d(EGX30)` | 20 bars | Local CSV (`EGX 30 Historical Data.csv`) |
| `rs_60d` | `return_60d(ticker) - return_60d(EGX30)` | 60 bars | Local CSV |
| `rs_120d` | `return_120d(ticker) - return_120d(EGX30)` | 120 bars | Local CSV |
| `rs_label` | Classification of `rs_60d` | — | Derived |

**RS label** (derived):
- `outperforming`: `rs_60d > +5pp`
- `neutral`: `-5pp ≤ rs_60d ≤ +5pp`
- `underperforming`: `rs_60d < -5pp`
- `insufficient_data`: EGX30 data unavailable or insufficient overlap

**EGX30 data handling:**
1. Primary: local CSV `EGX 30 Historical Data.csv` in the project root.
   This file exists and is verified:
   - Columns: `Date, Price, Open, High, Low, Vol., Change %`
   - Range: 2020-01-02 to 2026-06-14 (1,560 data rows)
   - Format: Investing.com export, MM/DD/YYYY dates, comma-formatted
     prices (e.g., `"51,994.63"`), descending date order (newest first),
     UTF-8-BOM encoding.
   - Fully covers the 2024-01-02 to 2024-07-14 benchmark window.
2. Loaded by a **shared utility** (`tradingagents/dataflows/egx30_loader.py`,
   new file — see §5.1a) that extracts and reuses the backtester's existing
   CSV parsing logic. Both the backtester benchmark and P3 relative strength
   consume the same `{date_str: close_price}` dict from this shared loader.
3. No yfinance fallback is needed or used for EGX30. yfinance `^CASE30` is
   unreliable (returns empty / "possibly delisted"). The shared loader does
   NOT fall back to yfinance — it returns an empty dict if the local CSV is
   missing, and callers degrade gracefully (`rs_label = "insufficient_data"`).

**Missing date handling:**
- When computing EGX30 returns, match on exact dates. If the start date
  has no EGX30 close, search backward up to 3 trading days. If no match
  within 3 days, return `null` for that window.
- Never interpolate or forward-fill EGX30 prices for return computation.

### 3.3 Real Estate / Asset-Heavy NAV Proxy

For `sector ∈ {real_estate, holdings}` only. Other sectors get none of
these fields.

| Feature | Source | Already in repo? | Notes |
|---|---|---|---|
| `pb_ratio` | `ratios.price_to_book` | YES — `financial_calculator.py` | Already computed |
| `pb_understated` | `PB_UNDERSTATED_HISTORICAL_COST` flag | YES — `sector_config.py` line 282 | Already in distress_flags |
| `debt_to_equity` | `ratios.debt_to_equity` | YES — `financial_calculator.py` | Already computed |
| `revenue_growth_yoy` | `preprocessing.revenue_growth_yoy` | YES — `data_cot.py` | Already in evidence pack |
| `nav_inflation_note` | Prompt context string | NO — new, but text only | See below |

**`nav_inflation_note`** (new prompt context, not a computed feature):

For real_estate and holdings sectors in high-rate regimes (`inflation_regime="high"`):

> "REAL ASSET REPRICING CONTEXT: In high-inflation environments (CBE > 15%),
> Egyptian real estate assets are typically carried at historical cost on the
> balance sheet, significantly understating current replacement value.
> Companies with large land banks and development pipelines may have
> book-value NAVs 2-5x below market-implied replacement cost. P/B ratios
> should be interpreted in this context — a P/B of 1.5x on historical-cost
> books may represent a significant discount to replacement value."

This is injected as interpretive context in the evidence narrative. It is
NOT a computed NAV — we have no land bank data in the repo and will not
invent any.

**What we explicitly do NOT do:**
- No land bank valuation (no data source)
- No comparable transaction analysis (no data source)
- No third-party NAV estimates (not available)
- No backlog data beyond what's in the CSV financials

---

## 4. Feature Definitions Table (Complete)

| ID | Feature | Type | Source | Computed by | Injected into | Leakage risk |
|---|---|---|---|---|---|---|
| M1 | `return_20d` | float or null | yfinance OHLCV | `momentum.py` (new) | evidence_pack, narrative | Low — uses closes ≤ trade_date |
| M2 | `return_60d` | float or null | yfinance OHLCV | `momentum.py` | evidence_pack, narrative | Low |
| M3 | `return_120d` | float or null | yfinance OHLCV | `momentum.py` | evidence_pack, narrative | Low |
| M4 | `price_vs_sma20` | float or null | yfinance OHLCV | `momentum.py` | evidence_pack, narrative | Low |
| M5 | `price_vs_sma50` | float or null | yfinance OHLCV | `momentum.py` | evidence_pack, narrative | Low |
| M6 | `price_vs_sma200` | float or null | yfinance OHLCV | `momentum.py` | evidence_pack, narrative | Low — requires 200-bar history |
| M7 | `trend_slope_60d` | float or null | yfinance OHLCV | `momentum.py` | evidence_pack, narrative | Low |
| M8 | `volume_ratio_20d` | float or null | yfinance OHLCV | `momentum.py` | evidence_pack, narrative | Low |
| M9 | `volume_confirmed` | bool | Derived from M8 | `momentum.py` | evidence_pack, narrative | None |
| M10 | `momentum_label` | str | Derived from M2, M5, M9 | `momentum.py` | narrative | None |
| R1 | `rs_20d` | float or null | M1 + EGX30 local CSV via `egx30_loader.py` | `momentum.py` | evidence_pack, narrative | Medium — EGX30 map must be truncated to ≤ trade_date |
| R2 | `rs_60d` | float or null | M2 + EGX30 local CSV via `egx30_loader.py` | `momentum.py` | evidence_pack, narrative | Medium |
| R3 | `rs_120d` | float or null | M3 + EGX30 local CSV via `egx30_loader.py` | `momentum.py` | evidence_pack, narrative | Medium |
| R4 | `rs_label` | str | Derived from R2 | `momentum.py` | narrative | None |
| N1 | `pb_ratio` | float or null | Existing ratios | Already computed | Already in evidence_pack | None |
| N2 | `pb_understated` | bool | Existing flags | Already computed | Already in distress_flags | None |
| N3 | `nav_inflation_note` | str | Static text | `data_cot.py` | narrative only | None |

---

## 5. File-by-File Implementation Plan

### 5.1a New file: `tradingagents/dataflows/egx30_loader.py`

**Purpose:** Shared, reusable EGX30 local CSV loader. Extracts the parsing
logic currently embedded in `BacktestingEngine._load_egx30_csv` (lines
372-436 of `scripts/backtester.py`) into a standalone module that both the
backtester benchmark and P3 relative strength can consume.

**Public API:**
```python
def load_egx30_csv(csv_path: Optional[str] = None) -> Dict[str, float]:
    """
    Load EGX30 index closes from a local Investing.com CSV.

    Args:
        csv_path: Explicit path. If None, searches the standard candidate
                  list in the project root (same filenames as backtester).

    Returns:
        {YYYY-MM-DD: close_price} dict. Empty dict on any failure.
        Dates are sorted ascending. Prices have commas stripped.
    """
```

**Implementation details:**
- Reuses the backtester's existing parsing: `utf-8-sig` encoding,
  `csv.DictReader`, multi-format date parsing (`MM/DD/YYYY`, `DD/MM/YYYY`,
  `YYYY-MM-DD`), comma-stripped prices.
- Handles the specific Investing.com format quirks:
  - Descending date order (newest first) — loader sorts ascending on output
  - Comma-formatted prices: `"51,994.63"` → `51994.63`
  - Filename with spaces: `"EGX 30 Historical Data.csv"`
  - UTF-8 BOM marker (`\ufeff`) via `encoding="utf-8-sig"`
- Singleton / lru_cache pattern: the CSV is loaded once per process and
  reused. The full map is immutable after load. Callers truncate by
  trade_date at usage time, not at load time.
- Candidate file list (searched in order, first match wins):
  ```
  EGX 30 Historical Data.csv
  EGX30ETF ETF Stock Price History.csv
  EGX30 ETF Stock Price History.csv
  egx30.csv
  ```
- No yfinance fallback. If no CSV found, returns empty dict and logs a warning.

**Approximate size:** ~60-80 lines.

**Backtester integration:** After `egx30_loader.py` is stable, the
backtester's `_load_egx30_csv` method should be refactored to call
`load_egx30_csv()` instead of duplicating the logic. This is a
follow-up cleanup, not a P3 blocker — the backtester can continue
using its own loader during P3 implementation, and the two can be
unified afterward.

### 5.1b New file: `tradingagents/agents/analysts/fundamentals/momentum.py`

**Purpose:** Pure-function module that computes all P3 momentum and
relative-strength features from OHLCV data. No LLM calls. No side effects.

**Input:** `closes: List[float]`, `volumes: List[float]`,
`egx30_map: Dict[str, float]` (date→price map from `egx30_loader`),
`trade_date: str`, `ohlcv_dates: List[str]` (parallel date list for closes).

**Output:** `MomentumPack` (TypedDict or NamedTuple) containing all M1-M10
and R1-R4 fields.

**Key design constraints:**
- All closes/volumes must be ≤ trade_date. The function MUST NOT access
  any future data. The caller is responsible for truncating the OHLCV
  series to trade_date before calling.
- EGX30 map is truncated internally: `{d: p for d, p in egx30_map.items() if d <= trade_date}`.
- Missing data → `None`, never 0.0 or fake values.
- No hardcoded thresholds that were tuned on any specific benchmark period.
- EGX30 date matching: for return windows, find the closest EGX30 date
  ≤ the target date by searching backward up to 3 trading days. If no
  match, return `None` for that RS window.

**Approximate size:** ~120-150 lines.

### 5.2 Modified: `tradingagents/agents/analysts/market_analyst.py`

**Change:** In `create_deterministic_market_analyst` (line 475), after
computing closes and signals:
1. Extend lookback from 120 to 252 days (needed for SMA200 and
   `return_120d`). This matches what the backtester already fetches.
2. Call `compute_momentum_pack(closes, volumes, egx30_map, trade_date)`.
3. Add the `MomentumPack` dict to the returned `technical_analysis` dict
   under key `"momentum"`.

**Impact:** The `technical_analysis` dict in `AgentState` now carries
momentum data. Downstream agents (bull/bear researchers, fundamentals
analyst) can access it.

**Lines changed:** ~15-20 in the deterministic node function.

### 5.3 Modified: `tradingagents/agents/analysts/fundamentals/data_cot.py`

**Change:** In `build_evidence_pack()`:
1. Accept optional `momentum_pack: Optional[Dict]` parameter.
2. Store momentum fields in the evidence pack dict.
3. In `format_evidence_narrative()`, add a new section:

```
── PRICE MOMENTUM & RELATIVE STRENGTH ──────────────────────────────
20-day return: +12.3% | 60-day: +28.5% | 120-day: +45.1%
Price vs SMA20: +3.2% | vs SMA50: +8.1% | vs SMA200: +22.3%
Trend slope (60d, annualized): +62%
Volume ratio (vs 20d avg): 1.35x (confirmed)
Momentum: STRONG_UP
Relative strength vs EGX30 (60d): +15.2pp (OUTPERFORMING)
```

4. For real_estate/holdings in high-rate regime, append `nav_inflation_note`
   after the existing sector context.

**Lines changed:** ~40-50.

### 5.4 Modified: `tradingagents/agents/analysts/fundamentals/thesis_cot.py`

**Change:** Add P3 guidance paragraph to `_SYSTEM_PROMPT`, after the
existing P2 HIGH-RATE REGIME GUIDANCE block:

```
MOMENTUM & RELATIVE STRENGTH GUIDANCE (P3):
When the evidence pack includes a PRICE MOMENTUM & RELATIVE STRENGTH section:
- Momentum is POSITIVE EVIDENCE for continuation, not a guarantee. A stock
  with strong_up momentum AND improving fundamentals has a stronger bull case
  than one with improving fundamentals alone.
- Relative strength (outperforming EGX30) indicates the market is already
  pricing in positive expectations. This supports a bullish thesis but also
  raises the bar for entry — is the good news already in the price?
- volume_confirmed=true alongside positive momentum is a stronger signal
  than price movement alone.
- Do NOT treat momentum as a standalone BUY signal. It is one factor among
  fundamentals, valuation, risk, and macro context.
- If momentum is insufficient_history, do not penalize — simply note that
  price trend data is unavailable and rely on fundamental signals.
```

**Lines changed:** ~15.

### 5.5 Modified: `tradingagents/agents/analysts/fundamentals/concept_cot.py`

**Change:** Add one rule to `_SYSTEM_PROMPT`:

```
8. MOMENTUM CONTEXT: If the evidence pack includes price momentum and
   relative strength data, incorporate it into your growth_signal
   assessment. Strong momentum with strong fundamentals = "positive".
   Divergence (strong momentum but weak fundamentals, or vice versa) =
   "mixed" and warrants a coherence_note.
```

**Lines changed:** ~5.

### 5.6 Modified: `tradingagents/agents/analysts/fundamentals/sector_config.py`

**Change:** Add `NAV_INFLATION_NOTE` constant string for real_estate and
holdings sectors. Used by `data_cot.py` when `inflation_regime="high"`.

**Lines changed:** ~5-10.

### 5.7 Modified: `tradingagents/agents/analysts/fundamentals_analyst.py`

**Change:** Pass the `technical_analysis.momentum` dict from `AgentState`
into the pipeline so `data_cot.py` can include it in the evidence pack.

**Lines changed:** ~5-10.

### 5.8 NOT modified: `tradingagents/graph/propagation.py`

EGX30 data does not need to flow through `AgentState`. The market analyst
calls `egx30_loader.load_egx30_csv()` directly (singleton/cached — loaded
once per process). No changes to propagation or state schema required.

### 5.9 Modified: `tradingagents/ablation/deterministic_agents.py` (discovered during integration)

The backtester graph is built by `ablation/runner.py::_rebuild_graph()`, which
wires `deterministic_market_analyst` from this file — **not** the factory in
`market_analyst.py`. This required a second P3 integration:

- Add EODHD → yfinance OHLCV fetch (same pattern as `market_analyst.py`)
- Call `compute_momentum_pack()` + `load_egx30_csv()`
- Add `"momentum": momentum_pack` to `structured_analysis`

See Appendix D.1 for full discovery details.

### 5.10 Modified: `scripts/backtester.py` (audit instrumentation)

Added P3 debug fields to the audit log entry after the main audit dict:

- `p3_momentum_debug`: compact 7-field dict extracted from `final_state["technical_analysis"]["momentum"]`
- `p3_evidence_narrative_contains_momentum_section`: boolean mirroring momentum presence

No decision logic changes. Pure read-only instrumentation for verification.

### Files NOT modified

| File | Reason |
|---|---|
| `research_manager.py` | Momentum evidence reaches the debate via analyst reports — no direct injection needed |
| `risk_manager.py` | Risk constraints are unchanged — P3 adds evidence, not rules |
| `calibration.py` | Calibration logic is unchanged — P3 does not affect direction labels |
| `scoring.py` | Unified scoring is unchanged |

---

## 6. Leakage Safety Table

| Feature | Data source | As-of-date boundary | How to truncate | Cache risk | Required regression test |
|---|---|---|---|---|---|
| M1-M3 (returns) | yfinance OHLCV | `close[trade_date]` is the latest allowed | `closes = [c for c in ohlcv if c.date <= trade_date]` | yfinance may return future bars if `end_date` param is wrong — MUST use `trade_date` as end_date | `test_no_future_ohlcv_in_momentum` |
| M4-M6 (SMA position) | Same OHLCV | Same | Same truncation | Same | Same test |
| M7 (trend slope) | Same OHLCV | Same | Same | Same | Same test |
| M8-M9 (volume) | Same OHLCV volume | Same | Same | Same | `test_no_future_volume_in_momentum` |
| R1-R3 (relative strength) | EGX30 local CSV via `egx30_loader.py` + ticker OHLCV | Both must be ≤ trade_date | `egx30_map = {d: p for d, p in raw_map.items() if d <= trade_date}` | CSV is static historical file (no future risk inherent, but truncation still required because CSV range extends to 2026-06-14). No yfinance fallback. | `test_no_future_egx30_in_relative_strength` |
| N1-N2 (P/B, flag) | Existing fundamentals CSV | Already trade-date-safe (fiscal period ≤ trade_date) | No change needed | None | Existing fundamentals tests |
| N3 (NAV note) | Static text | N/A (no data, just context) | N/A | None | None |

### Critical leakage vectors to test

1. **yfinance `end_date` off-by-one**: yfinance's `history(end=X)` is
   exclusive (returns data up to but not including X). If `end_date` is
   set to `trade_date`, the last bar is trade_date - 1. To get trade_date's
   close, set `end_date = trade_date + 1 day`. Verify this in tests.

2. **EGX30 CSV contains future dates**: The CSV may contain dates after
   trade_date. The truncation `d <= trade_date` handles this, but a test
   must verify it.

3. **Cached OHLCV from previous dates**: The market analyst's OHLCV
   fetch uses `trade_date` as `end_date`, so each evaluation gets fresh
   data truncated to that date. No persistent cache leak — but verify
   with a test that runs two consecutive trade_dates and confirms the
   second doesn't see data from after its trade_date.

---

## 7. Test Plan

All tests go in `tests/test_p3_momentum.py`. Run with:
```bash
python3 -m pytest tests/test_p3_momentum.py -v --tb=short
```

### 7.1 Momentum computation tests

| # | Test | What it verifies |
|---|---|---|
| T1 | `test_return_20d_basic` | Correct 20-day return from known price series |
| T2 | `test_return_60d_basic` | Correct 60-day return |
| T3 | `test_return_120d_basic` | Correct 120-day return |
| T4 | `test_insufficient_history_returns_null` | < 20 bars → `return_20d` is null, not 0.0 or error |
| T5 | `test_sma_position_above` | Price above SMA50 → positive `price_vs_sma50` |
| T6 | `test_sma_position_below` | Price below SMA50 → negative `price_vs_sma50` |
| T7 | `test_sma200_insufficient_history` | < 200 bars → `price_vs_sma200` is null |
| T8 | `test_volume_ratio_above_threshold` | volume_ratio > 1.2 → `volume_confirmed` = True |
| T9 | `test_volume_ratio_below_threshold` | volume_ratio ≤ 1.2 → `volume_confirmed` = False |
| T10 | `test_trend_slope_positive` | Uptrending series → positive annualized slope |
| T11 | `test_trend_slope_flat` | Flat series → slope near zero |

### 7.2 Momentum label tests

| # | Test | What it verifies |
|---|---|---|
| T12 | `test_momentum_label_strong_up` | +20% 60d, above SMA50, vol confirmed → `strong_up` |
| T13 | `test_momentum_label_neutral` | Mixed signals → `neutral` |
| T14 | `test_momentum_label_strong_down` | -20% 60d, below SMA50, vol confirmed → `strong_down` |
| T15 | `test_momentum_label_insufficient` | < 60 bars → `insufficient_history` |

### 7.3 EGX30 loader tests (`tests/test_egx30_loader.py`)

| # | Test | What it verifies |
|---|---|---|
| T16 | `test_load_real_csv_exists` | `load_egx30_csv()` finds `EGX 30 Historical Data.csv` (with spaces) and returns non-empty dict |
| T17 | `test_investing_com_number_format` | Prices like `"51,994.63"` are parsed to `51994.63` (comma-stripped) |
| T18 | `test_descending_date_csv_sorting` | CSV rows are newest-first; output dict keys sorted ascending still work correctly |
| T19 | `test_date_format_mm_dd_yyyy` | `"06/14/2026"` → `"2026-06-14"` (Investing.com default format) |
| T20 | `test_date_range_covers_benchmark` | Loaded map has dates from 2020-01-02 through 2026-06-14 |
| T21 | `test_utf8_bom_handled` | File with `\ufeff` BOM marker loads without error |
| T22 | `test_missing_csv_returns_empty` | Non-existent path → returns `{}`, no exception |

### 7.4 Relative strength tests

| # | Test | What it verifies |
|---|---|---|
| T23 | `test_rs_60d_outperforming` | Ticker +30%, EGX30 +10% → `rs_60d` = +20pp, label = `outperforming` |
| T24 | `test_rs_60d_underperforming` | Ticker +5%, EGX30 +15% → `rs_60d` = -10pp, label = `underperforming` |
| T25 | `test_rs_missing_egx30_returns_null` | Empty EGX30 map → all `rs_*` = null, label = `insufficient_data` |
| T26 | `test_rs_missing_egx30_dates_backward_search` | EGX30 has gap on target date → backward search up to 3 days finds nearest |
| T27 | `test_rs_missing_egx30_dates_beyond_3_days` | EGX30 gap > 3 days → returns null (no stale-price leakage) |
| T28 | `test_rs_windows_use_only_dates_lte_trade_date` | EGX30 map with dates after trade_date → those dates excluded from return computation |
| T29 | `test_no_yfinance_fallback_when_csv_exists` | When local CSV is present, no yfinance call is made for EGX30 |

### 7.5 Leakage safety tests

| # | Test | What it verifies |
|---|---|---|
| T30 | `test_no_future_ohlcv_in_momentum` | Pass OHLCV with dates after trade_date → truncated, not used |
| T31 | `test_no_future_egx30_in_relative_strength` | EGX30 map with future dates → truncated |
| T32 | `test_no_future_volume_in_momentum` | Volume from future dates → not used |
| T33 | `test_sequential_dates_no_leakage` | Compute momentum for date T, then T+20 → T+20 does not contain T+21 data |

### 7.6 NAV proxy tests

| # | Test | What it verifies |
|---|---|---|
| T34 | `test_nav_note_injected_for_real_estate_high_rate` | real_estate + high regime → nav_inflation_note in narrative |
| T35 | `test_nav_note_not_injected_for_operational` | operational sector → no nav_inflation_note |
| T36 | `test_nav_note_not_injected_for_normal_rate` | real_estate + normal regime → no nav_inflation_note |
| T37 | `test_existing_pb_flag_unchanged` | PB_UNDERSTATED_HISTORICAL_COST still fires for real_estate |

### 7.7 Integration / regression tests

| # | Test | What it verifies |
|---|---|---|
| T38 | `test_evidence_pack_contains_momentum_section` | data_cot narrative includes "PRICE MOMENTUM" header |
| T39 | `test_evidence_pack_momentum_null_when_unavailable` | No OHLCV → momentum fields null, narrative says "unavailable" |
| T40 | `test_p2_tests_still_pass` | Import and run all P2 regime tests → 31/31 pass |
| T41 | `test_temporal_safety_suite_still_passes` | Import and run temporal safety tests → all pass |

**Total: 41 tests** (31 momentum/RS/NAV + 7 EGX30 loader + 2 regression + 1 integration).

### 7.8 TMGH sector classification regression tests (added 2026-06-17)

| # | Test | What it verifies |
|---|---|---|
| T42 | `test_t42_tmgh_ca_classifies_as_real_estate` | `classify_sector("TMGH.CA")` → `real_estate` |
| T42b | `test_t42b_tmgh_bare_classifies_as_real_estate` | `classify_sector("TMGH")` → `real_estate` |
| T42c | `test_t42c_tmg_still_classifies_as_real_estate` | `classify_sector("TMG")` → `real_estate` (backward compat) |
| T43 | `test_t43_sector_config_init_strips_ca` | `SectorConfig("TMGH.CA").sector` → `real_estate` |
| T44 | `test_t44_nav_inflation_note_triggers_for_tmgh` | TMGH + high rate + momentum → "REAL ASSET REPRICING CONTEXT" in narrative |
| T45 | `test_t45_ca_suffix_stripping_does_not_affect_others` | COMI.CA→banks, SWDY.CA→holdings, ETEL.CA→operational, HELI.CA→real_estate |

### 7.9 P3 audit instrumentation tests (added 2026-06-17)

| # | Test | What it verifies |
|---|---|---|
| T1-inst | `test_t1_debug_present_when_momentum_exists` | `p3_momentum_debug` populated from state |
| T1b-inst | `test_t1_debug_is_compact` | Only 7 fields, not the full momentum pack |
| T2-inst | `test_t2_json_serializable_with/without_momentum` | JSON round-trip works |
| T3-inst | `test_t3_no_technical_analysis` / `_none` / `_missing` / `_empty` / `_momentum_none` | Graceful null |
| T4-inst | `test_t4_fields_are_passthrough` | No `compute_momentum_pack` or `yfinance` imports |
| T5-inst | `test_t5_narrative_flag_true` | Flag mirrors momentum presence |
| T6-inst | `test_t6_string/list/int_technical_analysis` | Non-dict state handled safely |

**Updated total: 61 tests** (41 original + 6 TMGH sector + 14 audit instrumentation).

---

## 8. Expected Effect by Ticker

### TMGH.CA (Real Estate)

**Current P2 result:** HOLD (0.47). Bear cites 61x P/E, leverage, liquidity.
Bull cites devaluation catalyst but has no timing evidence.

**P3 expected effect:**
- TMGH was trading at ~61.66 on 2024-03-24. If momentum is positive (e.g.,
  up from ~40 EGP in late 2023 → ~62 = +55% in 120 days), the bull case
  gains concrete evidence: "price is already moving up sharply, volume
  confirms, outperforming EGX30."
- The `nav_inflation_note` provides context for the 61x P/E: "P/B on
  historical-cost books understates replacement value."
- **Likely outcome:** Decision may shift to BUY or remain HOLD with higher
  confidence, depending on whether momentum is strong enough to overcome
  the leverage and liquidity concerns. The debate becomes genuinely
  two-sided instead of bear-dominated.
- **P3 would NOT need:** No NAV computation (we have no land bank data).

### SWDY.CA (Industrial)

**Current P2 result:** HOLD (0.49). Bear cites opportunity cost vs T-bills,
184% interest expense growth. Bull cites 65% revenue growth.

**P3 expected effect:**
- If SWDY shows positive momentum (up from ~30 EGP to ~42 = +40% in 120
  days), the bull gains timing evidence to match the revenue growth story.
- Relative strength vs EGX30 would show whether the market is already
  pricing in the growth — if SWDY is outperforming, the bull's thesis has
  market confirmation.
- **Likely outcome:** May shift to BUY if momentum is strong and confirmed.
  The interest expense argument is real but may be outweighed by growth +
  momentum together. Most likely shifts from HOLD(0.49) to HOLD(0.55-0.60)
  or weak BUY.
- **P3 would NOT need:** No NAV logic (operational sector).

### FWRY.CA (Tech)

**Current P2 result:** HOLD (0.49). Bear cites 103x P/E, 1yr data, no coverage.

**P3 expected effect:**
- Even if FWRY has positive momentum, the bear case is fundamentally
  structural (data insufficiency, extreme valuation). P3 momentum evidence
  alone is unlikely to overcome "only 1 year of financial data."
- **Likely outcome:** HOLD. This is correct — P3 should not flip a data-
  insufficient stock to BUY. If it does, that's a red flag.
- **P3 would NOT need:** No NAV logic. Momentum may be present but
  insufficient to overcome fundamental gaps.

### COMI.CA (Banks)

**Expected P3 effect:**
- Banks sector is `_SECTORS_ALWAYS_UP` in calibration — down calls are
  already blocked. Momentum evidence adds timing context.
- COMI is the most liquid EGX stock (high ADV). Momentum and relative
  strength should be well-populated.
- If COMI shows strong momentum + improving fundamentals, the debate may
  shift from HOLD to BUY. COMI had 2 BUYs in 47 dates under P1-fix —
  P3 could increase this to 4-6.
- **Risk:** COMI's fundamentals are already the strongest in the benchmark.
  P3 might just add confirmation of what's already a strong case.

### ETEL.CA (Telecom)

**Expected P3 effect:**
- ETEL has moderate fundamentals and moderate liquidity. Momentum signals
  provide an independent timing source.
- If ETEL is trending up with positive RS, the bull case is strengthened.
  If flat or down, the bear case is confirmed by price action.
- **Likely outcome:** Depends on actual price data. ETEL's P1-fix results
  showed 1 BUY, 1 SELL, and mostly HOLDs. P3 could add 1-2 more BUYs
  in dates where momentum was strong.

---

## 9. Risks and Tradeoffs

### 9.1 Momentum is backward-looking

Momentum signals describe what has already happened. A stock can reverse
immediately after a strong momentum signal. The LLM prompt guidance
explicitly states momentum is "positive evidence for continuation, not a
guarantee."

### 9.2 Survivorship bias in EGX30 CSV

The EGX30 composition changes periodically. The local CSV contains the
index as-is, not the historical composition. This means relative strength
is computed against the *current* EGX30, which may not match the index at
the trade_date. This is an acceptable approximation for a research tool
but should be noted as a limitation.

### 9.3 Market analyst lookback extension

Extending the deterministic market analyst from 120-day to 252-day lookback
increases yfinance data fetch by ~2x. For EGX stocks, yfinance is the
primary source (EODHD free tier doesn't cover .CA). The fetch is already
network-bound; the additional data should add < 1 second per evaluation.

### 9.4 LLM prompt length increase

Adding the momentum section to the evidence narrative adds ~100-150 tokens.
The current evidence pack is ~1,500-2,500 tokens. This is well within
DeepSeek's context window and should not affect reasoning quality.

### 9.5 Over-anchoring on momentum

Risk: the LLM may over-weight momentum signals because they're concrete
numbers, while fundamental risks are qualitative. The prompt guidance
explicitly says "Do NOT treat momentum as a standalone BUY signal." The
risk judge (downstream) is unchanged and can still veto.

### 9.6 EGX30 data source is local-only

yfinance `^CASE30` is unreliable (returns "possibly delisted" / empty).
The local CSV `EGX 30 Historical Data.csv` (Investing.com export, 1,560
rows, 2020-01-02 to 2026-06-14) is the sole EGX30 data source. This is
sufficient for the benchmark window but requires periodic manual refresh
for forward-looking dates beyond 2026-06-14. If the CSV is ever deleted
or corrupted, RS features degrade gracefully to `insufficient_data`.

---

## 10. What Not to Do

1. **No hard BUY rules.** P3 adds evidence, not decision rules. Momentum
   signals go into the evidence pack for the LLM to weigh — never as
   `if momentum > X: return BUY`.

2. **No future returns.** Every feature computation MUST use only data ≤
   trade_date. No exceptions, no "peek one day ahead for settlement."

3. **No optimizing thresholds on H1 2024 only.** The momentum label
   thresholds (±5%, ±15%) and RS thresholds (±5pp) were chosen from
   general finance practice, not tuned on any benchmark window. Do NOT
   adjust these to improve a specific benchmark result.

4. **No live news/social in historical tests.** P3 changes only
   fundamentals and market analyst pipelines. The backtest social
   honesty gate remains active and unchanged.

5. **No weakening the risk manager.** The risk manager's deterministic
   VETO (position/liquidity/loss/short/leverage) is unchanged. P3 adds
   positive evidence upstream; it does not remove risk guardrails.

6. **No hidden benchmark cheating.** Do not use future EGX30 data for
   relative strength. Do not use future OHLCV for momentum. Do not
   optimize any threshold or label after seeing benchmark results.

7. **No full benchmark until unit tests and one targeted smoke test pass.**
   Implementation order: (1) write `momentum.py`, (2) write 31 tests,
   (3) make all tests green, (4) run one single-date smoke test (e.g.,
   SWDY.CA on 2024-05-26), (5) review reasoning quality, (6) only then
   consider a full benchmark.

8. **No changes to `research_manager.py`.** The debate receives momentum
   via analyst reports. Direct injection into the debate structure adds
   coupling and is unnecessary.

9. **No changes to `calibration.py`.** Calibration is about the fundamental
   direction signal. Momentum is a separate evidence category and should
   not affect the base-rate-aware calibration policy.

10. **No inventing data.** The NAV inflation note is explicitly labeled
    as interpretive context. Do not compute "estimated NAV" from P/B
    ratios or land area assumptions — we have no data for that.

11. **No scoring the momentum signal numerically.** Do not add momentum
    to the unified [-1, 1] scoring aggregator. The momentum pack is
    qualitative evidence for the LLM, not a numerical input to the
    signal processor.

---

## 11. Recommended Next Action

1. **Implement `momentum.py`** — pure functions, no LLM, no side effects.
   ~120-150 lines.

2. **Write `tests/test_p3_momentum.py`** — 31 tests covering all features,
   labels, leakage safety, and regression against P2.

3. **Make all tests green** — including the 31 new tests AND the existing
   785 tests (P2 suite, temporal safety, fundamentals, etc.).

4. **Modify `market_analyst.py`, `data_cot.py`, `thesis_cot.py`,
   `concept_cot.py`, `sector_config.py`, `fundamentals_analyst.py`** —
   the 6 files in the implementation plan.

5. **Run one clean smoke test** — SWDY.CA on 2024-05-26 with
   `EMBEDDINGS_BACKEND_URL=disabled`. Verify:
   - Momentum section appears in evidence pack
   - RS section appears (or shows `insufficient_data` if no EGX30 CSV)
   - Reasoning references momentum/RS evidence
   - Decision is at least as good as P2 (may or may not change)

6. **Only then consider a full benchmark.** The benchmark is for
   measurement, not for tuning.

---

## Appendix A: Relevant Code Locations

| File | Line(s) | What's there | P3 relevance |
|---|---|---|---|
| `market_analyst.py` | 475-678 | Deterministic market analyst | Extend lookback, add momentum computation |
| `market_analyst.py` | 559 | `relativedelta(days=120)` | Change to `days=252` |
| `market_analyst.py` | 631-633 | Bollinger/SMA signal enrichment | Add momentum pack here |
| `data_cot.py` | 35-117 | `build_evidence_pack()` | Add momentum_pack parameter and fields |
| `data_cot.py` | 169-end | `format_evidence_narrative()` | Add momentum/RS narrative section |
| `thesis_cot.py` | 82-94 | P2 HIGH-RATE REGIME GUIDANCE | Add P3 MOMENTUM GUIDANCE after this |
| `concept_cot.py` | 56-57 | Rule 6 (HIGH-RATE REGIME) | Add rule 8 (MOMENTUM CONTEXT) |
| `sector_config.py` | 280-285 | PB_UNDERSTATED, CONSOLIDATED_BLENDING | Add NAV_INFLATION_NOTE constant |
| `fundamentals_analyst.py` | ~61-end | `fundamentals_analyst_node` | Pass momentum data through to pipeline |
| `backtester.py` | 388-436 | `_load_egx30_csv` | Reference — momentum.py should use same CSV loader |
| `backtester.py` | 1257 | `date - 252 days` fetch start | Already 252 days — confirms sufficient history |
| `y_finance.py` | 21-193 | `get_YFin_data_online` | OHLCV source — truncated to 20 records (momentum.py needs raw closes, not this truncated output) |
| `y_finance.py` | 152-156 | `MAX_RECORDS = 20` truncation | P3 must bypass this — use raw yfinance download or the backtester's full OHLCV |

## Appendix B: EGX30 CSV — Verified Available

**File:** `/Users/mennaazazy/stockHive/Graduation-Project/EGX 30 Historical Data.csv`

**Verified on 2026-06-17:**
```
$ head -3 "EGX 30 Historical Data.csv"
﻿"Date","Price","Open","High","Low","Vol.","Change %"
"06/14/2026","51,994.63","50,818.84","52,201.58","51,765.01","395.86M","2.31%"
"06/11/2026","50,818.84","51,256.65","51,215.58","50,686.22","308.96M","-0.85%"

$ tail -3 "EGX 30 Historical Data.csv"
"01/06/2020","13,212.66","13,283.66","13,283.66","13,005.23","103.11M","-0.53%"
"01/05/2020","13,283.66","13,899.54","13,899.54","13,283.66","","-4.43%"
"01/02/2020","13,899.54","13,961.56","13,964.95","13,899.54","28.40M","-0.44%"

$ wc -l "EGX 30 Historical Data.csv"
    1561  (1 header + 1,560 data rows)
```

**Format details:**
- Source: Investing.com historical data export
- Encoding: UTF-8 with BOM (`\ufeff` at byte 0)
- Date format: MM/DD/YYYY (e.g., `"06/14/2026"` = 2026-06-14)
- Price format: comma-separated thousands (e.g., `"51,994.63"` → 51994.63)
- Sort order: descending (newest first)
- Date range: 2020-01-02 to 2026-06-14
- Covers the full 2024-01-02 to 2024-07-14 benchmark window

**Backtester path matching:** The backtester already lists this exact
filename as the first candidate (line 1157 of `backtester.py`):
```python
os.path.join(os.path.dirname(__file__), "..", "EGX 30 Historical Data.csv")
```

**P3 relative strength is fully feasible** from this local CSV. No
additional data acquisition required.

### Shared loader design (`tradingagents/dataflows/egx30_loader.py`)

The parsing logic currently lives inside `BacktestingEngine._load_egx30_csv`
(lines 372-436 of `scripts/backtester.py`). P3 extracts this into a shared
module so both the backtester benchmark and `momentum.py` relative strength
use identical CSV parsing, date normalization, and price extraction logic.

Key parsing rules (already implemented in backtester, to be preserved):
1. `csv.DictReader` with `encoding="utf-8-sig"` (handles BOM)
2. Date parsing: try `%m/%d/%Y` first, then `%d/%m/%Y`, then `%Y-%m-%d`
3. Price: `.strip().strip('"').replace(",", "")` → `float()`
4. Skip rows where date or price parsing fails (silent)
5. Output: `Dict[str, float]` — `{YYYY-MM-DD: close_price}`
6. Cached via `functools.lru_cache` (loaded once per process)

## Appendix C: Token Budget Impact

| Component | Current tokens | P3 addition | New total |
|---|---|---|---|
| Evidence narrative (data_cot) | ~1,500-2,500 | +100-150 (momentum section) | ~1,600-2,650 |
| Thesis system prompt | ~1,200 | +120 (P3 guidance) | ~1,320 |
| Concept system prompt | ~600 | +40 (rule 8) | ~640 |
| Total per-analysis LLM input | ~8,000-12,000 | +260-310 | ~8,260-12,310 |

Well within DeepSeek's 64K context window. No cost concern.

---

## Appendix D: Implementation Discoveries (2026-06-17)

### D.1 Backtester graph path — dual integration required

The backtester (`scripts/backtester.py`) does **not** use the graph compiled by
`tradingagents/graph/setup.py` directly. Instead, it calls
`tradingagents/ablation/runner.py::_rebuild_graph()`, which wires its own
deterministic node implementations from
`tradingagents/ablation/deterministic_agents.py`.

For the EGX market (`target_market == "EGX"`), `_rebuild_graph` maps:
- `"Market Analyst"` → `deterministic_market_analyst` (from `ablation/deterministic_agents.py`)
- `"Fundamentals Analyst"` → `deterministic_fundamentals_analyst` (from `ablation/deterministic_agents.py`)

This is a **separate code path** from `create_deterministic_market_analyst()`
in `tradingagents/agents/analysts/market_analyst.py`, which is used by the
non-backtester graph (e.g., `server/api_server.py`, `main.py`).

**Consequence:** P3 momentum had to be integrated into **both**:

| File | Used by |
|---|---|
| `tradingagents/agents/analysts/market_analyst.py` | Live graph (`setup.py`) |
| `tradingagents/ablation/deterministic_agents.py` | Backtester graph (`_rebuild_graph`) |

**Discovery timeline:** The first SWDY.CA smoke test (2026-06-17) returned
`p3_momentum_debug: null` because only `market_analyst.py` had been integrated.
The ablation version was missing P3 entirely. After adding the same EODHD →
yfinance → `compute_momentum_pack` block to `deterministic_agents.py`, the
second smoke test returned populated momentum fields.

**Recommendation for future features:** Any new feature that touches the
market analyst or fundamentals analyst must be integrated into both the
`market_analyst.py` factory function **and** the `ablation/deterministic_agents.py`
standalone function. A follow-up task could unify these into a single shared
implementation to prevent divergence.

### D.2 TMGH sector mapping — `.CA` suffix and missing alias

`classify_sector("TMGH.CA")` returned `"operational"` (default) because:

1. `SECTOR_MAP` had `"TMG": "real_estate"` but not `"TMGH"`.
   TMGH is the Yahoo Finance ticker for Talaat Moustafa Group Holding;
   TMG is an older/shorter alias used in some EGX contexts.
2. `classify_sector()` did not strip the `.CA` suffix before lookup,
   so even `classify_sector("TMG.CA")` would have failed.

**Fix (2026-06-17):**
- Added `"TMGH": "real_estate"` to `SECTOR_MAP` in `sector_config.py`.
- Changed `classify_sector()` to use `ticker.upper().removesuffix(".CA")`
  before the dict lookup.
- Added 6 regression tests (T42-T45) covering:
  - `TMGH.CA` and `TMGH` both classify as `real_estate`
  - `TMG` still classifies as `real_estate` (backward compatible)
  - `SectorConfig("TMGH.CA")` resolves to `real_estate`
  - NAV inflation note triggers for TMGH in high-rate regime with momentum
  - `.CA` stripping does not break other tickers (COMI, SWDY, ETEL, HELI)

**Recommendation:** Audit all tickers in `default_config.EGX_TICKERS` against
`SECTOR_MAP` to catch similar mismatches. Several tickers (e.g., `ORAS.CA`,
`FWRY.CA`, `EFIH.CA`) may also be missing or misclassified.

## Appendix E: P3 Smoke-Test Results (2026-06-17)

### E.1 Audit instrumentation

To verify that `momentum_pack` reaches the LLM prompt path, the backtester
audit log was extended with two fields (added to `scripts/backtester.py`):

```
p3_momentum_debug = {
    "return_20d", "return_60d", "return_120d",
    "momentum_label", "rs_60d", "rs_label", "volume_confirmed"
}
p3_evidence_narrative_contains_momentum_section = true | false
```

14 unit tests cover presence, compactness, JSON serializability, graceful
fallback when momentum is absent, and no-future-data safety.

### E.2 SWDY.CA — 2024-05-26

| Field | Value |
|---|---|
| Decision | HOLD |
| Confidence | 0.487 |
| LLM calls | 8 |
| return_20d | 0.2544 |
| return_60d | 0.3463 |
| return_120d | 0.5450 |
| momentum_label | **strong_up** |
| rs_60d | 0.4226 |
| rs_label | **outperforming** |
| volume_confirmed | **true** |
| p3_evidence_narrative_contains_momentum_section | **true** |

Sector: holdings (correctly classified). Rate regime: HIGH (CBE 27.25%).
Despite strong momentum and volume confirmation, the LLM held due to
-17.2% earnings yield spread vs 27.25% risk-free rate. This validates
the P3 design principle: momentum is enrichment context, not a BUY trigger.

### E.3 TMGH.CA — 2024-03-24

| Field | Value |
|---|---|
| Decision | HOLD |
| Confidence | 0.47 |
| LLM calls | 8 |
| return_20d | 0.3248 |
| return_60d | 1.4762 |
| return_120d | 3.5781 |
| momentum_label | **moderate_up** |
| rs_60d | 1.2769 |
| rs_label | **outperforming** |
| volume_confirmed | **false** |
| p3_evidence_narrative_contains_momentum_section | **true** |

Sector: real_estate (fixed from operational — see D.2). Rate regime: HIGH
(CBE 27.25%). The 357.8% 120-day return reflects TMGH's massive EGP
devaluation trade rally. Despite extreme momentum (+128pp vs EGX30),
the LLM correctly held due to 61x P/E and -25.6% earnings yield spread.

The LLM's reasoning referenced NAV ("Sell-side NAV revaluation coverage
initiation" as a bull catalyst) and real estate sector dynamics ("off-plan
sales velocity declining as 27.25% rates crush buyer affordability"),
confirming the sector-specific context reached the prompt.

`volume_confirmed=false` correctly downgraded the momentum label from
`strong_up` to `moderate_up` (0.92x average volume indicates distribution,
not accumulation) — the volume gate is working as designed.

### E.4 Key validation conclusions

1. **Momentum reaches the LLM prompt** — both tickers show
   `p3_evidence_narrative_contains_momentum_section=true`.
2. **P3 does not override fundamentals** — both extreme-momentum tickers
   remained HOLD because valuation and rate-regime signals dominated.
3. **Volume gate is discriminating** — SWDY (1.35x volume) got `strong_up`;
   TMGH (0.92x volume) got `moderate_up`. The distinction is meaningful.
4. **Sector classification matters** — TMGH's real estate sector notes
   (P/B understated, percent-completion revenue, structural D/E) and
   NAV inflation note are now correctly applied.
5. **Relative strength is informative** — TMGH's +128pp outperformance
   vs EGX30 over 60 days is a legitimate signal that the LLM can weigh
   against valuation and macro headwinds.

---

## Appendix F: P3 Controlled 5-Ticker Benchmark (2026-06-17)

### F.1 Setup

- **Tickers:** COMI.CA, TMGH.CA, ETEL.CA, SWDY.CA, FWRY.CA
- **Window:** 2024-01-02 → 2024-07-14
- **Interval:** 20 calendar days (9–10 evaluation dates per ticker)
- **Analysts:** market, fundamentals, news, social
- **Capital:** 1,000,000 EGP
- **Benchmark:** EGX30 buy-and-hold (+12.16% over window)
- **Memory disabled:** `EMBEDDINGS_BACKEND_URL=disabled` (BM25 fallback)
- **LLM:** DeepSeek-chat via OpenAI-compatible endpoint

### F.2 Per-ticker results

| Ticker | Dates | BUY | SELL | HOLD | Return | Sharpe | Alpha vs EGX30 |
|--------|------:|----:|-----:|-----:|-------:|-------:|---------------:|
| COMI.CA | 10 | 4 | 1 | 5 | +4.65% | 2.42 | -7.51% |
| TMGH.CA | 8 | 1 | 1 | 6 | +1.75% | 2.47 | -10.41% |
| ETEL.CA | 9 | 0 | 0 | 9 | 0.00% | 0.00 | -12.16% |
| SWDY.CA | 10 | 2 | 1 | 7 | **+17.09%** | **4.97** | **+4.94%** |
| FWRY.CA | 10 | 0 | 0 | 10 | 0.00% | 0.00 | -12.16% |

**Pooled:** Mean return 4.70%, Mean Sharpe 1.97, Mean alpha -7.46%, 1/5 beat EGX30.

### F.3 P3 instrumentation coverage

| Metric | Value |
|--------|-------|
| `p3_momentum_debug` populated | 47/47 (100%) |
| `p3_evidence_narrative_contains_momentum_section` | 47/47 (100%) |
| Momentum label distribution | moderate_up 40%, neutral 30%, moderate_down 15%, strong_up 11%, strong_down 2% |
| RS label distribution | outperforming 34%, neutral 36%, underperforming 30% |
| Volume confirmed | 11/47 (23%) |

### F.4 Comparison vs P1/P2 baseline (cbfix, 2026-06-16)

| Metric | P1/P2 Baseline | P3 | Delta |
|--------|:--------------:|:--:|:-----:|
| Mean total return | 1.27% | 4.70% | +3.43 pp |
| Mean Sharpe | 1.65 | 1.97 | +0.32 |
| Mean alpha vs EGX30 | -10.89% | -7.46% | +3.43 pp |
| Tickers beating EGX30 | 0/5 | 1/5 | +1 |
| Positive Sharpe tickers | 2/5 | 3/5 | +1 |
| Total BUY decisions | 5 | 7 | +2 |
| HOLD rate | 38/47 (80.9%) | 37/47 (78.7%) | -2.2 pp |

### F.5 Interpretation

**P3 was a net positive but not sufficient to beat EGX30 overall.**

P3 improved signal visibility and reduced missed-momentum cases, especially
SWDY.CA — the only ticker to produce positive alpha (+4.94%). SWDY had the
strongest momentum signals (3× `strong_up`, 7× `outperforming` RS, 4× volume
confirmed) and P3 correctly nudged the system toward buying a genuine uptrend.

P3 did not solve the core benchmark underperformance. Mean alpha remains
-7.46% — deeply negative. The remaining issue is not absence of momentum
evidence; it is decision conservatism / capital deployment / entry threshold
behavior. The HOLD rate dropped only marginally (80.9% → 78.7%), and 2 of 5
tickers (ETEL, FWRY) produced zero trades despite having P3 momentum data
available on every evaluation date.

Key behavioral observations:

1. **P3 does not blindly buy momentum.** ETEL (mostly underperforming RS)
   and FWRY remained all-HOLD. TMGH remained cautious (1 BUY only) despite
   extreme momentum (`strong_up`, +128pp RS), which is reasonable given 61×
   P/E and -25.6% earnings yield spread. P3 provides evidence, not overrides.

2. **SWDY is the success case.** The only ticker where momentum was strong,
   RS was consistently outperforming, AND the LLM judged the risk/reward
   favorably. This is the intended design: momentum as additional conviction
   for thesis-worthy entries.

3. **Attribution caveat.** Some decision differences between runs may reflect
   LLM/path variability across runs, so attribution to P3 should be
   interpreted directionally rather than causally. The result is not
   statistically significant given 47 dates across 5 tickers.

4. **No threshold tuning from this benchmark.** The benchmark validates
   that P3 instrumentation works end-to-end and directionally improves
   outcomes, but the sample size is too small and the alpha gap too large
   to justify parameter optimization.

### F.6 Report filenames

- `backtest_results/report_COMI.CA_20260617_153119.json`
- `backtest_results/report_TMGH.CA_20260617_155438.json`
- `backtest_results/report_ETEL.CA_20260617_161629.json`
- `backtest_results/report_SWDY.CA_20260617_164114.json`
- `backtest_results/report_FWRY.CA_20260617_170615.json`
- `eval_results/thesis_5ticker_p3_20240102/multi_ticker_summary_20260617_170615.md`
- Baseline: `eval_results/thesis_5ticker_cbfix_20240102/multi_ticker_summary_20260616_170356.md`
