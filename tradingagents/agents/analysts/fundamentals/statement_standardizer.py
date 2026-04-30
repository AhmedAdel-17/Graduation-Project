"""
Statement standardizer for EGX Fundamental Analyst.

Preprocessing outputs (NOT ratios):
  common_size_income  — every income line as % of revenue
  common_size_balance — every balance line as % of total assets
  revenue_growth_yoy  — YoY revenue growth rate (requires ≥ 2 annual periods)
  net_income_growth_yoy — YoY net income growth rate
  yoy_changes         — dict of absolute + percentage changes for all fields
  qoq_changes         — dict of QoQ changes (null when only annual data)
  directions          — per-metric direction label: improving/stable/deteriorating

These belong here because they require multi-period data or cross-section
normalization — they are NOT single-period ratios from financial_calculator.py.

Evidence class: DS [P7] Kim et al. 2024 — standardization is the single most
validated preprocessing step in the analyst-mimicking CoT literature.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# Tolerance for "stable" direction classification: ±2%
_STABLE_THRESHOLD = 0.02


class StatementStandardizer:
    """
    Stateless standardizer for EGX financial statements.
    All methods take dicts of field → value (floats) and return dicts.
    """

    # ── Common-size statements ────────────────────────────────────────────────

    @staticmethod
    def common_size_income(
        income_row: Dict[str, Optional[float]]
    ) -> Dict[str, Optional[float]]:
        """
        Express each income statement field as a percentage of revenue.
        Returns None for fields where revenue is 0 or unavailable.

        Input keys (from EGX CSV):
          revenue, gross_profit, operating_income, net_income,
          cost_of_revenue, operating_expenses, interest_expense,
          tax_expense, ebitda, eps_basic, eps_diluted

        Output: same keys with '_pct' suffix, values 0.0–1.0 (or None).
        """
        revenue = income_row.get("revenue")
        if not revenue or revenue == 0:
            return {}

        result: Dict[str, Optional[float]] = {}
        # Skip date fields, revenue itself, per-share fields, and any metadata keys (_prefix)
        skip_keys = {"period_end_date", "_period_end_date", "revenue", "eps_basic", "eps_diluted"}

        for key, val in income_row.items():
            if key in skip_keys or key.startswith("_"):
                continue
            if isinstance(val, (int, float)) and val is not None:
                result[key + "_pct"] = val / revenue
            else:
                result[key + "_pct"] = None

        return result

    @staticmethod
    def common_size_balance(
        balance_row: Dict[str, Optional[float]]
    ) -> Dict[str, Optional[float]]:
        """
        Express each balance sheet field as a percentage of total assets.
        Returns empty dict if total_assets is 0 or unavailable.

        Input keys (from EGX CSV):
          total_assets, total_liabilities, total_equity,
          cash_and_equivalents, accounts_receivable, inventory,
          current_assets, fixed_assets, current_liabilities,
          long_term_debt, retained_earnings, shares_outstanding

        Output: same keys with '_pct' suffix (excluding total_assets itself).
        shares_outstanding is per-share count, not meaningful as % of assets — skipped.
        """
        total_assets = balance_row.get("total_assets")
        if not total_assets or total_assets == 0:
            return {}

        result: Dict[str, Optional[float]] = {}
        skip_keys = {"period_end_date", "_period_end_date", "total_assets", "shares_outstanding"}

        for key, val in balance_row.items():
            if key in skip_keys or key.startswith("_"):
                continue
            if isinstance(val, (int, float)) and val is not None:
                result[key + "_pct"] = val / total_assets
            else:
                result[key + "_pct"] = None

        return result

    # ── YoY and QoQ changes ───────────────────────────────────────────────────

    @staticmethod
    def yoy_changes(
        current_row: Dict[str, Optional[float]],
        prior_row: Dict[str, Optional[float]],
    ) -> Dict[str, Dict[str, Optional[float]]]:
        """
        Compute year-over-year absolute and percentage changes for all numeric fields.

        Returns dict: field → {"absolute": float|None, "pct": float|None}
        pct = (current - prior) / |prior|, None if prior is 0 or either is None.
        """
        result: Dict[str, Dict[str, Optional[float]]] = {}
        # Skip date fields and any metadata keys (_prefix); only compute changes on numerics
        skip_keys = {"period_end_date", "_period_end_date"}

        all_keys = set(current_row.keys()) | set(prior_row.keys())
        for key in all_keys:
            if key in skip_keys or key.startswith("_"):
                continue
            curr = current_row.get(key)
            prior = prior_row.get(key)
            # Skip non-numeric values (e.g. string metadata that slipped through)
            if curr is not None and not isinstance(curr, (int, float)):
                curr = None
            if prior is not None and not isinstance(prior, (int, float)):
                prior = None

            if curr is None or prior is None:
                result[key] = {"absolute": None, "pct": None}
                continue

            absolute = curr - prior
            if prior == 0:
                pct = None  # Avoid division by zero
            else:
                pct = (curr - prior) / abs(prior)

            result[key] = {"absolute": absolute, "pct": pct}

        return result

    @staticmethod
    def qoq_changes(
        current_q: Dict[str, Optional[float]],
        prior_q: Dict[str, Optional[float]],
    ) -> Dict[str, Dict[str, Optional[float]]]:
        """
        Compute quarter-over-quarter changes. Same logic as yoy_changes.
        Returns empty dict when called with the same row (caller should check
        if quarterly data is available before calling).
        """
        return StatementStandardizer.yoy_changes(current_q, prior_q)

    # ── Growth metrics ─────────────────────────────────────────────────────────

    @staticmethod
    def revenue_growth_yoy(
        current_revenue: Optional[float], prior_revenue: Optional[float]
    ) -> Optional[float]:
        """YoY revenue growth = (current - prior) / |prior|. None if unavailable."""
        if current_revenue is None or prior_revenue is None:
            return None
        if prior_revenue == 0:
            return None
        return (current_revenue - prior_revenue) / abs(prior_revenue)

    @staticmethod
    def net_income_growth_yoy(
        current_ni: Optional[float], prior_ni: Optional[float]
    ) -> Optional[float]:
        """YoY net income growth. None if unavailable.
        Note: meaningful sign interpretation requires checking whether prior was also negative.
        """
        if current_ni is None or prior_ni is None:
            return None
        if prior_ni == 0:
            return None
        return (current_ni - prior_ni) / abs(prior_ni)

    # ── Direction signals ─────────────────────────────────────────────────────

    @staticmethod
    def direction(
        current_val: Optional[float],
        prior_val: Optional[float],
        stable_threshold: float = _STABLE_THRESHOLD,
        higher_is_better: bool = True,
    ) -> str:
        """
        Classify a metric's YoY change as improving / stable / deteriorating.

        Uses ±stable_threshold (default ±2%) band for "stable".
        higher_is_better controls which direction maps to "improving":
          True  → increase = improving (e.g. margins, ROE, revenue growth)
          False → decrease = improving (e.g. D/E, cost ratios)

        Returns "insufficient_history" when either value is missing.
        """
        if current_val is None or prior_val is None:
            return "insufficient_history"

        if prior_val == 0:
            if current_val > 0:
                return "improving" if higher_is_better else "deteriorating"
            elif current_val < 0:
                return "deteriorating" if higher_is_better else "improving"
            else:
                return "stable"

        pct_change = (current_val - prior_val) / abs(prior_val)

        if abs(pct_change) <= stable_threshold:
            return "stable"

        if pct_change > 0:
            return "improving" if higher_is_better else "deteriorating"
        else:
            return "deteriorating" if higher_is_better else "improving"

    @classmethod
    def compute_directions(
        cls,
        current_income: Dict[str, Optional[float]],
        prior_income: Dict[str, Optional[float]],
        current_balance: Dict[str, Optional[float]],
        prior_balance: Dict[str, Optional[float]],
        current_ratios: Dict[str, Optional[float]],
        prior_ratios: Dict[str, Optional[float]],
    ) -> Dict[str, str]:
        """
        Compute direction signals for all key metrics.

        Returns dict: metric_name → direction string.
        """
        dirs: Dict[str, str] = {}

        def d(curr_dict, prior_dict, key, higher_is_better=True) -> str:
            return cls.direction(
                curr_dict.get(key), prior_dict.get(key),
                higher_is_better=higher_is_better,
            )

        # Profitability (higher is better)
        dirs["revenue"] = d(current_income, prior_income, "revenue")
        dirs["gross_profit"] = d(current_income, prior_income, "gross_profit")
        dirs["operating_income"] = d(current_income, prior_income, "operating_income")
        dirs["net_income"] = d(current_income, prior_income, "net_income")
        dirs["gross_margin"] = d(current_ratios, prior_ratios, "gross_margin")
        dirs["operating_margin"] = d(current_ratios, prior_ratios, "operating_margin")
        dirs["net_margin"] = d(current_ratios, prior_ratios, "net_margin")
        dirs["roe"] = d(current_ratios, prior_ratios, "roe")
        dirs["roa"] = d(current_ratios, prior_ratios, "roa")

        # Leverage (lower is better for D/E)
        dirs["debt_to_equity"] = d(current_ratios, prior_ratios, "debt_to_equity",
                                   higher_is_better=False)
        dirs["total_liabilities"] = d(current_balance, prior_balance, "total_liabilities",
                                      higher_is_better=False)

        # Liquidity (higher is better for current ratio)
        dirs["current_ratio"] = d(current_ratios, prior_ratios, "current_ratio")
        dirs["cash_and_equivalents"] = d(current_balance, prior_balance, "cash_and_equivalents")

        # Equity (higher is better)
        dirs["total_equity"] = d(current_balance, prior_balance, "total_equity")
        dirs["total_assets"] = d(current_balance, prior_balance, "total_assets")

        # Valuation (contextual — reporting direction without "good/bad" judgment)
        dirs["pe_ratio"] = d(current_ratios, prior_ratios, "pe_ratio")
        dirs["pb_ratio"] = d(current_ratios, prior_ratios, "pb_ratio")
        dirs["eps"] = d(current_ratios, prior_ratios, "eps")

        return dirs

    # ── Full standardization pass ─────────────────────────────────────────────

    @classmethod
    def standardize(
        cls,
        *,
        current_income: Dict[str, Optional[float]],
        current_balance: Dict[str, Optional[float]],
        current_ratios: Dict[str, Optional[float]],
        prior_income: Optional[Dict[str, Optional[float]]] = None,
        prior_balance: Optional[Dict[str, Optional[float]]] = None,
        prior_ratios: Optional[Dict[str, Optional[float]]] = None,
        prior_q_income: Optional[Dict[str, Optional[float]]] = None,
        prior_q_balance: Optional[Dict[str, Optional[float]]] = None,
    ) -> Dict[str, Any]:
        """
        Run the full standardization pipeline for one period.

        Args:
          current_*: Current period data
          prior_*: Prior annual period data (None = single period only)
          prior_q_*: Prior quarterly period data (None = annual only)

        Returns dict with all preprocessing outputs.
        """
        result: Dict[str, Any] = {}

        # Common-size
        result["common_size_income"] = cls.common_size_income(current_income)
        result["common_size_balance"] = cls.common_size_balance(current_balance)

        # YoY changes and growth
        if prior_income is not None and prior_balance is not None:
            result["yoy_changes_income"] = cls.yoy_changes(current_income, prior_income)
            result["yoy_changes_balance"] = cls.yoy_changes(current_balance, prior_balance)
            result["yoy_changes_ratios"] = cls.yoy_changes(
                current_ratios, prior_ratios or {}
            )
            result["revenue_growth_yoy"] = cls.revenue_growth_yoy(
                current_income.get("revenue"), prior_income.get("revenue")
            )
            result["net_income_growth_yoy"] = cls.net_income_growth_yoy(
                current_income.get("net_income"), prior_income.get("net_income")
            )
        else:
            result["yoy_changes_income"] = None
            result["yoy_changes_balance"] = None
            result["yoy_changes_ratios"] = None
            result["revenue_growth_yoy"] = None
            result["net_income_growth_yoy"] = None
            result["_yoy_note"] = "insufficient_history: only 1 period available"

        # QoQ changes
        if prior_q_income is not None and prior_q_balance is not None:
            result["qoq_changes_income"] = cls.qoq_changes(current_income, prior_q_income)
            result["qoq_changes_balance"] = cls.qoq_changes(current_balance, prior_q_balance)
        else:
            result["qoq_changes_income"] = None
            result["qoq_changes_balance"] = None
            result["_qoq_note"] = "not_available: annual filer or no prior quarter data"

        # Direction signals
        if prior_income is not None:
            result["directions"] = cls.compute_directions(
                current_income=current_income,
                prior_income=prior_income,
                current_balance=current_balance,
                prior_balance=prior_balance or {},
                current_ratios=current_ratios,
                prior_ratios=prior_ratios or {},
            )
        else:
            # Single period — mark all as insufficient_history
            all_metrics = [
                "revenue", "gross_profit", "operating_income", "net_income",
                "gross_margin", "operating_margin", "net_margin", "roe", "roa",
                "debt_to_equity", "total_liabilities", "current_ratio",
                "cash_and_equivalents", "total_equity", "total_assets",
                "pe_ratio", "pb_ratio", "eps",
            ]
            result["directions"] = {m: "insufficient_history" for m in all_metrics}

        return result
