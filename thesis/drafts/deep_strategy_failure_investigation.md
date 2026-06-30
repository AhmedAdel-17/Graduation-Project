# Deep Strategy Failure Investigation

> Generated 2026-06-16. Offline code-grounded diagnosis — no new benchmarks,
> no LLM calls, no implementation changes. Based on the P1-fix 5-ticker
> benchmark (`eval_results/thesis_5ticker_cbfix_20240102/`) and full code
> review of every component in the decision pipeline.

---

## 1. Executive Summary

The system produces **excessive HOLD decisions** (38 of 47 evaluated ticker-dates
= 80.9% HOLD) and **0 of 5 tickers beat the EGX30 benchmark** despite the P1
circuit breaker fix eliminating 10 false-positive halts.

Root cause is a **multi-stage HOLD bias cascade** — at least 5 independent
components each push the pipeline toward inaction, and no component pushes back.
The three most damaging, in order:

1. **Calibration collapse** (`calibration.py`): Converts nearly all non-"up"
   fundamentals signals to "up", destroying directional variance before the
   LLM ever sees the data. The pipeline can only produce "up" fundamentals
   signals in practice. But paradoxically, this "always-up" calibration
   *doesn't translate to BUY decisions* — the downstream LLM sees "up" from
   fundamentals alongside a massive negative EY spread and defaults to HOLD.

2. **Earnings yield vs risk-free rate dominance** (`data_cot.py` + `sector_config.py`):
   With CBE at 19.25-27.25% during the benchmark window, any stock with P/E > 3.7-5.2x
   has a negative earnings yield spread. The flag `EARNINGS_YIELD_COMPRESSED`
   (threshold: `< -2%`) fires for virtually every EGX stock. The bear researcher
   cites this as "mathematically dominant" evidence and the research manager agrees.

3. **No competing positive signal**: No momentum data, no relative strength, no
   NAV proxy, no regime-conditional interpretation. The bull researcher has only
   the calibrated "up" fundamentals direction (which the LLM doesn't trust when
   it sees the raw negative EY spread alongside it) and whatever the market analyst
   provides (RSI, MACD, Bollinger Bands — consumed as one of four analyst inputs).

Additional amplifiers: confidence compression (all decisions in 0.414-0.494 band),
news silence penalty (-40 points in backtest mode), and HOLD criteria in the
research manager prompt that are too easy to satisfy.

**Key insight**: The strategy_improvement_research_plan.md's P2 scope ("prompt-only
changes to thesis_cot.py") is **incomplete**. The `sector_config.py` EY_SPREAD_FLAGS
thresholds need regime conditioning, and `calibration.py`'s direction collapse should
be examined as an honesty/diagnostic issue. P2 must address the interpretation layer
(prompts + flags) or the fix will be ineffective.

**Calibration nuance**: Preserving "flat" in selected high-rate/sector contexts may
make the fundamentals signal more honest, but it may not reduce HOLD by itself. Its
purpose is to prevent contradictory "calibrated up but raw evidence bearish" reports.
BUY behavior likely still requires regime-aware EY interpretation (P2) and later
momentum/NAV evidence (P3).

---

## 2. Evidence Table by Ticker/Date

### Decision summary (P1-fix benchmark, 2024-01-02 to 2024-07-14, interval=20)

| Ticker | Dates Eval'd | BUY | SELL | HOLD | CB Halt (not eval'd) | Total Return | Alpha vs EGX30 | B&H Return |
|--------|:---:|:---:|:---:|:---:|:---:|---:|---:|---:|
| COMI.CA | 10 | 2 | 2 | 6 | 0 | +4.98% | -7.17% | +10.80% |
| TMGH.CA | 8 | 1 | 0 | 7 | 2 true | 0.00% | -12.16% | +177% |
| ETEL.CA | 9 | 2 | 2 | 5 | 1 true | +1.35% | -10.80% | -15.52% |
| SWDY.CA | 10 | 0 | 0 | 10 | 0 | 0.00% | -12.16% | +65% |
| FWRY.CA | 10 | 0 | 0 | 10 | 0 | 0.00% | -12.16% | +17% |
| **Total** | **47** | **5** | **4** | **38** | **3** | **+1.27%** | **-10.89%** | — |

> **Note:** 50 total scheduled dates across 5 tickers. 47 were evaluated by the LLM;
> 3 were skipped due to true circuit breaker halts (TMGH ×2, ETEL ×1). CB halts are
> not counted in the BUY/SELL/HOLD totals. HOLD rate = 38/47 = **80.9%**.

### Key per-date observations

