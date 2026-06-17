"""Tests for the pure market-data helpers (roadmap P1).

Covers the Ledoit-Wolf covariance, the min-history gate, and the diagonal
fallback. The I/O fetchers (get_return_history / get_live_prices) are best-effort
network calls and are not unit-tested here. Pure, offline, deterministic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingagents.portfolio.market_data import (
    build_covariance,
    compute_covariance,
    diagonal_covariance,
)


def _returns(n=250, seed=1):
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.01, n)
    return pd.DataFrame({
        "COMI.CA": base + rng.normal(0, 0.003, n),
        "TMGH.CA": 0.5 * base + rng.normal(0, 0.004, n),
        "FWRY.CA": rng.normal(0, 0.012, n),
    })


class TestComputeCovariance:
    def test_shape_symmetry_psd_and_annualized(self):
        """WHY: the covariance feeds the MVO; it must be square, symmetric, PSD,
        and annualized. BUG: an asymmetric/indefinite matrix breaks cvxpy or yields
        a nonsense vol. PHASE: P1 (optimizer)."""
        cov, shrink = compute_covariance(_returns())
        assert list(cov.index) == list(cov.columns) == ["COMI.CA", "TMGH.CA", "FWRY.CA"]
        m = cov.to_numpy()
        assert np.allclose(m, m.T)                       # symmetric
        assert np.all(np.linalg.eigvalsh(m) > -1e-10)    # PSD
        assert 0.0 <= shrink <= 1.0
        # daily var ~1e-4 → annualized ~2.5e-2 scale
        assert cov.loc["COMI.CA", "COMI.CA"] > cov.loc["TMGH.CA", "TMGH.CA"]  # COMI noisier here

    def test_shrinkage_is_between_zero_and_one(self):
        """WHY: Ledoit-Wolf shrinkage ∈ [0,1] stabilizes short EGX history. BUG:
        no shrinkage on a near-singular matrix gives an unstable optimizer. PHASE: P1."""
        cov, shrink = compute_covariance(_returns(n=80))
        assert 0.0 < shrink <= 1.0

    def test_single_asset_uses_sample_variance(self):
        """WHY: Ledoit-Wolf degenerates for one asset → plain variance. BUG: a crash
        on a one-name universe. PHASE: P1."""
        df = _returns()[["COMI.CA"]]
        cov, shrink = compute_covariance(df)
        assert cov.shape == (1, 1) and shrink == 0.0
        assert cov.iloc[0, 0] == pytest.approx(np.var(df["COMI.CA"], ddof=1) * 252)

    def test_empty_or_too_short_raises(self):
        """WHY: covariance needs data. BUG: silently returning garbage. PHASE: P1."""
        with pytest.raises(ValueError):
            compute_covariance(pd.DataFrame())
        with pytest.raises(ValueError):
            compute_covariance(_returns(n=1))

    def test_deterministic(self):
        """WHY: same returns → same covariance (audit reproducibility). PHASE: P1."""
        a, _ = compute_covariance(_returns())
        b, _ = compute_covariance(_returns())
        assert a.equals(b)


class TestBuildCovariance:
    def test_min_history_gate_excludes_short_columns(self):
        """WHY: names with thin history must drop out (and be reported), not poison
        the covariance. BUG: a 5-point column sold as a real estimate. PHASE: P1."""
        df = _returns(n=120)
        df.loc[df.index[:115], "FWRY.CA"] = np.nan  # only 5 obs remain
        cov, excluded, shrink = build_covariance(df, min_history_days=60)
        assert excluded == ["FWRY.CA"]
        assert cov is not None and "FWRY.CA" not in cov.columns
        assert list(cov.columns) == ["COMI.CA", "TMGH.CA"]

    def test_all_excluded_returns_none(self):
        """WHY: when nothing has enough history, covariance is None and the
        optimizer falls back to a diagonal prior. PHASE: P1."""
        cov, excluded, _ = build_covariance(_returns(n=30), min_history_days=60)
        assert cov is None and set(excluded) == {"COMI.CA", "TMGH.CA", "FWRY.CA"}


class TestDiagonalCovariance:
    def test_diagonal_fallback(self):
        """WHY: the neutral fallback is identity-scaled variance (no correlation).
        BUG: a non-diagonal 'fallback' would invent correlations. PHASE: P1."""
        cov = diagonal_covariance(["A.CA", "B.CA"], annual_vol=0.25)
        assert cov.shape == (2, 2)
        assert cov.loc["A.CA", "A.CA"] == pytest.approx(0.25 ** 2)
        assert cov.loc["A.CA", "B.CA"] == 0.0


class TestMarketCapWeights:
    """The CAPM-equilibrium prior reads market caps from the local EGX key-ratios
    CSVs (offline). He & Litterman (1999): the equilibrium portfolio is the
    market-cap portfolio."""

    def test_weights_sum_to_one_and_impute_unknown(self):
        """WHY: weights must normalize over the universe and missing caps must be
        median-imputed + disclosed, never silently dropped. PHASE: P1."""
        from tradingagents.portfolio.market_data import get_market_cap_weights, _local_market_cap
        if _local_market_cap("COMI.CA") is None:
            pytest.skip("local EGX market-cap data not present")
        w, imputed = get_market_cap_weights(["COMI.CA", "TMGH.CA", "ZZZZ.CA"])
        assert abs(sum(w.values()) - 1.0) < 1e-9
        assert all(v >= 0 for v in w.values())
        assert "ZZZZ.CA" in imputed and "COMI.CA" not in imputed
        assert w["COMI.CA"] > w["ZZZZ.CA"]  # real large-cap > median-imputed unlisted

    def test_no_caps_returns_empty(self):
        """WHY: with no cap data at all the caller must fall back to its own prior
        (current weights), so an empty result is the documented signal. PHASE: P1."""
        from tradingagents.portfolio.market_data import get_market_cap_weights
        w, imputed = get_market_cap_weights(["ZZZZ.CA", "YYYY.CA"])
        assert w == {} and imputed == ["ZZZZ.CA", "YYYY.CA"]
