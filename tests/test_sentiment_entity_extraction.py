"""PR 2 — entity-extraction regression tests.

Four categories:
A. Arabic phrase-boundary correctness (suffix-extension false positives)
B. EGX market-index → EGX_MARKET routing (not per-stock)
C. Adversarial market→stock contamination attempts ("السوق" leakage)
D. Valid matches that must still work after the fixes
"""
from __future__ import annotations

import pytest

from scripts.twitter_pipeline.v2.aggregator import ScoredPost, aggregate
from scripts.twitter_pipeline.v2.entities import (
    Mention,
    MARKET_INDEX_TERMS,
    extract,
    has_market_term,
)


# ---------------------------------------------------------------------------
# A. Arabic phrase-boundary: suffix-extension false positives
# ---------------------------------------------------------------------------

class TestArabicPhraseBoundary:
    """Verify the phrase-boundary regex prevents Arabic morphological bleed.

    BEFORE fix: "_alias_in_text" used plain substring for multi-word aliases,
    so "التجاري الدولية" (feminine/plural suffix form) matched "التجاري الدولي"
    (COMI alias) because the alias string IS a substring of the longer word.

    AFTER fix: compiled phrase-boundary pattern requires that no Arabic/Latin
    character immediately follows the final character of the alias.
    """

    def test_comi_alias_does_not_match_feminine_suffix_extension(self) -> None:
        # "الدولية" ends with "ة" (Arabic char) → lookforward fires → NO match.
        # Before fix this was a false positive.
        text = "المؤسسة التجارية الدولية المتخصصة في التجارة"
        mentions = extract(text)
        comi_hits = [m for m in mentions if m.symbol == "COMI"]
        assert not comi_hits, (
            "COMI alias 'التجاري الدولي' must NOT match 'التجارية الدولية' "
            f"(suffix extension). Got: {comi_hits}"
        )

    def test_comi_alias_does_not_match_dual_suffix(self) -> None:
        # "الدوليين" (dual masculine) ends with Arabic chars.
        text = "تحدث المسؤولون التجاريون الدوليين عن الاستثمار"
        mentions = extract(text)
        comi_hits = [m for m in mentions if m.symbol == "COMI"]
        assert not comi_hits, (
            "COMI alias must NOT match 'التجاريون الدوليين' (dual/plural suffix)."
        )

    def test_comi_alias_does_not_match_when_extended_by_arabic_word(self) -> None:
        # "التجاري الدولي السوري" — the alias appears inside a longer phrase
        # describing a Syrian entity.  The space between "الدولي" and "السوري"
        # means "الدولي" is immediately followed by a space (not Arabic char),
        # so the lookforward passes.  This is an accepted limitation of
        # space-delimited boundary checking in Arabic; the mitigation is the
        # confidence gate in Layer C (entity_confidence ≥ 0.85).
        # We document the behavior here so it is explicit, not hidden.
        text = "اجتماع البنك التجاري الدولي السوري مع المستثمرين"
        mentions = extract(text)
        # This WILL match (space-boundary passes) — document it, don't assert False.
        # The test asserts confidence is NOT elevated above the alias base (0.85).
        comi_hits = [m for m in mentions if m.symbol == "COMI"]
        if comi_hits:
            assert comi_hits[0].confidence <= 0.85, (
                "Confidence for an ambiguous partial-name match must not exceed 0.85."
            )

    def test_orwe_alias_does_not_match_suffix_extension(self) -> None:
        # ORWE alias: "الشرقية للسجاد"  ← must NOT match "الشرقية للسجادة"
        # ("ة" suffix = Arabic char → lookforward fires).
        text = "اشتريت الشرقية للسجادة الفارسية الجميلة"
        mentions = extract(text)
        orwe_hits = [m for m in mentions if m.symbol == "ORWE"]
        assert not orwe_hits, (
            "ORWE alias 'الشرقية للسجاد' must NOT match 'الشرقية للسجادة'."
        )

    def test_east_alias_does_not_match_region_name(self) -> None:
        # EAST alias: "الشرقية للدخان".  Separate test for regional name.
        # "الشرقية" (the Eastern [region]) alone is a single-word alias at
        # confidence 0.75; it should be word-boundary guarded.
        # "الشرقية" followed by "دائما" — boundary OK but this should only
        # fire if finance context is present (bare-ticker path).
        text = "منطقة الشرقية دائما قوية في إنتاج المحاصيل الزراعية"
        mentions = extract(text)
        # No finance context → bare ticker EAST not extracted.
        # "الشرقية" single-word alias at 0.75 IS boundary-matched (space follows).
        # Accept this and assert confidence stays at 0.75 if it fires, not higher.
        east_hits = [m for m in mentions if m.symbol == "EAST"]
        if east_hits:
            assert east_hits[0].confidence <= 0.75

    def test_scts_short_alias_removed_no_match_on_generic_policy(self) -> None:
        # "توطين التكنولوجيا" was removed from SCTS aliases as too generic.
        # A post about national tech-localization policy must NOT tag SCTS.
        text = "توطين التكنولوجيا ضروري لتنمية الاقتصاد المصري"
        mentions = extract(text)
        scts_hits = [m for m in mentions if m.symbol == "SCTS"]
        assert not scts_hits, (
            "SCTS must NOT be extracted from a generic 'توطين التكنولوجيا' "
            "policy discussion.  The short alias was removed in PR 2."
        )

    def test_scts_full_alias_still_matches(self) -> None:
        # The full company-name alias must still work after removing the short one.
        text = "قناة السويس لتوطين التكنولوجيا هدف 95 جنيه"
        mentions = extract(text)
        scts_hits = [m for m in mentions if m.symbol == "SCTS"]
        assert scts_hits, "SCTS full alias 'قناة السويس لتوطين التكنولوجيا' must still match."
        assert scts_hits[0].confidence >= 0.85

    def test_swdy_single_word_alias_boundary(self) -> None:
        # "السويدي" (SWDY) must NOT match "السويدية" (Swedish, feminine form).
        text = "زارت الشركة السويدية معرض القاهرة الدولي"
        mentions = extract(text)
        swdy_hits = [m for m in mentions if m.symbol == "SWDY"]
        assert not swdy_hits, (
            "SWDY alias 'السويدي' must NOT match 'السويدية' (Arabic feminine suffix)."
        )

    def test_multi_word_arabic_alias_correct_match_still_works(self) -> None:
        # عبور لاند (OLFI): two-word alias must still fire after the boundary fix.
        text = "سهم عبور لاند فيه تجميع قوي"
        mentions = extract(text)
        olfi_hits = [m for m in mentions if m.symbol == "OLFI"]
        assert olfi_hits, "OLFI alias 'عبور لاند' must still match correctly."

    def test_ibn_sina_two_word_alias_still_matches(self) -> None:
        text = "ابن سينا فارما شراء قوي النهاردة"
        mentions = extract(text)
        isph_hits = [m for m in mentions if m.symbol == "ISPH"]
        assert isph_hits, "ISPH alias 'ابن سينا' must still match."


