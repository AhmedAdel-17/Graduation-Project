# SESSION_BOOTSTRAP.md — EGX Sentiment Redesign State
> Last updated: 2026-05-04 (Phase 3 addendum complete — harness, docs, tests done). Read CLAUDE.md + MEMORY.md before coding.

---

## 1. Active workstream
**Sentiment subsystem Phase 3 redesign — COMPLETE (PR 1–10 + addendum, 2026-05-04).**
PR 1–10: 680 regression tests passing. Phase 3 addendum adds 106 harness tests → 786 total.
Next work: see MEMORY.md §3 (4-week ship plan) — Week 1 blockers remain open (§A EODHD key, §B LLM non-determinism, §E server auth, §F CI/Docker).

---

## 2. Completed PRs

| PR | Files changed | What it does | Tests |
|----|--------------|--------------|-------|
| PR 1 | `tradingagents/sentiment/{__init__,contracts,taxonomy,liquidity_tiers,config}.py` | Typed contracts + NO_SIGNAL sentinel + 6-sector taxonomy + liquidity tiers + config. Foundation only — no behavioral wiring. | 37 passing |
| PR 2 | `scripts/twitter_pipeline/v2/entities.py`, `aggregator.py` | Phrase-boundary regex for Arabic alias matching; EGX30/70/100 → EGX_30/70/100 rename; SCTS "توطين التكنولوجيا" alias removed; aggregator secondary-market comment. | 36 passing |
| PR 3 | `tradingagents/sentiment/market.py` (new); `tradingagents/sentiment/__init__.py`; `pipeline_v2.py` (dead sources removed) | Layer A MarketSentiment with 3 hard gates (n≥50, ≥2 sources, ≥30% recency); regime/volatility classification; confidence formula; dead sources (twitter_authed, facebook_groups, mubasher_news) unwired from pipeline. | 86 passing |
| PR 4 | `tradingagents/sentiment/sector.py` (new); `tradingagents/sentiment/__init__.py` | Layer B SectorSentiment with 3 hard gates (n≥10, ≥3 distinct days, mean_entity_conf≥0.70); weighted-mean score; confidence (40% size + 35% spread + 25% clarity); bilingual sector classifier (`classify_post_to_sector`); IEEE-754 float rounding fix in `_mean_entity_conf`. | 75 passing |
| PR 5 | `tradingagents/sentiment/stock.py` (new); `tradingagents/sentiment/__init__.py`; `tradingagents/agents/utils/agent_states.py`; `tradingagents/agents/analysts/social_media_analyst.py` | Layer C StockSentiment with 4 hard gates per-tier (n_strong_mentions/n_distinct_authors/n_distinct_sources/recent_72h_share); MEGA(8/5/3)/MID(5/3/2)/SMALL(3/2/2) thresholds; spam exclusion pre-count; contradicts_market flag; weighted-mean score; confidence (40% size + 35% diversity + 25% clarity). Pre-LLM gate in social_media_analyst: `prefetched_stock_datapoints` in state → `_try_layer_c_gate` → if NO_SIGNAL, skip LLM, return template `"Social sentiment: insufficient data — excluded."` | 89 passing |
| PR 6 | `tradingagents/sentiment/macro.py` (new); `tradingagents/sentiment/__init__.py` | Layer A0 MacroSentiment with 3 sequential hard gates: (1) macro.source_credibility — ≥1 post from OFFICIAL/TIER1/TIER2 source; (2) macro.corroborating_sources — ≥1 (category, direction) group with ≥2 distinct domains within 48 h; (3) macro.half_life_expired — ≥1 corroborated event within its category half-life. Composite regime: RISK_OFF dominates RISK_ON dominates NEUTRAL. Source ladder: OFFICIAL (gov/EGX domains), TIER1_NEWS (reuters/bloomberg/mubasher), TIER2_NEWS (almalnews/enterprise.press/...). Confidence = 0.60×credibility_weight + 0.40×corroboration_ratio. `MacroDataPoint` NamedTuple exposed for callers. | 115 passing |
| PR 7 | `tradingagents/agents/utils/scoring.py`; `tradingagents/graph/propagation.py`; `tradingagents/agents/utils/agent_states.py`; `scripts/system_validation.py` | Layer E Blender: replaces naive linear blend with confidence-weighted mean (weight = structural weight × analyst confidence) + quorum rule (≥2 directional analysts → else `INSUFFICIENT_DATA`). Adds `SentimentBlend` NamedTuple + `blend_sentiment(macro, market, sector)` with market-regime, macro-direction, sector-tilt multipliers. Sentiment never flips direction; only confidence × and position size ×. `propagate_confidence()` enforces quorum, reads `sentiment_blend_result` from state, returns `overall_status` + `position_size_multiplier`. `AgentState` gains `sentiment_blend_result: Optional[Dict]`. `calculate_unified_score()` returns 5-tuple (decision, conf, reasoning, component_scores, overall_status). | 74 passing |
| PR 8 | `tradingagents/agents/analysts/social_media_analyst.py`; `bull_researcher.py`; `bear_researcher.py` | LLM-as-explainer demotion + bull/bear NO_SIGNAL guard. LLM schema = `narrative`+`cited_post_ids` only. `_compute_blend_result()` reconstructs typed sentiment objects from prefetch JSON. `_format_sentiment_section()` in bull/bear: detects NO_SIGNAL → EXCLUDED instruction; else surfaces blend multipliers as non-directional execution context. | 47 passing |
| PR 9 | `tradingagents/sentiment/surfacing.py` (new); `tradingagents/sentiment/__init__.py`; `tradingagents/graph/trading_graph.py`; `server/api_server.py`; `cli/main.py` | Surfacing + audit trail. Pure functions: `extract_sentiment_audit_record`, `format_sentiment_for_api`, `format_sentiment_for_cli`, `build_sentiment_context_event`. `_log_state()` appends `sentiment_audit` block to eval JSON. API server appends `SENTIMENT_CONTEXT` JSONL event (best-effort). CLI renders "VI. Sentiment Context" Rich panel, cyan (signal) / yellow (no-signal). All paths guarded — never crash on missing data. | 50 passing |
| PR 10 | `tradingagents/dataflows/social_media_sources/aggregator.py`; deleted: `twitter_source.py`, `twitter_authed.py`, `facebook_groups.py`, `mubasher_news.py`; `scripts/calibrate_tier_thresholds.py` (new); `pipeline_v2.py` (comments) | Zombie source cleanup + calibration script. Deleted 4 dead source files (all 0 posts in production). Removed `"twitter"` from `PLATFORM_SOURCES`. Added offline-safe tier-threshold calibration script: reads rolling `results_*.json`, computes p25 per tier/gate metric, recommends updated MEGA/MID/SMALL thresholds. Resolves MEMORY §Q, §S. | 47 passing |
| Addendum | `scripts/test_sentiment_pipeline.py` (new); `agent_docs/sentiment_architecture.md` (new); `tests/test_sentiment_harness.py` (new) | Manual test harness (5 scenarios, no LLM), deep Mermaid architecture docs (14 sections), 106-test regression suite for the harness. Fixed MEGA-tier scenario data builders to provide 3 distinct sources; corrected DOMT to MID tier. | 106 passing |

