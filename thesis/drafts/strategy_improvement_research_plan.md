# Strategy Improvement Research Plan

> Generated 2026-06-16. Based on the clean post-leakage-fix 5-ticker benchmark
> (eval_results/thesis_5ticker_clean_20240102/) and the P1 circuit breaker fix.
> This document proposes changes grounded in finance theory and EGX market
> mechanics. No changes should be implemented without re-benchmarking after each
> phase.

---

## 1. Benchmark Evidence Summary

### Pre-P1-fix run (diagnostic, 2026-06-16 13:16–14:51)

| Ticker | Return | vs EGX30 | vs B&H | Trades | CB Skips | Pattern |
|--------|--------|----------|--------|--------|----------|---------|
| COMI.CA | -0.03% | -12.18% | -10.83% | 4 (1 closed) | 0 | Traded, lost |
| TMGH.CA | 0.00% | -12.16% | -150.25% | 0 | 3 | All-HOLD + CB |
| ETEL.CA | -4.69% | -16.85% | +10.83% | 4 (2 closed) | 0 | Traded, beat B&H |
| SWDY.CA | 0.00% | -12.16% | -65.32% | 0 | 3 | All-HOLD + CB |
| FWRY.CA | 0.00% | -12.16% | -17.06% | 0 | 4 | All-HOLD + CB |

- **Mean alpha vs EGX30:** -13.10%
- **Tickers beating EGX30:** 0/5
- **Circuit breaker false positives:** 10/50 dates (20%)

### P1-fix run (2026-06-16 15:32–17:03)

Circuit breaker now compares against previous trading-day close (from daily
OHLCV), not the previous evaluation-date price. All 13 regression tests pass.

| Ticker | Return | vs EGX30 | Trades | CB Halts (true) | Decisions (B/S/H) | Pattern |
|--------|--------|----------|--------|:---:|---|---------|
| COMI.CA | +4.98% | -7.17% | 4 (2 closed) | 0 | 2/2/6 | Traded, improved |
| TMGH.CA | 0.00% | -12.16% | 1 (0 closed) | 2 | 1/0/7 | 1 late BUY, still mostly HOLD |
| ETEL.CA | +1.35% | -10.80% | 4 (2 closed) | 1 | 2/2/5 | Traded, improved |
| SWDY.CA | 0.00% | -12.16% | 0 | 0 | 0/0/10 | All-HOLD (no CB now) |
| FWRY.CA | 0.00% | -12.16% | 0 | 0 | 0/0/10 | All-HOLD (no CB now) |

- **Mean alpha vs EGX30:** -10.89% (improved from -13.10%)
- **Mean Sharpe:** +1.65 (improved from -3.39)
- **Mean total return:** +1.27% (improved from -0.94%)
- **Tickers beating EGX30:** 0/5 (unchanged)
- **HOLD rate:** 38/47 = 80.9%
- **Circuit breaker events:** 3 true halts (TMGH ×2, ETEL ×1), 0 false positives

**Interpretation:** P1 fixed mechanics (Sharpe, return) but did not solve the
HOLD behavior. The bottleneck is now the strategy, not the infrastructure.

---

## 2. Root Cause Analysis

### Problem 1: Circuit breaker interval bug (RESOLVED — P1)

The backtester compared current evaluation price against the previous
evaluation date's price. With interval=20, this treated 20-day cumulative
moves as single-day EGX ±10% limit breaches. Result: 10 of 50 evaluation
dates falsely skipped, all during upward moves, across 3 of 5 tickers.

**Fix:** Compare against previous trading-day close from daily OHLCV.
**Status:** Fixed, tested, benchmark complete. False positives eliminated (10 → 0).
3 true halts remain (TMGH ×2, ETEL ×1). See §1 P1-fix run table.

### Problem 2: Earnings-yield-vs-risk-free-rate dominance (OPEN)

Every TMGH.CA and SWDY.CA judge rationale cites the same pattern:

| Date | Ticker | EY | CBE Rate | Spread | Decision |
|------|--------|-----|----------|--------|----------|
| Jan 2 | TMGH | 4.23% | 19.25% | -15.0% | HOLD |
| Mar 24 | TMGH | 1.64% | 27.25% | -25.6% | HOLD |
| Apr 14 | TMGH | 1.57% | 27.25% | -25.7% | HOLD |
| May 5 | TMGH | 2.51% | 27.25% | -24.7% | HOLD |
| May 26 | TMGH | 2.70% | 27.25% | -24.6% | HOLD |
| Jun 16 | TMGH | 2.70% | 27.25% | -24.6% | HOLD |

The research manager LLM sees "earnings yield < risk-free rate" and
consistently concludes cash is superior, despite TMGH rising +177% over
the period due to EGP devaluation and real asset repricing.

