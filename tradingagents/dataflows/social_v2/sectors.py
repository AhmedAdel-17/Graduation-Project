"""EGX sector taxonomy + sector-talk keyword detection.

Two purposes:

1. **Ticker → sector lookup.** Used by the layered aggregator to roll
   ticker-level sentiment up into sector-level sentiment.
2. **Sector keyword detection.** Lets a post that talks about an entire
   sector ("the banking sector is under pressure", "العقارات في مأزق")
   contribute to the *sector* layer even when it names no specific ticker.

Kept deterministic and dependency-free — same style as ``entities.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set

from tradingagents.utils.text_preprocessor import normalize_text


# Canonical sector codes. Keep these stable — downstream consumers key on them.
SECTORS = (
    "BANKS",
    "REAL_ESTATE",
    "INDUSTRY_MATERIALS",
    "TELECOM_TECH",
    "FINANCIAL_SERVICES",
    "FOOD_BEVERAGE",
    "HEALTHCARE_PHARMA",
    "EDUCATION",
    "TRANSPORT_LOGISTICS",
    "ENERGY_PETROCHEM",
)


# Ticker → sector. Mirrors the breakdown in CLAUDE.md §10 plus a handful of
# obvious assignments for the rest of the SYMBOL_REGISTRY. Unknown tickers
# default to "OTHER" via ticker_sector() below.
TICKER_SECTOR: Dict[str, str] = {
    # Banks
    "COMI": "BANKS", "ADIB": "BANKS", "CIEB": "BANKS", "EXPA": "BANKS",
    "HDBK": "BANKS", "QNBA": "BANKS", "QNBE": "BANKS", "SAUD": "BANKS",
    "FAIT": "BANKS", "CANA": "BANKS", "UBEE": "BANKS", "EGBE": "BANKS",
    # Real Estate
    "TMGH": "REAL_ESTATE", "HELI": "REAL_ESTATE", "PHDC": "REAL_ESTATE",
    "OCDI": "REAL_ESTATE", "ORAS": "REAL_ESTATE", "EMFD": "REAL_ESTATE",
    "MASR": "REAL_ESTATE", "ORHD": "REAL_ESTATE", "EGTS": "REAL_ESTATE",
    "GPPL": "REAL_ESTATE", "SPHT": "REAL_ESTATE", "GIHD": "REAL_ESTATE",
    "MHOT": "REAL_ESTATE",
    # Industry & Materials (incl. steel, fertilizers, cement, chemicals)
    "EAST": "INDUSTRY_MATERIALS", "ESRS": "INDUSTRY_MATERIALS",
    "SWDY": "INDUSTRY_MATERIALS", "ABUK": "INDUSTRY_MATERIALS",
    "MFPC": "INDUSTRY_MATERIALS", "EGAL": "INDUSTRY_MATERIALS",
    "EGCH": "INDUSTRY_MATERIALS", "EFIC": "INDUSTRY_MATERIALS",
    "ARCC": "INDUSTRY_MATERIALS", "MCQE": "INDUSTRY_MATERIALS",
    "SCEM": "INDUSTRY_MATERIALS", "MBSC": "INDUSTRY_MATERIALS",
    "IRON": "INDUSTRY_MATERIALS", "ATQA": "INDUSTRY_MATERIALS",
    "ISMQ": "INDUSTRY_MATERIALS", "ORWE": "INDUSTRY_MATERIALS",
    "ELEC": "INDUSTRY_MATERIALS", "FERC": "INDUSTRY_MATERIALS",
    # Telecom / Tech
    "ETEL": "TELECOM_TECH", "FWRY": "TELECOM_TECH",
    "EFIH": "TELECOM_TECH", "RAYA": "TELECOM_TECH",
    "EGSA": "TELECOM_TECH", "SCTS": "TELECOM_TECH",
    "MTIE": "TELECOM_TECH",
    # Financial services (non-bank)
    "HRHO": "FINANCIAL_SERVICES", "BTFH": "FINANCIAL_SERVICES",
    "CICH": "FINANCIAL_SERVICES", "VALU": "FINANCIAL_SERVICES",
    "GBCO": "FINANCIAL_SERVICES", "CCAP": "FINANCIAL_SERVICES",
    "BINV": "FINANCIAL_SERVICES", "VLMR": "FINANCIAL_SERVICES",
    "MOIN": "FINANCIAL_SERVICES",
    # Food & Beverage
    "JUFO": "FOOD_BEVERAGE", "EFID": "FOOD_BEVERAGE",
    "DOMT": "FOOD_BEVERAGE", "OLFI": "FOOD_BEVERAGE",
    "POUL": "FOOD_BEVERAGE", "SUGR": "FOOD_BEVERAGE",
    "IFAP": "FOOD_BEVERAGE",
    # Healthcare / Pharma
    "CLHO": "HEALTHCARE_PHARMA", "ISPH": "HEALTHCARE_PHARMA",
    "PHAR": "HEALTHCARE_PHARMA", "MIPH": "HEALTHCARE_PHARMA",
    "RMDA": "HEALTHCARE_PHARMA", "MPCI": "HEALTHCARE_PHARMA",
    "AMES": "HEALTHCARE_PHARMA", "NIPH": "HEALTHCARE_PHARMA",
    # Education
    "CIRA": "EDUCATION", "TALM": "EDUCATION",
    # Transport / Logistics
    "ALCN": "TRANSPORT_LOGISTICS", "CSAG": "TRANSPORT_LOGISTICS",
    # Energy / Petrochem
    "SKPC": "ENERGY_PETROCHEM", "AMOC": "ENERGY_PETROCHEM",
    "MOIL": "ENERGY_PETROCHEM", "TAQA": "ENERGY_PETROCHEM",
    "OIH": "ENERGY_PETROCHEM", "BONY": "ENERGY_PETROCHEM",
}


# Sector-talk keywords. EN + AR. A post that contains any of these *without*
# naming a specific ticker still contributes to the sector layer.
#
# Be conservative: terms here MUST be unambiguous sector pointers, otherwise
# we will assign generic discussion (e.g. "real estate") to the EGX real-estate
# basket when the writer is talking about Dubai property.
SECTOR_KEYWORDS: Dict[str, Set[str]] = {
    "BANKS": {
        "banking sector", "banks sector", "egyptian banks", "bank stocks",
        "القطاع المصرفي", "قطاع البنوك", "البنوك المصرية", "أسهم البنوك",
        "اسهم البنوك", "القطاع البنكي",
    },
    "REAL_ESTATE": {
        "real estate sector", "egyptian real estate", "property developers",
        "real estate developers", "egyptian developers",
        "قطاع العقارات", "القطاع العقاري", "الأسهم العقارية", "الاسهم العقارية",
        "شركات التطوير العقاري", "العقارات المصرية",
    },
    "INDUSTRY_MATERIALS": {
        "industrial sector", "steel sector", "cement sector", "fertilizer sector",
        "egyptian industrials", "egyptian steel", "egyptian cement",
        "القطاع الصناعي", "قطاع الصناعة", "قطاع الحديد", "قطاع الأسمنت",
        "قطاع الاسمنت", "قطاع الأسمدة", "قطاع الاسمدة", "الصناعات المصرية",
    },
    "TELECOM_TECH": {
        "telecom sector", "telecoms sector", "egyptian telecom", "fintech sector",
        "قطاع الاتصالات", "قطاع التكنولوجيا", "قطاع التكنولوجيا المالية",
        "شركات الاتصالات",
    },
    "FINANCIAL_SERVICES": {
        "brokerage sector", "investment banks", "non-bank financial",
        "قطاع الخدمات المالية", "شركات السمسرة", "بنوك الاستثمار",
        "الخدمات المالية غير المصرفية",
    },
    "FOOD_BEVERAGE": {
        "food sector", "food and beverage sector", "egyptian fmcg",
        "قطاع الأغذية", "قطاع الاغذية", "قطاع المواد الغذائية",
        "قطاع المشروبات", "شركات الغذاء",
    },
    "HEALTHCARE_PHARMA": {
        "pharma sector", "pharmaceutical sector", "healthcare sector",
        "egyptian pharma", "egyptian healthcare",
        "قطاع الأدوية", "قطاع الادوية", "قطاع الدواء",
        "قطاع الرعاية الصحية", "شركات الأدوية", "شركات الادوية",
    },
    "EDUCATION": {
        "education sector", "egyptian education",
        "قطاع التعليم", "شركات التعليم",
    },
    "TRANSPORT_LOGISTICS": {
        "shipping sector", "logistics sector", "egyptian shipping",
        "قطاع النقل", "قطاع الشحن", "قطاع الموانئ", "اللوجستيات",
    },
    "ENERGY_PETROCHEM": {
        "energy sector", "petrochemical sector", "petrochemicals sector",
        "egyptian petrochem",
        "قطاع الطاقة", "قطاع البتروكيماويات", "قطاع البترول",
        "شركات البترول", "البتروكيماويات المصرية",
    },
}


_NORMALIZED_SECTOR_KEYWORDS: Dict[str, List[re.Pattern]] = {
    sector: [
        re.compile(
            r"(?<![0-9A-Za-z؀-ۿ])"
            + r"\s+".join(re.escape(w) for w in normalize_text(kw).lower().split())
            + r"(?![0-9A-Za-z؀-ۿ])"
        )
        for kw in keywords
        if normalize_text(kw).strip()
    ]
    for sector, keywords in SECTOR_KEYWORDS.items()
}


@dataclass
class SectorMention:
    sector: str
    confidence: float
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sector": self.sector,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence[:3],
        }


def ticker_sector(ticker: str) -> str:
    """Return canonical sector code for a ticker. Falls back to 'OTHER'."""
    return TICKER_SECTOR.get(ticker.upper().replace(".CA", ""), "OTHER")


def extract_sector_mentions(text: str) -> List[SectorMention]:
    """Detect *sector-level* talk in free text.

    Returns one SectorMention per matched sector. Confidence is fixed at
    0.7 — sector keywords are an explicit signal but less precise than a
    ticker mention.
    """
    if not text:
        return []
    normalized = normalize_text(text).lower()
    if not normalized:
        return []
    out: List[SectorMention] = []
    for sector, patterns in _NORMALIZED_SECTOR_KEYWORDS.items():
        for pattern in patterns:
            m = pattern.search(normalized)
            if m:
                out.append(SectorMention(
                    sector=sector,
                    confidence=0.7,
                    evidence=[f"sector-kw:{m.group(0)[:40]}"],
                ))
                break
    return out


def sectors_from_ticker_mentions(ticker_symbols: List[str]) -> Dict[str, float]:
    """Roll up ticker mentions into sector confidences.

    A post that mentions COMI + HDBK contributes 2 votes to BANKS. We return
    a dict {sector: max_confidence} where confidence is 0.9 (a confirmed
    ticker mention is a stronger sector signal than a generic sector keyword).
    """
    out: Dict[str, float] = {}
    for sym in ticker_symbols:
        sector = ticker_sector(sym)
        if sector == "OTHER":
            continue
        out[sector] = max(out.get(sector, 0.0), 0.9)
    return out
