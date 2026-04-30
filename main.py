from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from dotenv import load_dotenv

load_dotenv()

# EGX market analysis config
config = DEFAULT_CONFIG.copy()
config["deep_think_llm"] = "deepseek-chat"
config["quick_think_llm"] = "deepseek-chat"
config["max_debate_rounds"] = 1

config["data_vendors"] = {
    "core_stock_apis": "yfinance",
    "technical_indicators": "yfinance",
    "fundamental_data": "local",
    "news_data": "local",
}

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("COMI.CA", "2026-04-17")
print(decision)
