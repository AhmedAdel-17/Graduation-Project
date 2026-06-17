"""Tests for the index-level (EGX30 / EGX70 / EGX100) sentiment layer.

Covers the redesign that produces market-level sentiment per index instead of
relying on thin per-stock chatter:
  * taxonomy index membership + primary_index
  * indices.py roll-up helpers
  * aggregator.aggregate_indices + split_outputs index_sentiment block
  * signal_adapter routing the ticker's own index into the blend
  * IndexSentiment typed contract invariants
"""
from __future__ import annotations

import pytest

from tradingagents.sentiment.taxonomy import (
    IndexEnum,
    members_of_index,
    primary_index,
    ticker_to_indices,
)
from tradingagents.dataflows.social_v2 import indices as idx_mod
from tradingagents.dataflows.social_v2.aggregator import (
    MIN_MENTIONS_PER_INDEX,
    ScoredPost,
    aggregate,
    aggregate_indices,
    split_outputs,
)
from tradingagents.dataflows.social_v2.entities import Mention


# ── taxonomy membership ──────────────────────────────────────────────────────

def test_blue_chip_in_egx30_and_egx100():
    indices = ticker_to_indices("COMI.CA")
    assert IndexEnum.EGX30 in indices
    assert IndexEnum.EGX100 in indices
    assert IndexEnum.EGX70 not in indices


def test_midcap_in_egx70_and_egx100():
    indices = ticker_to_indices("OCDI")
    assert IndexEnum.EGX70 in indices
    assert IndexEnum.EGX100 in indices
    assert IndexEnum.EGX30 not in indices


def test_egx30_egx70_are_disjoint():
    assert not (members_of_index(IndexEnum.EGX30) & members_of_index(IndexEnum.EGX70))


def test_egx100_is_the_union():
    union = members_of_index(IndexEnum.EGX30) | members_of_index(IndexEnum.EGX70)
    assert members_of_index(IndexEnum.EGX100) == union


def test_primary_index_prefers_egx30_then_egx70_then_none():
    assert primary_index("COMI") == IndexEnum.EGX30
    assert primary_index("OCDI") == IndexEnum.EGX70
    # EGX100 is never a *primary* index (it's the union).
    assert primary_index("COMI") != IndexEnum.EGX100


def test_unknown_ticker_has_no_index():
    assert ticker_to_indices("ZZZZ") == frozenset()
    assert primary_index("ZZZZ") is None


# ── indices.py helpers ───────────────────────────────────────────────────────

def test_pseudo_symbol_to_code():
    assert idx_mod.index_pseudo_symbol_to_code("EGX_30") == "EGX30"
    assert idx_mod.index_pseudo_symbol_to_code("EGX_100") == "EGX100"
    # EGX_BROAD is the whole-market mood, not an index.
    assert idx_mod.index_pseudo_symbol_to_code("EGX_BROAD") is None


def test_indices_from_ticker_mentions_rollup():
    rolled = idx_mod.indices_from_ticker_mentions(["COMI", "OCDI"])
    assert rolled["EGX30"] == pytest.approx(0.9)
    assert rolled["EGX70"] == pytest.approx(0.9)
    assert rolled["EGX100"] == pytest.approx(0.9)


# ── aggregate_indices ────────────────────────────────────────────────────────

def _mk(text, syms, score, n_engage=0):
    return ScoredPost(
        text=text, url="u:" + text, platform="news", source="s",
        timestamp="2026-06-01", engagement=n_engage,
        mentions=[Mention(symbol=s, confidence=0.9) for s in syms],
        intent={"intents": ["BULLISH"]},
        content={"label": "NEWS", "weight": 0.6},
        sentiment={"label": "bullish", "score": score},
    )


def test_aggregate_indices_splits_blue_chip_and_midcap():
    posts = (
        [_mk(f"COMI up {i}", ["COMI"], 0.6) for i in range(6)]
        + [_mk(f"OCDI down {i}", ["OCDI"], -0.5) for i in range(6)]
    )
    out = aggregate_indices(posts)
    assert out["EGX30"]["weighted_sentiment"] > 0   # blue-chip bullish
    assert out["EGX70"]["weighted_sentiment"] < 0   # mid-cap bearish
    # EGX100 sees both, so it sits between the two.
    assert out["EGX70"]["weighted_sentiment"] < out["EGX100"]["weighted_sentiment"] < out["EGX30"]["weighted_sentiment"]


def test_direct_index_mention_routes_to_that_index_only():
    posts = [_mk(f"EGX30 broke out {i}", ["EGX_30"], 0.4) for i in range(6)]
    out = aggregate_indices(posts)
    assert "EGX30" in out
    # A bare index-term mention must not leak into EGX70 or EGX100.
    assert "EGX70" not in out
    assert "EGX100" not in out


def test_split_outputs_emits_index_block_and_respects_threshold():
    enough = [_mk(f"COMI {i}", ["COMI"], 0.5) for i in range(MIN_MENTIONS_PER_INDEX + 1)]
    per_index = aggregate_indices(enough)
    out = split_outputs(aggregate(enough), total_posts=len(enough),
                        used_posts=len(enough), per_index=per_index)
    assert "index_sentiment" in out
    assert "EGX30" in out["index_sentiment"]

    # Below threshold → dropped.
    few = [_mk("COMI once", ["COMI"], 0.5)]
    per_index_few = aggregate_indices(few)
    out_few = split_outputs(aggregate(few), total_posts=len(few),
                            used_posts=len(few), per_index=per_index_few)
    assert out_few["index_sentiment"] == {}


# ── signal_adapter routing ───────────────────────────────────────────────────

def test_signal_adapter_routes_primary_index_into_blend():
    from tradingagents.dataflows.social_v2 import signal_adapter

    posts = [_mk(f"COMI strong {i}", ["COMI"], 0.6, n_engage=5) for i in range(8)]
    per_symbol = aggregate(posts)
    per_index = aggregate_indices(posts)
    out = split_outputs(per_symbol, total_posts=len(posts),
                        used_posts=len(posts), per_index=per_index)
    out["per_symbol_full"] = per_symbol

    shape = signal_adapter._to_agent_shape("COMI.CA", out, "test")
    assert shape["primary_index"] == "EGX30"
    # The block that drives the blend comes from the ticker's own index.
    assert shape["market_sentiment"]["driven_by"] == "index:EGX30"
    assert shape["market_sentiment"]["status"] == "SIGNAL"
    assert set(shape["index_sentiment"]).issuperset({"EGX30", "EGX100"})
    # The raw whole-market read is preserved separately.
    assert "market_overall" in shape


# ── IndexSentiment contract ──────────────────────────────────────────────────

def test_index_sentiment_contract_invariants():
    from tradingagents.sentiment.contracts import (
        IndexSentiment,
        LayerStatus,
        MarketRegime,
        NoSignalReason,
    )

    ok = IndexSentiment(
        status=LayerStatus.SIGNAL, score=0.4, confidence=0.6,
        index="EGX30", regime=MarketRegime.GREED, n_posts=12,
    )
    assert ok.score == 0.4

    # NO_SIGNAL must carry a reason and a null score.
    with pytest.raises(ValueError):
        IndexSentiment(status=LayerStatus.SIGNAL, index="EGX30")  # missing score
    none_sig = IndexSentiment(
        status=LayerStatus.NO_SIGNAL, index="EGX30",
        reason=NoSignalReason(gate_failed="index.n", human_readable="too few"),
    )
    assert none_sig.score is None
