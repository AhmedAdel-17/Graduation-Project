# Discussion Q&A Cheat Sheet

Concise, evidence-backed answers to likely panel questions. Every claim includes a file:line pointer so you can pull up the code live if asked.

---

## Q1: "How is this different from just asking ChatGPT?"

**Answer:** Five architectural differences:

1. **Multi-agent debate, not single-prompt.** A Bull Researcher and Bear Researcher argue over the same evidence in alternating rounds (`graph/conditional_logic.py:78-87`), then a Research Manager judges the debate. ChatGPT has no adversarial self-challenge.

2. **Deterministic guardrails override LLM output.** The Risk Scorer (`agents/risk_mgmt/risk_scorer.py`) has 13 hard checks (short-selling, leverage, liquidity, stop-loss, price band, etc.) that can VETO any LLM recommendation. The Signal Processor (`graph/signal_processing.py`) extracts BUY/SELL/HOLD via regex — the LLM never directly executes a trade.

3. **Specialized transformer models, not just GPT.** FinBERT for English financial sentiment, CAMeLBERT-DA for Arabic dialect sentiment, XLM-R as fallback — routed by `utils/sentiment_engine.py`. These are domain-fine-tuned models, not general-purpose.

4. **Real data grounding.** Tools fetch live OHLCV from yfinance, fundamentals from local EGX CSVs, news from RSS/NewsAPI/Google, social posts from Facebook/Reddit/Telegram. The LLM reasons over real data, not training-set memory.

5. **6 of 15 nodes are fully deterministic** (zero LLM calls) — Market Analyst, Fundamentals, Liquidity, Regime, Risk Scorer, Signal Processor. See `agent_docs/agent_behavior_tiers.md`. This means ~40% of the pipeline is reproducible, zero-cost, and instant.

---

## Q2: "How do you prevent the LLM from hallucinating a BUY signal?"

**Answer:** Three layers:

1. **Signal extraction is regex, not LLM.** `SignalProcessor` (`graph/signal_processing.py:7-64`) uses compiled regex in strict priority: VETO keyword -> JSON `"action"` field -> `FINAL TRANSACTION PROPOSAL` marker -> bare keyword -> fallback HOLD. The LLM never decides the final signal directly.

2. **Deterministic risk veto.** Even if the LLM says BUY, the Risk Scorer checks 13 constraints (`risk_scorer.py:45-74`). A short-sell attempt, leverage use, or >10% ADV position triggers an immediate VETO that cannot be overridden. Code path: `risk_scorer.py:472-540`.

3. **Confidence propagation with quorum.** At least 2 of 3 directional analysts must report a confidence score, or the system returns `INSUFFICIENT_DATA` with confidence floor of 0.10 (`propagation.py:243-253`). The weakest analyst drags down the combined score (30% min + 70% avg, line 260).

4. **Prompt injection sanitizer.** All external text (news, social posts) passes through `sanitize_external_text()` (`agents/utils/input_sanitizer.py`) before entering LLM prompts — strips HTML, neutralizes 12 injection patterns, entity-decode-safe.

---

## Q3: "What happens if all data sources fail?"

**Answer:** Graceful degradation at every level:

- **News:** Aggregator tries RSS feeds -> NewsAPI -> Google News -> local CSV, in order (`dataflows/news_providers/`). If all fail, news analyst reports 0 articles; silence penalty reduces confidence by 40 points (`news_analyst.py:336-348`).

- **Social media:** Aggregator tries Facebook Apify -> Reddit -> Telegram -> Mubasher News. If all fail, social analyst outputs `NO_SIGNAL` and the sentiment blend passes through confidence=1.0 / position-size=1.0 (no damage).

- **Fundamentals pipeline:** 3-stage CoT with fallback chain (`fundamentals/pipeline.py:1-21`):
  - Stage 1 fails -> return raw deterministic ratios
  - Stage 2 fails -> return Stage 1 + health heuristic
  - Stage 3 fails -> return Stage 1 + Stage 2 concepts
  - All succeed -> full CoT thesis

- **Market data (OHLCV):** yfinance primary -> EODHD fallback, managed by `DataGateway` (`dataflows/gateway.py`).

