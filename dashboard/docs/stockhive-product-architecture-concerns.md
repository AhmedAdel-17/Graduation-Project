# StockHive Product and Architecture Concerns

This note captures the current product, branding, and architecture concerns
around the dashboard. These are concerns and suggestions, not final
requirements. The next agent should critique them and propose a better design if
the codebase suggests one.

## Brand Direction

The project has been renamed to **StockHive**.

The UI should be built around this identity, not around the older
"EGX Intelligence" / "Research console" language.

The banner direction is:

> StockHive  
> Intelligence · Insight · Invested

Product description:

> An AI-powered equity analysis platform for the Egyptian Exchange, driven by a
> collaborative multi-agent trading workflow.

This gives the app a clearer product identity. The app should feel like a
polished recommendation platform, not a raw developer dashboard.

## Visual Identity

The banner suggests the following visual language:

- soft mint / pale blue backgrounds
- dark navy primary text
- emerald green accent color
- white or very light cards
- thin borders
- calm academic-fintech feeling
- clean spacing
- trustworthy, analytical, not flashy

The current dashboard should gradually move toward this StockHive identity.

Suggested palette direction:

- Primary navy: deep blue/navy for text, headings, logo, and serious actions
- Accent green: emerald/mint for status, highlights, active states, and data
  confidence
- Background: pale mint-blue or very light cool gradient
- Surfaces: white / near-white cards
- Warnings: restrained amber
- Errors: restrained red

Avoid making the interface too dark, too generic, or too much like an internal
operations dashboard on user-facing pages.

## Naming Changes

Replace old product wording:

- "EGX Intelligence" -> "StockHive"
- "Research console" -> "EGX equity intelligence" or "AI equity insights"
- "Run full pipeline" -> user-facing "Get recommendation"
- "Shadow Portfolio" -> "Decisions" or "Recommendations"
- "History" -> "Audit Log" in developer context

The user-facing product should use recommendation language.

Developer/internal pages can still use technical terms such as pipeline,
sessions, traces, metrics, Prometheus, and Grafana.

## Home Page Direction

The Home page should feel like the user's entry point into StockHive.

Suggested hero copy:

> Hello Omar  
> Your next EGX insight.

This is short, matches the slogan, and avoids implying that the app executes
trades.

The main button should say:

> Get recommendation

Not:

- Run full pipeline
- Start pipeline
- Execute analysis

Those are developer terms.

The Home page should show:

- active user greeting
- today's recommendation entry point
- current portfolio snapshot
- latest recommendation
- market/data status
- watchlist or selected ticker

The Home page should not feel like a model testing panel.

## Decisions Page Direction

The Decisions page should feel like the user's past recommendation history.

Possible labels:

- "Your past recommendations"
- "Recommendation history"
- "Past StockHive insights"

Each recommendation should make clear:

- ticker
- recommendation: BUY / HOLD / SELL
- confidence
- price used
- date/time recommended
- why this was recommended
- portfolio context used
- data freshness / data coverage
- user feedback, if available

It should not emphasize:

- model provider
- raw session IDs
- filesystem paths
- pipeline internals
- backtest mode
- profile IDs

Those details belong in Developer pages.

## Product Positioning

StockHive is a recommender. It should never imply that it executes trades.

Use language like:

- recommendation
- suggested action
- decision
- insight
- portfolio-aware recommendation
- recommendation history
- data used
- feedback

Avoid language like:

- executed trade
- order
- fill
- position opened
- trading account execution

The system can still connect to a user's portfolio. The portfolio should inform
recommendations, risk checks, sizing suggestions, and explanations. But the app
should not claim that it actually trades.

## Core Concern

The dashboard is starting to look personalized, but the underlying system is not
fully personalized yet.

Right now the user can see a profile/persona such as Omar or Amr Hassan, but the
trading pipeline does not clearly receive that profile as structured context.
That creates a mismatch: the UI feels like it belongs to a specific user, while
the recommendation may still be generated as a generic analysis.

The app should feel like a real user is opening a recommendation product, not a
developer dashboard with a persona attached.