**Regression gates (must stay green after every PR):**
- `tests/test_fundamentals_phase1a.py` — 79 tests
- `tests/test_twitter_pipeline_v2.py` — 8 tests
- PR 1 suite — 37 tests
- PR 2 suite — 36 tests
- PR 3 suite — 86 tests
- PR 4 suite — 75 tests
- PR 5 suite — 89 tests
- PR 6 suite — 115 tests
- PR 7 suite — 74 tests
- PR 8 suite — 47 tests
- PR 9 suite — 50 tests
- PR 10 suite — 47 tests
- Harness suite — 106 tests (`tests/test_sentiment_harness.py`)

---

## 3. Planned PRs (not yet started)

| PR | Target | Key files |
|----|--------|-----------|
| ~~PR 5~~ | ~~Layer C: StockSentiment + liquidity-tier gates + pre-LLM NO_SIGNAL gate~~ | ~~done~~ |
| ~~PR 6~~ | ~~Layer A0: MacroSentiment~~ | ~~done~~ |
| ~~PR 7~~ | ~~Blender/propagation rewrite~~ | ~~done~~ |
| ~~PR 8~~ | ~~LLM-as-explainer demotion + bull/bear NO_SIGNAL guard~~ | ~~done~~ |
| ~~PR 9~~ | ~~Surfacing + audit trail~~ | ~~done~~ |
| ~~PR 10~~ | ~~Cleanup + calibration script + MEMORY.md final~~ | ~~done~~ |
| PR 10 | Cleanup + calibration script + MEMORY.md final | Delete zombie sources; calibration script for tier thresholds |

