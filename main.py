from dotenv import load_dotenv

load_dotenv()

from datetime import date, timedelta
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


def last_trading_day() -> str:
    """Return the most recent weekday (Mon–Fri) as YYYY-MM-DD.

    EGX is closed Friday and Saturday; Sunday is a trading day in Egypt.
    For simplicity we exclude only Saturday and Sunday (international convention
    used by yfinance).  If today is Saturday, step back to Friday; if Sunday,
    step back to Friday.
    """
    today = date.today()
    # 5 = Saturday, 6 = Sunday in Python's weekday()
    if today.weekday() == 5:   # Saturday
        today -= timedelta(days=1)
    elif today.weekday() == 6:  # Sunday
        today -= timedelta(days=2)
    return today.isoformat()


def main() -> None:
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

    trade_date = last_trading_day()
    print(f"Analyzing COMI.CA on {trade_date}")

    ta = TradingAgentsGraph(debug=True, config=config)
    _, decision = ta.propagate("COMI.CA", trade_date)
    print(decision)


if __name__ == "__main__":
    main()