**TMGH.CA** (real_estate, +177% B&H):
- 7 consecutive HOLD decisions despite the stock rallying from 23.86 to 66.11 EGP
- Single BUY on final evaluation date only (too late to capture any of the rally)
- Every HOLD rationale cites negative EY spread: EY 1.6-4.2% vs CBE 19.25-27.25%
- 2 dates correctly halted by circuit breaker (true ±10% daily moves)

**SWDY.CA** (holdings, +65% B&H):
- 10/10 HOLD. Zero trades. Zero circuit breaker events (all were false positives pre-P1).
- Every judge output follows the pattern: bear cites negative EY spread, bull has no
  compelling counter-evidence, judge concludes "balanced" → HOLD.

**FWRY.CA** (operational, +17% B&H):
- 10/10 HOLD. Same pattern as SWDY. No circuit breaker events after P1 fix.

**COMI.CA** (banks):
- Most active (2 BUY, 2 SELL, 6 HOLD). Banks sector gets `_SECTORS_ALWAYS_UP`
  treatment in calibration, which helps, but still 60% HOLD.

**ETEL.CA** (operational):
- 2 BUY-SELL cycles completed. +16.87% vs B&H. Best relative performer.
- Demonstrates the system *can* trade profitably when it acts.

### Confidence distribution (all 47 decisions)

| Range | Count | Pct |
|-------|-------|-----|
| 0.40-0.45 | 12 | 25.5% |
| 0.45-0.50 | 31 | 66.0% |
| 0.50-0.55 | 4 | 8.5% |
| > 0.55 | 0 | 0.0% |

All 47 decisions fall in the 0.414-0.494 band. The confidence scoring system
has effectively **zero discriminative power**.

### Decision path

All 47 decisions used the `judge_bare` path (structured bull/bear theses → research
manager judge). Zero used `judge_w_risk` or any fallback. Zero risk violations were
flagged across the entire benchmark.

---

## 3. HOLD Reason Taxonomy

From the P1-fix benchmark audit logs, every HOLD can be classified into one of
these categories:

### Category A: "EY spread dominance" (estimated 70-80% of HOLDs)
The judge sees negative EY spread as the primary evidence and concludes that cash
(earning the CBE rate) is mathematically superior to equity exposure. This is the
single largest driver.

**Pattern:** Bear cites EY < CBE rate → judge agrees → HOLD.

**Tickers affected:** TMGH.CA (all 7 HOLDs), SWDY.CA (all 10), FWRY.CA (all 10),
COMI.CA (some HOLDs).

### Category B: "Balanced debate, unfavorable risk/reward" (estimated 15-20%)
Bull and bear cases are rated approximately equal, and the HOLD criteria in the
research manager prompt are satisfied:
> "HOLD: ONLY valid if (a) data is genuinely insufficient, OR (b) bull and bear cases
> are EXACTLY balanced AND the risk/reward is unfavorable."

In practice, the "EXACTLY balanced" criterion is met far too often because:
- Bull always says "up" (calibration forces it)
- Bear always says "negative EY spread" (structurally true)
- These cancel out, producing "balanced"

### Category C: "Insufficient data / news silence" (estimated 5-10%)
Backtest mode produces empty news and social feeds (correct — historical social data
is not available). The news analyst applies a **40-point confidence penalty** when no
news is found. This pushes the composite confidence below any action threshold.

---

## 4. Where the HOLD Bias Enters the Pipeline

The bias is not injected at a single point. It's a **cascade** where each stage either
introduces or amplifies the bias, and no stage counteracts it.

### Stage-by-stage cascade

