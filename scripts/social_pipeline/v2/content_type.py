"""
Content-type classifier — opinion vs news vs analysis vs question.

Heuristic, fast, deterministic. The aggregator weights:
    OPINION  1.0   short-form, first-person trader voice
    NEWS     0.6   factual report, third-person, often a headline
    ANALYSIS 0.4   long-form technical/fundamental write-up
    QUESTION 0.2   user is asking, not predicting
"""

from __future__ import annotations

import re
from dataclasses import dataclass

WEIGHTS = {
    "OPINION":  1.0,
    "NEWS":     0.6,
    "ANALYSIS": 0.4,
    "QUESTION": 0.2,
    "OTHER":    0.3,
}


@dataclass
class ContentType:
    label: str          # OPINION | NEWS | ANALYSIS | QUESTION | OTHER
    weight: float
    reasons: list

    def to_dict(self) -> dict:
        return {"label": self.label,
                "weight": self.weight,
                "reasons": self.reasons[:4]}


# Heuristic signals
_FIRST_PERSON_EN = re.compile(r"\b(i|i'?m|my|me|we|our|imo|imho)\b", re.IGNORECASE)
_FIRST_PERSON_AR = re.compile(r"رأيي|في\s+رأيي|أعتقد|اعتقد|باعتقادي|عندي|محفظتي")
_QUESTION_END    = re.compile(r"[?؟]\s*$")
_QUESTION_OPEN   = re.compile(
    r"^(what|why|when|how|should\s+i|does|can|is\s+it|anyone|"
    r"ايه|ليه|امتى|ازاي|هل|في\s+حد|حد\s+يعرف)",
    re.IGNORECASE,
)
_NEWS_VERBS = re.compile(
    r"\b(announc\w+|reports?|reported|posted\s+earnings|disclos\w+|filed|"
    r"named|appoint\w+|launch\w+|approves?|approved|to\s+acquire|acquir\w+|"
    r"raises?|cuts?|signs?|signed|wins?\s+contract)\b",
    re.IGNORECASE,
)
_NEWS_VERBS_AR = re.compile(
    r"أعلنت|اعلنت|تعلن|كشفت|تكشف|وقعت|توقع|تستحوذ|استحوذت|قررت|تقرر"
)
_HEADLINE_HINT = re.compile(r":\s+\S")  # "Company X: does Y"
_NUMERIC_DENSITY = re.compile(r"\d+(\.\d+)?%?")
_TECH_TERMS = re.compile(
    r"\b(rsi|macd|moving\s+average|ma\s*\d+|fibonacci|retracement|"
    r"support|resistance|head\s+and\s+shoulders|cup\s+and\s+handle|"
    r"divergence|volume\s+profile|p/?e|eps|fcf|dcf|book\s+value)\b",
    re.IGNORECASE,
)
_TECH_TERMS_AR = re.compile(
    r"المتوسط\s+المتحرك|آر\s*اس\s*آي|ماكد|الدعم|المقاومة|"
    r"ربحية\s+السهم|التدفق\s+النقدي"
)


def classify(text: str) -> ContentType:
    if not text:
        return ContentType("OTHER", WEIGHTS["OTHER"], ["empty"])
    t = text.strip()
    low = t.lower()
    n_words = len(re.findall(r"\S+", t))

    reasons = []

    # Question?
    if _QUESTION_END.search(t) or _QUESTION_OPEN.match(low):
        reasons.append("question-marker")
        return ContentType("QUESTION", WEIGHTS["QUESTION"], reasons)

    # News? heuristics: third-person verb of disclosure + no first-person, OR
    # a headline pattern with numbers, OR short factual line.
    is_news_verb = bool(_NEWS_VERBS.search(t) or _NEWS_VERBS_AR.search(t))
    is_first_person = bool(
        _FIRST_PERSON_EN.search(t) or _FIRST_PERSON_AR.search(t))
    if is_news_verb and not is_first_person:
        reasons.append("news-verb-3p")
        return ContentType("NEWS", WEIGHTS["NEWS"], reasons)
    if (n_words <= 25 and _HEADLINE_HINT.search(t)
            and not is_first_person and is_news_verb):
        reasons.append("headline-pattern")
        return ContentType("NEWS", WEIGHTS["NEWS"], reasons)

    # Analysis? long form + technical/fundamental terminology
    tech_hits = (len(_TECH_TERMS.findall(t))
                 + len(_TECH_TERMS_AR.findall(t)))
    num_hits = len(_NUMERIC_DENSITY.findall(t))
    if n_words >= 60 and (tech_hits >= 2 or num_hits >= 4):
        reasons.append(f"long-form-tech(words={n_words},tech={tech_hits},num={num_hits})")
        return ContentType("ANALYSIS", WEIGHTS["ANALYSIS"], reasons)

    # Opinion: short, first-person, trader voice — the GOOD case for signals
    if is_first_person and n_words <= 60:
        reasons.append("first-person-short")
        return ContentType("OPINION", WEIGHTS["OPINION"], reasons)
    if n_words <= 30:
        # short reactions / one-liners are treated as opinion-grade
        reasons.append("short-reaction")
        return ContentType("OPINION", WEIGHTS["OPINION"], reasons)

    reasons.append(f"fallback(words={n_words})")
    return ContentType("OTHER", WEIGHTS["OTHER"], reasons)
