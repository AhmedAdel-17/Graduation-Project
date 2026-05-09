"""
Trading-intent detection.

A post passes the intent gate if it expresses one of:
    BUY      direct buy intent / accumulation
    SELL     direct sell intent / distribution
    BULLISH  prediction up / breakout / target above
    BEARISH  prediction down / breakdown / target below
    REACTION emotional reaction (hype, fear, panic, FOMO, anger)
    HOLD     explicit hold/sit-tight
    NONE     no trading intent (educational, neutral discussion, news report)

Patterns are bilingual (English + MSA + Egyptian-Arabic dialect).

The output is structured: an ordered list of (intent, evidence_phrase) so the
weighted aggregator can apply per-intent weights and so a trader can audit
WHY a post counted as a buy signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class IntentResult:
    intents: List[str] = field(default_factory=list)   # e.g. ["BUY", "BULLISH"]
    evidence: List[str] = field(default_factory=list)  # matched phrases
    score: float = 0.0    # net directional intent: -1 (sell/bearish) .. +1 (buy/bullish)

    @property
    def has_intent(self) -> bool:
        return bool(self.intents) and self.intents != ["NONE"]

    def to_dict(self) -> dict:
        return {"intents": self.intents,
                "evidence": self.evidence[:6],
                "score": round(self.score, 3)}


# ---------------------------------------------------------------------------
# Pattern bank.  Order matters: more specific patterns first.
# Each pattern is (regex, intent_label, polarity).  Polarity contributes to the
# net directional score when summed and clamped to [-1, +1].
# ---------------------------------------------------------------------------
#
# We use word-boundary regex (?<![A-Za-z]) / (?![A-Za-z]) because Arabic words
# don't have \b boundaries the way ASCII does. For Arabic patterns we rely on
# whitespace/punctuation neighbours.

_EN = [
    # BUY
    (r"\bgoing\s+long\b",                       "BUY",      +0.9),
    (r"\b(loaded|loading)\s+up\b",              "BUY",      +0.8),
    (r"\b(bought|buying|gonna\s+buy|going\s+to\s+buy|i\s+buy)\b",
                                                 "BUY",      +0.7),
    (r"\b(accumulat\w*|adding\s+(more|to\s+my))\b",
                                                 "BUY",      +0.7),
    (r"\bdca(?:\b|ing)",                         "BUY",      +0.6),
    (r"\b(strong\s+)?buy(\s+the\s+dip|\s+now|\s+signal)?\b",
                                                 "BUY",      +0.7),

    # SELL
    (r"\b(sold|selling|gonna\s+sell|cutting\s+losses|stopped\s+out|stop\s+loss\s+hit)\b",
                                                 "SELL",     -0.7),
    (r"\b(taking|took)\s+profits?\b",            "SELL",     -0.4),
    (r"\b(dump|dumping|dumped)\b",               "SELL",     -0.7),
    (r"\b(distribut\w+|trim(ming)?\s+position)\b",
                                                 "SELL",     -0.5),
    (r"\bshort(ed|ing)?\b(?!\s+(term|seller|term))","SELL",     -0.6),

    # BULLISH prediction
    (r"\b(price\s+target|pt|target)\s*[:=]?\s*\$?\d",
                                                 "BULLISH",  +0.5),
    (r"\bbreakout\b",                            "BULLISH",  +0.7),
    (r"\b(moon|to\s+the\s+moon|🚀)\b",            "BULLISH",  +0.8),
    (r"\b(rally|rallying|surge|surging|spik(ed|ing))\b",
                                                 "BULLISH",  +0.5),
    (r"\b(undervalued|oversold|cheap)\b",        "BULLISH",  +0.4),
    (r"\b(going\s+(up|higher)|will\s+(go\s+up|rise|pump))\b",
                                                 "BULLISH",  +0.6),
    (r"\b(bullish)\b",                           "BULLISH",  +0.5),

    # BEARISH prediction
    (r"\bbreak\s*down\b|\bbreakdown\b",          "BEARISH",  -0.7),
    (r"\b(crash|crashing|tank(ed|ing)?|plunge|plunging)\b",
                                                 "BEARISH",  -0.7),
    (r"\b(overvalued|overbought|bubble)\b",      "BEARISH",  -0.4),
    (r"\b(going\s+(down|lower)|will\s+(go\s+down|fall|drop|dump))\b",
                                                 "BEARISH",  -0.6),
    (r"\b(bearish|red\s+day|bloodbath)\b",       "BEARISH",  -0.5),

    # REACTION (emotional, ambiguous direction)
    (r"\b(panic|fear|terrifying|scary|nervous|worried|anxious)\b",
                                                 "REACTION", -0.3),
    (r"\b(fomo|hype|euphori\w+|insane|crazy|wild)\b",
                                                 "REACTION", +0.2),
    (r"\b(rip(ped)?\s+my\s+portfolio|wiped\s+out|got\s+rekt)\b",
                                                 "REACTION", -0.6),
    (r"\b(let'?s\s+go|lfg|huge\s+win|i'?m\s+up)\b","REACTION", +0.4),

    # HOLD
    (r"\b(hodl|holding|i'?m\s+holding|just\s+hold|diamond\s+hands)\b",
                                                 "HOLD",      0.0),
]

# Arabic dialect + MSA. We match on raw text (no .lower since Arabic has no case).
_AR = [
    # BUY
    (r"اشتري|أشتري|اشتريت|بشتري|هشتري|شريت",     "BUY",      +0.7),
    (r"تجميع|بجمع|هجمع|جمعت",                    "BUY",      +0.7),  # accumulation
    (r"دخلت\s+(ال)?سهم|داخل\s+(ال)?سهم",          "BUY",      +0.7),

    # SELL
    (r"بيع|بعت|هبيع|ببيع|خرجت\s+(من\s+)?(ال)?سهم","SELL",     -0.7),
    (r"تصريف|بصرّف|هصرّف",                        "SELL",     -0.6),  # distribution
    (r"وقف\s+الخسارة|ستوب\s+لوس|ضرب\s+الستوب",     "SELL",     -0.6),

    # BULLISH prediction
    (r"هيطلع|هيرتفع|هيصعد|هيفجّر|هيكسر\s+المقاومة","BULLISH", +0.7),
    (r"هدف\s+سعري|تارجت\s*\d|الهدف\s*\d",         "BULLISH", +0.5),
    (r"صاعد|اتجاه\s+صاعد|انفجار\s+سعري",         "BULLISH", +0.5),
    (r"رخيص|مقوّم\s+بأقل|مقوم\s+باقل",           "BULLISH", +0.4),

    # BEARISH prediction
    (r"هينزل|هيهبط|هينهار|هيكسر\s+الدعم",         "BEARISH", -0.7),
    (r"نازل|اتجاه\s+هابط|انهيار|تشبع\s+شرائي",   "BEARISH", -0.6),
    (r"مبالغ\s+فيه|فقاعة",                       "BEARISH", -0.4),

    # REACTION
    (r"رعب|خوف|قلق|متوتر|مرعوب",                 "REACTION", -0.3),
    (r"نار|طحن|جامد\s*جدا|روعة|🔥",               "REACTION", +0.3),

    # HOLD
    (r"محتفظ|مسك(ت)?|انتظار|مستني",              "HOLD",      0.0),
]


def _compile(rules):
    return [(re.compile(rx, re.IGNORECASE), label, pol) for rx, label, pol in rules]


_EN_C = _compile(_EN)
_AR_C = _compile(_AR)


def detect(text: str) -> IntentResult:
    if not text:
        return IntentResult(intents=["NONE"])
    found: List[Tuple[str, float, str]] = []  # (label, polarity, phrase)
    for rx, label, pol in _EN_C:
        for m in rx.finditer(text):
            found.append((label, pol, m.group(0)))
    for rx, label, pol in _AR_C:
        for m in rx.finditer(text):
            found.append((label, pol, m.group(0)))
    if not found:
        return IntentResult(intents=["NONE"])

    # de-dup labels but keep evidence ordered
    labels: List[str] = []
    evidence: List[str] = []
    score = 0.0
    for label, pol, phrase in found:
        if label not in labels:
            labels.append(label)
        evidence.append(f"{label}:{phrase}")
        score += pol
    # clamp
    if score > 1.0:
        score = 1.0
    elif score < -1.0:
        score = -1.0
    return IntentResult(intents=labels, evidence=evidence, score=score)