---

## 4. Architecture: redesigned sentiment layers

```
Layer A0  MacroSentiment      — named macro events (CBE rate, EGP deval, IMF, inflation)
Layer A   MarketSentiment     — retail mood/regime (EUPHORIA/GREED/NEUTRAL/FEAR/PANIC)
Layer B   SectorSentiment     — 6-sector tilt (banks/real_estate/industry/telecom_tech/financial_services/food_bev)
Layer C   StockSentiment      — ticker-specific (intentionally sparse; usually NO_SIGNAL)
Layer E   Blender             — sentiment modifies confidence/size ONLY, never flips direction
```

**Philosophy:** Sentiment = context engine + confidence modifier. NOT alpha generator.
**Default state:** NO_SIGNAL. Signal requires passing an explicit hard gate.

---

## 5. Critical design decisions (locked)

- **NO_SIGNAL is first-class.** `NoSignalReason(gate_failed, human_readable, metrics)` propagates into logs, agent context, reports, audit. Canonical format: `NO_SIGNAL: <human_readable> (gate=<gate>, k=v, ...)`
- **Hard gates only.** No soft confidence multipliers. Gate fails → NO_SIGNAL. No partial credit.
- **Sentiment never flips direction.** Fundamentals=BUY + market=PANIC → still BUY, lower confidence + smaller position.
- **LLM is explainer only (PR 8).** Numbers come from deterministic aggregator. LLM produces `narrative` + `cited_post_ids`. No directional fields in output schema.
- **Pre-LLM gate (PR 5).** If Layer C emits NO_SIGNAL for ticker → LLM not invoked. Template: `"Social sentiment: insufficient data — excluded."`
- **Quorum rule (PR 7).** Final decision requires ≥2 non-NO_SIGNAL analysts. Else `overall_status: INSUFFICIENT_DATA`.
- **6-sector taxonomy is canonical.** `tradingagents/sentiment/taxonomy.py` is single source of truth. Fundamentals uses 4-sector shim (`to_fundamentals_sector()`) until migrated.
- **Liquidity tiers are PROVISIONAL.** MEGA=8 / MID=5 / SMALL=3 strong mentions. Calibrate from 30-day rolling window in PR 10.

---

## 6. Layer C stock-level gates (all must pass — AND)

| Gate | MEGA | MID | SMALL |
|------|------|-----|-------|
| `n_strong_mentions` (entity_conf ≥ 0.85) | 8 | 5 | 3 |
| `n_distinct_authors` | 5 | 3 | 2 |
| `n_distinct_sources` | 3 | 2 | 2 |
| `≥60% mentions within 72h` | ✓ | ✓ | ✓ |
| Spam/promo posts excluded before count | ✓ | ✓ | ✓ |

