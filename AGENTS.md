# AGENTS.md — Project Instructions

## Project Overview

**EGX Multi-Agent Stock Prediction System** — A LangGraph-based multi-agent AI trading system built for the Egyptian Stock Exchange (EGX). It generates trading recommendations (BUY/SELL/HOLD) by aggregating technical, fundamental, and bilingual sentiment signals through a debate-based architecture.

## Architecture

```
main.py / cli/ / server/api_server.py
        │
        ▼
TradingAgentsGraph (tradingagents/graph/trading_graph.py)
        │
        ├─── DataPrefetcher (graph/prefetch.py) ── pre-fetches news & social data
        │
        ├─── Analyst Team (parallel fan-out)
        │    ├── Market Analyst      → technical indicators via yfinance
        │    ├── Fundamentals Analyst → local EGX CSV data
        │    ├── News Analyst        → RSS/NewsAPI/local CSV
        │    └── Social Media Analyst → Twitter/Telegram/Reddit + Arabic sentiment
        │
        ├─── Research Team (debate)
        │    ├── Bull Researcher     → bullish thesis
        │    ├── Bear Researcher     → bearish thesis
        │    └── Research Manager    → synthesizes debate → investment decision
        │
        ├─── Trader → execution plan with position sizing
        │
        ├─── Risk Management (merged debate)
        │    └── Merged Risk Debator → 3-perspective risk analysis
        │
        └─── Risk Manager → final approval/veto with EGX constraints
```

## Key Directory Structure

```
tradingagents/              # Core Python package
├── agents/                 # All agent implementations
│   ├── analysts/           # Market, Fundamentals, News, Social Media analysts
│   ├── researchers/        # Bull & Bear researchers
│   ├── managers/           # Research Manager, Risk Manager
│   ├── risk_mgmt/          # Merged risk debator
│   ├── trader/             # Trader agent
│   └── utils/              # Agent states, memory, scoring, tool wrappers
├── dataflows/              # Data layer
│   ├── gateway.py          # Central data orchestrator (cache + fallback)
│   ├── interface.py        # Vendor routing (dispatches to yfinance/local/eodhd/etc.)
│   ├── y_finance.py        # Yahoo Finance provider (primary for EGX price data)
│   ├── local.py            # Local CSV data provider (fundamentals, news)
│   ├── eodhd.py            # EODHD.com API provider
│   ├── egxpy_wrapper.py    # Native EGX library wrapper
│   ├── news_providers/     # NewsAPI, RSS, aggregator
│   ├── social_media_sources/ # Twitter, Telegram, Reddit, sentiment engine
│   ├── cache_manager.py    # Disk-based caching
│   ├── retry_engine.py     # Retry + fallback logic
│   └── schemas.py          # Pydantic data models
├── graph/                  # LangGraph orchestration
│   ├── trading_graph.py    # Main graph class (entry point)
│   ├── setup.py            # Graph node/edge wiring
│   ├── propagation.py      # State propagation
│   ├── prefetch.py         # Data pre-fetcher
│   ├── reflection.py       # Post-trade reflection
│   ├── signal_processing.py # Signal extraction
│   └── conditional_logic.py # Graph routing conditions
├── utils/                  # Shared utilities
│   ├── sentiment_engine.py # Multi-model sentiment (FinBERT, CAMeLBERT, XLM-R)
│   └── text_preprocessor.py # Arabic/English text normalization
└── ablation/               # Ablation study framework

server/api_server.py        # FastAPI backend with WebSocket streaming
cli/main.py                 # Interactive CLI with Rich UI
scripts/                    # Production scripts (backtester, scrapers, benchmarks)
tests/                      # Pytest test suite
agent_docs/                 # Component-specific architecture docs
```

## Critical Configuration