**Root cause chain:**
1. `data_cot.py` computes `earnings_yield_spread` = earnings yield − CBE rate
2. This arrives in `fundamental_analysis` JSON consumed by bull/bear researchers
3. Bear researcher cites it as "mathematically dominant" evidence
4. Research manager LLM agrees — with CBE at 27.25%, any stock with P/E > 3.7x
   has negative EY spread, making the bear case structurally overwhelming
5. No competing signal (momentum, NAV, inflation hedge) exists to offset

### Problem 3: No price momentum/trend signal (OPEN)

The research manager and fundamentals pipeline have **zero price context**.
The market analyst provides technical indicators (RSI, MACD, Bollinger
Bands, SMA), but this analysis is consumed as one of four analyst inputs —
it does not override the fundamentals-driven EY-vs-rate comparison.

When TMGH rallied from 23.86 → 66.11 EGP (+177%), the market analyst likely
saw bullish momentum. But the research manager weighted the fundamentals
bear case (negative EY spread) more heavily.

### Problem 4: No sector-conditional valuation (OPEN)

Real estate companies on EGX (TMGH, HELI, PHDC, ORAS, EMFD) are valued
primarily on:
- Net Asset Value (NAV) of land bank and development pipeline
- Replacement cost of completed units
- Pricing power under inflationary conditions
- EGP devaluation upside (USD-denominated international sales)

NOT on earnings yield vs. risk-free rate (the Fed Model).

The system has `sector_config.py` with sector-specific safety floors and
`PB_UNDERSTATED_HISTORICAL_COST` flag for real estate, but these are
informational — they never override the EY spread comparison.

### Problem 5: No regime awareness in thesis prompt (OPEN)

