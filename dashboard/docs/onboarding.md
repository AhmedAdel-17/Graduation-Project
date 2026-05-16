# Analyst Onboarding

> A 20-minute walkthrough of the EGX research console for an analyst or PM
> getting hands-on for the first time.

The console is an **AI-augmented research tool**. It does not place orders.
Every recommendation must be reviewed by a human PM before any trade is
executed.

---

## 1. Open the dashboard

1. Make sure the backend is running: `uvicorn server.api_server:app --reload`.
2. Start the dashboard: `cd dashboard && npm run dev` → open
   <http://localhost:5173>.
3. The first page you'll see is a **disclaimer modal**. Read it, click
   *I understand*. The disclaimer ribbon stays at the top of every page —
   that's deliberate, not a bug.
4. In the TopBar, check the **API status pill** (right side). Green means
   the FastAPI server is reachable. If it's red, fix the backend first.

---

## 2. Pick a ticker

The default landing page is `/workspace`. The shell exposes six tabs across
the top — Overview / Reasoning / Fundamentals / Sentiment / Memory / History.

In the StockSelector at the top, type a ticker (e.g. `COMI.CA`). The page
will:

- Refresh the **Overview** tab with price history + latest signal.
- Pre-load the rest of the tabs (they share a cached `/api/sessions/:id/trace`
  fetch via `useLatestSessionTrace`).

> The selected ticker is persisted to `localStorage` — your next visit
> defaults to the last ticker you used.

---

## 3. Read the latest agent run

Click into **Reasoning**. The tab groups the LangGraph nodes by phase:

```
Analysts        →  Market · Fundamentals · News · Social
Research        →  Bull · Bear · Research Manager
Execution       →  Trader
Risk debate     →  Risky · Safe · Neutral · Merged
Risk decision   →  Risk Manager (final)
```

Each row carries:

- The **prompt ID** (e.g. `P-RESMGR v2`) — clickable into `PROMPTS.md` for
  the exact text of the prompt that produced this answer.
- The **structured output JSON** rendered with the `JSONViewer`.
- The **model fingerprint** — `weights_sha256_16` (if present), provider,
  temperature. Use this to reproduce the run later.
- A **parse-failed badge** (magenta) when the regex fallback was used
  instead of clean JSON.

**Fundamentals** tab — full Phase 1A/1B report (14 ratios + sector
benchmarks + distress flags + signal-coherence gauge + fair-value range).

**Sentiment** tab — Layers A0/A/B/C/E. The NO_SIGNAL gates are listed
explicitly with the failed-gate reason, so you can tell when a "neutral"
verdict is actually "we don't have enough data."

**Memory** tab — five collections (`bull_memory`, `bear_memory`,
`trader_memory`, `invest_judge_memory`, `risk_manager_memory`). The
threshold slider (default 0.30) is the `min_similarity` floor — anything
below is shown greyed-out as "below threshold."

**History** tab — every past session for this ticker, linked to the audit
trail.

---

## 4. Trigger a fresh run

Two paths:

### 4.1 Live multi-agent run (`/run`)

Click **Live Run** in the sidebar. Set the ticker, the trade date, and the
analysts you want included, then click **Start analysis**. You'll see:

- **Left: ReasoningCanvas** — streams the current node's structured
  output and report tab-by-tab.
- **Right: AgentTimeline** — 13 nodes light up `idle → in_progress →
  completed` (or `error`).
- **Top ribbon** — connection state, event count, last-message age.

Click **Cancel** to abort. On `complete` the page emits a toast — go back
to `/sessions` to see the audit row.

### 4.2 Quick prediction (legacy)

`/predict` still works. Faster (no full graph), less informative — used by
the Universe page's bulk action.

---

## 5. Browse the universe

Click **Universe** in the sidebar. The EGX-30 names are grouped by sector.
For each ticker you'll see:

- Sector + liquidity tier (MEGA / MID / SMALL)
- The timestamp of the last agent run
- A clickable session count

**Bulk-run:** check up to 10 tickers, click *Quick-predict N*. The page
fires `POST /api/test/random-egx` sequentially and shows a progress
ribbon. When it finishes, the resultsIndex cache is invalidated so the
"Last analyzed" column refreshes.

---

## 6. Replay a session