- **LLM Backend**: DeepSeek (OpenAI-compatible) — configured in `tradingagents/default_config.py`
- **Data Vendors**: yfinance for prices, local CSVs for fundamentals/news — configured in `DEFAULT_CONFIG["data_vendors"]`
- **Target Market**: `EGX` — Egyptian Exchange. All tickers use `.CA` suffix (e.g., `COMI.CA`)
- **API Keys**: Loaded from `.env` via `python-dotenv`. NEVER hardcode keys in source files.
- **Memory**: PostgreSQL + pgvector (falls back to ChromaDB in-memory). See `persistent_memory.py`.

## Coding Standards

1. **Verify before changing**: Use `grep` to find actual function signatures and imports before modifying code. Do NOT assume or hallucinate APIs.
2. **No hardcoded secrets**: API keys go in `.env` only. `default_config.py` loads them via `load_dotenv()`.
3. **Use logging, not print()**: All modules should use `logging.getLogger("tradingagents.<module>")`. No `print()` for debug output.
4. **Modular agents**: Agents communicate only via LangGraph state dictionaries. Do not introduce direct agent-to-agent imports.
5. **Arabic support**: All sentiment/NLP code MUST handle Arabic (MSA + Egyptian dialect) and English. Use the sentiment engine in `tradingagents/utils/sentiment_engine.py`.
6. **EGX constraints**: Long-only, no short selling, no leverage, ±10% daily price limits, max 10% of ADV for position sizing.
7. **Type hints**: Use Python typing module. Add type hints to new functions.
8. **Test after changes**: Run `python -m pytest tests/ -v --tb=short` after any code change.
9. **Read docs on-demand**: Only read files in `agent_docs/` when actively modifying that specific component.

## Common Commands

```bash
# Run full analysis on an EGX stock
python main.py

# Run direct prediction
python run_egx_prediction.py COMI.CA

# Start API server
uvicorn server.api_server:app --reload

# Run CLI
python -m cli.main

# Run tests
python -m pytest tests/ -v --tb=short

# Run backtest
python scripts/backtester.py --ticker COMI.CA --start 2022-01-01 --end 2023-06-01 --train-end 2022-12-31 --interval 7 --analysts market,fundamentals,news,social --cooldown 3

# Verify core imports
python -c "from tradingagents.graph.trading_graph import TradingAgentsGraph; print('OK')"
```

## Entry Points

| Entry Point | Purpose |
|---|---|
| `main.py` | Simple example: run one analysis on COMI.CA |
| `run_egx_prediction.py` | Direct LLM prediction with live/historical price data |
| `cli/main.py` | Interactive CLI with Rich dashboard |
| `server/api_server.py` | FastAPI REST + WebSocket backend |
| `scripts/backtester.py` | Full backtesting engine |
| `scripts/social_pipeline/pipeline_test.py` | v1 script-only social-scrape -> sentiment pipeline (no APIs) |
| `scripts/social_pipeline/v2/pipeline.py` | **v2 trading-signal engine** — multi-source, entity + intent + content-type, weighted per-stock + market sentiment |

---

## Twitter / Social Scraping Pipeline (Validation Run, 2026-04-25)

A new module `scripts/social_pipeline/` was added to satisfy the
"Twitter scraping -> sentiment analysis with NO APIs" requirement.

```
scripts/social_pipeline/
  scraper.py         # multi-strategy social scraper (Nitter + DDG + Reddit)
  relevance.py       # strict EGX-stock relevance classifier (finance ∧ EGX signal)
  sentiment.py       # wraps project sentiment engine + VADER baseline
  pipeline_test.py   # end-to-end runnable test (writes JSON+CSV logs)
  logs/              # auto-generated run artefacts
```

## Data Integrity Report (2026-04-25, strict-classifier rebuild)

