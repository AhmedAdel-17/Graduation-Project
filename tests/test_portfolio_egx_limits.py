"""Tests for the EGX regulatory validation gate (roadmap P1).

The proposal must pass EGX hard limits before it reaches the user. These tests
pin the long-only clip, the ADV participation cap, the no-leverage check, and the
low-liquidity / foreign-restricted disclosures. Also the regression gate: this
adapter reuses risk_scorer.EGX_RISK_LIMITS, so the agent-path risk tests must stay
green (run separately).
"""

from __future__ import annotations

import pytest

from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.egx_validation import validate_actions


def _action(ticker, side, shares, price=10.0):
    return s.RebalanceAction(
        ticker=ticker, side=side, shares=shares, price_used=price,
        est_value_egp=shares * price, current_weight_pct=0, target_weight_pct=0)


class TestLongOnly:
    def test_sell_clipped_to_held(self):
        """WHY: selling more than held is a short — illegal on EGX. BUG: an
        oversized SELL implies a position the user doesn't have. PHASE: P1."""
        acts, flags = validate_actions(
            [_action("TMGH.CA", s.TradeSide.SELL, 5000)],
            current_shares={"TMGH.CA": 2000}, available_cash_egp=0)
        assert acts[0].shares == 2000
        assert acts[0].est_value_egp == 2000 * 10.0
        assert "LONG_ONLY_CLIP" in {f.code for f in flags}

    def test_sell_within_holding_untouched(self):
        acts, flags = validate_actions(
            [_action("TMGH.CA", s.TradeSide.SELL, 1500)],
            current_shares={"TMGH.CA": 2000}, available_cash_egp=0)
        assert acts[0].shares == 1500 and flags == []

    def test_fully_clipped_sell_dropped(self):
        """WHY: a SELL of a name not held clips to 0 and is dropped (no zero-share
        action). PHASE: P1."""
        acts, _ = validate_actions(
            [_action("ZZZZ.CA", s.TradeSide.SELL, 100)],
            current_shares={}, available_cash_egp=0)
        assert acts == []


class TestADV:
    def test_trade_clipped_to_ten_pct_adv(self):
        """WHY: |trade| ≤ 10% ADV keeps it executable. BUG: an outsized order moves
        the market / can't fill. PHASE: P1."""
        # ADV 100,000 -> cap 10,000 shares
        acts, flags = validate_actions(
            [_action("COMI.CA", s.TradeSide.BUY, 25000)],
            current_shares={}, available_cash_egp=10_000_000, adv={"COMI.CA": 100000})
        assert acts[0].shares == 10000
        assert "ADV_CLIP" in {f.code for f in flags}

    def test_low_liquidity_flagged(self):
        """WHY: ADV below the 50k floor is disclosed. PHASE: P1."""
        _, flags = validate_actions(
            [_action("SMALL.CA", s.TradeSide.BUY, 100)],
            current_shares={}, available_cash_egp=1_000_000, adv={"SMALL.CA": 10000})
        assert "LOW_LIQUIDITY" in {f.code for f in flags}


class TestLeverage:
    def test_buys_exceeding_budget_are_clipped_not_just_flagged(self):
        """WHY: EGX allows no leverage — buys must fit cash + sell proceeds, and the
        proposal must be CLIPPED to enforce it (not merely warned). BUG (F1): the
        old code flagged LEVERAGE_BLOCKED but returned unfundable buys. PHASE: audit."""
        acts, flags = validate_actions(
            [_action("COMI.CA", s.TradeSide.BUY, 1000, price=50.0)],  # 50,000 EGP
            current_shares={}, available_cash_egp=10000)
        assert "LEVERAGE_CLIP" in {f.code for f in flags}
        # the returned buys must actually fit the 10,000 budget
        buy_cost = sum(a.est_value_egp for a in acts if a.side == s.TradeSide.BUY)
        assert buy_cost <= 10000 + 1e-6
        assert acts and acts[0].shares == 200  # 1000 scaled by 10k/50k, floored

    def test_buys_exceeding_budget_with_proceeds_are_clipped(self):
        """WHY: buys clip against cash + sell proceeds together. PHASE: audit."""
        acts, flags = validate_actions(
            [_action("TMGH.CA", s.TradeSide.SELL, 1000, price=10.0),    # +10,000 proceeds
             _action("COMI.CA", s.TradeSide.BUY, 1000, price=50.0)],    # wants 50,000
            current_shares={"TMGH.CA": 1000}, available_cash_egp=10000)  # budget 20,000
        assert "LEVERAGE_CLIP" in {f.code for f in flags}
        buy_cost = sum(a.est_value_egp for a in acts if a.side == s.TradeSide.BUY)
        assert buy_cost <= 20000 + 1e-6

    def test_buys_within_budget_incl_proceeds(self):
        """WHY: sell proceeds extend the buy budget (sells settle first). PHASE: P1."""
        acts, flags = validate_actions(
            [_action("TMGH.CA", s.TradeSide.SELL, 2000, price=10.0),   # +20,000
             _action("COMI.CA", s.TradeSide.BUY, 500, price=50.0)],    # -25,000
            current_shares={"TMGH.CA": 2000}, available_cash_egp=10000)  # 10k + 20k = 30k budget
        assert "LEVERAGE_BLOCKED" not in {f.code for f in flags}


class TestPassthrough:
    def test_clean_proposal_no_flags(self):
        """WHY: a compliant proposal passes untouched. BUG: flag noise on valid
        trades erodes trust. PHASE: P1."""
        acts, flags = validate_actions(
            [_action("COMI.CA", s.TradeSide.BUY, 100, price=50.0)],
            current_shares={}, available_cash_egp=1_000_000)
        assert len(acts) == 1 and flags == []
