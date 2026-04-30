# 🏛 System Design & Architecture

## Agent Topology
We utilize a stateful Star-topology orchestration framework using **LangGraph**:
- **Coordinator Node (`main`)**: Validates the incoming requested ticker, handles caching lookups, and triggers the parallel worker nodes.
- **Worker Level**:
  - `TechnicalAnalyst`: Receives price dataframe. Returns `{ "score": Float[-1:1], "signals": List[String] }`
  - `FundamentalAnalyst`: Receives corporate filings. Returns `{ "score": Float[-1:1], "metrics": Dict }`
  - `NewsSentimentAnalyst`: Receives translated English & Arabic news. Returns `{ "score": Float[-1:1], "summary": String }`
  - `SocialSentimentAnalyst`: Receives Telegram/Twitter chatter. Returns `{ "score": Float[-1:1], "hype_index": Float[0:1] }`
- **Aggregation Node (`DecisionManager`)**: Waits for all worker nodes to complete. Synthesizes standard signals, executes weighting, checks constraints, and applies the overarching trading action (`BUY`, `SELL`, `HOLD`).
- **Risk Management Firewall**: Intercepts the decision. If stop-loss, margin-call, or liquidity conditions are violated, it vetoes the DecisionManager.

## Dynamic Weighting
Weights are not static. The `DecisionManager` adjusts weights based on market context:
- **High Hype Environment**: If `hype_index > 0.7`, retail sentiment and technical support override fundamentals.
- **Earnings Season**: If a fundamental update occurred in the last N hours, fundamental weight is tripled.

## Backtesting Strictness
- **Zero Lookahead Bias**: When backtesting via `BacktestingEngine`, the pipeline steps through time iteratively. An agent running on date T must NEVER have access to arrays that leak price data of T+1.
- **Execution Simulation**: Buy/Sell decisions assume slippage. Highly illiquid EGX trades should penalize profitability in the simulation.