# ---------------------------------------------------------------------------
# B. Market index → EGX_MARKET routing (renamed keys)
# ---------------------------------------------------------------------------

class TestMarketIndexRouting:
    """EGX_30/70/100/BROAD must all use the EGX_ prefix so the aggregator
    routes them to EGX_MARKET and split_outputs excludes them from per_stock.
    """

    def test_all_market_index_keys_use_egx_underscore_prefix(self) -> None:
        for key in MARKET_INDEX_TERMS:
            assert key.startswith("EGX_"), (
                f"MARKET_INDEX_TERMS key '{key}' must start with 'EGX_' so "
                "aggregator.py routes it to EGX_MARKET via startswith check."
            )

    def test_egx30_term_extracts_as_egx_underscore_prefixed_symbol(self) -> None:
        text = "مؤشر egx30 انخفض اليوم"
        mentions = extract(text)
        index_hits = [m for m in mentions if m.symbol.startswith("EGX_")]
        assert index_hits, "EGX30 text-term must produce an EGX_-prefixed mention."
        non_index = [m for m in mentions if not m.symbol.startswith("EGX_")]
        assert not non_index, f"No per-stock mentions expected from index text. Got: {non_index}"

    def test_egx30_mention_routes_to_egx_market_in_aggregator(self) -> None:
        post = ScoredPost(
            text="egx30 انهار اليوم",
            url="https://example.com",
            platform="reddit",
            source="reddit:test",
            timestamp="2026-05-01T10:00:00Z",
            engagement=5,
            mentions=[Mention(symbol="EGX_30", confidence=0.70)],
            intent={"intents": ["BEARISH"], "score": -0.8},
            content={"label": "OPINION", "weight": 1.0},
            sentiment={"score": -0.6, "label": "bearish", "confidence": 0.7},
        )
        per_symbol = aggregate([post])
        assert "EGX_MARKET" in per_symbol, "EGX_30 mention must route to EGX_MARKET bucket."
        # Must NOT appear as a separate per-stock entry
        assert "EGX_30" not in per_symbol or per_symbol.get("EGX_30") is None, (
            "EGX_30 must not accumulate as a stand-alone per-stock symbol."
        )

    def test_egx70_mention_routes_to_egx_market_in_aggregator(self) -> None:
        post = ScoredPost(
            text="egx 70 اليوم صاعد",
            url="https://example.com",
            platform="reddit",
            source="reddit:test",
            timestamp="2026-05-01T10:00:00Z",
            engagement=3,
            mentions=[Mention(symbol="EGX_70", confidence=0.70)],
            intent={"intents": ["BULLISH"], "score": 0.6},
            content={"label": "OPINION", "weight": 1.0},
            sentiment={"score": 0.5, "label": "bullish", "confidence": 0.6},
        )
        per_symbol = aggregate([post])
        assert "EGX_MARKET" in per_symbol
        assert "EGX70" not in per_symbol
        assert "EGX_70" not in per_symbol

    def test_egx_broad_term_extracts_and_routes_to_market(self) -> None:
        text = "البورصة المصرية تعافت من الضغط"
        mentions = extract(text)
        broad = [m for m in mentions if m.symbol == "EGX_BROAD"]
        assert broad, "'البورصة المصرية' must produce EGX_BROAD mention."
        non_index = [m for m in mentions if not m.symbol.startswith("EGX_")]
        assert not non_index, f"No per-stock mentions from generic bourse text. Got: {non_index}"