`/sessions` is the read-only audit log. Filters: ticker (free text),
session-id substring. Click a row to land on `/sessions/:id` — same
Reasoning panel as the Workspace tab, frozen in time, with buttons to
view the raw JSONL and the markdown summary.

This is the surface you reach for when an analyst asks "what made the
system say BUY on COMI two weeks ago?" Combine with the **model fingerprint
in the row header** to know exactly which model checkpoint produced the
answer.

---

## 7. Backtest a strategy

`/backtest` is the Lab. Click **New backtest** to configure a run:

- Choose a ticker + a preset window (2w, 1m, 3m, 6m, 1y).
- Set initial capital (default 1,000,000 EGP).
- Pick analyst agents to include.
- Click **Run Multi-Agent** for the LLM strategy, or **Run Classical
  Benchmark** for the rules-based Backtrader baseline.

The page polls `/api/backtests` every 5 s while pending. When the run
finishes, the toast fires and you're redirected to the run's detail
page.

**Detail page** — 8 KPIs (Total Return, Sharpe, Drawdown, etc.), the
equity curve with benchmark overlay, and a per-trade table. If
`RL_META_POLICY_ENABLED=1` server-side, the trades carry three extra
columns (RL mult / RL idx / RL fingerprint) and a separate RL activity
card lists every `rl_meta_size_adjustment` event for the session.

**Compare** — `/backtest/compare?ticker=COMI.CA` puts LLM vs Backtrader
side-by-side, with a winner-per-metric matrix.

---

## 8. Watch the system

`/diagnostics`:

- **Health** — full `/api/health` payload with degraded-reason list.
- **Memory** — per-collection seed status (green = seeded, gray = not).
- **Persistence** — Postgres reachability + audit-write lag.
- **Streaming** — Redis + RL flag visibility.
- **RL meta-policy** — current model_fingerprint of the loaded
  checkpoint (or `loaded=false` if it failed).
- **Model fingerprint drift** — distinct fingerprints seen in
  `agent_events` over the past 7/30/90 days. **Amber bars** in the
  sparkline highlight days with mixed fingerprints (a possible audit
  flag — somebody re-pointed the LLM mid-day).
- **Prompt registry** — every P-ID in `PROMPTS.md` with line numbers.

`/settings`:

- Mutable form: backend URL, deep/quick model IDs, target market.
- Redacted API-key display + vendor map (read-only).
- RL status (read-only — flag is server-controlled).
- Locale toggle (EN / AR).
- EGX risk limits JSON viewer.
- Build identity (git SHA, LLM provider, online tools flag).

---

## 9. Audit gauntlet (manual smoke test)

If you touched the dashboard, run this in order before merging:

1. **Build green**: `npm run lint && npm run build`.
2. **Backend green**: `python -m pytest tests/test_api_session_trace.py tests/test_api_memory.py tests/test_api_rl.py tests/test_api_diagnostics.py -v`.
3. **Smoke**: `uvicorn server.api_server:app --reload --port 8000` + `npm run dev`.
4. **Top bar**: green health pill, locale toggle works, disclaimer modal shows once and dismisses.
5. **/workspace**: pick COMI.CA, all six tabs render without errors.
6. **/run**: pick COMI.CA, today, market+fundamentals only, click Start. Timeline lights up, canvas streams. Cancel mid-flight; status drops to `closed`.
7. **/sessions**: filter `COMI`, open one row, see the audit trail.
8. **/universe**: search `ETEL`, run a quick-predict, watch the toast.
9. **/backtest/new**: 3m preset on COMI.CA, click Run Classical Benchmark. Confirm `backtest_runs` row exists, equity curve renders.
10. **/diagnostics**: degraded reasons sane, prompts count = 21, fingerprints either populated or "Postgres unavailable".
11. **/settings**: change deep-think model, save, see toast, refresh, verify config persisted.
12. **RTL**: toggle locale to العربية. Page mirrors; numbers and charts stay LTR.

If anything is red, **stop**.

---

## 10. Reading the disclaimer

You'll see this snippet at every page footer:

> Research tool — not investment advice. Decisions require human review.

That's not boilerplate. The known issues in `MEMORY.md` §B (LLM
non-determinism), §C (look-ahead in the backtester), and §N (regex signal
extraction) mean that **any numeric output you see here is subject to
reproducibility caveats**. The system is a research console, not a
regulator-cleared trader. Treat its recommendations as inputs to your own
analysis, not outputs.
