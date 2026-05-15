# Macro Subsystem — How It Actually Works

> This document describes the **current implementation** of the three macro-layer analysts (Macro, Liquidity, Regime). For the aspirational data-engineering roadmap, see `macro_data_architecture_egx.md`.

---

## Overview

The macro subsystem consists of three analyst nodes added in Phase 2. They run in parallel with the original four analysts (Market, Fundamentals, News, Social) during the LangGraph fan-out step.

| Analyst | File | LLM Calls | Directional? |
|---|---|---|---|
| Macro / FX / Rates | `tradingagents/agents/analysts/macro_analyst.py` | 0–1 (optional narrative) | No — modifies confidence only |
| Liquidity / Flow | `tradingagents/agents/analysts/liquidity_analyst.py` | 0 (fully deterministic) | No — modifies position size only |
| Regime Detection | `tradingagents/agents/analysts/regime_analyst.py` | 0 (fully deterministic) | No — modifies strategy bias only |

All three are **non-directional**: they do NOT vote in the BUY/SELL quorum. They influence the final decision through the sentiment blend cascade (confidence dampening, position-size caps, strategy framing).

They are registered in `tradingagents/graph/setup.py:147–170` and activated when `"macro"`, `"liquidity"`, or `"regime"` appear in the selected analysts list.

---

## 1. Macro Analyst (`macro_analyst.py`)

**Type:** Hybrid (deterministic data + optional LLM narrative)

### What it does

Collects Egyptian macroeconomic data, computes deterministic signals, and optionally asks the LLM to write a 3–4 sentence interpretation.

### Data collection — 3-layer priority chain

```
Layer 1: Local CSVs (fast, no network)
    egx_macro/egypt_macro.csv     → CBE policy rate, CPI YoY, rate changes
    egx_macro/tbill_yields.csv    → 91-day T-bill monthly average yield
    egx_macro/fx_premium.csv      → Parallel FX premium (official vs parallel rate)

Layer 2: FRED API (DISABLED — no suitable Egypt series found)
    Returns empty dict. Kept as infrastructure for future use.

Layer 3: yfinance (live, needs internet)
    ^VIX        → Global risk appetite proxy
    EGPUSD=X    → Official EGP/USD exchange rate + 30-day change
```

Each layer only fills fields that previous layers left empty. CSV always wins over yfinance for the same field.

### Point-in-time discipline

Every CSV has an `available_at` column. The analyst filters rows where `available_at <= trade_date`, so backtests never see data that wasn't published yet.

**Current limitation:** `available_at` is set equal to `date` in most rows (schema support only). CPI publication lag (~10 days) is acknowledged but not yet enforced at the row level. The T-bill CSV is the exception — it uses the PDF creation date of the MoF bulletin as `available_at`.

### Deterministic signals computed

| Signal | Formula | Thresholds |
|---|---|---|
| `real_yield_91d` | T-bill yield − CPI YoY | > +5% → RISK_OFF, < −2% → RISK_ON |
| `rate_shock` | CBE change ≥ 200bps AND within 14 calendar days | Boolean |
| `vix_regime` | VIX level | ≥ 35 → PANIC, ≥ 25 → ELEVATED, else CALM |
| `fx_premium_signal` | Parallel premium % | > 25% → CRITICAL, > 10% → ELEVATED (verified data only) |
| `composite_direction` | Vote count across signals | ≥ 2 RISK_OFF → RISK_OFF, ≥ 2 RISK_ON → RISK_ON (unless rate_shock clamps to NEUTRAL) |

**Rate-shock guard rule:** Even if negative real yield + calm VIX produce 2 RISK_ON votes, a concurrent rate shock clamps the composite to NEUTRAL. This was motivated by the March 2024 +600bps CBE hike.

### Optional LLM call (line 557)

If `quick_llm` is provided, the analyst sends the structured signals to the LLM with this prompt context:
- Egypt post-2016 "inflation-hedge" regime (rising CPI is positively correlated with equity returns)
- Asks for rate trajectory, real yields, FX stability, VIX interpretation

If the LLM call fails, the analyst falls back to the deterministic report only.

### Output

- `macro_report`: Human-readable text summary
- `macro_analysis`: Structured dict with all signals + `data_sources` provenance map
- `macro_messages`: Always empty (no tool calls)

---

## 2. Liquidity Analyst (`liquidity_analyst.py`)

**Type:** Fully deterministic (zero LLM calls)

### What it does

Computes microstructure and liquidity metrics from the stock's own OHLCV data (120 calendar days lookback).

### Data source

Calls `get_eodhd_stock_data()` for the specific ticker. This goes through the DataGateway (cache → yfinance → EODHD fallback).

### Metrics computed

| Metric | Method | Reference |
|---|---|---|
| Amihud ILLIQ (21d) | mean(\|daily return\| / volume) | Amihud (2002) |
| Zero-return frequency (21d) | % of days with 0% price change | BHL (2007), Sussex (2023) — outperforms Amihud on EGX |
| Average Daily Volume (21d) | Mean shares traded | — |
| ADV trend (5d / 21d) | Short-term vs long-term volume ratio | — |
| Volume concentration | % of total volume in top 20% of days | — |
| High-low spread proxy (21d) | Bid-ask estimate from daily range | Corwin & Schultz (2012) |

