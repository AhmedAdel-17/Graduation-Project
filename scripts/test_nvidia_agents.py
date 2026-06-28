"""
Test script: run the full multi-agent graph with NVIDIA DeepSeek-V4-Pro
and print every agent's input and output to the console.

Usage:
    python scripts/test_nvidia_agents.py [TICKER]   (default: COMI.CA)
"""

import sys
import os
import io
import json
import textwrap
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Union

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(
    level=logging.WARNING,          # suppress library noise
    format="%(asctime)s [%(name)s] %(message)s",
)
# Show our own logger at INFO
logging.getLogger("tradingagents").setLevel(logging.INFO)

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

# ─── Callback: print every LLM call ─────────────────────────────────────────

DIVIDER = "═" * 80

_agent_counter = [0]


def _fmt_messages(messages: List[List[BaseMessage]]) -> str:
    parts = []
    for batch in messages:
        for m in batch:
            role = getattr(m, "type", m.__class__.__name__)
            content = str(getattr(m, "content", m))
            # wrap long lines
            wrapped = textwrap.fill(content, width=100, subsequent_indent="    ")
            parts.append(f"  [{role.upper()}]\n    {wrapped}")
    return "\n".join(parts)


def _fmt_output(response: LLMResult) -> str:
    parts = []
    for gen_list in response.generations:
        for gen in gen_list:
            text = getattr(gen, "text", None) or str(getattr(gen, "message", gen))
            wrapped = textwrap.fill(text, width=100, subsequent_indent="  ")
            parts.append(f"  {wrapped}")
    return "\n".join(parts)


class AgentIOLogger(BaseCallbackHandler):
    """Logs every LLM call with its full input messages and output text."""

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[BaseMessage]],
        *,
        run_id,
        parent_run_id=None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        _agent_counter[0] += 1
        n = _agent_counter[0]
        model = (serialized.get("kwargs", {}).get("model_name") or
                 serialized.get("name", "unknown-model"))
        print(f"\n{DIVIDER}")
        print(f"  AGENT CALL #{n}  |  model: {model}")
        print(f"{DIVIDER}")
        print("── INPUT (messages) ──────────────────────────────────────────────────")
        print(_fmt_messages(messages))
        sys.stdout.flush()

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id,
        parent_run_id=None,
        **kwargs: Any,
    ) -> None:
        print("\n── OUTPUT ────────────────────────────────────────────────────────────")
        print(_fmt_output(response))
        print(DIVIDER)
        sys.stdout.flush()

    def on_llm_error(
        self,
        error: Union[Exception, KeyboardInterrupt],
        *,
        run_id,
        parent_run_id=None,
        **kwargs: Any,
    ) -> None:
        print(f"\n── ERROR ─────────────────────────────────────────────────────────────")
        print(f"  {error}")
        print(DIVIDER)
        sys.stdout.flush()


# ─── Patch ChatOpenAI to inject our callback ────────────────────────────────
# We monkey-patch at import time so every ChatOpenAI the graph builds picks up
# our callback without modifying any agent code.

from langchain_openai import ChatOpenAI as _OrigChatOpenAI

_logger_instance = AgentIOLogger()


class _PatchedChatOpenAI(_OrigChatOpenAI):
    def __init__(self, *args, **kwargs):
        existing = list(kwargs.get("callbacks") or [])
        if not any(isinstance(c, AgentIOLogger) for c in existing):
            existing.append(_logger_instance)
        kwargs["callbacks"] = existing
        super().__init__(*args, **kwargs)


import langchain_openai as _lco
_lco.ChatOpenAI = _PatchedChatOpenAI

# Also patch the symbol used directly in trading_graph.py
import tradingagents.graph.trading_graph as _tg
_tg.ChatOpenAI = _PatchedChatOpenAI


# ─── Main ────────────────────────────────────────────────────────────────────

def last_trading_day() -> str:
    today = date.today()
    if today.weekday() == 5:
        today -= timedelta(days=1)
    elif today.weekday() == 6:
        today -= timedelta(days=2)
    return today.isoformat()


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "COMI.CA"
    trade_date = last_trading_day()

    print(DIVIDER)
    print(f"  NVIDIA DeepSeek-V4-Pro — Full Agent Test")
    print(f"  Ticker: {ticker}   |   Trade date: {trade_date}")
    print(f"  Backend: {os.getenv('LLM_BACKEND_URL', 'https://integrate.api.nvidia.com/v1')}")
    print(DIVIDER)

    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1

    ta = TradingAgentsGraph(
        selected_analysts=["market", "fundamentals", "news", "social"],
        debug=True,
        config=config,
    )

    print(f"\n Starting graph propagation for {ticker} ...\n")
    state, decision = ta.propagate(ticker, trade_date)

    print(f"\n{DIVIDER}")
    print("  FINAL DECISION")
    print(DIVIDER)
    print(decision)

    # Also dump key state fields for reference
    print(f"\n{DIVIDER}")
    print("  STATE SUMMARY")
    print(DIVIDER)
    for key in (
        "market_report",
        "fundamentals_report",
        "news_report",
        "social_report",
        "investment_debate_state",
        "risk_debate_state",
        "final_trade_decision",
    ):
        val = state.get(key, "<not set>")
        if isinstance(val, dict):
            val = json.dumps(val, ensure_ascii=False, indent=2)
        if val and val != "<not set>":
            short = str(val)[:1200]
            print(f"\n[{key}]\n{short}{'...' if len(str(val)) > 1200 else ''}")

    print(f"\n{DIVIDER}")
    print(f"  Total LLM calls: {_agent_counter[0]}")
    print(DIVIDER)


if __name__ == "__main__":
    main()