MEGA tickers: COMI, TMGH, FWRY, ETEL, HRHO.

---

## 7. Layer A/B gates

| Layer | Gate |
|-------|------|
| Market (A) | n_total_posts ≥ 50, ≥2 distinct sources, ≥30% within 24h |
| Sector (B) | n_sector_posts ≥ 10, ≥3 distinct days, agg entity_conf ≥ 0.70 |
| Macro (A0) | source_credibility ≥ TIER2, ≥2 corroborating sources within 48h |

---

## 8. Source ladder (as of PR 2)

| Source | Status | Use for |
|--------|--------|---------|
| Apify Facebook | PRIMARY (needs entity gate on bypass) | Market, Sector, Stock |
| Telegram Public | KEEP | Market, Sector, Stock (highest SNR) |
| Reddit Targeted | KEEP — market/sector only | Market, Sector |
| Mubasher v2 | REMOVE (selectors broken, 0 posts) | — |
| Twitter Authed | REMOVE (dead, 0 posts) | — |
| Facebook Groups PW | REMOVE (dead, duplicate of Apify) | — |
| Twitter v1 scraper | REMOVE | — |
| `dataflows/twitter_source.py` | DELETE (always returns []) | — |

---

## 9. Macro source credibility ladder

**TIER1:** cbe.org.eg, mof.gov.eg, fra.gov.eg, reuters.com, bloomberg.com, mubasher.info (official), egx.com.eg
**TIER2:** enterprise.press, almalnews.com, dailynewsegypt.com, alborsaanews.com

---

## 10. Layer E blending multipliers

| Market regime | Confidence × | Position size × |
|---------------|-------------|----------------|
| PANIC | 0.70 | 0.50 |
| FEAR | 0.85 | 0.75 |
| GREED | 0.90 | 0.90 |
| EUPHORIA | 0.70 | 0.60 |
| NEUTRAL/NO_SIGNAL | 1.00 | 1.00 |

Sector tilt: max ±0.10 confidence shift. Macro RISK_OFF: ×0.80 confidence.

---

## 11. Key known issues (from MEMORY.md — unresolved)

- **§A** Hardcoded EODHD key in `dataflows/eodhd.py:17` — CRIT, rotate + scrub.
- **§B** LLM non-determinism — `LLM_INVOKE_KWARGS = {"temperature":0,"seed":42}` not applied everywhere. PR 8 fixes social analyst only.
- **§C** Look-ahead bias in `scripts/backtester.py` — out of scope for sentiment PRs.
- **§J** `scoring.py` linear blend misleading — fixed in PR 7.
- **§N** Regex BUY/SELL/HOLD extractor fragile — partially addressed in PR 8 (schema gains NO_SIGNAL enum, LLM no longer emits directional for sparse data).
- **§R** Entity registry coverage — PR 2 fixed phrase-boundary; full EGX-70/200 expansion deferred to PR 10.

---

## 12. Phase 3 complete — what's next

**Sentiment subsystem Phase 3 (PR 1–10) is done.** The remaining open issues in MEMORY.md are from the broader 4-week ship plan.

**Highest-priority next items (MEMORY.md §3 Week 1):**
- **§A** Rotate hardcoded EODHD API key in `dataflows/eodhd.py:17` — CRIT security
- **§B** Wire `LLM_INVOKE_KWARGS = {"temperature":0, "seed":42}` into every `.invoke()` call — CRIT reproducibility
- **§E** Add JWT auth + CORS allow-list + rate-limit to `server/api_server.py` — CRIT security
- **§F** Dockerfile + GitHub Actions CI — CRIT deployability

**Calibration follow-up (low urgency):**
Run `python scripts/calibrate_tier_thresholds.py` after ≥30 days of real pipeline_v2 results
accumulate in `scripts/twitter_pipeline/v2/logs/`, then apply recommended threshold edits to
`tradingagents/sentiment/liquidity_tiers.py`.