## Profile Abstraction Concern

The profile should not be hardcoded in prompts.

The system should use a generic investor context abstraction that can work for
any future user, not just the demo persona.

Recommended conceptual split:

### Investor Profile

Stable preferences and constraints:

- risk tolerance
- investment horizon
- trading style
- sector preferences
- sector exclusions
- benchmark
- max single-position exposure
- liquidity preferences

### Portfolio State

Dynamic account reality:

- current portfolio value
- cash
- holdings
- existing exposure by ticker
- existing exposure by sector
- unrealized P&L if available
- buying power

### Feedback Memory

User preference feedback accumulated over time:

- "too conservative"
- "too risky"
- "avoid banks"
- "prefer clearer entry/exit levels"
- "I agree with this reasoning"

Raw feedback should be stored, but future prompts should receive a summarized
feedback memory rather than every raw comment.

### Investment Policy / Constraints

Explicit rules the system must respect:

- max position size
- max sector concentration
- allowed universe
- long-only constraint
- no execution permission
- recommendation-only mode

## Proposed Request Flow

A scalable flow should look roughly like:

1. Frontend sends an analysis/recommendation request with `profile_id` or
   `user_id`.
2. API loads the user profile.
3. API loads the latest portfolio snapshot.
4. API loads or summarizes recent user feedback.
5. API builds an `InvestorContext` object.
6. Graph initial state receives `InvestorContext`.
7. Relevant agents use that context.
8. The recommendation record stores:
   - `profile_id`
   - portfolio snapshot used
   - profile snapshot used
   - feedback summary used
   - recommendation output

This makes the system auditable. If the user asks why a recommendation happened,
we can show exactly what context the agent saw.

## Which Agents Should Use Investor Context

Do not blindly inject the profile into every prompt.

Suggested division:

- Market analyst: mostly profile-neutral
- Fundamentals analyst: mostly profile-neutral
- News analyst: mostly profile-neutral
- Social analyst: mostly profile-neutral
- Research manager: lightly profile-aware, to weigh what matters for the user
- Trader: strongly profile-aware, because recommendation/action should fit the
  user's mandate
- Risk manager / risk judge: strongly profile-aware and portfolio-aware
- Position sizing logic: strongly portfolio-aware

The factual analysts should answer "what is true about this stock?" The trader
and risk stages should answer "what should this user do with that information?"

## Portfolio Connection

The app should connect recommendations to portfolio state without pretending to
execute trades.

Example:

> Recommendation: HOLD. The stock has a reasonable fundamental case, but your
> current portfolio is already highly exposed to banks, so the system does not
> recommend adding more exposure now.

This is much more real than a generic HOLD because it explains the decision in
the user's actual context.

## Feedback Feature

A feedback feature is a strong idea.

After each recommendation, the user should be able to leave feedback such as:

- agree
- disagree
- too risky
- too conservative
- not enough evidence
- avoid this sector
- useful reasoning
- custom comment

The feedback should not immediately become raw prompt text. It should be stored
and later summarized into a compact preference memory.

Possible flow:

1. User gives feedback on a recommendation.
2. Feedback is stored with `user_id`, `profile_id`, `ticker`, and
   `recommendation_id`.
3. A summarizer updates a short feedback memory.
4. Future recommendations receive the summarized feedback memory.

## Current Concerns To Verify

These still need careful verification before the UI should present itself as a
fully personalized recommender:

- Are shadow/live recommendation requests passing a real `profile_id`?
- Why are some shadow runs recorded with `profile_id = null`?
- Why are some shadow runs recorded with `backtest_mode = true`?
- Is the profile injected into graph state?
- Is portfolio state available to the graph?
- Are trader/risk prompts actually using the investor context?
- Are all three current recommendations HOLD because of expected conservatism,
  or because fallbacks/defaults are too HOLD-biased?
- Is sparse news/social data pushing every ticker toward HOLD?
- Does the UI explain HOLD using actual blockers, or a generic message?

## Shadow Run Diagnostic Findings

