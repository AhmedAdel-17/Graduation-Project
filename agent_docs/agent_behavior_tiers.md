# Agent Behavior Tiers

The system mixes deterministic, hybrid, RAG-augmented, and fully agentic nodes within a single LangGraph StateGraph. This is intentional: deterministic nodes save cost and guarantee reproducibility where input is structured; agentic nodes are used only where input is unstructured and requires LLM judgment.

All nodes communicate exclusively through the typed `AgentState` dict (`agents/utils/agent_states.py`). No agent imports or calls another agent directly.

---

## Node Classification

| Node | Tier | LLM Calls | Autonomous Tool Use | Memory Retrieval | Code Evidence |
|---|---|---|---|---|---|
| Market Analyst (EGX) | Deterministic | 0 | No | No | `agents/analysts/market_analyst.py` — `create_deterministic_market_analyst()` |
| Fundamentals Analyst | Deterministic | 0 (default mode) | No | No | `agents/analysts/fundamentals/pipeline.py` — 3-stage CoT, all deterministic unless hybrid mode enabled |
| Liquidity Analyst | Deterministic | 0 | No | No | `agents/analysts/liquidity_analyst.py` — ADV, bid-ask proxy, volume profile |
| Regime Analyst | Deterministic | 0 | No | No | `agents/analysts/regime_analyst.py` — trend/volatility/breadth classification |
| Risk Scorer | Deterministic | 0 | No | No | `agents/risk_mgmt/risk_scorer.py` — `EGX_RISK_LIMITS` hard veto checks |
| Signal Processor | Deterministic | 0 | No | No | `graph/signal_processing.py` — regex-only BUY/SELL/HOLD extraction |
| Macro Analyst | Hybrid | 0-1 (opt-in) | No | No | `agents/analysts/macro_analyst.py` — deterministic data + optional LLM narrative (line 557) |
| News Analyst | Agentic | 1-3 | Yes (ToolNode) | No | `agents/analysts/news_analyst.py` — LLM selects and calls news tools autonomously |
| Social Media Analyst | Agentic | 1-2 | Yes (ToolNode) | No | `agents/analysts/social_media_analyst.py` — LLM calls social tools; Layer C pre-LLM gate can skip LLM entirely |
| Bull Researcher | RAG-augmented | 1 | No | Yes (vector similarity) | `agents/researchers/bull_researcher.py` — queries `FinancialSituationMemory.get_memories()` |
| Bear Researcher | RAG-augmented | 1 | No | Yes (vector similarity) | `agents/researchers/bear_researcher.py` — same memory retrieval pattern |
| Research Manager | Agentic (judge) | 1 | No | No | `agents/managers/research_manager.py` — judges bull/bear debate, emits investment decision |
| Trader | Agentic | 1 | No | Yes (vector similarity) | `agents/trader/trader.py` — execution plan with position sizing |
| Merged Risk Debator | Agentic | 1 | No | No | `agents/risk_mgmt/merged_debator.py` — 3-perspective risk discussion in a single call |
| Risk Manager | Agentic (judge) | 1 | No | Yes (vector similarity) | `agents/managers/risk_manager.py` — final approval/veto with 21 EGX-specific clauses |

---

## Tier Definitions

**Deterministic** — Zero LLM calls. Input is structured data (OHLCV, CSV ratios, volume). Output is computed via formulas, thresholds, or regex. Fully reproducible, zero cost, instant.

**Hybrid** — Primarily deterministic with an optional LLM narrative layer. The LLM adds human-readable interpretation but does not change the directional signal. Can run without LLM.

**RAG-augmented** — Single LLM call enhanced by vector-similarity retrieval of past situations from `FinancialSituationMemory` (ChromaDB). Memory is empty on cold start; system degrades gracefully to no-memory mode.

**Agentic** — LLM reasons over unstructured input (news text, social posts, debate history). News and Social analysts autonomously select tools via LangGraph `ToolNode` with per-analyst message isolation (`PerAnalystToolNode` at `graph/setup.py:14-32`). Judge nodes (Research Manager, Risk Manager) make decisions under uncertainty.

---

## Why This Mix Matters

Six of 15 nodes are fully deterministic. This means:
- **Cost**: ~40% of the pipeline has zero LLM cost
- **Reproducibility**: Deterministic nodes produce identical output for identical input, always
- **Safety**: The Risk Scorer (deterministic) can veto the entire LLM pipeline — no LLM opinion overrides hard regulatory constraints
- **Speed**: Deterministic nodes complete in milliseconds; LLM nodes take seconds

The agentic nodes are justified because their inputs are genuinely unstructured: multilingual news articles, noisy social media posts, adversarial debate transcripts, and ambiguous risk edge cases.