### Liquidity classification

```
SEVERELY_ILLIQUID: ADV < 10K shares/day
ILLIQUID:          ADV < 50K or zero-return > 30%
MODERATE_CONCERN:  zero-return > 15% or high Amihud
ADEQUATE:          everything below thresholds
```

### FPI foreign flow integration

Reads `egx_macro/foreign_flow.csv` for institutional flow data. **Currently stub data only** (`data_quality: "stub"`). When verified data is available and shows `STRONG_OUTFLOW`, the liquidity classification escalates one level (e.g., ADEQUATE → MODERATE_CONCERN).

Stub/demo data is reported for visibility but does NOT affect the classification.

### Position sizing output

- `max_shares_at_10pct_adv`: Hard cap = ADV(21d) x 10%
- `days_to_exit_1m_egp`: How many days to liquidate a 1M EGP position at 10% ADV participation

### Output

- `liquidity_report`: Human-readable text
- `liquidity_analysis`: Structured dict with all metrics
- `liquidity_messages`: Always empty

---

## 3. Regime Analyst (`regime_analyst.py`)

**Type:** Fully deterministic (zero LLM calls)

### What it does

Classifies the broad EGX market regime from EGX30 index data (not the individual stock).

### Data source

Tries EGX30 index tickers in order: `EGX30.CA` → `^EGX30` → `CASE30.CA`. Needs ~400 calendar days of data for the 52-week high calculation. Falls back to the individual stock's data if no index data is available.

### Regime classification

| Regime | Condition | Strategy Bias |
|---|---|---|
| CRASH | Drawdown > 20% from 52W high, OR 1M return < −15% | Defensive |
| BEAR | Drawdown > 10% from 52W high, OR 3M return < −10% | Cautious |
| NORMAL | No significant drawdown, moderate volatility | Balanced |
| BULL | 3M return > +10% AND within 10% of 52W high | Opportunistic |

### Additional signals

| Signal | What it captures | Academic basis |
|---|---|---|
| `regime_age_days` | How long the current regime has lasted | Giner & Zakamulin (2023) — duration dependence |
| `vol_persistence_flag` | Was 63d annualized vol high both now AND 90 days ago? | Ezzat (2013) — "Joseph Effect" on EGX |
| `duration_warning` | Warning if BULL regime > 120 trading days | Positive duration dependence → higher reversal probability |

### Regime → sentiment mapping

The regime maps to the `MarketRegime` enum used by the sentiment blend cascade:

```
CRASH  → PANIC
BEAR   → FEAR
NORMAL → NEUTRAL
BULL   → GREED
```

### Output

- `regime_report`: Human-readable text
- `regime_analysis`: Structured dict with regime + all metrics
- `regime_messages`: Always empty

---

## CSV Data Files

All stored in `tradingagents/dataflows/data_cache/egx_macro/`:

| File | Rows | Coverage | Quality |
|---|---|---|---|
| `egypt_macro.csv` | ~15 rows | Feb 2023 – Apr 2025 (MPC meeting dates) | `verified_policy` — sourced from CBE MPC press releases |
| `tbill_yields.csv` | ~30 rows | Jun 2022 – Feb 2025 (monthly) | `verified_pit_monthly` — MoF Financial Monthly Table 29, cross-verified across 3 bulletins |
| `cbe_policy_rates.csv` | ~20 rows | Jan 2019 – present | Mix of `verified` and `provisional` — full CBE rate history |
| `fx_premium.csv` | ~4 rows | Jan 2024 – Jan 2025 | `stub` — placeholder data, NOT used for decision-making |
| `foreign_flow.csv` | ~5 rows | May 2025 | `stub` — demo data, NOT used for classification |

**Data quality rules:** Only rows with `data_quality` of `"real"` or `"verified"` can trigger decision-impacting signals (FX premium escalation, FPI flow escalation). Stub data passes through as informational only.

---

## How They Integrate Into the Graph

```
TradingAgentsGraph.propagate()
        |
        v
   Analyst fan-out (parallel)
   ┌─────────────┬──────────────┬────────────────┬────────────────┐
   │  Market     │ Fundamentals │  News          │  Social        │  ← Original 4
   ├─────────────┼──────────────┼────────────────┼────────────────┤
   │  Macro      │  Liquidity   │  Regime        │                │  ← Phase 2
   └─────────────┴──────────────┴────────────────┴────────────────┘
        |
        v
   Reports → Bull/Bear Researchers (use macro/regime context in thesis)
        |
        v
   Trader Agent (uses liquidity constraints for position sizing)
        |
        v
   Risk Manager (uses regime + macro for veto/approval context)
```

The macro subsystem nodes have no tool nodes — they fetch data inline and return directly. This is configured in `setup.py:156,164,171` with no-op lambda tool nodes.

---

## What's NOT Implemented Yet

- Live CBE/CAPMAS scraping (described in `macro_data_architecture_egx.md`)
- Bi-temporal database with proper publication-date tracking
- MoF PDF parsing pipeline
- CIB GDR implied exchange rate calculation
- Live parallel FX rate scraping (sarf-today, parallelrate.org)
- Verified FPI foreign flow data (current data is stub)
- FRED integration (disabled — no suitable monthly Egypt series)