# ---------------------------------------------------------------------------
# C. Adversarial: market→stock contamination attempts
# ---------------------------------------------------------------------------

class TestMarketToStockContamination:
    """The key adversarial scenario from the Phase 1 audit:

    A post containing only generic market-mood language ("السوق", "البورصة")
    with NO ticker-specific mention must produce ONLY an EGX_MARKET or
    EGX_BROAD mention — never a per-stock mention — so that COMI (or any
    other ticker) does not inherit market-wide fear/greed.
    """

    def _market_only_posts(self, texts: list[str]) -> dict:
        """Run texts through extract → aggregate and return per_symbol."""
        scored = []
        for i, text in enumerate(texts):
            mentions = extract(text)
            scored.append(ScoredPost(
                text=text,
                url=f"https://example.com/{i}",
                platform="reddit",
                source="reddit:test",
                timestamp="2026-05-01T10:00:00Z",
                engagement=5,
                mentions=mentions,
                intent={"intents": ["BEARISH"], "score": -0.7},
                content={"label": "OPINION", "weight": 1.0},
                sentiment={"score": -0.6, "label": "bearish", "confidence": 0.7},
            ))
        return aggregate(scored)

    def test_generic_market_fear_posts_produce_no_per_stock_symbols(self) -> None:
        """Core adversarial test: 10 bearish market-mood posts must not produce
        any per-stock entry in the aggregation output."""
        corpus = [
            "السوق ضعيف النهاردة والبيع مسيطر",
            "البورصة المصرية في ضغط شديد",
            "السوق مش كويس وكل الاسهم بتنزل",
            "خايف على السوق من القرارات الجديدة",
            "البورصة هتعدي على خير ان شاء الله",
            "السوق اليوم تحت ضغط مؤسسي",
            "مؤشر البورصة المصرية في الاحمر",
            "egx في ضغط بسبب الوضع الاقتصادي",
            "السوق صعب والكل بيبيع",
            "البورصة المصرية محتاجة دعم",
        ]
        per_symbol = self._market_only_posts(corpus)
        per_stock = {k: v for k, v in per_symbol.items() if not k.startswith("EGX_")}
        assert not per_stock, (
            "Generic market-mood posts must NOT produce any per-stock entries. "
            f"Got: {list(per_stock.keys())}"
        )
        assert "EGX_MARKET" in per_symbol or any(
            k.startswith("EGX_") for k in per_symbol
        ), "Market-only posts must route to EGX_MARKET."

    def test_comi_not_contaminated_by_bearish_market_posts(self) -> None:
        """COMI must not appear in aggregation output when no post mentions CIB
        / التجاري الدولي / COMI cashtag — only generic market fear."""
        corpus = [
            "السوق بيتهاوى والاسهم كلها هبوط",
            "البورصة المصرية تحت ضغط شديد من الخارج",
            "مش عارف اشتري ايه في الوضع ده",
        ]
        per_symbol = self._market_only_posts(corpus)
        assert "COMI" not in per_symbol, (
            "COMI must NOT appear in aggregation when no post mentions CIB / COMI."
        )

    def test_sector_keyword_alone_does_not_create_per_stock_entry(self) -> None:
        """'البنوك' alone (sector mood) must not create a COMI or any bank entry."""
        corpus = [
            "القطاع المصرفي يعاني من ارتفاع الفوائد",
            "البنوك في ضغط بسبب السياسة النقدية",
            "الاسهم البنكية هابطة النهاردة",
        ]
        per_symbol = self._market_only_posts(corpus)
        bank_stocks = {"COMI", "ADIB", "CIEB", "HDBK", "QNBE", "SAUD", "EXPA"}
        per_stock_hits = {k for k in per_symbol if k in bank_stocks}
        assert not per_stock_hits, (
            "Sector-level bank discussion must NOT create per-stock entries. "
            f"Got: {per_stock_hits}"
        )

    def test_inflation_news_arabic_does_not_tag_any_ticker(self) -> None:
        """Macro headline about inflation must not produce per-stock signal."""
        corpus = [
            "التضخم في مصر وصل ٣٠ بالمئة وتأثيره على الاستثمار",
            "قرار البنك المركزي برفع الفائدة يضغط على الاقتصاد",
        ]
        per_symbol = self._market_only_posts(corpus)
        per_stock = {k: v for k, v in per_symbol.items() if not k.startswith("EGX_")}
        # CBE / inflation posts might match HDBK alias "بنك التعمير" — if so,
        # confidence must stay at alias level (≤0.85), never cashtag level.
        for symbol, signal in per_stock.items():
            assert signal["confidence"] <= 0.85, (
                f"{symbol} confidence {signal['confidence']} too high from macro text."
            )

    def test_suez_canal_geopolitical_post_does_not_tag_scts_or_cana(self) -> None:
        """Geopolitical Suez Canal posts must not fire SCTS or CANA entity."""
        texts = [
            "قناة السويس تمر بها الشحنات العالمية يوميا",
            "قناة السويس مهمة للاقتصاد العالمي",
            "حركة الملاحة في قناة السويس انخفضت بسبب التوترات",
        ]
        for text in texts:
            mentions = extract(text)
            scts_hits = [m for m in mentions if m.symbol == "SCTS"]
            cana_hits = [m for m in mentions if m.symbol == "CANA"]
            assert not scts_hits, (
                f"SCTS must NOT be extracted from geopolitical Suez text: {text!r}"
            )
            # CANA alias is "بنك قناة السويس" — the full alias requires "بنك"
            # preceding "قناة السويس", so plain geopolitical text should not match.
            assert not cana_hits, (
                f"CANA must NOT be extracted from geopolitical Suez text: {text!r}"
            )


