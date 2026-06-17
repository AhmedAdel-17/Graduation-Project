"""Offline tests for the new free data sources + their pipeline wiring.

Network is never touched here — we test URL construction, env parsing, query
reuse, and that the pipeline registers every source. Live reachability is
validated separately during data-source probing, not in CI.
"""
from __future__ import annotations

import importlib


def test_bing_reuses_google_query_taxonomy():
    from tradingagents.dataflows.social_v2.sources import bing_news_ar, google_news_ar

    # Same object → one source of truth for the layered query set.
    assert bing_news_ar.DEFAULT_QUERIES is google_news_ar.DEFAULT_QUERIES


def test_bing_rss_url_is_bing_rss():
    from tradingagents.dataflows.social_v2.sources import bing_news_ar

    url = bing_news_ar._build_rss_url('"البورصة المصرية"')
    assert url.startswith("https://www.bing.com/news/search?")
    assert "format=RSS" in url
    assert "q=" in url


def test_google_rss_url_is_google_rss():
    from tradingagents.dataflows.social_v2.sources import google_news_ar

    url = google_news_ar._build_rss_url('"البورصة المصرية"')
    assert url.startswith("https://news.google.com/rss/search?")
    assert "hl=ar" in url


def test_egypt_rss_env_feed_parsing():
    from tradingagents.dataflows.social_v2.sources import egypt_news_rss

    parsed = egypt_news_rss._parse_feeds_env(
        "almal=https://almalnews.com/feed/, https://x.com/rss"
    )
    assert ("almal", "https://almalnews.com/feed/") in parsed
    # Bare URL gets an auto label.
    assert any(url == "https://x.com/rss" for _label, url in parsed)
    assert egypt_news_rss._parse_feeds_env(None) == []


def test_bing_disabled_via_env(monkeypatch):
    from tradingagents.dataflows.social_v2.sources import bing_news_ar

    monkeypatch.setenv("ENABLE_BING_NEWS_AR", "0")
    assert bing_news_ar.scrape() == []


def test_telegram_default_channels_curated():
    from tradingagents.dataflows.social_v2.sources import telegram_public

    # The known-private (302) channels were removed; high-signal EGX channels kept.
    chans = set(telegram_public.DEFAULT_CHANNELS)
    assert "EGX_30" in chans
    assert "alborsanews" in chans
    # These were dropped because they return HTTP 302 (private).
    assert "mubasher_eg" not in chans
    assert "youm7channel" not in chans


def test_pipeline_registers_new_sources():
    pipeline = importlib.import_module(
        "tradingagents.dataflows.social_v2.pipeline"
    )
    # The new aggregator + direct-RSS sources are imported and callable.
    assert hasattr(pipeline, "bing_news_ar")
    assert hasattr(pipeline, "egypt_news_rss")
    assert callable(pipeline.bing_news_ar.scrape)
    assert callable(pipeline.egypt_news_rss.scrape)


def test_all_news_sources_share_post_platform_news():
    """News-grade sources must emit platform='news' so the pipeline's light
    EGX guard (LIGHT_GUARDED_PLATFORMS) applies to them."""
    from tradingagents.dataflows.social_v2.pipeline import LIGHT_GUARDED_PLATFORMS

    assert "news" in LIGHT_GUARDED_PLATFORMS


# --------------------------------------------------------------------------- #
# facebook_dork — the Apify-fallback Google-dorking source
# --------------------------------------------------------------------------- #
def test_dork_reuses_apify_filter_contract():
    """The dork source must share Apify's signal-keyword + length contract so
    'what counts as a tradeable EGX FB post' lives in exactly one place."""
    from tradingagents.dataflows.social_v2.sources import facebook_apify, facebook_dork

    assert facebook_dork.SIGNAL_RX is facebook_apify.SIGNAL_RX
    assert facebook_dork.MAX_WORDS == facebook_apify.MAX_WORDS


def test_dork_queries_cover_all_default_groups():
    from tradingagents.dataflows.social_v2.sources import facebook_dork

    queries = facebook_dork._build_queries()
    labels = {label for label, _ in queries}
    # Each of the 5 confirmed EGX groups gets a precise site: dork ...
    assert "group_4021602644518797" in labels  # البورصة المصرية
    assert "group_618025406208276" in labels  # جروب الخبره
    # ... plus the broad cross-group dorks.
    assert "broad_market" in labels
    for _label, dork in queries:
        assert dork.startswith("site:facebook.com/groups")