- **Quorum rule:** If fewer than 2 analysts produce confidence scores, the system returns `INSUFFICIENT_DATA` rather than a false-confidence recommendation (`propagation.py:245-253`).

---

## Q4: "Show me your backtest results — are they trustworthy?"

**Answer (honest):** The backtest results have **known limitations** that we document openly:

1. **Look-ahead in evaluation.** `_evaluate_trade_outcomes` in `scripts/backtester.py` uses future prices to evaluate trades. This means reported Sharpe/alpha numbers are overstated. This is documented in `MEMORY.md` as a known issue.

2. **Reflection runs inside the loop.** `reflect_and_remember()` is called per trade, but in `backtest_mode` it queues reflections instead of writing to memory immediately (`trading_graph.py:380-385`). This mitigates but doesn't fully eliminate the look-ahead risk.

3. **LLM non-determinism.** Most `.invoke()` calls pin `temperature=0` and `seed=42` (17/18 compliant), but LLM outputs are inherently non-deterministic across providers. Results are approximately but not exactly reproducible.

**What we DO have:** A classical baseline (`scripts/bt_benchmark.py`) using Backtrader with RSI/MACD/Bollinger Band strategies that has zero look-ahead. We can compare the LLM system's directional accuracy against this baseline.

**Framing:** This is a research prototype demonstrating the architecture. The backtest shows the system produces reasonable signals, but we don't claim production-grade alpha measurement.

---

## Q5: "How does the bull/bear debate work?"

**Answer:**

1. **Fan-out:** All 4-7 analysts run in parallel (Market, Fundamentals, News, Social, optionally Macro/Liquidity/Regime). Each writes its report to the shared `AgentState` dict.

2. **Bull Researcher** reads all analyst reports + retrieves similar past situations from vector memory (ChromaDB), then argues a bullish thesis.

3. **Bear Researcher** reads the same reports + the bull's argument, then argues a bearish thesis.

4. **Alternating rounds:** Bull and Bear alternate for up to `max_debate_rounds` rounds (default 1 round = 2 messages). Routing logic at `conditional_logic.py:78-87`.

5. **Research Manager** (judge) reads the full debate transcript and emits an investment decision with reasoning.

6. **Trader** translates the decision into a concrete execution plan with position sizing.

7. **Risk pipeline:** Risk Scorer (deterministic) -> if not vetoed -> Merged Risk Debate (3-perspective LLM: risky/safe/neutral, up to `max_risk_discuss_rounds`) -> Risk Manager (final judge with 21 EGX-specific clauses).

---

## Q6: "Why Egyptian Exchange specifically? What's different about EGX?"

**Answer:**

- **Regulatory constraints are hard-coded.** Long-only (no short selling), no leverage, +/-10% daily price limit circuit breaker, T+2 settlement. All enforced in `risk_scorer.py:45-74` as `EGX_RISK_LIMITS`.

- **Bilingual NLP is required.** Retail investor discussion happens in Arabic (MSA + Egyptian dialect). The system routes through CAMeLBERT-DA for Arabic and FinBERT for English (`utils/sentiment_engine.py`). Text preprocessing handles hamza normalization, diacritic removal, and Egyptian dialect patterns (`utils/text_preprocessor.py`).

- **Low liquidity market.** Many EGX stocks trade <50K shares/day. The system has two-tier liquidity checks: THROTTLE at 5% ADV participation, hard VETO at 10% ADV (`risk_scorer.py:52-53`). Position sizing respects `max_days_to_exit=10` (Almgren-Chriss characteristic time).

- **Local data sources.** Fundamentals come from EGX Annex 5 PDF disclosures parsed into CSVs. News comes from Arabic RSS feeds, local CSV files, and bilingual NewsAPI queries. Social data includes Facebook Groups and Telegram channels popular with Egyptian retail investors.

---

## Q7: "What's the role of each agent? How many are there?"

**Answer:** 15 nodes in 4 tiers. See `agent_docs/agent_behavior_tiers.md` for the full table. Quick summary:

