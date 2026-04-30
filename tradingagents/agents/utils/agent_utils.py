from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_sentiment,
    get_insider_transactions,
    get_global_news
)

def create_msg_delete(field: str = "messages"):
    """
    Return a node function that clears the given message channel.

    Args:
        field: The state key whose messages should be cleared
               (e.g. "market_messages", "news_messages").
               Defaults to the shared "messages" channel for
               backwards-compatibility with non-parallel graphs.
    """
    def delete_messages(state):
        messages = state.get(field, [])
        removal_operations = [RemoveMessage(id=m.id) for m in messages]
        return {field: removal_operations}

    return delete_messages


        