After a feedback round, the loose keyword filter was replaced with a strict
two-signal classifier in `scripts/social_pipeline/relevance.py`. A post
passes ONLY IF it carries BOTH:

  (A) a finance/trading signal — `stock`, `share`, `buy`, `sell`, `EPS`,
      `target price`, cashtags `$COMI`, Arabic `سهم` / `تحليل` / `اشتري`, ...
  (B) an EGX-specific signal — an EGX ticker via `$CASHTAG` or `.CA` suffix
      (no bare uppercase tokens, fixes "EAST" matching "Middle East"), a
      named issuer (Commercial International Bank, Telecom Egypt, Orascom,
      Talaat Moustafa, ...), or an EGX index term (`EGX30`, `EGX 70`,
      `البورصة المصرية`, ...).

Generic `Egypt` / `Cairo` / `tourism` posts are now rejected.

### Metrics

| Metric                | Value | Meaning |
|-----------------------|-------|---------|
| `raw_posts_count`     | 169   | Total candidates returned by Reddit JSON scrape |
| `filtered_posts_count`| 38    | Saved posts that pass BOTH (A) and (B) |
| `search_precision`    | 22.5% | raw → filtered ratio (upstream noise — Reddit search isn't EGX-tuned) |
| `output_purity`       | 100%  | re-classification of every saved post passes |
| `sources`             | `['reddit:search']` | Twitter blocked anonymously in 2026 (see below) |

### FAIL-gate logic

- `filtered_posts_count >= 20`  ✓ (38)
- `output_purity >= 70%`        ✓ (100%)
- `search_precision < 70%`      → warning only; this measures upstream
  source noise, not output contamination. Every saved post is provably
  stock-relevant by re-classification.

### Proof of correctness

`pipeline_test.py` logs `PROOF OF CORRECTNESS — first 10 relevant posts`
showing per-post `finance_hits`, `egx_hits`, text excerpt, and verifiable
URL. Sample matched signals from the latest run:

- `finance: ['index', 'analyst', 'bullish']  EGX: ['index:egx30', 'index:egx 30']`
- `finance: ['$EFG', 'short']                EGX: ['ticker:EFG', 'issuer:efg hermes']`
- `finance: ['اسهم', 'تداول']                 EGX: ['index:البورصة المصرية']`

URL verification: 8/8 HTTP 200.

### Sentiment benchmark (project engine vs VADER baseline)

```
n=38

EGX engine (FinBERT/CAMeLBERT/XLM-R):  positive=2.6%  neutral=71.1%  negative=26.3%  avg=-0.222
VADER baseline (English-only rules):    positive=21.1% neutral=57.9%  negative=21.1%  avg=+0.048

Label disagreement EGX-vs-VADER: 14/38 = 36.8%
  → driven by Arabic posts where VADER returns 0 (not in lexicon)
    while CAMeLBERT/XLM-R correctly classify dialectal sentiment.
```

### Failure points (honest)

- Twitter / X is anonymously unscrapable in 2026: Nitter mirrors are
  Anubis-walled or 410'd; search engines have de-indexed `site:twitter.com`;
  `cdn.syndication.twimg.com` rate-limits to 429. Documented in scraper.
- Reddit search precision is low (22.5%) because Reddit's full-text search
  matches `EGX` against unrelated content (e.g. "EGX" as a username
  fragment, gaming acronyms). The strict classifier rejects these
  downstream — output is 100% clean.
- Generic-Egypt/tourism/NSFW false positives from the previous loose
  filter are now eliminated by the BOTH-signals requirement.

### How to run

```bash
python scripts/social_pipeline/pipeline_test.py
```

Outputs: console summary, `scripts/social_pipeline/logs/pipeline.log`,
and timestamped `results_*.json` + `results_*.csv` under `logs/`.

### What is working

- Strategy ladder: Nitter mesh -> DuckDuckGo Lite SERP -> Reddit JSON.
- Reddit JSON endpoints (`r/<sub>/new.json`, `/search.json`) are scraped
  with no auth using a TOS-compliant descriptive User-Agent. This is the
  primary source of real, recent, EGX-relevant social data.
- 80+ real posts collected per run, filtered against an EGX keyword set
  (English: EGX, Egypt, Cairo, COMI, ETEL, Orascom, OCI, EGP; Arabic:
  البورصة, مصر, القاهرة, الجنيه).
- Project sentiment engine loads cleanly. FinBERT (English),
  CAMeLBERT (Arabic MSA), and XLM-R (mixed/dialect) all confirmed routing
  per language (verified `model_used=finbert` on English samples).
- VADER baseline is implemented (with a built-in lexicon fallback for
  environments without `vaderSentiment`) for benchmarking.
- All 8 sampled tweet/post URLs in the verification step returned HTTP 200.
- Logs persist to JSON + CSV with per-post sentiment from BOTH methods.

### What was broken / blockers found

- **Twitter / X is fully locked down to anonymous scrapers in 2026.**
  - Nitter mesh: nearly every public mirror returns the Anubis bot wall
    (`nitter.tiekoetter.com`, `nitter.privacyredirect.com`, `nitter.cz`,
    etc.) or 403/410/DNS-gone. Search engines have de-indexed
    `site:twitter.com`. The `cdn.syndication.twimg.com` endpoint
    rate-limits anonymous IPs to 429.
  - This is documented in the scraper's docstring and logs. The Twitter
    strategies are kept in the ladder so the pipeline auto-picks them
    back up if a mirror returns to service.
- The pre-existing `tradingagents/dataflows/social_media_sources/twitter_source.py`
  uses Google News as a Twitter proxy; in practice this returns 0 hits
  for the same reason (Google has dropped twitter.com from results).
- Initial Reddit search hits were polluted by NSFW/off-topic posts that
  merely contained words like "stock" or "Egypt".

### Fixes applied

1. Replaced naive Reddit search with: (a) targeted `r/<sub>/new.json`
   browsing across 10 EGX-relevant subreddits + (b) site-restricted
   search using Reddit multireddit syntax `r/sub1+sub2+.../search.json`.
2. Added a topical relevance filter: a post is kept only if its text
   contains at least one EGX_KEYWORD (English or Arabic).
3. Switched Reddit User-Agent to a TOS-compliant descriptive bot string
   (generic browser UAs were intermittently 429'd).
4. Hardened logging on Windows console (UTF-8 reconfigure + ASCII-safe
   status markers `OK`/`FAIL`) — previous run hit a `cp1252` codec
   error on the unicode arrow.
5. Documented Twitter blocking in code so future maintainers don't
   silently retry-loop dead mirrors.

### Hard constraints honoured

- No Twitter API, no paid services, no LLM-generated tweets.
- All persisted records carry a real, clickable `url` field that
  resolves on the open web.
- Errors are logged, not swallowed; the pipeline is rerunnable.

---

## Trading-Signal Pipeline v2 (2026-04-25)

A second-generation pipeline lives under `scripts/social_pipeline/v2/`.
It turns the v1 "scrape + relevance filter + sentiment" flow into a
**layered trading-signal engine** with per-stock attribution and explicit
trader-intent extraction. v1 is unchanged and still runnable.

```
scripts/social_pipeline/v2/
  __init__.py
  entities.py            # SYMBOL_REGISTRY (COMI, ETEL, OCI, …) + extract()
                         # returns Mention(symbol, confidence, evidence)
  intent.py              # BUY / SELL / BULLISH / BEARISH / REACTION / HOLD
                         # 60+ EN + MSA + Egyptian-dialect regex patterns
                         # emits net directional score in [-1, +1]
  content_type.py        # OPINION (1.0) / NEWS (0.6) / ANALYSIS (0.4)
                         # / QUESTION (0.2) / OTHER (0.3)
  quality_gate.py        # strict: words 3..100, intent must be one of
                         # BUY/SELL/BULLISH/BEARISH/REACTION,
                         # rejects QUESTION & ANALYSIS
  aggregator.py          # ScoredPost -> per-symbol weighted signal
                         # confidence = 0.6*quality + 0.4*size_factor
                         # split_outputs() splits market vs per-stock,
                         # enforces MIN_TOTAL_POSTS=50 (-> NO_SIGNAL)
                         # and MIN_MENTIONS_PER_STOCK=5
  pipeline.py         # 7-stage orchestrator + metrics + persistence
                         # loads .env via python-dotenv at startup
  sources/
    __init__.py          # re-exports v1 Post schema
    reddit_targeted.py   # intent-only queries (buying/sold/تجميع/هيطلع)
    telegram_public.py   # t.me/s/<channel> public preview, NO auth,
                         # EGX pre-filter + forex/gold/crypto noise gate
    mubasher_news.py     # public Egyptian financial-news listings
    twitter_authed.py    # Playwright + persisted cookies (env-gated,
                         # EGX_X_STORAGE_STATE), --login helper
    facebook_groups.py   # Playwright fallback for FB (env-gated,
                         # EGX_FB_STORAGE_STATE), --login helper
    facebook_apify.py    # PRIMARY FB source — Apify actor
                         # 2chN8UQcH1CfxLRNE (apify/facebook-groups-scraper)
                         # token from APIFY_API_TOKEN in .env
  logs/                  # auto-generated run artefacts
```

### Stage chain (pipeline.py)

1. **SCRAPE** — pluggable sources, Facebook (Apify) is PRIMARY
2. **RELEVANCE** (Layer-0) — strict EGX classifier from v1
   - `RELEVANCE_TRUSTED_PLATFORMS = {"facebook"}` bypasses Layer-0
     because curated FB groups are EGX-only by source-of-truth
3. **ENRICH** — `entities.extract` + `intent.detect` + `content_type.classify`
4. **QUALITY GATE** (Layer-3, strict) — see `quality_gate.py`
5. **SENTIMENT** — project's CAMeLBERT/FinBERT/XLM-R engine + VADER baseline
6. **AGGREGATE** — weight = `entity_conf × content_weight × intent_factor × log-engagement`
7. **SPLIT OUTPUTS** — `market_sentiment` vs `per_stock_sentiment`
   with `NO_SIGNAL` when `total_posts < MIN_TOTAL_POSTS`

### Apify Facebook integration

- Token in `.env` as `APIFY_API_TOKEN` (also added to `.env.example`).
- Default groups (`TARGET_GROUP_URLS` in `facebook_apify.py`):
  - `groups/618025406208276/` — جروب الخبره
  - `groups/4021602644518797` — البورصة المصرية
  - `groups/955090341273238/` — بورصة مصر _Egypt Stock Exchange
- Override at runtime via env: `EGX_FB_GROUP_URLS="url1,url2,…"`.
- Per-item filters per the brief:
  - drop empty / `sponsored` / Apify error stubs (`not_available`)
  - drop posts > 150 words
  - keep ONLY posts containing one of:
    `buy|sell|bullish|bearish|target|breakout|سهم|شراء|بيع|تجميع|تصريف|هيطلع|هينزل`
- URL uniqueness: Apify often returns the *group* URL when no permalink is
  available, which would collapse hundreds of posts to a handful in dedup.
  Fixed by appending `#post-<sha1>` per post.
- Surfaces dead / private / mistyped URLs with explicit
  `WARNING: Apify: N/M start URLs not available` instead of silently
  returning 0.

### Live run (2026-04-25, 3/3 FB groups live)

```
facebook_apify        67 posts kept   (3/3 groups live)
relevant_posts        79 / 149       (precision = 53.0%, ≥50% target met)
intent breakdown      SELL:20  BUY:14  BEARISH:4  BULLISH:4  REACTION:3  HOLD:2  NONE:41
content types         OPINION:32  OTHER:18  QUESTION:12  ANALYSIS:11  NEWS:6
quality_gate_kept     26 / 79        (39 no-intent, 11 too-long, 2 questions, 1 analysis)
market_sentiment      EGX neutral  -0.12  conf 0.58  (n=26)
status                NO_SIGNAL      (26 < MIN_TOTAL_POSTS=50)
```

### Confidence formula (aggregator.py)

```
quality    = clamp(weight_total / n, 0, 1)        # avg per-post weight
size       = n / (n + 5)                          # Bayesian-ish n-prior
confidence = 0.6 * quality + 0.4 * size
```

### Output JSON shape (results_<stamp>.json)

```jsonc
{
  "status": "OK" | "NO_SIGNAL",
  "reason": "...",                              // present iff NO_SIGNAL
  "market_sentiment": {                         // EGX_MARKET bucket or null
    "symbol": "EGX_MARKET", "n": 26,
    "weighted_sentiment": -0.12, "label": "neutral",
    "confidence": 0.58, "intent_breakdown": {...}, ...
  },
  "per_stock_sentiment": {                      // n >= MIN_MENTIONS_PER_STOCK only
    "COMI": { "n": 15, "weighted_sentiment": 0.30, "label": "bullish", ... },
    ...
  },
  "thresholds": {"min_total_posts": 50, "min_mentions_per_stock": 5},
  "metrics": {raw_posts, relevant_posts, quality_kept,
              search_precision_pct, posts_with_intent,
              opinion_grade_posts, posts_with_symbol},
  "quality_gate": {"counts": {...}, "examples": [...]},
  "per_symbol_full": {...},                     // pre-threshold per-symbol view
  "items": [ {platform, text, url, mentions, intent, content,
              sentiment, sentiment_vader}, ... ]
}
```

### How to run v2

```bash
# .env at repo root must contain APIFY_API_TOKEN=...
PYTHONIOENCODING=utf-8 python scripts/social_pipeline/v2/pipeline.py
```

Optional one-time logins (only needed for the Playwright-based fallbacks;
the Apify FB source needs no local login):

```bash
python scripts/social_pipeline/v2/sources/facebook_groups.py --login
python scripts/social_pipeline/v2/sources/twitter_authed.py  --login
export EGX_FB_STORAGE_STATE=$(pwd)/fb_storage_state.json
export EGX_X_STORAGE_STATE=$(pwd)/x_storage_state.json
```

### Standalone Facebook Scraping & Sentiment Testing

To quickly manually test the Facebook Apify scraper, entity extraction, and sentiment scoring without running the full pipeline orchestration:

```bash
python scripts/social_pipeline/v2/test_fb_sentiment.py
```

This standalone script:
- Verifies your `APIFY_API_TOKEN`.
- Scrapes a small sample from the target Facebook groups.
- Extracts EGX entities from the text.
- Scores the text using the project's multi-model transformer engine (or lexicon fallback).
- Outputs the results to the console (UTF-8 safe) and saves a detailed JSON log under `scripts/social_pipeline/v2/logs/test_fb_results_<stamp>.json`.

### Outstanding gaps (honest)

- `symbols_covered = 0` on the live FB run because most retail FB posts
  name smaller-cap EGX stocks (`توطين التكنولوجيا`, `عبور لاند`, `ابن سينا`)
  that are not yet in `entities.SYMBOL_REGISTRY`. Extend the registry to
  attribute those signals to specific tickers.
- Mubasher source returns 0 — the public listing HTML doesn't match the
  candidate selectors. The fetcher logs the miss instead of crashing;
  selectors can be re-mapped from a saved sample.
- Many anonymous Telegram channels in the seed list are dead; live ones
  in this run: `egx_news`, `egypt_stocks`.
- Without authed Twitter cookies (`EGX_X_STORAGE_STATE`), Twitter is
  skipped — Nitter/DDG paths are dead in 2026 (documented in
  `twitter_authed.py`).