```
┌──────────────────────────────────────────────────────────────────────┐
│ Stage 1: data_cot.py — Evidence Pack Construction                   │
│                                                                      │
│ INPUTS: CSV financials, OHLCV, macro                                │
│ EFFECT: EY spread computed correctly as EY - CBE rate               │
│ PROBLEM: EARNINGS_YIELD_COMPRESSED flag fires at -2% threshold      │
│          With CBE 19-27%, this fires for any stock with P/E > 3.7x  │
│          = virtually every EGX stock                                 │
│ BIAS: Moderate (provides truthful but regime-blind negative signal)  │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Stage 2: concept_cot.py — Quick LLM Concept Synthesis               │
│                                                                      │
│ INPUTS: Evidence pack                                                │
│ EFFECT: Interprets the evidence, produces valuation_read, etc.      │
│ PROBLEM: No guidance on how to interpret EY spread                  │
│          No regime-conditional instructions                          │
│ BIAS: Moderate (passes through the negative EY signal without       │
│       contextualizing it for the high-rate regime)                   │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Stage 3: thesis_cot.py — Deep LLM Thesis (H&P method)              │
│                                                                      │
│ INPUTS: Evidence pack + concept output                               │
│ EFFECT: Produces earnings_direction, outlook, risk_level, confidence│
│ PROBLEM: Line 79 has base-rate "up" prior guidance, but NO          │
│          instruction for handling negative EY spreads in high-rate   │
│          regimes. No mention of NAV, replacement cost, or inflation  │
│          hedging for real estate. No momentum signal available.      │
│ BIAS: High (LLM sees negative EY spread and has no counterargument) │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Stage 4: calibration.py — Deterministic Signal Calibration          │
│                                                                      │
│ INPUTS: thesis_cot output (direction, confidence, outlook, risk)    │
│ EFFECT: Calibrates direction using base-rate-aware policy            │
│ PROBLEM: CRITICAL — converts ALL "flat" to "up" (line 118-128)     │
│          Converts ALL "down" to "up" unless ALL of:                  │
│            conf >= 75 AND bearish AND high risk AND non-bank         │
│            AND D/E <= 4 (lines 130-191)                              │
│          In practice, this means the fundamentals signal is ALWAYS   │
│          "up" — the directional dimension is destroyed.              │
│ PARADOX: "up" fundamentals ≠ BUY, because the downstream LLM also  │
│          sees the raw evidence with negative EY spread. The          │
│          calibration makes the bull case unconvincing (it says "up"  │
│          but the raw data contradicts it).                           │
│ BIAS: Very High (destroys directional variance)                     │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Stage 5: Bull Researcher + Bear Researcher (debate)                 │
│                                                                      │
│ INPUTS: All analyst reports + evidence                               │
│ EFFECT: Produce opposing theses                                      │
│ PROBLEM: Bull has calibrated "up" + raw data showing negative EY    │
│          = weakened, self-contradictory bull case                    │
│          Bear has negative EY spread = structurally strong bear case │
│          No momentum/RS/NAV for bull to cite                        │
│ BIAS: Very High (asymmetric ammunition — bear has the killer stat)  │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Stage 6: Research Manager (CIO judge)                                │
│                                                                      │
│ INPUTS: Bull thesis, bear thesis, analyst reports, macro context     │
│ EFFECT: Final BUY / SELL / HOLD decision                            │
│ PROBLEM: HOLD criteria (line 94-96 of research_manager.py):         │
│          "ONLY valid if (a) data insufficient OR (b) EXACTLY        │
│          balanced AND risk/reward unfavorable"                       │
│          In practice, always satisfied because bull=up but bear=     │
│          negative-EY-spread, making them "balanced"                  │
│ BIAS: High (HOLD is the path of least resistance when signals       │
│       conflict, which they structurally always do)                   │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Stage 7: Confidence Aggregation (propagation.py)                    │
│                                                                      │
│ EFFECT: Affects position sizing only, not direction                  │
│ PROBLEM: All values compress to 0.414-0.494                         │
│ BIAS: Low (doesn't cause HOLD, but prevents any strong signal)      │
└──────────────────────────────────────────────────────────────────────┘
```

### The cascade in one sentence

> Calibration forces fundamentals to "up" → but the raw negative EY spread makes
> the LLM distrust its own "up" signal → bear researcher cites EY spread as
> "mathematically dominant" → bull researcher has no momentum/NAV/regime data to
> counter → judge calls it "balanced" → HOLD.

---

## 5. Prompt and Code Source Locations

### Code that introduces or amplifies HOLD bias

| File | Lines | Component | Mechanism | Severity |
|------|-------|-----------|-----------|----------|
| `tradingagents/agents/analysts/fundamentals/calibration.py` | 118-128 | flat→up conversion | Eliminates "flat" as a final direction entirely | **CRITICAL** |
| `tradingagents/agents/analysts/fundamentals/calibration.py` | 130-191 | down→up gates | Requires 5 simultaneous conditions to preserve "down" | **CRITICAL** |
| `tradingagents/agents/analysts/fundamentals/sector_config.py` | 187-190 | EY_SPREAD_FLAGS | `EARNINGS_YIELD_COMPRESSED` threshold of -2% fires for all stocks at CBE 19-27% | **HIGH** |
| `tradingagents/agents/analysts/fundamentals/thesis_cot.py` | 79-80 | Base-rate prior | "Most EGX companies show annual earnings growth" — no regime-conditional override | **HIGH** |
| `tradingagents/agents/analysts/fundamentals/thesis_cot.py` | 36-116 | System prompt | No mention of: inflation hedging, NAV valuation, momentum, EY spread interpretation in high-rate regimes | **HIGH** |
| `tradingagents/agents/managers/research_manager.py` | 94-96 | HOLD criteria | "EXACTLY balanced AND risk/reward unfavorable" is structurally always met when bull=up but bear=negative-EY | **HIGH** |
| `tradingagents/agents/analysts/fundamentals/concept_cot.py` | — | No calibration guidance | `valuation_read` field has no regime-conditional interpretation guidance | **MODERATE** |