def test_dork_provider_chain_respects_env_keys(monkeypatch):
    from tradingagents.dataflows.social_v2.sources import facebook_dork

    # No keys + HTML allowed (default) → free HTML tier only.
    monkeypatch.delenv("GOOGLE_CSE_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CSE_CX", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("FACEBOOK_DORK_ALLOW_HTML", raising=False)
    assert [n for n, _ in facebook_dork._select_providers()] == ["html"]

    # Keys present → CSE first, then Serper, then HTML.
    monkeypatch.setenv("GOOGLE_CSE_KEY", "k")
    monkeypatch.setenv("GOOGLE_CSE_CX", "cx")
    monkeypatch.setenv("SERPER_API_KEY", "s")
    assert [n for n, _ in facebook_dork._select_providers()] == ["google_cse", "serper", "html"]

    # HTML opt-out with no keys → no providers at all (source no-ops).
    monkeypatch.delenv("GOOGLE_CSE_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CSE_CX", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.setenv("FACEBOOK_DORK_ALLOW_HTML", "0")
    assert facebook_dork._select_providers() == []


def test_dork_disabled_via_env(monkeypatch):
    from tradingagents.dataflows.social_v2.sources import facebook_dork

    monkeypatch.setenv("ENABLE_FACEBOOK_DORK", "0")
    assert facebook_dork.scrape() == []


def test_dork_hit_to_post_filters():
    from tradingagents.dataflows.social_v2.sources import facebook_dork

    fb = "https://www.facebook.com/groups/4021602644518797/posts/123456"

    # Valid: FB group post URL + signal keyword + long enough.
    good = facebook_dork._Hit(
        title="سهم البنك التجاري الدولي هدف قوي والتوصية شراء الآن",
        snippet="تحليل فني يدعم الاتجاه الصاعد",
        url=fb,
    )
    post = facebook_dork._hit_to_post(good, "google_cse", max_age_days=7)
    assert post is not None
    assert post.platform == "facebook"  # → routed through LIGHT_GUARDED guard
    assert post.source == "facebook_dork:google_cse"
    assert post.engagement == 0

    # Non-Facebook URL → rejected.
    assert facebook_dork._hit_to_post(
        facebook_dork._Hit(title="سهم شراء هدف", snippet="x", url="https://example.com/a"),
        "html", 7,
    ) is None

    # No trading-signal keyword → rejected even though it's a real FB post.
    assert facebook_dork._hit_to_post(
        facebook_dork._Hit(
            title="مرحبا بكم في الجروب نتمنى لكم يوما سعيدا اهلا وسهلا بالجميع",
            snippet="", url=fb,
        ),
        "html", 7,
    ) is None

    # Too short → rejected.
    assert facebook_dork._hit_to_post(
        facebook_dork._Hit(title="سهم", snippet="", url=fb), "html", 7
    ) is None


def test_dork_google_cse_parses_json(monkeypatch):
    """CSE JSON → _Hit list, without touching the network."""
    from tradingagents.dataflows.social_v2.sources import facebook_dork

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "items": [
                    {
                        "title": "سهم شراء هدف",
                        "snippet": "تحليل",
                        "link": "https://www.facebook.com/groups/4021602644518797/posts/9",
                    }
                ]
            }

    monkeypatch.setenv("GOOGLE_CSE_KEY", "k")
    monkeypatch.setenv("GOOGLE_CSE_CX", "cx")
    monkeypatch.setattr(facebook_dork.requests, "get", lambda *a, **k: _Resp())

    hits = facebook_dork._provider_google_cse("site:facebook.com/groups/x سهم", timeout=5)
    assert len(hits) == 1
    assert hits[0].url.endswith("/posts/9")


def test_pipeline_registers_dork_fallback():
    pipeline = importlib.import_module(
        "tradingagents.dataflows.social_v2.pipeline"
    )
    assert hasattr(pipeline, "facebook_dork")
    assert callable(pipeline.facebook_dork.scrape)