`thesis_cot.py` has no instruction for handling high-inflation/high-rate
regimes where the EY spread is structurally negative for most equities.
In such regimes:
- Nearly all EGX stocks have negative EY spreads
- The signal becomes uninformative (it can't differentiate good from bad)
- The relevant question shifts to "which stocks benefit most from
  inflation/devaluation" — i.e., real asset owners

### Problem 6: Calibration direction collapse (OPEN — diagnostic)

> Added 2026-06-16 based on `deep_strategy_failure_investigation.md` findings.

`calibration.py` (v3_sector_leverage_aware policy) converts ALL "flat" predictions
to "up" (lines 118-128) and requires 5 simultaneous conditions to preserve "down"
(lines 130-191): conf ≥ 75, bearish outlook, high risk, non-bank, D/E ≤ 4.

In practice, this means the fundamentals pipeline can only produce "up" as the
calibrated direction. The directional dimension is destroyed.

**Paradox:** "always up" calibration does not produce BUY decisions. The downstream
LLM sees the calibrated "up" alongside the raw evidence (which shows negative EY
spread), creating a contradictory signal. The LLM resolves the contradiction by
defaulting to HOLD.

**Important framing:** The base-rate-aware policy is well-motivated (LLM "down"
predictions have ~27% precision on EGX historical data). Preserving "flat" in
high-rate non-bank contexts may make the fundamentals signal more honest, but
it may not reduce HOLD by itself. Its purpose is to prevent contradictory reports
(calibrated up ≠ raw evidence bearish). BUY behavior likely requires P2 prompt
changes + P3 momentum/NAV evidence.

**Status:** Diagnostic fix in P2 (conditional — only if tests confirm improvement).
Not a guaranteed alpha fix.

---

## 3. Problem–Evidence–Research–Fix Matrix

| # | Problem | Benchmark Evidence | Research Concept | Proposed Fix | Files | Leakage Risk | Priority |
|---|---------|-------------------|-----------------|-------------|-------|-------------|----------|
| 1 | CB interval bug | 10/50 dates falsely skipped, all upward | EGX daily price limits are per-day, not per-interval | Compare vs prev trading-day close | `scripts/backtester.py` | None | **P1 (DONE)** |
| 2 | EY-vs-rate dominance | TMGH/SWDY/FWRY all-HOLD (38/47=80.9% HOLD); -25% EY spread cited as bear veto | Fed Model is unreliable in EM high-inflation regimes (Estrada 2006, Asness 2003) | Regime-conditional interpretation in thesis/concept prompts + regime flag in evidence pack + regime-context note on EY spread flag | `thesis_cot.py`, `concept_cot.py`, `data_cot.py`, `sector_config.py` | None (prompt/flag changes, date-aware CBE already available) | **P2** |
| 2b | Calibration direction collapse | `calibration.py` converts ALL flat→up and nearly all down→up; pipeline cannot express non-bullish fundamentals | Base-rate-aware calibration is valid but too aggressive in high-rate regimes where flat is honest | Narrow calibration adjustment: allow "flat" to survive in high-rate non-bank contexts (honesty fix, not alpha fix) | `calibration.py` | None | **P2 (conditional — only if tests confirm more honest reports)** |
| 3 | No momentum signal | TMGH +177% rally completely missed despite market analyst seeing bullish indicators | Momentum factor (Jegadeesh & Titman 1993), dual momentum (Antonacci 2014) | Inject 1M/3M/6M price returns into evidence pack | `data_cot.py`, `pipeline.py`, `deterministic_agents.py` | Must use only as-of-date prices | **P3 (DONE)** |
| 4 | No sector-conditional valuation | Real estate stocks all HOLD despite NAV appreciation | Sector-specific valuation (Damodaran: real estate = NAV, banks = P/B + NIM) | Add sector valuation pathway in evidence pack | `data_cot.py`, `sector_config.py` | None | **P3 (partial — sector context present, standalone NAV estimate deferred)** |
| 5 | No regime awareness in allocation | All tickers HOLD in high-rate regime; EY spread uninformative when universally negative | Regime-switching models (Hamilton 1989), macro-conditioned allocation | Add regime classification to macro_provider output; regime-conditional research_manager prompt | `macro_provider.py`, `research_manager.py` | Must use only as-of-date macro data | **P4** |

---

## 4. Proposed Changes — Detailed

### Phase 2: Regime-aware EY interpretation (smallest safe strategy fix)

> **Updated 2026-06-16** based on findings from `deep_strategy_failure_investigation.md`.
> P2 scope expanded from 2 files to 3-4 files. See §4a (P2 Implementation Spec) for
> exact behavior, tests, and success criteria.

| Proposed Change | Exact Code/Prompt Location | Test Required | Expected Effect (hypothesis) |
|----------------|---------------------------|---------------|-----------------|
| Add `inflation_regime` flag to evidence pack: `"high"` when CBE > 15%, `"normal"` when CBE ≤ 15% | `tradingagents/agents/analysts/fundamentals/data_cot.py` — evidence pack construction | Test: flag correctly reflects date-aware CBE rate. No future data. | Downstream stages can condition analysis on macro regime. |
| Add regime-context note on `EARNINGS_YIELD_COMPRESSED` flag when CBE > 15%: "Negative EY spread is structurally common in high-rate EM regimes and does not carry the same signal as in normal-rate environments." | `tradingagents/agents/analysts/fundamentals/sector_config.py` — `EY_SPREAD_FLAGS` or flag generation logic | Test: note present when CBE > 15%, absent when CBE ≤ 15%. Flag itself preserved. | Bear researcher still sees the flag but with context that weakens its "mathematical dominance" argument. |
| Add regime-conditional instruction to thesis prompt: "When CBE rate > 15%, negative EY spread is structurally expected for most EGX equities and should not be treated as a standalone veto. For real_estate and holdings sectors, also assess pricing power, inflation-hedge properties, and balance-sheet composition." | `tradingagents/agents/analysts/fundamentals/thesis_cot.py` — system prompt, after line 80 | Test: verify prompt includes regime clause when rate>15%. No temporal leakage. | LLM no longer treats negative EY spread as the dominant bearish argument in high-rate regimes. |
| Add minor regime note to concept_cot prompt: "When inflation_regime=high, valuation_read should note that EY spread compression is regime-driven, not company-specific." | `tradingagents/agents/analysts/fundamentals/concept_cot.py` — system prompt | Test: note present when regime=high. | Concept synthesis passes regime context to thesis stage. |
| (Conditional) Allow "flat" to survive calibration in high-rate non-bank contexts | `tradingagents/agents/analysts/fundamentals/calibration.py` — flat→up gate (lines 118-128) | Test: flat survives when CBE > 15% AND sector ∉ {banks}. Backward compatibility: all existing tests pass, banks always calibrated to up. | More honest fundamentals reports (removes contradictory "calibrated up but raw bearish" signal). **Not expected to reduce HOLD by itself.** |

**Leakage risk:** None. CBE rate is already date-aware (macro_provider fix from prior session). Sector classification is static metadata. `inflation_regime` is derived from the same date-aware CBE rate.

**Scope:** Prompt text + informational flags + narrow calibration gate. No new data sources, no new models, no new infrastructure. No changes to research_manager, risk_manager, market_analyst, or pipeline orchestration.

**Key constraint:** The calibration adjustment (last row) is conditional — implement only if unit tests confirm it produces more honest (less self-contradictory) fundamentals reports. Preserving "flat" may make reports more truthful but may not reduce HOLD rate, since "flat" fundamentals still lacks conviction to trigger BUY. BUY behavior likely requires P3 momentum/NAV evidence.

### Phase 2 Implementation Spec (§4a)

> This section is the definitive P2 spec. It supersedes any P2 description elsewhere
> in this document or in `deep_strategy_failure_investigation.md`.

#### Exact files and changes

| # | File | Change | Type |
|---|------|--------|------|
| 2.1 | `tradingagents/agents/analysts/fundamentals/data_cot.py` | Add `inflation_regime` field to evidence pack dict. Value: `"high"` when date-aware CBE rate > 15%, `"normal"` otherwise. Insert alongside existing macro fields. | Code (1 conditional + 1 dict assignment) |
| 2.2 | `tradingagents/agents/analysts/fundamentals/sector_config.py` | Add `EARNINGS_YIELD_HIGH_RATE_NOTE` to `INFORMATIONAL_FLAGS`. In the flag-generation logic that checks `EY_SPREAD_FLAGS`, when CBE > 15% AND `EARNINGS_YIELD_COMPRESSED` fires, also append the informational note. Preserve the original flag. | Code (add flag constant + 1 conditional in flag generator) |
| 2.3 | `tradingagents/agents/analysts/fundamentals/thesis_cot.py` | Append regime-conditional paragraph to `_SYSTEM_PROMPT` after line 80 (earnings direction guidance). Content: "When the evidence pack indicates inflation_regime='high' (CBE > 15%), negative EY spread is structurally expected for most EGX equities and should not be treated as a standalone veto of equity exposure. For real_estate and holdings sectors, also assess pricing power under inflation, replacement cost dynamics, and balance-sheet composition as competing valuation signals." | Prompt text only |
| 2.4 | `tradingagents/agents/analysts/fundamentals/concept_cot.py` | Add 1-2 sentence regime note to concept_cot system prompt: when interpreting valuation_read, note that EY spread compression in high-rate regimes is regime-driven and not company-specific. | Prompt text only |
| 2.5 | `tradingagents/agents/analysts/fundamentals/calibration.py` | **(Conditional)** In the flat→up gate (lines 118-128), add a condition: if CBE > 15% AND sector not in `_SECTORS_ALWAYS_UP`, preserve "flat" as the calibrated direction instead of converting to "up". Requires passing `cbe_rate` and `sector` to the calibration function (or adding a regime flag parameter). | Code (1 conditional branch) |

#### Exact behavior specification

**2.1 — inflation_regime flag:**
```python
# In data_cot.py, evidence pack construction:
cbe_rate = <existing date-aware CBE rate from macro_provider>
evidence_pack["inflation_regime"] = "high" if cbe_rate > 0.15 else "normal"
```
- The CBE rate is already fetched date-aware by the macro_provider (patched 2026-06-15).
- The 15% threshold is a config constant (not hardcoded in the conditional).
- The flag is informational — it does not change any computation.

**2.2 — EY spread regime note:**
- When `EARNINGS_YIELD_COMPRESSED` flag fires AND CBE > 15%, append to flags list:
  `"EARNINGS_YIELD_HIGH_RATE_CONTEXT: Negative EY spread is structurally common when CBE > 15%. In high-rate EM regimes, this flag indicates regime-level compression, not company-specific overvaluation."`
- The original `EARNINGS_YIELD_COMPRESSED` flag is preserved unchanged.
- When CBE ≤ 15%, no additional note is added (backward compatible).

**2.3 — thesis_cot prompt:**
- The regime paragraph is always present in the prompt (static text).
- It references the `inflation_regime` field that may or may not be `"high"`.
- The LLM reads the field value and applies the guidance conditionally.
- No code branching in prompt construction — the text is unconditional.

**2.4 — concept_cot prompt:**
- Same approach: static text referencing `inflation_regime` field.

**2.5 — calibration (conditional):**
- Gate change: `if raw == "flat" AND cbe_rate > 0.15 AND sector not in _SECTORS_ALWAYS_UP → keep "flat"`
- All other calibration logic unchanged.
- Banks sector: always calibrated to "up" regardless of rate.
- Normal-rate regimes (CBE ≤ 15%): flat→up conversion preserved.
- Down→up gates: unchanged (relaxing "down" is out of P2 scope).

#### Tests required

| # | Test | What it verifies | Leakage check |
|---|------|-----------------|---------------|
| T1 | `test_inflation_regime_flag_high` | `inflation_regime = "high"` when CBE = 27.25% | Verify CBE rate is date-aware, not hardcoded |
| T2 | `test_inflation_regime_flag_normal` | `inflation_regime = "normal"` when CBE = 10.0% | — |
| T3 | `test_inflation_regime_flag_boundary` | `inflation_regime = "normal"` when CBE = 15.0% (boundary: ≤ 15% = normal) | — |
| T4 | `test_ey_spread_high_rate_note_present` | When CBE > 15% and `EARNINGS_YIELD_COMPRESSED` fires, the high-rate context note is also in the flags list | — |
| T5 | `test_ey_spread_normal_rate_no_note` | When CBE ≤ 15% and `EARNINGS_YIELD_COMPRESSED` fires, no high-rate context note is added | — |
| T6 | `test_thesis_prompt_contains_regime_guidance` | The thesis_cot system prompt contains the regime-conditional paragraph | — |
| T7 | `test_calibration_flat_survives_high_rate` | (If 2.5 implemented) `calibrated_direction = "flat"` when raw=flat, CBE>15%, sector=real_estate | — |
| T8 | `test_calibration_flat_converted_normal_rate` | flat→up conversion still works when CBE ≤ 15% (backward compat) | — |
| T9 | `test_calibration_banks_always_up` | Banks sector: calibrated to "up" regardless of CBE rate | — |
| T10 | `test_no_temporal_leakage_in_regime_flag` | `inflation_regime` uses the same date-aware CBE as existing macro_provider; no future CBE data | Explicit temporal check |

**Regression gate:** All existing tests in `tests/` must pass, especially:
- `tests/test_fundamentals_phase1a.py`
- `tests/test_circuit_breaker.py`
- `tests/test_execution_costs.py`

#### Leakage safety checks

1. `inflation_regime` is derived from the date-aware CBE rate already in the macro_provider. The macro_provider was patched (2026-06-15) to use only as-of-date rates. No new temporal surface.
2. Sector classification is static metadata (`sector_config.SECTOR_MAP`). No temporal dimension.
3. `EARNINGS_YIELD_HIGH_RATE_CONTEXT` note is derived from the same date-aware CBE rate. No new data source.
4. Prompt text changes are static (unconditional text in the prompt template). No temporal risk.
5. Calibration change (if implemented) uses the same CBE rate. No new temporal surface.

#### Success criteria (stated as hypothesis)

**Hypothesis:** Regime-aware EY interpretation will reduce the HOLD rate from the
current 80.9% (38/47) because the bear researcher's "EY < CBE rate = cash is
mathematically superior" argument will be weakened by the regime context note, and
the thesis/concept prompts will produce less EY-dominated reasoning.

**Measurable test (post-benchmark):**
- HOLD rate drops below 70% (at least 5 fewer HOLDs out of 47).
- At least 2 of {TMGH, SWDY, FWRY} produce at least 1 non-HOLD decision.
- No increase in false-positive risk violations (risk manager behavior unchanged).
- No temporal leakage in any new field.

**What this will NOT prove:** P2 is unlikely to produce positive alpha vs EGX30.
The regime fix removes an interpretation failure but does not add the positive
signals (momentum, relative strength, NAV proxy) needed for the bull researcher
to make conviction-level cases. Positive alpha likely requires P3 at minimum.

**Failure modes:**
- If the LLM ignores the regime guidance and continues citing EY spread as dominant → prompt needs strengthening or the signal is too deeply embedded in training data.
- If HOLD rate drops but alpha worsens (more losing trades) → the EY-spread-based caution was partially justified, and the fix needs sector/timing refinement.
- If calibration "flat" preservation increases HOLD rate instead of reducing it → revert the calibration change, keep only the prompt/flag changes.

#### Expected effect on per-ticker behavior

| Ticker | Current (P1-fix) | P2 Hypothesis | Reasoning |
|--------|-------------------|---------------|-----------|
| COMI.CA (banks) | 2 BUY, 2 SELL, 6 HOLD | Minimal change — banks already get special calibration treatment, and bank EY spreads are less extreme | COMI already trades; P2 primarily targets non-bank sectors |
| TMGH.CA (real_estate) | 1 BUY, 0 SELL, 7 HOLD | May produce 1-3 additional BUY decisions when EY spread is contextualized | TMGH has strong revenue/income growth that the thesis_cot should surface once EY veto is weakened |
| ETEL.CA (operational) | 2 BUY, 2 SELL, 5 HOLD | Modest change — operational sector gets regime note but not the sector-specific NAV guidance | ETEL already trades; may get 1-2 fewer HOLDs |
| SWDY.CA (holdings) | 0 BUY, 0 SELL, 10 HOLD | May produce 1-2 non-HOLD decisions | Holdings sector gets regime guidance, but SWDY lacks the strong growth narrative that TMGH has |
| FWRY.CA (operational) | 0 BUY, 0 SELL, 10 HOLD | Uncertain — operational sector benefits less from P2 than real_estate/holdings | FWRY may need P3 momentum signals to break out of all-HOLD |

---

### Phase 3: Momentum signal + sector valuation pathway

| Proposed Change | Exact Code/Prompt Location | Test Required | Expected Effect |
|----------------|---------------------------|---------------|-----------------|
| Compute and inject 1M/3M/6M price returns (vs self and vs EGX30) into evidence pack | `tradingagents/agents/analysts/fundamentals/data_cot.py` — add `price_momentum` section | Test: returns computed from OHLCV ending at trade_date only. No future prices. Verify with `test_ohlcv_temporal_safety.py` pattern. | Bull researcher has concrete momentum evidence to counter negative EY spread. Market analyst's bullish signals get reinforced in the evidence pack. |
| Add `nav_proxy` field for real estate tickers: P/B ratio + PB_UNDERSTATED flag + land bank estimate (if available) | `tradingagents/agents/analysts/fundamentals/data_cot.py` — add `nav_valuation` section for sector=real_estate | Test: only uses financial data available as of trade_date (120d filing lag filter already in place). | Provides a competing valuation signal to EY spread for real estate stocks. |
| Add relative strength vs EGX30 to market analyst output | `tradingagents/agents/analysts/market_analyst.py` — add RS calculation | Test: uses only OHLCV data up to trade_date. | Research manager sees whether stock is outperforming the market. |

**Leakage risk:** Medium — momentum computation must be strictly bounded by trade_date. Requires same temporal safety pattern as the OHLCV fix (use fetched daily data ending at trade_date, belt-and-suspenders filter).

### Phase 4: Regime-aware allocation and starter-position rule

| Proposed Change | Exact Code/Prompt Location | Test Required | Expected Effect |
|----------------|---------------------------|---------------|-----------------|
| Add `macro_regime` classification to `macro_provider.py` output: { "rate_regime": "high_inflation" / "normal" / "easing", "fx_regime": "depreciating" / "stable" / "appreciating" } | `tradingagents/dataflows/macro_provider.py` — `get_egx_macro_context()` | Test: regime derived only from as-of-date data. No forward-looking. | Research manager can condition thesis quality assessment on the macro regime. |
| Add a regime-conditional instruction to research manager prompt: "In high_inflation + depreciating FX regimes, real asset owners (real estate, industrials with pricing power) are inflation hedges — weight NAV and pricing-power evidence more heavily than EY spread." | `tradingagents/agents/managers/research_manager.py` — system prompt | Test: verify prompt injection is conditional on regime flag. | Research manager explicitly instructed to override EY-spread dominance in high-inflation regimes. |
| Consider starter-position rule: when (a) momentum positive, (b) EGX30 trend positive, (c) bull thesis identifies asset-revaluation catalyst, (d) no critical risk violations → allow minimum 2% position even if debate is "balanced" | `tradingagents/agents/trader/trader.py` or `risk_manager.py` — position sizing | Test: rule only activates under specific conditions. Does not bypass risk limits. | Prevents the all-cash outcome when conditions are favorable but thesis is ambiguous. |

**Leakage risk:** Low for regime classification (same date-aware CBE + FX data). Medium for starter-position rule (must not look ahead for "momentum positive" calculation).

---

## 5. Benchmark Status and Validity Issues

### P3 benchmark status (2026-06-17) — LATEST

The P3 benchmark (`eval_results/thesis_5ticker_p3_20240102/`) adds momentum evidence
(20d/60d/120d returns, RS vs EGX30, volume confirmation) to the decision pipeline.
P3 instrumentation coverage: 47/47 dates (100%).

- Mean alpha vs EGX30: **-7.46%** (1/5 tickers beat EGX30)
- Mean total return: +4.70% (vs EGX30 ~+12.16%)
- HOLD rate: **78.7%** (37/47 evaluated dates)
- Mean Sharpe: +1.97
- BUY count: 7 (up from 5 in P1/P2 baseline)
- SWDY.CA: **+17.09% return, +4.94% alpha** — the success case

**Interpretation:** P3 improved signal visibility and reduced missed-momentum cases.
P3 did not solve the core benchmark underperformance. The remaining issue is not
absence of momentum evidence; it is decision conservatism / capital deployment /
entry threshold behavior. Some decision differences may reflect LLM/path variability
across runs, so attribution to P3 should be interpreted directionally rather than
causally. No threshold tuning recommended from this benchmark.

### P1-fix benchmark status (2026-06-16) — superseded by P3

The P1-fix benchmark (`eval_results/thesis_5ticker_cbfix_20240102/`) is **mechanically
valid** — circuit breaker halts are now correctly based on daily OHLCV, not interval
prices. However, it is **not positive-alpha thesis evidence**:

- Mean alpha vs EGX30: **-10.89%** (all 5 tickers underperform)
- 0/5 tickers beat EGX30
- Mean total return: +1.27% (vs EGX30 ~+12.16%)
- HOLD rate: **80.9%** (38/47 evaluated dates)
- P1 improved mechanics (eliminated 10 false CB halts, improved Sharpe from -3.39
  to +1.65) but did not solve the HOLD behavior

**Interpretation:** The backtester infrastructure is now trustworthy. The bottleneck
is the agent's decision-making strategy, not the benchmark mechanics.

### Known validity issues

| Issue | Evidence | Impact on Alpha | Required Fix |
|-------|----------|----------------|-------------|
| Circuit breaker interval bug | 10/50 dates skipped, all upward | ~10-20% alpha lost (TMGH/SWDY/FWRY rally dates blocked) | **P1 (DONE)** |
| EGX30 benchmark coverage ~70% | yfinance `^CASE30` returns "possibly delisted" | Alpha calculation unreliable (7/10 dates matched) | Use local EGX30 CSV exclusively; improve date matching |
| Low trade count (4 closed trades in 47 evaluated dates) | Agent too defensive (80.9% HOLD) | Win rate CI too wide to be statistically meaningful | P2 should increase decision variance; P3 should increase trade frequency |
| Single backtest window (H1 2024) | Only one 6-month period tested | Results may not generalize | Add H2 2023 and H2 2024 windows after P3 |
| Calibration direction collapse | calibration.py converts all flat/most down → up | Fundamentals signal has no directional variance | P2 diagnostic fix (conditional) |

---

## 6. Phased Roadmap

### Phase 1: Benchmark mechanics (COMPLETE)
- [x] Fix circuit breaker daily-close logic
- [x] 13 regression tests
- [x] Rerun benchmark — `eval_results/thesis_5ticker_cbfix_20240102/`
- [x] Verify: false CB halts eliminated (10 → 0), 3 true halts remain
- **Result:** Mechanics fixed. Mean Sharpe improved -3.39 → +1.65. But 0/5 beat EGX30, 80.9% HOLD.

### Phase 2: Regime-aware EY interpretation
- [ ] Add `inflation_regime` flag to evidence pack (`data_cot.py`)
- [ ] Add `EARNINGS_YIELD_HIGH_RATE_CONTEXT` note to `sector_config.py`
- [ ] Add regime-conditional prompt guidance to `thesis_cot.py` and `concept_cot.py`
- [ ] (Conditional) Adjust calibration flat→up gate for high-rate non-bank contexts (`calibration.py`)
- [ ] Tests T1-T10 (see §4a Implementation Spec)
- [ ] Regression gate: all existing tests pass
- **Benchmark after:** thesis_5ticker_p2regime_20240102
- **Hypothesis (not a promise):** HOLD rate drops below 70%; at least 2 of {TMGH, SWDY, FWRY} produce at least 1 non-HOLD decision. Positive alpha is not expected from P2 alone.

### Phase 3: Momentum + sector valuation (COMPLETE — 2026-06-17)
- [x] Inject 20d/60d/120d price returns into evidence pack
- [x] Add relative strength vs EGX30 to market analyst (deterministic + ablation paths)
- [x] Add volume confirmation gate
- [x] Add momentum labels (strong_up/moderate_up/neutral/moderate_down/strong_down)
- [x] Temporal safety tests for all new data
- [x] P3 audit instrumentation (p3_momentum_debug + narrative flag)
- [x] 5-ticker controlled benchmark — `eval_results/thesis_5ticker_p3_20240102/`
- **Result:** Mean return improved 1.27% → 4.70%, mean alpha improved -10.89% → -7.46%, 1/5 tickers beat EGX30 (SWDY: +4.94% alpha). HOLD rate dropped marginally 80.9% → 78.7%. P3 instrumentation coverage: 47/47 dates (100%).
- **Interpretation:** P3 improved signal visibility and reduced missed-momentum cases (especially SWDY). P3 did not solve the core benchmark underperformance. The remaining issue is not absence of momentum evidence; it is decision conservatism / capital deployment / entry threshold behavior. Attribution should be interpreted directionally rather than causally given LLM/path variability. No threshold tuning recommended from this benchmark.
- **NAV-proxy valuation:** Not yet implemented as a standalone evidence pack field. Sector-specific context (P/B understated, inflation note) is present via sector_config but not a computed NAV estimate. Deferred to future work.

### Phase 4: Regime-aware allocation
- [ ] Add macro_regime classification
- [ ] Regime-conditional research manager prompt
- [ ] Starter-position rule (conditional)
- **Benchmark after:** thesis_5ticker_p4regime_20240102
- **Expected effect:** System participates in high-inflation rallies instead of defaulting to cash

### Multi-window validation (after Phase 3 or 4)
- [ ] Add H2 2023 backtest window
- [ ] Add H2 2024 backtest window
- [ ] Cross-validate: does the improvement persist across regimes?

---

## 7. What NOT to Do

1. **Do not overfit to H1 2024.** The goal is a generalizable system, not one that perfectly trades the EGP devaluation rally.
2. **Do not add momentum as a hard signal.** It should be evidence for the LLM to weigh, not a deterministic override.
3. **Do not bypass or weaken the risk manager.** The EGX constraints (long-only, no leverage, ±10% daily limit, max 10% ADV, T+2 settlement) are hard regulatory rules. Do not relax them to improve alpha.
4. **Do not use future data.** All price computations must end at trade_date. No future returns in prompts. No look-ahead of any kind.
5. **Do not change all prompts at once.** One phase at a time, benchmark after each.
6. **Do not claim alpha until we honestly beat EGX30** in a clean, temporal-safe benchmark.
7. **Do not hardcode BUY for specific tickers.** No special-case logic for TMGH/SWDY/FWRY. Fixes must be general (regime-aware interpretation), not ticker-specific overrides.
8. **Do not use live news/social data in historical backtest mode.** The backtest honesty gate is correct — historical social data is not available retroactively and must not be fabricated.
9. **Do not run a full benchmark rerun until P2 is implemented and unit-tested.** The P1-fix benchmark is mechanically valid. Another run without strategy changes will produce the same 80.9% HOLD rate and waste LLM API budget.
10. **Do not remove calibration.py entirely.** The base-rate-aware policy is well-motivated (LLM "down" calls have ~27% precision on EGX). Only make narrow, tested adjustments.
11. **Do not add P3/P4 scope into P2.** No momentum, NAV proxy, starter-position rules, or research_manager prompt changes in P2. One phase at a time.

---

## 8. Key Source Files

| File | Role | Relevant to |
|------|------|-------------|
| `scripts/backtester.py` | Backtest engine, circuit breaker | P1 (done) |
| `tradingagents/agents/analysts/fundamentals/thesis_cot.py` | Deep thesis prompt | P2 (regime instruction) |
| `tradingagents/agents/analysts/fundamentals/data_cot.py` | Evidence pack construction | P2 (inflation_regime flag), P3 (momentum, NAV) |
| `tradingagents/agents/analysts/fundamentals/concept_cot.py` | Concept synthesis prompt | P2 (regime note) |
| `tradingagents/agents/analysts/fundamentals/sector_config.py` | Sector thresholds, EY flags | P2 (EY_HIGH_RATE_CONTEXT note), P3 (NAV pathway) |
| `tradingagents/agents/analysts/fundamentals/calibration.py` | Deterministic signal calibration | P2 (conditional: flat→up gate adjustment in high-rate contexts) |
| `tradingagents/agents/analysts/fundamentals/sector_config.py` | Sector thresholds | P3 (NAV pathway) |
| `tradingagents/agents/analysts/fundamentals/pipeline.py` | 3-stage orchestrator | P3 (calibration adjustment) |
| `tradingagents/agents/analysts/market_analyst.py` | Technical analysis | P3 (relative strength) |
| `tradingagents/agents/managers/research_manager.py` | BUY/HOLD/SELL judge | P4 (regime-conditional prompt) |
| `tradingagents/agents/trader/trader.py` | Position sizing | P4 (starter-position rule) |
| `tradingagents/dataflows/macro_provider.py` | Macro context | P4 (regime classification) |
| `tradingagents/agents/utils/scoring.py` | Confidence aggregation | Disconnected from live graph — not a priority |
| `agent_docs/RATIO_SOURCES.md` | Documents EY spread as experimental | Reference for P2 justification |

---

## 9. Finance Theory References

| Concept | Reference | Relevance |
|---------|-----------|-----------|
| Fed Model unreliability | Estrada (2006), Asness (2003) | EY-vs-rate comparison is known to be unreliable, especially in non-US markets |
| Momentum factor | Jegadeesh & Titman (1993) | 3-12 month momentum is a robust cross-sectional factor |
| Dual momentum | Antonacci (2014) | Absolute + relative momentum for asset allocation |
| EM equity risk premium | Damodaran (annual) | EM equities require different valuation framework than DM |
| Regime-switching | Hamilton (1989) | Macro regimes (inflation, rate cycles) change optimal allocation |
| Real estate valuation | Damodaran: sector-specific | NAV, replacement cost, cap rate — not P/E or EY spread |
| Inflation hedging | Fama & Schwert (1977), Bekaert & Wang (2010) | Real assets (real estate, commodities) hedge inflation |
| EGX-specific dynamics | CBE reports, FRA regulations | ±10% daily limits, T+2 settlement, long-only constraint |