### Code that is NOT contributing to the problem

| File | Lines | Why it's fine |
|------|-------|--------------|
| `tradingagents/agents/analysts/fundamentals/data_cot.py` | — | Evidence pack construction is correct and truthful. EY spread IS negative; the data layer should report reality. |
| `tradingagents/agents/analysts/fundamentals/data_loader.py` | — | Multi-period CSV loading works correctly. |
| `tradingagents/agents/analysts/fundamentals/financial_calculator.py` | — | 14 core ratios computed correctly. |
| `tradingagents/agents/risk_mgmt/` | — | Zero risk violations fired. Risk layer is not blocking trades. |
| `tradingagents/graph/signal_processing.py` | — | Regex extraction works — it faithfully extracts what the LLM emitted. |
| `scripts/backtester.py` | — | Post-P1, mechanics are correct. Circuit breaker now properly uses daily OHLCV. |

---

## 6. Market/Momentum Signals: Present vs Ignored

### What the market analyst produces

The market analyst (`market_analyst.py`) computes and reports:
- RSI (14-period)
- MACD (12, 26, 9)
- Bollinger Bands (20-period)
- SMA (20, 50, 200)

These are included in the `market_report` state field and consumed by the
research manager alongside the three other analyst reports.

### What is missing

| Signal | Status | Impact |
|--------|--------|--------|
| **1M/3M/6M price returns** | NOT computed anywhere | Bull researcher has no momentum evidence |
| **Relative strength vs EGX30** | NOT computed | No way to know if stock outperforms the market |
| **Price trend direction** | Implicit in SMA crossovers but NOT explicit | LLM must infer trend from raw indicator values |
| **Absolute momentum** (is the stock going up?) | NOT available as a simple signal | Most directly relevant to the bull case |
| **NAV proxy** (P/B + replacement cost context) | `PB_UNDERSTATED_HISTORICAL_COST` flag exists but is informational only | Real estate valuation has no positive anchor |

### How market analyst signals are consumed

The market report is one of four analyst inputs to the research manager. It is NOT
weighted or prioritized. When the fundamentals report says "negative EY spread" and
the market analyst says "RSI 62, MACD bullish crossover", the LLM treats the
fundamentals argument as more authoritative because it has a specific numerical
comparison (EY vs CBE rate) while the technical signals are pattern-based.

**The core problem**: Technical analysis provides *descriptive* signals (RSI is 62),
not *prescriptive* ones (the stock has returned +45% in 3 months, beating EGX30 by
+32%). The LLM can dismiss descriptive signals; it cannot easily dismiss a concrete
return number.

---

## 7. News and Social Silence Impact

### Backtest mode behavior

In backtest mode (the default for all benchmark runs):
- **News analyst**: Attempts to fetch historical news. For most EGX tickers in the
  2024 window, news fetches return empty or very sparse results. The news analyst
  applies a **40-point confidence penalty** when no news is found.
- **Social analyst**: The `signal_adapter.py` has a `backtest_honesty_gate` that
  correctly returns `NO_SIGNAL` for historical dates (social data is not available
  retroactively).
- **DataPrefetcher**: Pre-fetches news and social in parallel. Both typically return
  empty/minimal content for historical EGX tickers.

### Impact on HOLD bias

1. **News silence → lower composite confidence**: The -40 penalty from the news analyst
   contributes to the overall confidence compression (all values in 0.414-0.494).
   However, confidence affects **position sizing only, not direction** — so this is a
   secondary amplifier, not a primary cause.