| Tier | Count | Examples | LLM Calls |
|---|---|---|---|
| Deterministic | 6 | Market Analyst, Fundamentals, Risk Scorer, Signal Processor | 0 |
| Hybrid | 1 | Macro Analyst (deterministic + optional LLM narrative) | 0-1 |
| RAG-augmented | 3 | Bull/Bear Researcher, Trader (vector memory retrieval) | 1 each |
| Agentic | 5 | News/Social Analyst, Research/Risk Manager, Risk Debator | 1-3 each |

**Key insight:** The expensive LLM calls are concentrated in unstructured-input nodes (news analysis, debate judging). Structured data flows through deterministic nodes at zero cost.

---

## Q8: "How do you handle prompt injection from external data?"

**Answer:**

All external text entering LLM prompts passes through `sanitize_external_text()` (`agents/utils/input_sanitizer.py`):

1. **Injection detection (pass 1)** — catches `<system>` tags before HTML stripping
2. **HTML entity decode** — `&#111;` -> `o`
3. **Injection detection (pass 2)** — catches entity-encoded injection phrases
4. **HTML tag stripping** — removes `<script>`, `<b>`, etc.
5. **Truncation** — max 4000 chars
6. **Whitespace normalization**

12 injection patterns are detected (case-insensitive): "ignore previous instructions", "system:", `<system>`, "you are now a", "override instructions", etc.

**Two protected paths:**
- **Prefetch path:** Data sanitized + wrapped in `[EXTERNAL_CONTENT (label)]...[/EXTERNAL_CONTENT]` delimiters before prompt interpolation (news_analyst.py:165-168, social_media_analyst.py:362-365)
- **Tool-output path:** All 6 tool return values wrapped with `sanitize_external_text()` at the tool level (news_data_tools.py, social_media_tools.py)

**40 tests** cover normal text preservation, injection neutralization (including entity-encoded), HTML stripping, truncation, and end-to-end integration for both paths.

---

## Q9: "What course concepts does this implement?"

**Key mappings** (with code evidence):

| Concept | Implementation | Evidence |
|---|---|---|
| Multi-agent systems | 15-node LangGraph StateGraph | `graph/setup.py` |
| Agent communication | Shared typed state dict (no direct imports) | `agents/utils/agent_states.py` |
| Adversarial debate | Bull/Bear researcher alternating rounds | `conditional_logic.py:78-87` |
| RAG (Retrieval-Augmented Generation) | Vector memory retrieval for researchers | `agents/utils/memory.py` (ChromaDB) |
| Tool use / function calling | News + Social analysts call tools autonomously | `PerAnalystToolNode` at `graph/setup.py:14-32` |
| Deterministic shielding | Risk Scorer hard veto before LLM | `risk_scorer.py` (Alshiekh et al. pattern) |
| Sentiment analysis | Domain-specific transformers (FinBERT, CAMeLBERT-DA) | `utils/sentiment_engine.py` |
| Prompt injection defense | Input sanitizer on all external text | `agents/utils/input_sanitizer.py` |
| Graceful degradation | Fallback chains at every data layer | `fundamentals/pipeline.py`, news aggregator |
| Confidence calibration | Quorum rule + weakest-link dampening | `graph/propagation.py:150-284` |

---

## Q10: "What are the known limitations? What would you do differently?"

**Answer (honest, shows maturity):**

1. **Backtest look-ahead** — evaluation function uses future prices. Fix: evaluate only at T+N with lagged data.
2. **LLM non-determinism** — temperature=0 + seed helps but doesn't guarantee identical outputs across providers.
3. **Symbol registry too narrow** — social media entity extraction misses small-cap EGX names not in the registry.
4. **No live execution** — this is a research tool, not a trading bot. No broker API integration.
5. **Token cost** — a full run with all analysts + debate costs ~$0.15-0.50 in DeepSeek tokens. Not viable for high-frequency use.
6. **Memory cold start** — vector memory is empty on first run; researchers have no past situations to retrieve.

**What I'd do differently:** Start with the deterministic pipeline only (fundamentals + risk scorer), prove alpha there, then layer on LLM agents one at a time with A/B testing against the baseline.
