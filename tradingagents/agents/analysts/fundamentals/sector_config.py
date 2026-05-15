"""
Sector classification and configuration for EGX Fundamental Analyst.

4 sector profiles replace the prior 2-sector (financials / non-financials) split.
The two-sector split flagged every Egyptian bank as critically leveraged and
every real estate developer as absurdly overvalued on P/B — both wrong.

Sectors:
  banks        — COMI, NBE, BDC, AUDI, ADIB, etc.
  real_estate  — OCDI, PHD, MNHD, etc.
  holdings     — HRHO, SWDY, etc.
  operational  — Default. All other companies (telecom, cement, consumer, etc.)

Evidence class: [EI] — sector classification boundaries are engineering judgment.
No paper provides EGX-specific sector-to-threshold mapping.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

# =============================================================================
# Sector → Ticker mapping
# Known EGX30 tickers assigned to their correct sector profile.
# Tickers not in this map default to "operational".
# =============================================================================

SECTOR_MAP: Dict[str, str] = {
    # Banks
    "COMI": "banks",
    "NBE": "banks",
    "BDC": "banks",
    "AUDI": "banks",
    "ADIB": "banks",
    "CIEB": "banks",
    "FAWRY": "banks",    # digital payments — operational semantics, but bank regulatory framework
    "QNBA": "banks",
    "MIDB": "banks",
    "EGBE": "banks",
    "ALEX": "banks",
    "DCRC": "banks",
    "ARCC": "banks",     # Arab African International Bank
    "BTFH": "banks",     # Banque du Trésor (Egyptian banking subsidiary)
    "EGAL": "banks",     # Al Ahly Bank Egypt (banking institution)

    # Real estate / developers
    "OCDI": "real_estate",
    "PHD": "real_estate",
    "PHDC": "real_estate",     # Palm Hills Development Company (same entity as PHD)
    "MNHD": "real_estate",
    "EMFD": "real_estate",
    "TALAAT": "real_estate",   # Talaat Moustafa Group
    "TMG": "real_estate",
    "SODIC": "real_estate",
    "HELI": "real_estate",     # Heliopolis Housing

    # Holdings / diversified
    "HRHO": "holdings",
    "SWDY": "holdings",
    "EFG": "holdings",
    "EFGD": "holdings",

    # Operational (explicitly mapped for clarity; would also be default)
    "ETEL": "operational",    # Telecom Egypt
    "SVCE": "operational",    # South Valley Cement
    "HEMM": "operational",    # Hassan Allam Construction
    "SPMD": "operational",    # Sphinx Medical
    "JUFO": "operational",    # Juhayna Food Industries — consumer/food, not a holding company
    "ECMI": "operational",
    "MFPC": "operational",
    "AMER": "operational",
    "ABUK": "operational",
    "GSCO": "operational",
    "SUGR": "operational",
    "KABO": "operational",
}


def classify_sector(ticker: str) -> str:
    """
    Return the sector for a given EGX ticker.
    Defaults to 'operational' for unknown tickers.
    """
    return SECTOR_MAP.get(ticker.upper(), "operational")


# =============================================================================
# Metric applicability rules per sector
# True = metric is applicable and should be computed + included in evidence pack
# False = metric is structurally inapplicable for this sector
# =============================================================================

# Which metrics are excluded per sector (not applicable — omit from evidence pack)
METRIC_EXCLUSIONS: Dict[str, Set[str]] = {
    "banks": {
        "current_ratio",      # Banks don't have current assets/liabilities in same sense
        "debt_to_equity",     # D/E 5-10× is structurally normal; including raw value misleads
        # Note: D/E is still computed but flagged with context note, not safety-floor alert
    },
    "real_estate": {
        # All metrics computed but PB gets a special informational flag
    },
    "holdings": {
        # All metrics computed but with CONSOLIDATED_BLENDING flag
    },
    "operational": {
        # No exclusions — all metrics applicable
    },
}

# Which metrics get special contextual notes in the evidence pack
METRIC_CONTEXT_NOTES: Dict[str, Dict[str, str]] = {
    "banks": {
        "debt_to_equity": "For banks, D/E 5-10× is structurally normal due to deposit leverage. Do not apply non-financial thresholds.",
        "current_ratio": "Current ratio is not a meaningful liquidity metric for banks. Capital adequacy ratio would be relevant but is not in CSV data.",
        "net_margin": "Net margin for banks reflects spread on lending operations. NIM (Net Interest Margin) is the primary profitability metric but is not available in CSV data.",
        "pe_ratio": "P/E for banks should be interpreted relative to ROE and book value. Banks typically trade at P/B 1.5-3× in EGX.",
        "pb_ratio": "For banks, P/B 1.5-3× is structurally normal in EGX. Higher multiples reflect franchise value.",
    },
    "real_estate": {
        "pb_ratio": "Real estate developers carry land at historical cost. Book value is understated by 30-60% vs. market value. P/B < 1× does not indicate undervaluation for this sector.",
        "revenue_growth_yoy": "Real estate revenue recognition uses percent-completion method. YoY changes reflect project completion timing, not underlying demand trends.",
        "debt_to_equity": "D/E from project-financing debt is structurally elevated during development phases. Compare to peers, not to non-financial thresholds.",
    },
    "holdings": {
        "roe": "ROE is a blended measure across all subsidiaries. Individual subsidiary performance is masked in consolidated financials.",
        "debt_to_equity": "D/E includes subsidiary leverage. May be higher than pure holding company leverage.",
        "net_margin": "Net margin reflects consolidated operations. Minority interest adjustments may affect reported figures.",
    },
    "operational": {},  # No special notes for default sector
}


# =============================================================================
# Safety floors — trigger conditions for distress_flags
# Only three hard safety floors + informational flags for non-distress conditions.
# Evidence class: [EI] — floor values are engineering judgment, not sourced thresholds.
# =============================================================================

# Hard safety floors (distress conditions)
SAFETY_FLOORS = {
    # Universal — applies to all sectors
    "NEGATIVE_MARGIN_ALERT": {
        "condition": lambda nm: nm is not None and nm < 0,
        "sectors": {"banks", "real_estate", "holdings", "operational"},
        "description": "Net margin is negative — company is losing money.",
    },
    # Sector-specific — operational only
    "HIGH_LEVERAGE_ALERT": {
        "condition": lambda de: de is not None and de > 5.0,
        "sectors": {"operational"},
        "description": "D/E > 5.0 for non-financial company is a potential distress signal.",
    },
    "LIQUIDITY_EMERGENCY": {
        "condition": lambda cr: cr is not None and cr < 0.5,
        "sectors": {"operational"},
        "description": "Current ratio < 0.5 indicates severe short-term liquidity risk.",
    },
    # Universal edge cases
    "NEGATIVE_EQUITY_ALERT": {
        "condition": lambda te: te is not None and te < 0,
        "sectors": {"banks", "real_estate", "holdings", "operational"},
        "description": "Negative total equity. D/E undefined. ROE sign is reversed.",
    },
    "ZERO_REVENUE_PERIOD": {
        "condition": lambda rev: rev is not None and rev == 0,
        "sectors": {"banks", "real_estate", "holdings", "operational"},
        "description": "Revenue is zero for this period. Net margin and common-size income undefined.",
    },
}

# Informational flags (not distress — context for LLM)
INFORMATIONAL_FLAGS = {
    "PE_UNDEFINED": "EPS <= 0 or P/E data missing. Valuation via P/B only.",
    "PB_UNDERSTATED_HISTORICAL_COST": "Real estate sector: book value understated by historical-cost land valuation.",
    "CONSOLIDATED_BLENDING": "Holdings sector: consolidated financials blend subsidiary performance.",
    "ROE_MARGIN_INCONSISTENCY": "ROE is positive but net_margin is negative — mathematical inconsistency in source data.",
}

# Earnings yield spread labels (tentative — [EI], not sourced)
EY_SPREAD_FLAGS = {
    "EARNINGS_YIELD_ATTRACTIVE": {
        "threshold": 0.05,
        "description": "Earnings yield spread > 5% above risk-free rate. Tentative attractive signal [EI].",
    },
    "EARNINGS_YIELD_COMPRESSED": {
        "threshold": -0.02,
        "description": "Earnings yield spread < -2% (yield below risk-free rate). Tentative compressed signal [EI].",
    },
}


# =============================================================================
# SectorConfig — main interface
# =============================================================================

class SectorConfig:
    """
    Encapsulates sector classification, metric applicability, and alert generation
    for a given ticker.
    """

    def __init__(self, ticker: str):
        self.ticker = ticker.upper()
        self.sector = classify_sector(self.ticker)

    def is_metric_applicable(self, metric_name: str) -> bool:
        """Return True if this metric should appear in the evidence pack for this sector."""
        excluded = METRIC_EXCLUSIONS.get(self.sector, set())
        return metric_name not in excluded

    def get_context_note(self, metric_name: str) -> Optional[str]:
        """Return an interpretive context note for a metric in this sector, or None."""
        sector_notes = METRIC_CONTEXT_NOTES.get(self.sector, {})
        return sector_notes.get(metric_name)

    def generate_distress_flags(
        self,
        net_margin: Optional[float],
        debt_to_equity: Optional[float],
        current_ratio: Optional[float],
        total_equity: Optional[float],
        revenue: Optional[float],
        eps: Optional[float],
        pe_ratio: Optional[float],
        roe: Optional[float],
        earnings_yield_spread: Optional[float] = None,
    ) -> List[str]:
        """
        Generate the list of distress_flags and informational flags for this company.

        Returns a list of flag strings. The list may be empty (no alerts).
        Business distress flags are separate from signal_coherence — do not feed
        these into the coherence score.
        """
        flags: List[str] = []

        # Hard safety floors
        if SAFETY_FLOORS["NEGATIVE_MARGIN_ALERT"]["condition"](net_margin):
            if self.sector in SAFETY_FLOORS["NEGATIVE_MARGIN_ALERT"]["sectors"]:
                flags.append("NEGATIVE_MARGIN_ALERT")

        if SAFETY_FLOORS["HIGH_LEVERAGE_ALERT"]["condition"](debt_to_equity):
            if self.sector in SAFETY_FLOORS["HIGH_LEVERAGE_ALERT"]["sectors"]:
                flags.append("HIGH_LEVERAGE_ALERT")

        if SAFETY_FLOORS["LIQUIDITY_EMERGENCY"]["condition"](current_ratio):
            if self.sector in SAFETY_FLOORS["LIQUIDITY_EMERGENCY"]["sectors"]:
                flags.append("LIQUIDITY_EMERGENCY")

        if SAFETY_FLOORS["NEGATIVE_EQUITY_ALERT"]["condition"](total_equity):
            flags.append("NEGATIVE_EQUITY_ALERT")

        if SAFETY_FLOORS["ZERO_REVENUE_PERIOD"]["condition"](revenue):
            flags.append("ZERO_REVENUE_PERIOD")

        # Informational: P/E undefined
        if eps is not None and eps <= 0:
            flags.append("PE_UNDEFINED")
        elif pe_ratio is None and eps is None:
            flags.append("PE_UNDEFINED")

        # Informational: sector-specific structural notes
        if self.sector == "real_estate":
            flags.append("PB_UNDERSTATED_HISTORICAL_COST")

        if self.sector == "holdings":
            flags.append("CONSOLIDATED_BLENDING")

        # ROE/margin mathematical inconsistency — informational flag
        # (also penalized in signal_coherence, but listed here for evidence pack)
        if roe is not None and roe > 0 and net_margin is not None and net_margin < 0:
            flags.append("ROE_MARGIN_INCONSISTENCY")

        # Earnings yield spread signals (tentative [EI])
        if earnings_yield_spread is not None:
            if earnings_yield_spread > EY_SPREAD_FLAGS["EARNINGS_YIELD_ATTRACTIVE"]["threshold"]:
                flags.append("EARNINGS_YIELD_ATTRACTIVE")
            elif earnings_yield_spread < EY_SPREAD_FLAGS["EARNINGS_YIELD_COMPRESSED"]["threshold"]:
                flags.append("EARNINGS_YIELD_COMPRESSED")

        return flags

    def get_sector_summary(self) -> Dict[str, str]:
        """
        Return a dict of sector-level context notes for evidence pack injection.
        Used by data_cot.py to annotate the evidence pack.
        """
        notes = METRIC_CONTEXT_NOTES.get(self.sector, {})
        return dict(notes)
