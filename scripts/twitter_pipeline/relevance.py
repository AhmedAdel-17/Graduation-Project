"""
Strict EGX *stock* relevance classifier.

A post passes ONLY IF it carries BOTH:
  (A) a finance / trading signal     — stock, share, buy, sell, target, EPS, ...
  (B) an EGX-specific signal         — an EGX ticker, a known EGX issuer, the
                                       index name, "Egyptian stock(s)/bourse",
                                       "البورصة المصرية", or an Arabic
                                       trading verb attached to a stock noun.

Generic "Egypt"/"Cairo" alone DOES NOT pass — that was the bug in the previous
filter that let in NSFW and tourism posts.
"""

from __future__ import annotations

import re
from typing import Tuple, List

# ---------------------------------------------------------------------------
# (A) Finance / trading signals
# ---------------------------------------------------------------------------

# English: stock-market language
EN_FINANCE = {
    "stock", "stocks", "share", "shares", "ticker", "equity", "equities",
    "bourse", "exchange index", "index", "etf", "earnings", "eps",
    "dividend", "buy", "sell", "hold", "long", "short", "bullish", "bearish",
    "target price", "price target", "valuation", "p/e", "pe ratio", "ipo",
    "trading", "trader", "investor", "portfolio", "rally", "selloff",
    "uptrend", "downtrend", "support level", "resistance", "breakout",
    "fundamentals", "technicals", "analyst", "upgrade", "downgrade",
    "yield", "dividends", "market cap", "outperform", "underperform",
}

# Cashtag pattern ($COMI, $ETEL, $OCI, $TMG, ...)
CASHTAG_RX = re.compile(r"\$[A-Z]{2,6}(?:\.CA)?\b")

# Arabic: trading / market vocabulary
AR_FINANCE = {
    "سهم", "اسهم", "أسهم", "السهم", "البورصة", "بورصة",
    "تداول", "تحليل", "اشتري", "أشتري", "ابيع", "أبيع",
    "صعود", "هبوط", "ارتفاع", "انخفاض", "ربح", "خسارة",
    "أرباح", "خسائر", "صفقة", "صفقات", "مؤشر", "توزيعات",
    "ايبس", "ربحية", "اكتتاب", "محفظة", "مستثمر", "مضارب",
    "هدف سعري", "مقاومة", "دعم",
}

# ---------------------------------------------------------------------------
# (B) EGX-specific signals
# ---------------------------------------------------------------------------

# Known EGX tickers (subset of EGX-30 + heavily-discussed names)
EGX_TICKERS = {
    "COMI", "ETEL", "TMGH", "TMG", "SWDY", "FWRY", "ABUK", "ADIB",
    "EFIH", "EGAL", "MFPC", "CCAP", "SKPC", "AMOC", "ESRS", "ORWE",
    "HELI", "GBCO", "ORAS", "PHDC", "CIEB", "ISPH", "DSCW", "RMDA",
    "ARCC", "BTFH", "JUFO", "ORHD", "RAYA", "VLMR", "EAST", "HRHO",
    "OCDI", "OCI", "EFG",
}

# Issuer names — English
EN_ISSUERS = {
    "commercial international bank", "telecom egypt", "orascom",
    "talaat moustafa", "elsewedy electric", "fawry", "abu qir fertilizers",
    "egyptian financial", "efg hermes", "global telecom", "alexandria mineral",
    "eastern company", "egyptian aluminum", "raya holding",
    "ezz steel", "edita", "juhayna", "cleopatra hospitals",
    "palm hills", "sodic", "madinet nasr",
}

# Issuer names — Arabic
AR_ISSUERS = {
    "البنك التجاري الدولي", "المصرية للاتصالات", "اوراسكوم", "أوراسكوم",
    "طلعت مصطفى", "السويدي اليكتريك", "السويدي إليكتريك", "فوري",
    "ابو قير", "أبو قير", "هيرميس", "ايي اف جي", "إي اف جي",
    "شرقية الدخان", "الشرقية للدخان", "حديد عز", "اديتا", "إديتا",
    "جهينة", "كليوباترا للمستشفيات", "بالم هيلز", "سوديك",
}

# Index / market terms
INDEX_TERMS = {
    "egx", "egx30", "egx 30", "egx70", "egx100",
    "egyptian stock exchange", "egyptian stock market", "egyptian bourse",
    "cairo stock exchange", "msci egypt", "ftse egypt",
    "egyptian stocks", "stocks in egypt", "egypt stock", "egypt equities",
    "egp stocks", "egyptian shares",
    # Arabic
    "البورصة المصرية", "البورصه المصريه", "بورصة مصر",
    "مؤشر اي جي اكس", "اي جي اكس",
}


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify(text: str) -> Tuple[bool, dict]:
    """
    Return (is_relevant, debug_dict).
    Pass condition: at least one finance signal AND at least one EGX signal.
    """
    if not text:
        return False, {"reason": "empty"}

    raw = text
    low = text.lower()

    # ---- (A) finance signal --------------------------------------------------
    fin_hits: List[str] = []
    for w in EN_FINANCE:
        if w in low:
            fin_hits.append(w)
    for w in AR_FINANCE:
        if w in raw:
            fin_hits.append(w)
    cashtags = CASHTAG_RX.findall(raw)
    if cashtags:
        fin_hits.extend(cashtags)

    # ---- (B) EGX signal ------------------------------------------------------
    egx_hits: List[str] = []
    # Ticker matching is STRICT to avoid "EAST" matching "Middle East" etc.:
    #   - cashtag form  $COMI  ->  match
    #   - suffix form   COMI.CA ->  match
    # Bare uppercase tokens are NOT enough on their own.
    cashtag_tickers = {t.lstrip("$").upper() for t in cashtags}
    suffix_tickers = {m.upper() for m in re.findall(r"\b([A-Z]{2,6})\.CA\b", raw)}
    matched_tickers = (cashtag_tickers | suffix_tickers) & EGX_TICKERS
    for tk in matched_tickers:
        egx_hits.append(f"ticker:{tk}")
    for name in EN_ISSUERS:
        if name in low:
            egx_hits.append(f"issuer:{name}")
    for name in AR_ISSUERS:
        if name in raw:
            egx_hits.append(f"issuer-ar:{name}")
    for term in INDEX_TERMS:
        if term in low or term in raw:
            egx_hits.append(f"index:{term}")

    is_rel = bool(fin_hits) and bool(egx_hits)
    return is_rel, {
        "finance_hits": fin_hits[:6],
        "egx_hits": egx_hits[:6],
        "cashtags": cashtags,
    }


def is_relevant(text: str) -> bool:
    return classify(text)[0]
