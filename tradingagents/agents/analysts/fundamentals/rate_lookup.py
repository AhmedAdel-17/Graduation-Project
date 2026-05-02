"""
Date-aware CBE (Central Bank of Egypt) policy rate lookup for the EGX Fundamental Analyst.

Problem solved:
  Using a single static risk-free rate (e.g. 0.275 = late-2024 CBE rate) for all
  historical backtests creates temporal leakage. A 2020 analysis should use the 9.25%
  rate in effect after the March 2020 COVID emergency cut, not the 2024 rate.

Solution:
  A local CSV file stores CBE overnight deposit rate changes with their effective_date.
  The lookup function returns the latest rate whose effective_date <= trade_date,
  ensuring no future rate information is used.

Data file:
  tradingagents/dataflows/data_cache/egx_macro/cbe_policy_rates.csv
  Required columns (used by lookup):
    effective_date       — ISO date (YYYY-MM-DD) when CBE decision took effect
    rate                 — overnight deposit rate as a decimal (e.g. 0.0925 = 9.25%)
  Documentation columns (ignored by lookup, preserved for audit trail):
    source_name          — name of the decision body
    source_url           — canonical URL for CBE MPC decisions (verify manually)
    verification_status  — "synthetic_anchor" | "provisional" | "high_confidence_provisional"
                           | "corroborated_in_project" | "provisional_gap_risk"
    note                 — human-readable audit note explaining the row

Fallback chain:
  1. Date-aware CSV lookup (source = "date_aware_cbe_policy_rate")
  2. Static config value  (source = "static_config_fallback")
  3. None                 (source = "not_configured")

IMPORTANT: The CSV data is PROVISIONAL — reconstructed from public CBE announcements.
  The overnight deposit rate is used (lower of the two corridor rates).
  Rows marked "PROVISIONAL" in the note column should be verified against
  official CBE Monetary Policy Committee press releases before use in
  production or academic work.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Default path: tradingagents/dataflows/data_cache/egx_macro/cbe_policy_rates.csv
_DEFAULT_RATES_CSV: Path = (
    Path(__file__).parent  # fundamentals/
    .parent                # analysts/
    .parent                # agents/
    .parent                # tradingagents/
    / "dataflows"
    / "data_cache"
    / "egx_macro"
    / "cbe_policy_rates.csv"
)

# Return type alias for clarity
RateLookupResult = Tuple[Optional[float], str, Optional[str]]
# (rate, source_label, effective_date_iso)


def get_egx_risk_free_rate_as_of(
    trade_date: str,
    config: dict,
    rates_csv_path: Optional[Path] = None,
) -> RateLookupResult:
    """
    Return the CBE overnight deposit rate effective as of trade_date.

    Lookup is strictly backwards-looking: only rates with
    effective_date <= trade_date are considered. Future rate
    changes are never visible.

    Args:
        trade_date: ISO date string (YYYY-MM-DD), e.g. "2022-06-30"
        config: Trading config dict (from get_config())
        rates_csv_path: Override path for testing. Defaults to _DEFAULT_RATES_CSV.

    Returns:
        Tuple of (rate, source_label, effective_date):
          rate:           float or None
          source_label:   "date_aware_cbe_policy_rate"
                          | "static_config_fallback"
                          | "not_configured"
          effective_date: ISO date string when rate took effect, or None
    """
    csv_path = rates_csv_path if rates_csv_path is not None else _DEFAULT_RATES_CSV

    # ── Tier 1: Date-aware CSV lookup ─────────────────────────────────────────
    if csv_path.exists():
        try:
            rate, eff_date = _lookup_rate_from_csv(csv_path, trade_date)
            if rate is not None and eff_date is not None:
                logger.debug(
                    "rate_lookup[%s]: date-aware CBE rate %.4f (effective %s)",
                    trade_date, rate, eff_date,
                )
                return rate, "date_aware_cbe_policy_rate", eff_date
            else:
                # CSV exists but no row covers this date (trade_date is before
                # the earliest row). Fall through to static config.
                logger.debug(
                    "rate_lookup[%s]: no CBE rate found on/before this date in CSV",
                    trade_date,
                )
        except Exception as exc:
            logger.warning(
                "rate_lookup[%s]: CSV lookup failed (%s); falling back to static config",
                trade_date, exc,
            )

    # ── Tier 2: Static config fallback ────────────────────────────────────────
    static_rate = config.get("egx_risk_free_rate", None)
    if static_rate is not None:
        try:
            rate_float = float(static_rate)
        except (ValueError, TypeError):
            logger.warning("rate_lookup: egx_risk_free_rate in config is not a valid float")
            return None, "not_configured", None
        logger.debug(
            "rate_lookup[%s]: using static_config_fallback %.4f",
            trade_date, rate_float,
        )
        return rate_float, "static_config_fallback", None

    # ── Tier 3: Not configured ─────────────────────────────────────────────────
    return None, "not_configured", None


def _lookup_rate_from_csv(
    csv_path: Path,
    trade_date: str,
) -> Tuple[Optional[float], Optional[str]]:
    """
    Read the CSV and return the latest (rate, effective_date) pair
    where effective_date <= trade_date.

    Uses plain string comparison for dates — safe because dates are ISO
    format (YYYY-MM-DD) and lexicographic order equals chronological order.

    Raises:
        OSError: if the file cannot be read
        ValueError: if a row has a malformed rate field (skipped with warning)
    """
    best_rate: Optional[float] = None
    best_date: Optional[str] = None

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            eff_date = row.get("effective_date", "").strip()
            rate_str = row.get("rate", "").strip()

            if not eff_date or not rate_str:
                continue  # skip blank rows

            # Temporal safety: only use rates in effect ON OR BEFORE trade_date
            if eff_date > trade_date:
                continue

            try:
                rate_val = float(rate_str)
            except ValueError:
                logger.warning(
                    "rate_lookup: skipping row with invalid rate value %r (date: %s)",
                    rate_str, eff_date,
                )
                continue

            # Keep the most recent qualifying row
            if best_date is None or eff_date > best_date:
                best_date = eff_date
                best_rate = rate_val

    return best_rate, best_date
