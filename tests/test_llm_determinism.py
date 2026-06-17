"""Guards the LLM reproducibility invariant (CLAUDE.md §5.8 / §9.1).

Every LLM must be built with temperature=0 + a pinned seed on providers that
support it. We can't call live LLMs in CI, so we assert the config knob exists
and that the central construction code references both temperature and seed.
"""
from __future__ import annotations

import re
from pathlib import Path

from tradingagents.default_config import DEFAULT_CONFIG

REPO = Path(__file__).resolve().parents[1]


def test_llm_seed_config_present_and_int():
    assert "llm_seed" in DEFAULT_CONFIG
    assert isinstance(DEFAULT_CONFIG["llm_seed"], int)


def test_trading_graph_pins_temperature_and_seed():
    src = (REPO / "tradingagents" / "graph" / "trading_graph.py").read_text(encoding="utf-8")
    # Both OpenAI/DeepSeek lines must carry temperature=0 and seed=.
    openai_lines = [l for l in src.splitlines() if "ChatOpenAI(" in l and "model=" in l]
    assert openai_lines, "expected ChatOpenAI construction in trading_graph.py"
    for line in openai_lines:
        assert "temperature=0" in line, line
        assert "seed=" in line, line


def test_run_egx_prediction_pins_seed():
    src = (REPO / "run_egx_prediction.py").read_text(encoding="utf-8")
    n_chat = src.count("ChatOpenAI(")
    n_seed = len(re.findall(r"seed=int\(DEFAULT_CONFIG", src))
    assert n_chat >= 2
    assert n_seed >= n_chat, "every ChatOpenAI in run_egx_prediction must pin a seed"