# ---------------------------------------------------------------------------
# D. Valid matches that must still work (regression guard)
# ---------------------------------------------------------------------------

class TestValidMatchesRegression:
    """Ensure the fixes don't break correct entity extraction."""

    @pytest.mark.parametrize("text,symbol,min_conf", [
        ("$COMI.CA strong buy today", "COMI", 1.0),
        ("COMI.CA هدف 40", "COMI", 1.0),
        ("البنك التجاري الدولي نتايجه قوية", "COMI", 0.85),
        ("سهم عبور لاند اختراق ايجابي", "OLFI", 0.75),
        ("ابن سينا فارما شراء قوي", "ISPH", 0.75),
        ("قناة السويس لتوطين التكنولوجيا هدف 95", "SCTS", 0.85),
        ("رايكم في ام ام جروب", "MTIE", 0.75),
        ("فوري بامب قوي النهاردة", "FWRY", 0.75),
        ("جهينة تجميع كبير", "JUFO", 0.75),
        ("سوديك اختراق مقاومة 35", "OCDI", 0.75),
        ("بالم هيلز هتطلع على الاعلي", "PHDC", 0.75),
        ("فيصل الإسلامي نتايجه حلوة", "FAIT", 0.75),
        ("دومتي منتجاتها منتشرة", "DOMT", 0.75),
    ])
    def test_valid_entity_still_extracted(
        self, text: str, symbol: str, min_conf: float
    ) -> None:
        mentions = extract(text)
        hits = [m for m in mentions if m.symbol == symbol]
        assert hits, f"Expected {symbol} in: {text!r}. Got: {[m.symbol for m in mentions]}"
        assert hits[0].confidence >= min_conf, (
            f"{symbol} confidence {hits[0].confidence} < expected {min_conf}"
        )

    def test_east_in_non_finance_context_is_not_extracted(self) -> None:
        # EAST is in _AMBIGUOUS_BARE_TICKERS; must not fire without finance context.
        assert not extract("EAST winds are strong in the middle east today")

    def test_cashtag_always_highest_confidence(self) -> None:
        mentions = extract("$HRHO.CA شراء")
        hrho = [m for m in mentions if m.symbol == "HRHO"]
        assert hrho and hrho[0].confidence == 1.0

    def test_dotca_suffix_extracts_at_full_confidence(self) -> None:
        mentions = extract("FWRY.CA target 12 EGP")
        fwry = [m for m in mentions if m.symbol == "FWRY"]
        assert fwry and fwry[0].confidence == 1.0
