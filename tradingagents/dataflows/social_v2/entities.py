"""Per-post EGX entity extraction.

Ported from scripts/social_pipeline/v2/entities.py with no functional change.
Stays deterministic and lightweight; covers the wide EGX universe and reuses
the project's Arabic normalization so retail posts can match issuer names
written in Egyptian Arabic, MSA, or mixed text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Set

from tradingagents.utils.text_preprocessor import normalize_text


@dataclass
class Mention:
    symbol: str
    confidence: float
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence[:4],
        }


EGX_COMPANIES: Dict[str, str] = {
    "COMI": "Commercial International Bank Egypt (CIB) S.A.E.",
    "TMGH": "Talaat Moustafa Group Holding",
    "SWDY": "El Sewedy Electric Company",
    "ETEL": "Telecom Egypt Company",
    "MFPC": "Misr Fertilizer Production Company",
    "EGAL": "Egypt Aluminum",
    "EAST": "Eastern Company S.A.E",
    "ABUK": "Abu Qir Fertilizers & Chemical Industries Company (S.A.E)",
    "QNBE": "Qatar National Bank",
    "ALCN": "Alexandria Container&Cargo Handling Company",
    "HDBK": "Housing and Development Bank- Egypt (S.A.E)",
    "EFIH": "e-finance for Digital and Financial Investments S.A.E.",
    "FWRY": "Fawry for Banking Technology and Electronic Payments S.A.E.",
    "ORAS": "Orascom Construction PLC",
    "SCTS": "Suez Canal Company for Technology Settling (S.A.E)",
    "EMFD": "Emaar Misr for Development Company (S.A.E.)",
    "ADIB": "Abu Dhabi Islamic Bank - Egypt - S.A.E",
    "VLMR": "Valmore Holding S.A.E.",
    "GPPL": "Golden Pyramids Plaza S.A.E.",
    "EFID": "Edita Food Industries Company (S.A.E)",
    "HRHO": "EFG Holding Company S.A.E",
    "IRON": "Egyptian Iron and Steel Company",
    "JUFO": "Juhayna Food Industries S.A.E.",
    "ORHD": "Orascom Development Egypt S.A.E.",
    "CANA": "Suez Canal Bank (S.A.E)",
    "FERC": "Ferchem Misr for fertilizers and chemicals S.A.E",
    "BTFH": "Beltone Holding S.A.E",
    "PHDC": "Palm Hills Developments S.A.E.",
    "GBCO": "GB Corp",
    "CIEB": "Credit Agricole - Egypt Bank (S.A.E.)",
    "OCDI": 'Sixth of October for Development and Investment Company "SODIC" (S.A.E.)',
    "FAIT": "Faisal Islamic Bank of Egypt",
    "VALU": "U Consumer Finance S.A.E.",
    "RAYA": "Raya Holding Company for Financial Investments (S.A.E)",
    "HELI": "Heliopolis Co. for Housing & Development",
    "EXPA": "Export Development Bank of Egypt (S.A.E.)",
    "EGCH": "Egyptian Chemical Industries",
    "EFIC": "Egyptian Financial and Industrial SAE",
    "ARCC": "Arabian Cement Company S.A.E.",
    "SKPC": "Sidi Kerir Petrochemicals Co.",
    "CCAP": "QALA For Financial Investments",
    "MCQE": "Misr Cement (Qena) Company (S.A.E)",
    "CLHO": "Cleopatra Hospitals Group S.A.E.",
    "TAQA": "TAQA Arabia S.A.E.",
    "POUL": "Cairo Poultry Company S.A.E.",
    "SAUD": "alBaraka Bank Egypt S.A.E.",
    "EGSA": "The Egyptian Satellite Company Nilesat",
    "SCEM": "Sinai Cement Co. (S.A.E)",
    "ORWE": "Oriental Weavers Carpets Company (S.A.E)",
    "UBEE": "The United Bank",
    "MTIE": "MM Group for Industry and International Trade S.A.E.",
    "EGTS": "Egyptian Resorts Company (S.A.E)",
    "MBSC": "Misr Beni Suef Cement Co. S.A.E",
    "PHAR": "Egyptian International Pharmaceutical Industries Company",
    "MASR": "Madinet Masr For Housing and Development",
    "ATQA": "Misr National Steel - Ataqa",
    "CICH": "CI Capital Holding For Financial Investments (S.A.E)",
    "CIRA": "Cairo For Investment And Real Estate Developments-CIRA Education",
    "ISPH": "Ibnsina Pharma",
    "TALM": "Taaleem Management Services Company S.A.E.",
    "MHOT": "Misr Hotels Company",
    "MOIL": "Maridive and Oil Services S.A.E.",
    "AMOC": "Alexandria Mineral Oils Company",
    "EGBE": "Egyptian Gulf Bank (S.A.E)",
    "IFAP": "International Company for Agricultural Crops",
    "RMDA": "Tenth of Ramadan for Pharmaceutical Industries and Diagnostic Reagents (Rameda) (S.A.E)",
    "CSAG": "Canal Shipping Agencies Company",
    "BINV": "B Investments Holding S.A.E.",
    "OLFI": "Obour Land for Food Industries S.A.E.",
    "SPHT": "El Shams Pyramids Co. For Hotels & Touristic Projects S.A.E",
    "OIH": "Orascom Investment Holding S.A.E.",
    "BONY": "Bonyan for Development and Trade",
    "ELEC": "Electro Cable Egypt",
    "MIPH": "MINAPHARM Pharmaceuticals",
    "DOMT": "Arabian Food Industries Company (DOMTY) - S.A.E",
    "ISMQ": "Iron & Steel for Mines & Quarries",
    "AMES": "Alexandria New Medical Center",
    "SUGR": "Delta Sugar Company",
    "NIPH": "EI- Nile Co. for Pharmaceuticals and Chemical Industries",
    "MOIN": "Mohandes Insurance Company",
    "GIHD": "Gharbia Islamic Housing Development Company",
    "MPCI": "Memphis Pharmaceuticals & Chemical Industries",
    "QNBA": "QNB AlAhli",
    "ESRS": "Ezz Steel",
}

MANUAL_EN_ALIASES: Dict[str, Set[str]] = {
    "COMI": {"commercial international bank", "cib", "cib egypt"},
    "TMGH": {"talaat moustafa", "tmg", "tmg holding"},
    "SWDY": {"elsewedy electric", "el sewedy", "sewedy electric", "elsewedy"},
    "ETEL": {"telecom egypt", "we telecom", "egyptian telecom", "we"},
    "MFPC": {"mopco", "misr fertilizers", "misr fertilizer production", "mopco misr fertilizers"},
    "EAST": {"eastern company", "eastern tobacco"},
    "ABUK": {"abu qir fertilizers", "abu qir", "abou kir"},
    "QNBE": {"qnb alahli", "qnb", "qnb ahly"},
    "QNBA": {"qnb alahli", "qnb al ahli", "qnb ahly"},
    "HDBK": {"housing and development bank", "hdb"},
    "EFIH": {"e-finance", "efinance", "e finance"},
    "FWRY": {"fawry", "fawry banking", "fawry plus"},
    "ORAS": {"orascom construction", "orascom", "oci", "ocic"},
    "SCTS": {
        "suez canal company for technology settling",
        "suez canal technology settling",
        "canal of suez for technology settling",
        "tawteen technology",
    },
    "EMFD": {"emaar misr", "emaar egypt"},
    "ADIB": {"abu dhabi islamic bank egypt", "adib egypt", "adib"},
    "EFID": {"edita", "edita food", "edita food industries"},
    "HRHO": {"efg holding", "efg hermes", "hermes", "efg"},
    "JUFO": {"juhayna", "juhayna food", "juhayna food industries"},
    "ORHD": {"orascom development", "orascom development egypt"},
    "CANA": {"suez canal bank"},
    "BTFH": {"beltone", "beltone holding"},
    "PHDC": {"palm hills", "palm hills developments", "palm hills development"},
    "GBCO": {"gb corp", "gb auto"},
    "CIEB": {"credit agricole egypt", "credit agricole", "cae"},
    "OCDI": {"sodic", "sixth of october development and investment"},
    "FAIT": {"faisal islamic bank", "faisal bank egypt"},
    "RAYA": {"raya", "raya holding", "raya group"},
    "HELI": {"heliopolis housing", "heliopolis"},
    "ARCC": {"arabian cement"},
    "SKPC": {"sidi kerir", "sidpec", "sidi kerir petrochemicals"},
    "CCAP": {"qala", "qala financial", "qala holding"},
    "CLHO": {"cleopatra hospitals", "cleopatra hospital", "cleopatra"},
    "TAQA": {"taqa arabia", "taqa"},
    "POUL": {"cairo poultry"},
    "SAUD": {"al baraka bank", "albaraka bank egypt"},
    "EGSA": {"nilesat", "egyptian satellite company"},
    "ORWE": {"oriental weavers", "oriental carpets"},
    "MTIE": {"mm group", "mmgroup", "mti", "mti mm group"},
    "PHAR": {"eipico", "egyptian international pharmaceutical industries"},
    "MASR": {"madinet masr", "madinet masr housing", "madinet nasr", "mnhd"},
    "CICH": {"ci capital", "ci capital holding"},
    "CIRA": {"cira", "cira education"},
    "ISPH": {"ibnsina pharma", "ibn sina pharma", "ibnsina", "ibn sina"},
    "TALM": {"taaleem", "taaleem management"},
    "AMOC": {"alexandria mineral oils", "amoc"},
    "RMDA": {"rameda", "rameda pharma"},
    "OLFI": {"obour land", "obour land food", "obourland"},
    "OIH": {"orascom investment", "orascom investment holding"},
    "ELEC": {"electro cable", "electro cable egypt", "electric cables"},
    "MIPH": {"minapharm", "mina pharm"},
    "DOMT": {"domty", "arabian food industries"},
    "SUGR": {"delta sugar"},
    "GIHD": {"gharbia islamic", "gharbia islamic housing", "gihd"},
    "MPCI": {"memphis pharmaceuticals", "memphis pharma"},
    "ESRS": {"ezz steel", "ezz dekheila", "ezz"},
}

MANUAL_AR_ALIASES: Dict[str, Set[str]] = {
    "COMI": {"البنك التجاري الدولي", "التجاري الدولي", "سي اي بي"},
    "TMGH": {"طلعت مصطفى", "مجموعة طلعت مصطفى", "تي ام جي"},
    "SWDY": {"السويدي", "السويدي اليكتريك", "السويدي إليكتريك", "السويدى"},
    "ETEL": {"المصرية للاتصالات", "تليكوم مصر", "وي"},
    "MFPC": {"موبكو", "مصر للأسمدة", "مصر للاسمدة", "مصنع موبكو"},
    "EGAL": {"مصر للألومنيوم", "مصر للالومنيوم", "الألومنيوم العربية", "الالومنيوم العربية"},
    "EAST": {"الشرقية للدخان", "شرقية الدخان", "ايسترن"},
    "ABUK": {"أبو قير", "ابو قير", "أبو قير للأسمدة", "ابو قير للاسمدة"},
    "QNBE": {"بنك قطر الوطني", "كيو ان بي"},
    "QNBA": {"بنك قطر الوطني الأهلي", "بنك قطر الوطني الاهلي", "كيو ان بي الأهلي"},
    "HDBK": {"بنك التعمير والإسكان", "بنك التعمير والاسكان"},
    "EFIH": {"إي فاينانس", "اي فاينانس", "إي-فاينانس"},
    "FWRY": {"فوري", "فورى"},
    "ORAS": {"أوراسكوم", "اوراسكوم", "أوراسكوم للإنشاء", "اوراسكوم للانشاء"},
    "SCTS": {
        "قناة السويس لتوطين التكنولوجيا",
        "شركة قناة السويس لتوطين التكنولوجيا",
    },
    "EMFD": {"إعمار مصر", "اعمار مصر"},
    "ADIB": {"أبوظبي الإسلامي", "ابوظبي الاسلامي", "بنك أبوظبي الإسلامي", "بنك ابوظبي الاسلامي"},
    "EFID": {"إيديتا", "ايديتا"},
    "HRHO": {"إي اف جي", "اي اف جي", "هيرميس", "إي اف جي هيرميس", "اي اف جي هيرميس"},
    "JUFO": {"جهينة"},
    "ORHD": {"أوراسكوم للتنمية", "اوراسكوم للتنمية",
             "اوراسكوم ديفيلوبمنت", "أوراسكوم ديفيلوبمنت", "اوراسكوم ديفلوبمنت",
             "اوراسكوم لتنمية مصر"},
    "CANA": {"بنك قناة السويس"},
    "BTFH": {"بلتون", "بلتون القابضة"},
    "PHDC": {"بالم هيلز", "بالم هيلز للتعمير"},
    "GBCO": {"جي بي كورب", "جي بي أوتو", "جي بي اوتو"},
    "CIEB": {"كريدي أجريكول", "كريدي اجريكول"},
    "OCDI": {"سوديك", "شركة السادس من أكتوبر للتنمية والاستثمار", "شركة السادس من اكتوبر للتنمية والاستثمار"},
    "FAIT": {"فيصل الإسلامي", "بنك فيصل الإسلامي", "بنك فيصل الاسلامي"},
    "RAYA": {"راية", "راية القابضة"},
    "HELI": {"هليوبوليس", "مصر الجديدة للإسكان", "مصر الجديدة للاسكان"},
    "ARCC": {"العربية للأسمنت", "العربية للاسمنت"},
    "SKPC": {"سيدي كرير", "سيدي كرير للبتروكيماويات", "سيدبك"},
    "CCAP": {"قلعة", "القلعة"},
    "CLHO": {"كليوباترا", "كليوباترا للمستشفيات"},
    "TAQA": {"طاقة عربية"},
    "POUL": {"القاهرة للدواجن"},
    "SAUD": {"البركة", "البركة مصر"},
    "EGSA": {"نايل سات", "نيل سات"},
    "ORWE": {"الشرقية للسجاد", "الشرقية للمفروشات"},
    "MTIE": {"ام ام جروب", "إم إم جروب", "مم جروب", "ام تي اي"},
    "PHAR": {"إيبيكو", "ايبيكو", "المصرية الدولية للصناعات الدوائية"},
    "MASR": {"مدينة مصر", "مدينة مصر للإسكان", "مدينة مصر للاسكان", "مدينة نصر", "مدينة نصر للإسكان"},
    "CICH": {"سي آي كابيتال", "سي اي كابيتال"},
    "CIRA": {"سيرا", "سيرا للتعليم"},
    "ISPH": {"ابن سينا", "ابن سينا فارما", "ابن سينا فارم", "إبن سينا", "إبن سينا فارما"},
    "TALM": {"تعليم", "تعليم للخدمات"},
    "AMOC": {"أموك", "اموك", "الإسكندرية للزيوت المعدنية", "الاسكندرية للزيوت المعدنية"},
    "RMDA": {"راميدا", "العاشر من رمضان للصناعات الدوائية"},
    "OLFI": {"عبور لاند", "العبور لاند"},
    "OIH": {"أوراسكوم للاستثمار", "اوراسكوم للاستثمار"},
    "ELEC": {"الكابلات الكهربائية", "اليكترو كابل", "إلكترو كابل"},
    "MIPH": {"مينا فارم", "مينافارم"},
    "DOMT": {"دومتي", "آرابيان فود", "ارابيان فود"},
    "SUGR": {"الدلتا للسكر"},
    "GIHD": {"الغربية الإسلامية للتنمية العمرانية", "الغربية الاسلامية للتنمية العمرانية", "الغربية الإسلامية", "الغربية الاسلامية"},
    "MPCI": {"ممفيس للأدوية", "ممفيس للادوية", "ممفيس"},
    "ESRS": {"حديد عز", "عز الدخيلة", "عز للحديد"},
}

ALT_TICKERS: Dict[str, Set[str]] = {
    "COMI": {"CIB"},
    "TMGH": {"TMG"},
    "EFIH": {"EFINANCE"},
    "ORAS": {"OCI", "OCIC"},
    "SCTS": {"TAWTEEN"},
    "EFID": {"EDITA"},
    "HRHO": {"EFG", "EFGH"},
    "OCDI": {"SODIC"},
    "SKPC": {"SIDPEC"},
    "PHAR": {"EIPICO"},
    "MASR": {"MNHD"},
    "RMDA": {"RAMEDA"},
    "DOMT": {"DOMTY"},
    "ESRS": {"EZZ"},
}

_CORP_SUFFIX_RX = re.compile(
    r"\b(s\.?\s*a\.?\s*e\.?|sae|plc|holding|company|co\.?|group|bank)\b",
    re.IGNORECASE,
)
_PARENS_RX = re.compile(r"\(([^)]+)\)")

_EN_FINANCE_TERMS = {
    "stock", "stocks", "ticker", "buy", "sell", "hold", "target", "trade",
    "trading", "support", "resistance", "breakout", "bullish", "bearish",
    "volume", "chart", "forecast", "analysis", "trend", "price", "portfolio",
    "long", "short", "calls", "puts", "earnings",
}
_AR_FINANCE_TERMS = {
    "سهم", "اسهم", "أسهم", "السهم", "شراء", "بيع", "تجميع", "تصريف",
    "تحليل", "توقعات", "مقاومة", "دعم", "تارجت", "هدف", "صاعد", "هابط",
    "هيطلع", "هينزل", "تداول", "مضاربة", "المضاربه", "المضاربة",
    "استثمار", "محفظة", "بورصة", "البورصة",
}
_AMBIGUOUS_BARE_TICKERS = {"EAST"}

# Tokens we refuse to add as company aliases when derived from parenthesized
# parts of the official name. Without this block-list, names like
# "QNB AlAhli (Egypt)" would register "egypt" as a ticker alias, matching
# any text that mentions the country.
_PARENS_ALIAS_BLOCKLIST = {
    "s.a.e", "sae", "s.a.e.", "plc", "ltd", "llc",
    "egypt", "egy", "eg", "egyptian",
    "company", "co", "co.", "group", "holding",
    "the", "and",
}


def _normalize_entity_text(text: str) -> str:
    return normalize_text(text or "").lower().strip()


def _clean_english_company_alias(name: str) -> str:
    cleaned = _PARENS_RX.sub(" ", name)
    cleaned = cleaned.replace("&", " and ").replace('"', " ")
    cleaned = _CORP_SUFFIX_RX.sub(" ", cleaned)
    cleaned = re.sub(r"\begypt\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^0-9a-zA-Z\s-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _derive_english_aliases(symbol: str, name: str) -> Set[str]:
    aliases = {name}
    cleaned = _clean_english_company_alias(name)
    if cleaned:
        aliases.add(cleaned)
    cleaned_no_hyphen = cleaned.replace("-", " ").strip()
    if cleaned_no_hyphen:
        aliases.add(cleaned_no_hyphen)
    for match in _PARENS_RX.findall(name):
        token = match.strip()
        if 2 <= len(token) <= 16 and token.lower() not in _PARENS_ALIAS_BLOCKLIST:
            aliases.add(token)
    aliases.update(MANUAL_EN_ALIASES.get(symbol, set()))
    return {
        alias.lower().strip()
        for alias in aliases
        if alias and len(alias.strip()) >= 3
    }


def _build_registry() -> Dict[str, Dict[str, Set[str]]]:
    registry: Dict[str, Dict[str, Set[str]]] = {}
    for symbol, company_name in EGX_COMPANIES.items():
        registry[symbol] = {
            "en": _derive_english_aliases(symbol, company_name),
            "ar": {
                alias.strip()
                for alias in MANUAL_AR_ALIASES.get(symbol, set())
                if alias.strip()
            },
            "alts": {f"{symbol}.CA"} | set(ALT_TICKERS.get(symbol, set())),
        }
    return registry


SYMBOL_REGISTRY: Dict[str, Dict[str, Set[str]]] = _build_registry()


MARKET_INDEX_TERMS = {
    "EGX_30": {"egx30", "egx 30", "ايجي اكس 30", "المؤشر الثلاثيني"},
    "EGX_70": {"egx70", "egx 70", "ايجي اكس 70"},
    "EGX_100": {"egx100", "egx 100", "ايجي اكس 100"},
    "EGX_BROAD": {
        "egyptian stock market", "egyptian bourse", "egyptian stock exchange",
        "cairo stock exchange", "egyptian stocks", "egypt equities",
        "egx market", "egx", "البورصة المصرية", "البورصه المصريه",
        "بورصة مصر", "سوق المال المصري",
    },
}
_NORMALIZED_MARKET_TERMS = {
    idx: {_normalize_entity_text(term) for term in terms}
    for idx, terms in MARKET_INDEX_TERMS.items()
}

CASHTAG_RX = re.compile(r"\$([A-Z0-9]{2,6})(?:\.CA)?\b", re.IGNORECASE)
DOTCA_RX = re.compile(r"\b([A-Z0-9]{2,6})\.CA\b", re.IGNORECASE)
BARE_TICKER_RX = re.compile(r"(?<![A-Z0-9])([A-Z]{2,6})(?![A-Z0-9])")


def _canonical(ticker: str) -> str | None:
    token = ticker.upper().replace(".CA", "")
    if token in SYMBOL_REGISTRY:
        return token
    for canonical, meta in SYMBOL_REGISTRY.items():
        alts = {alt.upper().replace(".CA", "") for alt in meta["alts"]}
        if token in alts:
            return canonical
    return None


def has_finance_context(text: str) -> bool:
    normalized = _normalize_entity_text(text)
    if not normalized:
        return False
    if any(term in normalized for term in _EN_FINANCE_TERMS):
        return True
    if any(term in normalized for term in _AR_FINANCE_TERMS):
        return True
    if re.search(r"\b(rsi|macd|ema|ma\d+|pe|eps|volume|support|resistance)\b", normalized):
        return True
    return False


def has_market_term(text: str) -> bool:
    normalized = _normalize_entity_text(text)
    if not normalized:
        return False
    return any(
        term in normalized
        for terms in _NORMALIZED_MARKET_TERMS.values()
        for term in terms
    )


def _compile_phrase_pattern(alias: str) -> re.Pattern:
    phrase = r"\s+".join(re.escape(w) for w in alias.split())
    return re.compile(
        rf"(?<![0-9A-Za-z؀-ۿ]){phrase}(?![0-9A-Za-z؀-ۿ])"
    )


def _is_short_alias(alias: str) -> bool:
    tokens = alias.split()
    if len(tokens) == 1:
        return True
    return len(alias) <= 8


def _iter_symbol_aliases() -> Iterable[tuple[str, str, str, float, re.Pattern]]:
    for symbol, meta in SYMBOL_REGISTRY.items():
        for alias in meta["en"]:
            conf = 0.75 if _is_short_alias(alias) else 0.85
            yield symbol, "en", alias, conf, _compile_phrase_pattern(alias)
        for alias in meta["ar"]:
            normalized_alias = _normalize_entity_text(alias)
            if not normalized_alias:
                continue
            conf = 0.75 if _is_short_alias(normalized_alias) else 0.85
            yield (
                symbol, "ar", normalized_alias, conf,
                _compile_phrase_pattern(normalized_alias),
            )


_SYMBOL_ALIASES: list[tuple[str, str, str, float, re.Pattern]] = sorted(
    list(_iter_symbol_aliases()),
    key=lambda item: len(item[2]),
    reverse=True,
)


def extract(text: str) -> List[Mention]:
    """Return symbol mentions from a single post."""
    if not text:
        return []

    raw = text
    normalized = _normalize_entity_text(text)
    bag: Dict[str, Mention] = {}

    def add(symbol: str, confidence: float, evidence: str) -> None:
        mention = bag.get(symbol)
        if mention is None:
            bag[symbol] = Mention(symbol=symbol, confidence=confidence, evidence=[evidence])
            return
        mention.confidence = max(mention.confidence, confidence)
        if evidence not in mention.evidence:
            mention.evidence.append(evidence)

    for ticker in CASHTAG_RX.findall(raw):
        canonical = _canonical(ticker)
        if canonical:
            add(canonical, 1.0, f"cashtag:${ticker.upper()}")

    for ticker in DOTCA_RX.findall(raw):
        canonical = _canonical(ticker)
        if canonical:
            add(canonical, 1.0, f"suffix:{ticker.upper()}.CA")

    if has_finance_context(normalized):
        for ticker in BARE_TICKER_RX.findall(raw):
            canonical = _canonical(ticker)
            if not canonical:
                continue
            if canonical in _AMBIGUOUS_BARE_TICKERS and not ticker.isupper():
                continue
            add(canonical, 0.95, f"ticker:{ticker.upper()}")

    for symbol, alias_kind, alias, confidence, pattern in _SYMBOL_ALIASES:
        if pattern.search(normalized):
            add(symbol, confidence, f"name-{alias_kind}:{alias}")

    for idx, terms in _NORMALIZED_MARKET_TERMS.items():
        for term in terms:
            if term and term in normalized:
                add(idx, 0.70, f"index:{term}")
                break

    return list(bag.values())


def primary_symbol(mentions: List[Mention]) -> str | None:
    if not mentions:
        return None
    stocks = [mention for mention in mentions if not mention.symbol.startswith("EGX")]
    pool = stocks or mentions
    return max(pool, key=lambda mention: mention.confidence).symbol
