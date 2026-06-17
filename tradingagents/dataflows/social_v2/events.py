"""Special-event taxonomy + detector.

Detects mentions of macro / geopolitical / shock events that move the
*whole market* regardless of which specific tickers are named. These feed
the *events* layer of the layered sentiment aggregator so the agents can
reason about background risk (e.g., "social mood about the EGP devaluation
is sharply bearish this week" — independent of any single stock).

The taxonomy is intentionally narrow and EGX-relevant: we list the
recurring macro topics that visibly move EGX prices. Add new categories
only when there is durable signal and an obvious unambiguous keyword set
(otherwise this becomes a topic-modeling problem).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set

from tradingagents.utils.text_preprocessor import normalize_text


EVENTS = (
    "WAR_REGIONAL",      # Gaza war, Israel-Hamas, Sudan war, Houthi/Red Sea
    "PANDEMIC",          # COVID-19, new outbreaks
    "EGP_DEVALUATION",   # Pound float / FX moves
    "IMF_DEAL",          # IMF programme, tranches, conditions
    "INTEREST_RATE",     # CBE rate decisions
    "INFLATION",         # Inflation prints, cost of living
    "OIL_PRICE",         # Brent moves, OPEC actions
    "ELECTIONS",         # Egyptian or regional elections
    "REGULATION",        # EGX / FRA / capital controls rules changes
    "SOVEREIGN_DEBT",    # Egypt rating, eurobonds, default risk
)


# EN + AR keyword sets per event. Keep these strict and unambiguous —
# detection here is treated as a confident topic signal downstream.
EVENT_KEYWORDS: Dict[str, Set[str]] = {
    "WAR_REGIONAL": {
        "gaza war", "israel hamas", "israel-hamas", "war on gaza",
        "red sea attacks", "red sea crisis", "houthi attacks", "houthis",
        "suez canal disruption", "sudan war",
        "حرب غزة", "العدوان على غزة", "العدوان الإسرائيلي", "العدوان الاسرائيلي",
        "الحرب في غزة", "أزمة البحر الأحمر", "ازمة البحر الاحمر",
        "الحوثيين", "الحوثيون", "هجمات الحوثيين", "حرب السودان",
        "اضطراب قناة السويس",
    },
    "PANDEMIC": {
        "covid", "covid-19", "covid 19", "coronavirus", "pandemic", "lockdown",
        "كورونا", "فيروس كورونا", "كوفيد", "كوفيد-19", "كوفيد 19",
        "جائحة", "وباء", "إغلاق", "اغلاق", "حظر تجول",
    },
    "EGP_DEVALUATION": {
        "egp devaluation", "pound devaluation", "egyptian pound float",
        "egp float", "currency devaluation egypt", "egyptian currency crisis",
        "تعويم الجنيه", "تعويم العملة", "انخفاض الجنيه", "تخفيض الجنيه",
        "أزمة الجنيه", "ازمة الجنيه", "انهيار الجنيه", "أزمة الدولار",
        "ازمة الدولار", "سعر الصرف",
    },
    "IMF_DEAL": {
        "imf deal", "imf programme", "imf program", "imf tranche",
        "international monetary fund egypt",
        "صندوق النقد الدولي", "صندوق النقد", "اتفاق صندوق النقد",
        "قرض صندوق النقد", "شريحة صندوق النقد", "برنامج صندوق النقد",
    },
    "INTEREST_RATE": {
        "interest rate hike", "interest rate cut", "rate decision",
        "central bank of egypt", "cbe rate", "monetary policy committee",
        "سعر الفائدة", "أسعار الفائدة", "اسعار الفائدة", "رفع الفائدة",
        "خفض الفائدة", "البنك المركزي", "البنك المركزي المصري",
        "لجنة السياسة النقدية", "اجتماع المركزي",
    },
    "INFLATION": {
        "inflation rate", "consumer prices", "cpi egypt", "cost of living",
        "egyptian inflation",
        "التضخم", "معدل التضخم", "أسعار المستهلكين", "اسعار المستهلكين",
        "غلاء الأسعار", "غلاء الاسعار", "ارتفاع الأسعار", "ارتفاع الاسعار",
        "موجة الغلاء",
    },
    "OIL_PRICE": {
        "oil price", "brent crude", "wti crude", "opec cut", "opec decision",
        "أسعار النفط", "اسعار النفط", "أسعار البترول", "اسعار البترول",
        "خام برنت", "أوبك", "اوبك", "قرار أوبك", "قرار اوبك",
    },
    "ELECTIONS": {
        "presidential elections", "egyptian elections", "parliamentary elections",
        "election results egypt",
        "الانتخابات الرئاسية", "الانتخابات البرلمانية", "انتخابات مصر",
        "نتائج الانتخابات", "الانتخابات المصرية",
    },
    "REGULATION": {
        "egx regulation", "fra decision", "capital controls", "trading halt",
        "trading suspension", "circuit breaker egx", "price limit change",
        "قرار البورصة", "قرار الهيئة العامة للرقابة المالية",
        "الرقابة المالية", "قواعد التداول", "تعليق التداول",
        "إيقاف التداول", "ايقاف التداول", "حدود التداول السعرية",
    },
    "SOVEREIGN_DEBT": {
        "egypt rating downgrade", "egypt rating upgrade", "moody's egypt",
        "fitch egypt", "s&p egypt", "egyptian eurobonds", "sovereign default",
        "تصنيف مصر الائتماني", "خفض تصنيف مصر", "رفع تصنيف مصر",
        "سندات مصر الدولية", "اليوروبوند", "ديون مصر السيادية",
    },
}


_NORMALIZED_EVENT_PATTERNS: Dict[str, List[re.Pattern]] = {
    event: [
        re.compile(
            r"(?<![0-9A-Za-z؀-ۿ])"
            + r"\s+".join(re.escape(w) for w in normalize_text(kw).lower().split())
            + r"(?![0-9A-Za-z؀-ۿ])"
        )
        for kw in keywords
        if normalize_text(kw).strip()
    ]
    for event, keywords in EVENT_KEYWORDS.items()
}


@dataclass
class EventMention:
    event: str
    confidence: float
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "event": self.event,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence[:3],
        }


def extract_events(text: str) -> List[EventMention]:
    """Return all event categories matched in the given text."""
    if not text:
        return []
    normalized = normalize_text(text).lower()
    if not normalized:
        return []
    out: List[EventMention] = []
    for event, patterns in _NORMALIZED_EVENT_PATTERNS.items():
        for pattern in patterns:
            m = pattern.search(normalized)
            if m:
                out.append(EventMention(
                    event=event,
                    confidence=0.8,
                    evidence=[f"event-kw:{m.group(0)[:40]}"],
                ))
                break
    return out