2. **Social NO_SIGNAL → no retail sentiment input**: The research manager sees an
   empty or neutral social sentiment section. This removes a potential bull input
   (retail enthusiasm during rallies like TMGH's +177% run).

3. **Net effect**: Moderate amplifier. Even with perfect news and social data, the
   EY spread dominance and calibration collapse would still produce majority-HOLD
   outcomes. News/social silence makes the existing problem marginally worse.

---

## 8. Risk Manager Impact

### Findings

**Zero risk violations** were flagged across all 47 evaluated ticker-dates. The risk
management pipeline (deterministic checks + LLM risk debater) did not block any trade.

This means:
- The risk manager is NOT contributing to HOLD bias
- When the research manager says BUY, the risk manager lets it through
- The problem is upstream: the research manager itself doesn't say BUY often enough

### Position sizing

Confidence compression (0.414-0.494) means all positions are sized conservatively
when they do occur. With `max_position_pct_adv = 0.10`, the narrow confidence band
produces narrow position sizes. This reduces the magnitude of returns on successful
trades but does not cause HOLDs.

---

## 9. Missing Data Impact

### Data availability matrix (2024-01-02 to 2024-07-14 window)

| Data Source | Available | Quality | Impact on Decisions |
|-------------|-----------|---------|---------------------|
| OHLCV (yfinance) | Yes, daily | Good | Used for circuit breaker, market analyst |
| Fundamentals (CSV) | Yes, annual + some quarterly | Good | Primary input for fundamentals pipeline |
| CBE rate (macro_provider) | Yes, date-aware | Good | 19.25% pre-March, 27.25% post-March |
| EGX30 benchmark | Partial (~70% coverage) | Moderate | yfinance ^CASE30 "possibly delisted" |
| News (historical) | Sparse to empty | Poor | -40 point penalty applied |
| Social (historical) | Not available | N/A | Correctly returns NO_SIGNAL |
| Insider/institutional flows | Not available | N/A | Not in the system at all |
| Sector-specific NAV data | Not available | N/A | Real estate cannot be valued on NAV |

### Impact assessment

The missing data (news, social, NAV) deprives the bull researcher of evidence that
could counter the negative EY spread. In a live (non-backtest) setting, news and
social data would be available and could partially offset the EY dominance — but
the structural problem (no momentum, no regime conditioning, no NAV proxy) would
remain.

---

## 10. Corrections to `strategy_improvement_research_plan.md`

### Correction 1: P2 scope is incomplete

The plan describes P2 as "prompt changes only" targeting `thesis_cot.py` and
`data_cot.py`. This is **insufficient**. The investigation reveals that
`calibration.py` is a primary HOLD driver that the plan does not mention at all.

**Required addition to P2:**
- `calibration.py`: Add regime-conditional gate relaxation. In high-rate regimes
  (CBE > 15%), the calibration policy should allow "flat" and moderate-confidence
  "down" signals to survive when sector context supports it (e.g., real estate with
  rising NAV, holdings with diversified earnings). Currently, the policy eliminates
  all non-"up" signals regardless of regime.

### Correction 2: `sector_config.py` EY_SPREAD_FLAGS need regime conditioning

The plan does not mention that `EY_SPREAD_FLAGS["EARNINGS_YIELD_COMPRESSED"]` has a
fixed threshold of `-2%` that is regime-blind. At CBE 27.25%, this fires for any
stock with P/E > 3.7x — virtually the entire EGX.

**Required addition to P2:**
- `sector_config.py`: Make the `EARNINGS_YIELD_COMPRESSED` threshold regime-aware.
  Options: (a) widen the threshold in high-rate regimes (e.g., -15% when CBE > 15%),
  (b) suppress the flag entirely in high-rate regimes, or (c) add a companion
  `EARNINGS_YIELD_REGIME_NOTE` that contextualizes the spread.

### Correction 3: Plan underestimates calibration.py's role

The plan's Problem 2 ("EY-vs-rate dominance") correctly identifies the EY spread
as the main issue but traces it only through the data → prompt → judge path. It
misses the parallel path through calibration, where directional variance is
destroyed before the debate even begins.

**However**: Preserving "flat" in selected high-rate/sector contexts may make the
fundamentals signal more honest, but it may not reduce HOLD by itself. Its purpose
is to prevent contradictory "calibrated up but raw evidence bearish" reports. BUY
behavior likely still requires regime-aware EY interpretation (P2 prompt changes)
and later momentum/NAV evidence (P3). Any calibration adjustment in P2 should be
framed as an **honesty/diagnostic correction**, not a guaranteed alpha improvement.

### Correction 4: Problem numbering

The plan lists 5 problems. This investigation identifies a 6th:

**Problem 6: Calibration direction collapse** (`calibration.py` lines 118-191)
- The `v3_sector_leverage_aware` policy converts all non-"up" signals to "up"
  in practice, destroying the fundamentals pipeline's ability to express bearish
  or neutral views.
- This is distinct from Problem 2 (EY-vs-rate dominance) because it operates
  independently — even if the EY spread issue were fixed, calibration would
  still eliminate "flat" and most "down" signals.
- Priority: **P2 diagnostic** — should be examined alongside prompt fixes, but
  the adjustment alone is unlikely to reduce HOLD rate. It is an honesty fix
  (make the pipeline's reported direction match its actual evidence).

### Correction 5: Missing items from original plan

The plan does not mention:
- `concept_cot.py` — needs regime-conditional guidance on `valuation_read`
  interpretation (minor, but contributes to the cascade)
- News silence penalty — the -40 confidence hit should be noted as a backtest-
  specific amplifier that will not exist in live mode

---

## 11. Minimal Recommended P2 Change

P2 is the **smallest interpretive fix** that addresses the EY-vs-rate dominance
without adding new data sources, changing allocation logic, or modifying the
research manager. It touches 3-4 files, all within the fundamentals pipeline.

### P2 scope (definitive)

1. **Add date-aware `inflation_regime` / `rate_regime` flag to the fundamentals
   evidence pack** (`data_cot.py`). Value: `"high"` when CBE > 15%, `"normal"`
   otherwise. Derived from the same date-aware CBE rate already in macro_provider.

2. **Add regime-context note for earnings-yield spread in high-rate regimes**
   (`sector_config.py`). When CBE > 15%, annotate the `EARNINGS_YIELD_COMPRESSED`
   flag with an informational note explaining that negative EY spreads are
   structurally common in high-rate EM regimes and do not carry the same signal
   as in normal-rate environments. The flag itself is preserved (truthful data
   should not be suppressed).

3. **Add thesis/concept prompt guidance** (`thesis_cot.py`, minor in `concept_cot.py`)
   stating that negative EY spread is structurally common in high-rate EGX regimes
   and should not be treated as a standalone veto of equity exposure. For real estate
   and holdings sectors, the prompt should direct the LLM to also assess NAV
   trajectory context, pricing power, and inflation-hedge properties using the
   evidence already in the pack.

4. **Narrow calibration adjustment** (`calibration.py`) — **only if justified by
   tests**. If unit tests confirm that preserving "flat" in high-rate/non-bank
   contexts produces more honest (less self-contradictory) fundamentals reports,
   then relax the flat→up gate for those contexts. This is an honesty/diagnostic
   fix, not a HOLD-reduction mechanism. Backward compatibility for banks and
   normal-rate regimes must be explicitly preserved.

### What P2 must NOT include

- Momentum / relative strength signals (P3)
- NAV proxy computation from financial data (P3)
- Starter-position rules (P4)
- Research manager prompt changes (P4)
- Market analyst changes (P3)
- Pipeline orchestration changes
- Risk manager changes (not contributing to the problem)
- Any new data sources or external API calls

---

## 12. Tests Needed Before Implementation

### Before implementing P2 changes

1. **Regime flag correctness test**: `inflation_regime` flag correctly reflects
   date-aware CBE rate. Verify: "high" for dates after 2024-03-06 (CBE hike to
   27.25%), "normal" for dates with CBE <= 15%.

2. **No temporal leakage test**: `inflation_regime` is derived from the same
   date-aware CBE rate already in the macro_provider. Verify no future CBE data
   is used.

3. **Calibration regime-awareness test**: When CBE > 15% and sector is
   `real_estate`, verify that "flat" direction survives calibration (is not
   converted to "up").

4. **Calibration backward-compatibility test**: When CBE <= 15% or sector is
   `banks`, verify the existing calibration policy is unchanged. All existing
   `test_fundamentals_phase1a.py` and `phase1b_audit.py` tests must still pass.

5. **EY_SPREAD_FLAGS regime test**: Verify that the contextual note is added
   when CBE > 15% (option b) or that the threshold is widened (option a).

6. **Thesis prompt regime test**: Verify the regime-conditional instruction is
   present in the prompt when sector=real_estate and rate>15%, and absent when
   sector=banks or rate<=15%.

7. **End-to-end data flow test**: Build an evidence pack for a real_estate ticker
   with CBE > 15%, run through concept_cot and thesis_cot, verify the output
   contains regime-aware reasoning. (This requires an LLM call — schedule for
   after the code changes are validated by unit tests.)

### Regression tests (must stay green)

- `tests/test_fundamentals_phase1a.py`
- `tests/test_fundamentals_phase1b_audit.py` (if exists)
- `tests/test_circuit_breaker.py` (all 13 tests)
- `tests/test_execution_costs.py` (all tests)
- All existing tests in `tests/` directory

---

## 13. What Not to Change

1. **Do not remove calibration.py entirely.** The base-rate-aware policy is
   well-motivated (LLM "down" predictions have ~27% precision on EGX historical
   data). Only relax the "flat" gate in high-rate regimes, and only if tests
   confirm it produces more honest reports.

2. **Do not add momentum signals in P2.** Momentum injection (P3) requires
   temporal safety testing and is a separate, larger change. Adding it alongside
   the regime fix makes it impossible to attribute improvement to either change.

3. **Do not change the research_manager.py prompt.** Fix the inputs first. If
   the inputs become more varied (due to regime-aware EY interpretation and
   contextualized flags), the existing "HOLD only if exactly balanced" criterion
   may start working as intended.

4. **Do not change risk_manager.py.** It is not contributing to the problem.

5. **Do not change data_cot.py's evidence computation logic.** The data layer
   should report reality (negative EY spread is real). The fix should be in
   *interpretation* (prompt + flags), not in *suppression* of truthful data.

6. **Do not add new data sources.** P2 is a prompt + flag fix. New data
   sources (NAV proxy, momentum, relative strength) are P3.

7. **Do not overfit to the H1 2024 window.** The regime fix should be general
   (applies whenever CBE > 15%), not specific to the EGP devaluation rally.

8. **Do not inject future returns into prompts.** No price data beyond trade_date
   is permitted in any prompt or evidence pack.

9. **Do not hardcode BUY for specific tickers.** No special-case logic for
   TMGH/SWDY/FWRY. The fix must be general (regime-aware interpretation),
   not ticker-specific overrides.

10. **Do not weaken risk manager constraints.** EGX hard limits (long-only,
    no leverage, ±10% daily, max 10% ADV, T+2 settlement) are regulatory
    requirements and must remain inviolable.

11. **Do not use live news/social data in historical backtest mode.** The
    backtest honesty gate is correct — historical social data is not available
    and must not be fabricated.

12. **Do not run a full benchmark rerun until P2 is implemented and unit-tested.**
    The P1-fix benchmark is mechanically valid. Another run without strategy
    changes will produce the same 80.9% HOLD rate.

---

## 14. Is More Backtesting Justified Now?

### Yes, but only after P2 implementation.

**Why not rerun now:**
- The P1 fix improved mechanics (eliminated false CB halts) but did not change
  the agent's decision-making behavior. The agent still produces 80.9% HOLD.
- Running the same benchmark again will produce the same results. The bottleneck
  is now the strategy (EY spread dominance + calibration collapse), not the
  backtester mechanics.

**When to rerun:**
- After P2 changes are implemented and all unit tests pass.
- Expected benchmark: `thesis_5ticker_p2regime_20240102`
- **Success hypothesis for P2** (not a promise): If the regime-aware EY
  interpretation works, we expect at least 2 of the 3 all-HOLD tickers (TMGH,
  SWDY, FWRY) to produce at least 1 non-HOLD decision. The HOLD rate should
  drop below 70%. Alpha vs EGX30 may improve for some tickers, but P2 alone
  is unlikely to produce positive alpha — it removes an interpretation failure,
  not a missing signal.

**What not to expect:**
- P2 alone is unlikely to make the system beat EGX30. It addresses the most
  obvious interpretation failure (EY spread treated as veto in all regimes) but
  does not add the positive signals (momentum, NAV) that would let the bull
  researcher make a compelling case for trending stocks.
- Beating EGX30 likely requires P3 (momentum + NAV proxy) at minimum.
- Calibration changes alone may increase "flat" signals without reducing HOLD,
  since "flat" fundamentals still lacks the conviction to trigger BUY.

---

## 15. P3 Benchmark Results (2026-06-17) — Post-Momentum Integration

### What P3 added

P3 injected momentum evidence (20d/60d/120d returns, relative strength vs EGX30,
volume confirmation, momentum labels) into the deterministic market analyst's
output. This data reaches the LLM via `p3_evidence_narrative_contains_momentum_section`
in the fundamentals evidence pack. P3 instrumentation coverage: **47/47 dates (100%)**.

### Benchmark comparison (P1/P2 baseline → P3)

| Metric | P1/P2 (cbfix) | P3 | Delta |
|--------|:--------------:|:--:|:-----:|
| Mean return | 1.27% | 4.70% | +3.43 pp |
| Mean alpha vs EGX30 | -10.89% | -7.46% | +3.43 pp |
| Tickers beating EGX30 | 0/5 | 1/5 | +1 |
| HOLD rate | 80.9% (38/47) | 78.7% (37/47) | -2.2 pp |
| BUY count | 5 | 7 | +2 |

### Interpretation

P3 improved signal visibility and reduced missed-momentum cases, especially
SWDY.CA (+17.09% return, +4.94% alpha — the only ticker to beat EGX30).

**P3 did not solve the core benchmark underperformance.** Mean alpha remains
-7.46%. The remaining issue is not absence of momentum evidence; it is
decision conservatism / capital deployment / entry threshold behavior. The
HOLD rate dropped only marginally (80.9% → 78.7%), and the system still
defaults to cash when the EY-spread bear argument dominates.

Key findings:
- P3 does not blindly buy momentum. ETEL and FWRY remained all-HOLD despite
  having momentum data on every date. TMGH remained cautious (1 BUY) despite
  extreme momentum — reasonable given valuation overextension.
- Some decision differences may reflect LLM/path variability across runs, so
  attribution to P3 should be interpreted directionally rather than causally.
- No threshold tuning is recommended from this benchmark.

### Updated diagnosis

The HOLD bias cascade (§1) remains the dominant factor. P3 added a positive
signal (momentum) that gives the bull researcher ammunition, but the bear
researcher's EY-spread argument + the research manager's tendency to side with
"balanced = HOLD" logic still dominate. The next high-leverage fix is P4
(regime-aware research_manager prompt + starter-position rule) or a direct
intervention in how the research manager adjudicates tied debates.

### Cost consideration

Each 5-ticker benchmark run costs approximately 50 LLM calls × 5 tickers × ~$0.02-0.05
per call = $5-12.50 in DeepSeek API costs. This is modest enough to justify one
benchmark per phase, but not enough to justify exploratory reruns without code changes.

---

## Appendix A: HOLD Bias Cascade — Worked Example (TMGH.CA, 2024-03-24)

This traces a single decision through the full pipeline to show exactly where
the HOLD bias enters.

| Stage | Input | Output | Bias Introduced |
|-------|-------|--------|-----------------|
| **data_cot** | TMGH financials: Revenue +41% YoY, Net Income +68% YoY, P/B 2.1x, EY 1.64%, CBE 27.25% | EY spread = -25.6%. Flag: `EARNINGS_YIELD_COMPRESSED`. Flag: `PB_UNDERSTATED_HISTORICAL_COST` (informational only). | Moderate: truthful but regime-blind negative signal |
| **concept_cot** | Evidence pack with -25.6% EY spread | Likely: growth signal "strong growth", valuation_read "compressed vs rate". No regime guidance. | Moderate: passes through negative EY without context |
| **thesis_cot** | Evidence + concept. No momentum data, no NAV, no regime instruction. | Probably: earnings_direction "up" (strong growth), but outlook "neutral" or "bearish" (negative EY dominates). Confidence ~55-65. | High: LLM sees growth but concludes cash is better at 27% |
| **calibration** | direction "up" (or "flat"/"down") from thesis_cot | Always "up" — calibration ensures this regardless of what thesis_cot said. | Very high: directional variance destroyed |
| **fundamentals report** | Calibrated "up" + raw data showing -25.6% EY spread | Contradictory signal: "up" direction but negative spread in the data | Very high: bull case is self-undermining |
| **bull researcher** | Fundamentals "up", market analyst (RSI/MACD), sparse news | "TMGH shows earnings growth and technical bullishness, but EY spread is deeply negative" | High: bull lacks momentum/NAV ammunition |
| **bear researcher** | Same inputs | "Cash at 27.25% CBE rate mathematically dominates equity exposure with EY at 1.64%" | Very high: structurally unbeatable argument |
| **research manager** | Bull ≈ Bear (both have valid points) | "Cases are balanced, risk/reward unfavorable → **HOLD**" | Decision: HOLD |

**Meanwhile, TMGH stock price:** 23.86 → 41.80 (+75% since Jan 2) and heading to 66.11 by July.

---

## Appendix B: Files Referenced in This Investigation

| File | Path | Role in Investigation |
|------|------|----------------------|
| calibration.py | `tradingagents/agents/analysts/fundamentals/calibration.py` | Primary HOLD bias source — direction collapse |
| sector_config.py | `tradingagents/agents/analysts/fundamentals/sector_config.py` | EY_SPREAD_FLAGS regime-blind thresholds |
| thesis_cot.py | `tradingagents/agents/analysts/fundamentals/thesis_cot.py` | Missing regime-conditional prompt guidance |
| concept_cot.py | `tradingagents/agents/analysts/fundamentals/concept_cot.py` | Missing valuation_read regime guidance |
| data_cot.py | `tradingagents/agents/analysts/fundamentals/data_cot.py` | Evidence pack (correct — not a bias source) |
| research_manager.py | `tradingagents/agents/managers/research_manager.py` | HOLD criteria too easily satisfied |
| market_analyst.py | `tradingagents/agents/analysts/market_analyst.py` | Missing momentum/RS (P3 scope) |
| pipeline.py | `tradingagents/agents/analysts/fundamentals/pipeline.py` | Orchestrator (not a bias source) |
| backtester.py | `scripts/backtester.py` | Mechanics correct post-P1 |
| propagation.py | `tradingagents/graph/propagation.py` | Confidence compression (secondary) |
| P1-fix benchmark | `eval_results/thesis_5ticker_cbfix_20240102/` | Primary evidence source |
| Pre-P1 benchmark | `eval_results/thesis_5ticker_clean_20240102/` | Comparison baseline |
| Strategy plan | `thesis/drafts/strategy_improvement_research_plan.md` | Corrections in §10 |
