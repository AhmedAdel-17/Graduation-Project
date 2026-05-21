"""EGX Social Sentiment v2 (production package).

Self-contained successor to scripts/social_pipeline/v2. Wired into the
LangGraph agent via prefetch.py and social_media_tools.py.

Entry points:
    fetch_v2_signal(ticker, curr_date, look_back_days) -> dict
        Returns the agent-compatible JSON shape consumed by
        tradingagents/agents/analysts/social_media_analyst.py.
    run_pipeline(...) -> dict
        Full pipeline output (raw) for offline analysis / scripts.
"""

from .signal_adapter import fetch_v2_signal  # noqa: F401
from .pipeline import run_pipeline  # noqa: F401