On 2026-06-21, five additional sector-diverse shadow diagnostic runs were
started to test whether the system was only returning HOLD for the original
tickers or whether HOLD was a broader pattern.

Additional tickers:

- `SWDY.CA`
- `EAST.CA`
- `TMGH.CA`
- `ADIB.CA`
- `EFIH.CA`

Results:

| Ticker | Signal | Confidence | Status | profile_id | backtest_mode |
|---|---:|---:|---|---|---|
| `SWDY.CA` | HOLD | 64.8% | completed | `None` | `True` |
| `EAST.CA` | HOLD | 72.6% | completed | `None` | `True` |
| `TMGH.CA` | HOLD | 70.7% | completed | `None` | `True` |
| `ADIB.CA` | HOLD | 64.4% | completed | `None` | `True` |
| `EFIH.CA` | HOLD | 68.1% | completed | `None` | `True` |

Combined with the earlier `COMI.CA`, `ETEL.CA`, and `FWRY.CA` runs, the current
observed pattern is:

> 8 / 8 shadow runs returned HOLD.

This is enough evidence to stop running more diagnostic shadow runs for now.
More runs are unlikely to teach much until the underlying issues are addressed.

### Confirmed Issues From The Batch

The latest shadow runs confirm two important architecture problems:

- `profile_id = None`
- `backtest_mode = True`

This means the current "shadow" recommendations are not yet attached to the
active investor profile and are still being recorded as backtest-mode runs.

That matters because the Decisions page may look user-specific, but the backend
record does not yet prove that the recommendation was generated for that user.

### Repeated Operational Pattern

Across the batch, logs repeatedly showed:

- social sentiment prefetch timeout
- yfinance fallback for market data
- memory embedding mismatch: `384 vs 768`
- BM25 fallback for memory retrieval
- DeepSeek calls succeeding

The provider/LLM path appears to work, but social data and memory retrieval are
not clean. These issues may contribute to conservative HOLD behavior.

### HOLD Interpretation

The HOLD pattern is probably not only a UI issue. It may come from a combination
of:

- conservative research manager/trader/risk prompts
- missing or sparse social/news signal
- fallback behavior that defaults to HOLD
- risk-stage caution
- no investor profile or portfolio context influencing the final decision
- shadow requests accidentally running with `backtest_mode = True`

One run, `TMGH.CA`, returned HOLD with high confidence. This means HOLD is not
only caused by low confidence. The system can be confidently conservative.

### Recommended Stop Condition

Do not run more shadow diagnostics until these are fixed:

1. Shadow/recommendation requests should pass a real `profile_id`.
2. Shadow/recommendation requests should not use `backtest_mode = True` unless
   intentionally testing.
3. The pipeline should receive an `InvestorContext`.
4. The recommendation record should store the profile and portfolio snapshot
   used.
5. HOLD explanations should use actual blockers, not generic copy.

After those fixes, rerun only 2-3 tickers and compare against the previous HOLD
baseline.

## UI Concerns

Current direction is better than before, but the user experience still does not
fully feel real.

Concerns:

- The Home page should greet the active user and show their context.
- The Decisions page should say "your past recommendations," not feel like a
  run log.
- Developer phrases should be hidden from user pages.
- A profile/persona should not feel decorative; it should affect the
  recommendation.
- A recommendation should show what portfolio context was used.
- A HOLD recommendation should show concrete reasons, not only "the system is
  conservative."
- The dashboard should be honest that this is recommendation-only.
- The StockHive name, colors, and slogan should be reflected throughout the UI.

## Suggested Next Step

Before more visual polish, implement the backend truth:

1. Add an `InvestorContext` abstraction.
2. Pass it into graph state.
3. Store profile and portfolio snapshots with each recommendation.
4. Update trader and risk prompts to use it.
5. Add user feedback storage.
6. Then update Home and Decisions to show a real StockHive user-facing
   recommendation experience.

The dashboard should not merely look personalized. The recommendation should
actually be generated with the user's profile, portfolio, and feedback context.
