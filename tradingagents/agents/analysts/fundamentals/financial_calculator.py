"""
Financial ratio calculator for EGX Fundamental Analyst.

Computes 14 core ratios + 3 experimental metrics from raw CSV values.
Returns raw numbers only — no thresholds, no labels, no direction signals.
All computation is null-safe: returns None on undefined cases.

Core ratios (14):
  ROE, ROA, gross_margin, operating_margin, net_margin,
  debt_to_equity, current_ratio, asset_turnover, equity_multiplier,
  dupont_3factor, eps, pe_ratio, pb_ratio, earnings_yield

Experimental (3):
  earnings_yield_spread, dividend_yield, piotroski_score (7-signal variant)

Evidence class:
  Core: IS [P7] Kim et al. 2024, [P4] FinAgent, FinAR-Bench — consensus
  DuPont identity checks: ALG (mathematical identity)
  Piotroski: [AF] — validated for US value stocks, not EGX agents
  EY spread: [EI] — concept established (Fed Model), no agent-system validation
  Dividend yield: [AF] — optional CSV field, coverage unknown

Design rules:
  - LLM never computes. Python computes. LLM interprets.
  - Zero denominators → return None
  - Negative equity → flag for caller, continue computing where possible
  - No "healthy/concerning/critical" labels here — that is the LLM's job
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple


class FinancialCalculator:
    """
    Stateless calculator for EGX financial ratios.
    All methods are pure functions of the inputs.
    """

    # ── Profitability ─────────────────────────────────────────────────────────

    @staticmethod
    def roe(net_income: Optional[float], total_equity: Optional[float]) -> Optional[float]:
        """Return on Equity = Net Income / Total Equity.

        If total_equity < 0 (negative equity), returns the mathematically
        correct but sign-reversed value. The caller is responsible for
        generating NEGATIVE_EQUITY_ALERT via sector_config.
        Returns None only if equity is exactly 0 (undefined division).
        """
        if net_income is None or total_equity is None:
            return None
        if total_equity == 0:
            return None
        return net_income / total_equity

    @staticmethod
    def roa(net_income: Optional[float], total_assets: Optional[float]) -> Optional[float]:
        """Return on Assets = Net Income / Total Assets."""
        if net_income is None or total_assets is None or total_assets == 0:
            return None
        return net_income / total_assets

    @staticmethod
    def gross_margin(gross_profit: Optional[float], revenue: Optional[float]) -> Optional[float]:
        """Gross Margin = Gross Profit / Revenue."""
        if gross_profit is None or revenue is None or revenue == 0:
            return None
        return gross_profit / revenue

    @staticmethod
    def operating_margin(
        operating_income: Optional[float], revenue: Optional[float]
    ) -> Optional[float]:
        """Operating Margin = Operating Income / Revenue."""
        if operating_income is None or revenue is None or revenue == 0:
            return None
        return operating_income / revenue

    @staticmethod
    def net_margin(net_income: Optional[float], revenue: Optional[float]) -> Optional[float]:
        """Net Profit Margin = Net Income / Revenue."""
        if net_income is None or revenue is None or revenue == 0:
            return None
        return net_income / revenue

    # ── Leverage & Liquidity ──────────────────────────────────────────────────

    @staticmethod
    def debt_to_equity(
        total_liabilities: Optional[float], total_equity: Optional[float]
    ) -> Optional[float]:
        """Debt-to-Equity = Total Liabilities / Total Equity.
        Returns None if equity is zero or negative (undefined / sign-reversed).
        """
        if total_liabilities is None or total_equity is None:
            return None
        if total_equity <= 0:
            # Negative equity makes D/E undefined or sign-reversed.
            # The caller (sector_config) will generate NEGATIVE_EQUITY_ALERT.
            return None
        return total_liabilities / total_equity

    @staticmethod
    def current_ratio(
        current_assets: Optional[float], current_liabilities: Optional[float]
    ) -> Optional[float]:
        """Current Ratio = Current Assets / Current Liabilities."""
        if current_assets is None or current_liabilities is None:
            return None
        if current_liabilities == 0:
            return None
        return current_assets / current_liabilities

    # ── DuPont decomposition ──────────────────────────────────────────────────

    @staticmethod
    def asset_turnover(
        revenue: Optional[float], total_assets: Optional[float]
    ) -> Optional[float]:
        """Asset Turnover = Revenue / Total Assets."""
        if revenue is None or total_assets is None or total_assets == 0:
            return None
        return revenue / total_assets

    @staticmethod
    def equity_multiplier(
        total_assets: Optional[float], total_equity: Optional[float]
    ) -> Optional[float]:
        """Equity Multiplier = Total Assets / Total Equity."""
        if total_assets is None or total_equity is None or total_equity == 0:
            return None
        return total_assets / total_equity

    @staticmethod
    def dupont_3factor(
        net_margin: Optional[float],
        asset_turnover: Optional[float],
        equity_multiplier: Optional[float],
    ) -> Optional[float]:
        """DuPont 3-factor ROE = Net Margin × Asset Turnover × Equity Multiplier.
        ALG identity: this should equal ROE within rounding error.
        """
        if net_margin is None or asset_turnover is None or equity_multiplier is None:
            return None
        return net_margin * asset_turnover * equity_multiplier

    # ── EPS and Valuation ─────────────────────────────────────────────────────

    @staticmethod
    def eps(net_income: Optional[float], shares_outstanding: Optional[float]) -> Optional[float]:
        """Earnings Per Share = Net Income / Shares Outstanding."""
        if net_income is None or shares_outstanding is None or shares_outstanding == 0:
            return None
        return net_income / shares_outstanding

    @staticmethod
    def pe_ratio(
        price: Optional[float], eps_value: Optional[float]
    ) -> Optional[float]:
        """Price-to-Earnings = Price / EPS.
        Returns None if EPS <= 0 (negative earnings or zero).
        """
        if price is None or eps_value is None:
            return None
        if eps_value <= 0:
            # PE is undefined for negative or zero EPS.
            # Caller should generate PE_UNDEFINED flag.
            return None
        return price / eps_value

    @staticmethod
    def pb_ratio(
        price: Optional[float], book_value_per_share: Optional[float]
    ) -> Optional[float]:
        """Price-to-Book = Price / Book Value Per Share."""
        if price is None or book_value_per_share is None or book_value_per_share == 0:
            return None
        return price / book_value_per_share

    @staticmethod
    def earnings_yield(pe_value: Optional[float]) -> Optional[float]:
        """Earnings Yield = 1 / P/E.
        ALG identity (inverse of P/E). Returns None if P/E is undefined.
        """
        if pe_value is None or pe_value == 0:
            return None
        return 1.0 / pe_value

    # ── Experimental ─────────────────────────────────────────────────────────

    @staticmethod
    def earnings_yield_spread(
        ey: Optional[float], risk_free_rate: Optional[float]
    ) -> Optional[float]:
        """Earnings Yield Spread = Earnings Yield − Risk-Free Rate.
        Evidence class: [EI] — concept established (Fed Model) but not validated
        in any LLM agent system. Requires a sourced risk-free proxy.
        Returns None if either input is unavailable.
        """
        if ey is None or risk_free_rate is None:
            return None
        return ey - risk_free_rate

    @staticmethod
    def dividend_yield(
        dividend_per_share: Optional[float], price: Optional[float]
    ) -> Optional[float]:
        """Dividend Yield = DPS / Price.
        Evidence class: [AF]. Optional CSV field — coverage across EGX30 unknown.
        """
        if dividend_per_share is None or price is None or price == 0:
            return None
        return dividend_per_share / price

    @staticmethod
    def piotroski_7signal(
        net_income: Optional[float],
        roa_current: Optional[float],
        roa_prior: Optional[float],
        gross_margin_current: Optional[float],
        gross_margin_prior: Optional[float],
        asset_turnover_current: Optional[float],
        asset_turnover_prior: Optional[float],
        leverage_current: Optional[float],
        leverage_prior: Optional[float],
        current_ratio_current: Optional[float],
        current_ratio_prior: Optional[float],
        shares_current: Optional[float],
        shares_prior: Optional[float],
    ) -> Tuple[Optional[int], Dict[str, Optional[bool]]]:
        """
        Piotroski F-score, 7-signal variant (signals 1–4, 6–7, 9 from original 9).
        Signal 5 (OCF profitability) and signal 8 (accruals) are excluded because
        the EGX CSV schema has no cash flow fields.

        Evidence class: [AF] — validated for US value stocks (Piotroski 2000).
        NOT validated for EGX or LLM agent systems.
        Do NOT wire into evidence pack until Phase 1B confirms analytical validity.

        Returns:
          (total_score, signal_breakdown) — score is None if < 4 signals computable.
        """
        signals: Dict[str, Optional[bool]] = {}

        # F1: ROA > 0 (profitability)
        if roa_current is not None:
            signals["f1_roa_positive"] = roa_current > 0
        else:
            signals["f1_roa_positive"] = None

        # F2: ROA improving (vs prior year)
        if roa_current is not None and roa_prior is not None:
            signals["f2_roa_improving"] = roa_current > roa_prior
        else:
            signals["f2_roa_improving"] = None

        # F3: Net income positive
        if net_income is not None:
            signals["f3_net_income_positive"] = net_income > 0
        else:
            signals["f3_net_income_positive"] = None

        # F4: Leverage decreasing (lower is better — more conservative)
        if leverage_current is not None and leverage_prior is not None:
            signals["f4_leverage_decreasing"] = leverage_current < leverage_prior
        else:
            signals["f4_leverage_decreasing"] = None

        # F6: Current ratio improving
        if current_ratio_current is not None and current_ratio_prior is not None:
            signals["f6_liquidity_improving"] = current_ratio_current > current_ratio_prior
        else:
            signals["f6_liquidity_improving"] = None

        # F7: No share dilution (shares not increased)
        if shares_current is not None and shares_prior is not None:
            signals["f7_no_dilution"] = shares_current <= shares_prior
        else:
            signals["f7_no_dilution"] = None

        # F9: Asset turnover improving
        if asset_turnover_current is not None and asset_turnover_prior is not None:
            signals["f9_asset_turnover_improving"] = asset_turnover_current > asset_turnover_prior
        else:
            signals["f9_asset_turnover_improving"] = None

        # Count computable signals
        computable = [v for v in signals.values() if v is not None]
        if len(computable) < 4:
            return None, signals

        total = sum(1 for v in signals.values() if v is True)
        return total, signals

    # ── Full computation pass ─────────────────────────────────────────────────

    @classmethod
    def compute_all(
        cls,
        *,
        # Income statement
        revenue: Optional[float] = None,
        gross_profit: Optional[float] = None,
        operating_income: Optional[float] = None,
        net_income: Optional[float] = None,
        # Balance sheet
        total_assets: Optional[float] = None,
        total_liabilities: Optional[float] = None,
        total_equity: Optional[float] = None,
        current_assets: Optional[float] = None,
        current_liabilities: Optional[float] = None,
        shares_outstanding: Optional[float] = None,
        # From key_ratios CSV (pre-computed by data provider)
        eps_csv: Optional[float] = None,
        pe_ratio_csv: Optional[float] = None,
        pb_ratio_csv: Optional[float] = None,
        current_ratio_csv: Optional[float] = None,
        roe_csv: Optional[float] = None,
        roa_csv: Optional[float] = None,
        gross_margin_csv: Optional[float] = None,
        operating_margin_csv: Optional[float] = None,
        net_margin_csv: Optional[float] = None,
        book_value_per_share: Optional[float] = None,
        dividend_yield_csv: Optional[float] = None,
        # Market data
        current_price: Optional[float] = None,
        risk_free_rate: Optional[float] = None,
        # Prior period for Piotroski
        roa_prior: Optional[float] = None,
        gross_margin_prior: Optional[float] = None,
        asset_turnover_prior: Optional[float] = None,
        leverage_prior: Optional[float] = None,
        current_ratio_prior: Optional[float] = None,
        shares_prior: Optional[float] = None,
    ) -> Dict[str, object]:
        """
        Compute all ratios in a single pass. Returns a dict of metric → value.

        Preference order: compute from raw fields first (more verifiable).
        Fall back to CSV-provided values if raw fields are missing.
        Never mix computed and CSV values for the same ratio silently —
        log which source was used.

        Returns dict with keys matching FundamentalAnalysisReport.ratios schema.
        """
        result: Dict[str, object] = {}

        # ── Profitability ────────────────────────────────────────────────────
        nm = cls.net_margin(net_income, revenue)
        result["net_margin"] = nm if nm is not None else net_margin_csv
        result["_net_margin_source"] = "computed" if nm is not None else ("csv" if net_margin_csv is not None else "missing")

        gm = cls.gross_margin(gross_profit, revenue)
        result["gross_margin"] = gm if gm is not None else gross_margin_csv
        result["_gross_margin_source"] = "computed" if gm is not None else ("csv" if gross_margin_csv is not None else "missing")

        om = cls.operating_margin(operating_income, revenue)
        result["operating_margin"] = om if om is not None else operating_margin_csv
        result["_operating_margin_source"] = "computed" if om is not None else ("csv" if operating_margin_csv is not None else "missing")

        # Use resolved values for downstream
        nm_val = result["net_margin"]
        gm_val = result["gross_margin"]

        # ROE: compute from financials, fall back to CSV
        roe_computed = cls.roe(net_income, total_equity)
        result["roe"] = roe_computed if roe_computed is not None else roe_csv
        result["_roe_source"] = "computed" if roe_computed is not None else ("csv" if roe_csv is not None else "missing")

        roa_computed = cls.roa(net_income, total_assets)
        result["roa"] = roa_computed if roa_computed is not None else roa_csv
        result["_roa_source"] = "computed" if roa_computed is not None else ("csv" if roa_csv is not None else "missing")

        # ── Leverage & Liquidity ─────────────────────────────────────────────
        de = cls.debt_to_equity(total_liabilities, total_equity)
        result["debt_to_equity"] = de

        cr = cls.current_ratio(current_assets, current_liabilities)
        result["current_ratio"] = cr if cr is not None else current_ratio_csv

        # ── DuPont ───────────────────────────────────────────────────────────
        at = cls.asset_turnover(revenue, total_assets)
        result["asset_turnover"] = at

        em = cls.equity_multiplier(total_assets, total_equity)
        result["equity_multiplier"] = em

        dp3 = cls.dupont_3factor(nm_val, at, em)
        result["dupont_3factor"] = dp3

        # ── EPS and Valuation ────────────────────────────────────────────────
        eps_computed = cls.eps(net_income, shares_outstanding)
        eps_val = eps_computed if eps_computed is not None else eps_csv
        result["eps"] = eps_val
        result["_eps_source"] = "computed" if eps_computed is not None else ("csv" if eps_csv is not None else "missing")

        pe_computed = cls.pe_ratio(current_price, eps_val) if current_price else None
        result["pe_ratio"] = pe_computed if pe_computed is not None else pe_ratio_csv
        if pe_computed is not None:
            result["_pe_ratio_source"] = "trade_date_price"
        elif pe_ratio_csv is not None:
            result["_pe_ratio_source"] = "csv_fallback"
        else:
            result["_pe_ratio_source"] = "unavailable"

        pb_computed = cls.pb_ratio(current_price, book_value_per_share) if current_price else None
        result["pb_ratio"] = pb_computed if pb_computed is not None else pb_ratio_csv

        pe_val = result["pe_ratio"]
        ey = cls.earnings_yield(pe_val)
        result["earnings_yield"] = ey

        # ── Experimental ─────────────────────────────────────────────────────
        ey_spread = cls.earnings_yield_spread(ey, risk_free_rate)
        result["earnings_yield_spread"] = ey_spread

        result["dividend_yield"] = dividend_yield_csv  # Only from CSV (no raw field)

        # Piotroski 7-signal
        piotroski_total, piotroski_signals = cls.piotroski_7signal(
            net_income=net_income,
            roa_current=result.get("roa"),
            roa_prior=roa_prior,
            gross_margin_current=gm_val,
            gross_margin_prior=gross_margin_prior,
            asset_turnover_current=at,
            asset_turnover_prior=asset_turnover_prior,
            leverage_current=de,
            leverage_prior=leverage_prior,
            current_ratio_current=result.get("current_ratio"),
            current_ratio_prior=current_ratio_prior,
            shares_current=shares_outstanding,
            shares_prior=shares_prior,
        )
        result["piotroski_score"] = piotroski_total
        result["_piotroski_signals"] = piotroski_signals

        return result

    # ── Signal coherence computation ──────────────────────────────────────────

    @classmethod
    def compute_signal_coherence(
        cls,
        ratios: Dict[str, object],
        current_ratio_csv: Optional[float] = None,
        current_assets: Optional[float] = None,
        current_liabilities: Optional[float] = None,
    ) -> Tuple[int, List[str]]:
        """
        Compute signal_coherence score (0–100).

        Only checks mathematical impossibilities and cross-source contradictions.
        Does NOT check: distress conditions, missing optional fields, data availability.

        Returns:
          (score, deduction_reasons)
        """
        score = 100
        reasons: List[str] = []

        roe_val = ratios.get("roe")
        nm_val = ratios.get("net_margin")
        at_val = ratios.get("asset_turnover")
        em_val = ratios.get("equity_multiplier")
        dp3_val = ratios.get("dupont_3factor")
        cr_computed = ratios.get("current_ratio")

        # Check 1: ROE positive but net_margin negative
        # ROE = NM × AT × EM; if NM < 0 and ROE > 0, then AT × EM must be negative,
        # which requires AT < 0 — impossible (revenue and assets are both positive).
        if roe_val is not None and nm_val is not None:
            if roe_val > 0 and nm_val < 0:
                score -= 15
                reasons.append(
                    "ROE is positive but net_margin is negative — "
                    "DuPont identity requires AT < 0, which is impossible"
                )

        # Check 2: Current ratio cross-source divergence > 30%
        # If both the CSV-provided current_ratio and the computed value exist,
        # they should agree within 30%.
        if (
            current_ratio_csv is not None
            and current_assets is not None
            and current_liabilities is not None
            and current_liabilities > 0
        ):
            cr_recomputed = current_assets / current_liabilities
            divergence = abs(cr_recomputed - current_ratio_csv) / max(abs(current_ratio_csv), 1e-9)
            if divergence > 0.30:
                score -= 15
                reasons.append(
                    f"Current ratio divergence: computed {cr_recomputed:.3f} vs CSV {current_ratio_csv:.3f} "
                    f"({divergence:.1%} difference) — source data inconsistency"
                )

        # Check 3: DuPont identity failure
        # |NM × AT × EM − ROE| > 0.05 indicates income/balance/ratios CSVs are inconsistent.
        if dp3_val is not None and roe_val is not None:
            dupont_error = abs(dp3_val - roe_val)
            if dupont_error > 0.05:
                score -= 10
                reasons.append(
                    f"DuPont identity fails: NM×AT×EM = {dp3_val:.4f}, ROE = {roe_val:.4f}, "
                    f"error = {dupont_error:.4f} > 0.05 — income/balance/ratios CSVs are inconsistent"
                )

        return max(0, score), reasons
