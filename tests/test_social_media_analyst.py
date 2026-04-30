"""
Tests for the EGX Social Media Analyst System
===============================================
Tests all components of the new social media analysis pipeline:
1. Schema validation (SocialPost, SocialMediaResult)
2. Cached data integrity
3. Sentiment engine (Arabic + English)
4. Data aggregation
5. Tool wrappers
6. Agent output format

Run: python -m pytest tests/test_social_media_analyst.py -v
  or: python tests/test_social_media_analyst.py  (standalone)
"""

import sys
import os
import json

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# =============================================================================
# Test 1: Schema Validation
# =============================================================================

def test_social_post_schema():
    """Test SocialPost dataclass creation and serialization."""
    from tradingagents.dataflows.social_media_sources.schema import SocialPost
    
    post = SocialPost(
        text="سهم CIB طالع النهارده 🚀",
        timestamp="2026-04-07T10:30:00",
        platform="twitter",
        engagement={"likes": 15, "shares": 3, "comments": 5, "views": 200},
        ticker="COMI",
        language="ar",
        author="test_user",
    )
    
    # Test creation
    assert post.text == "سهم CIB طالع النهارده 🚀"
    assert post.platform == "twitter"
    assert post.ticker == "COMI"
    assert post.language == "ar"
    
    # Test serialization
    d = post.to_dict()
    assert isinstance(d, dict)
    assert d["ticker"] == "COMI"
    assert d["engagement"]["likes"] == 15
    
    # Test from_dict
    post2 = SocialPost.from_dict(d)
    assert post2.text == post.text
    assert post2.ticker == post.ticker
    
    print("  ✓ test_social_post_schema PASSED")


def test_social_media_result_schema():
    """Test SocialMediaResult with metadata computation."""
    from tradingagents.dataflows.social_media_sources.schema import SocialPost, SocialMediaResult
    
    posts = [
        SocialPost(text="bullish signal", timestamp="2026-04-07T10:00:00", 
                   platform="twitter", language="en", ticker="COMI"),
        SocialPost(text="سهم كويس", timestamp="2026-04-07T11:00:00",
                   platform="telegram", language="ar", ticker="COMI"),
        SocialPost(text="mixed content يا جماعة", timestamp="2026-04-07T12:00:00",
                   platform="reddit", language="mixed", ticker="COMI"),
    ]
    
    result = SocialMediaResult(
        ticker="COMI",
        query_date="2026-04-07",
        look_back_days=7,
        posts=posts,
        platforms_queried=["twitter", "telegram", "reddit"],
        platforms_succeeded=["twitter", "telegram", "reddit"],
    )
    
    result.compute_metadata()
    
    assert result.total_posts == 3
    assert result.arabic_posts == 1
    assert result.english_posts == 1
    assert result.mixed_posts == 1
    assert not result.has_sufficient_data  # <5 posts
    
    # Test serialization
    d = result.to_dict()
    assert isinstance(d, dict)
    assert d["ticker"] == "COMI"
    assert len(d["posts"]) == 3
    
    print("  ✓ test_social_media_result_schema PASSED")


# =============================================================================
# Test 2: Cached Data
# =============================================================================

def test_cached_data_exists():
    """Test that cached data returns posts for known tickers."""
    from tradingagents.dataflows.social_media_sources.cached_data import (
        get_cached_social_data, EGX_TICKER_ALIASES
    )
    
    # Test known tickers
    for ticker in ["COMI", "HRHO", "EAST", "EFIH", "SWDY"]:
        posts = get_cached_social_data(ticker)
        assert len(posts) > 0, f"No cached data for {ticker}"
        assert all(hasattr(p, 'text') for p in posts)
        assert all(hasattr(p, 'platform') for p in posts)
    
    # Test unknown ticker (should get generic data)
    posts = get_cached_social_data("ZZZZZ")
    assert len(posts) > 0, "No generic data generated"
    
    # Test aliases map
    assert "COMI" in EGX_TICKER_ALIASES
    assert "التجاري الدولي" in EGX_TICKER_ALIASES["COMI"]
    
    print("  ✓ test_cached_data_exists PASSED")


def test_cached_data_has_arabic():
    """Test that cached data includes Arabic content."""
    from tradingagents.dataflows.social_media_sources.cached_data import get_cached_social_data
    
    posts = get_cached_social_data("COMI")
    arabic_posts = [p for p in posts if p.language == "ar"]
    english_posts = [p for p in posts if p.language == "en"]
    
    assert len(arabic_posts) >= 2, f"Too few Arabic posts: {len(arabic_posts)}"
    assert len(english_posts) >= 2, f"Too few English posts: {len(english_posts)}"
    
    # Verify Arabic text has Arabic characters
    for post in arabic_posts:
        import re
        arabic_chars = len(re.findall(r'[\u0600-\u06FF]', post.text))
        assert arabic_chars > 0, f"Arabic post has no Arabic chars: {post.text[:50]}"
    
    print("  ✓ test_cached_data_has_arabic PASSED")


# =============================================================================
# Test 3: Sentiment Engine
# =============================================================================

def test_arabic_bullish_sentiment():
    """Test Arabic bullish sentiment detection."""
    from tradingagents.dataflows.social_media_sources.sentiment_engine import _score_arabic_text
    
    # Strong bullish
    score, signals = _score_arabic_text("السهم ده صاروخ وهيطير قريب")
    assert score > 0.5, f"Expected strong bullish, got {score}"
    assert len(signals) > 0
    
    # Moderate bullish
    score, _ = _score_arabic_text("سهم كويس ومتفائل فيه")
    assert score > 0.2, f"Expected moderate bullish, got {score}"
    
    # Weak bullish
    score, _ = _score_arabic_text("مش وحش يعني")
    assert score > 0, f"Expected weak bullish, got {score}"
    
    print("  ✓ test_arabic_bullish_sentiment PASSED")


def test_arabic_bearish_sentiment():
    """Test Arabic bearish sentiment detection."""
    from tradingagents.dataflows.social_media_sources.sentiment_engine import _score_arabic_text
    
    # Strong bearish
    score, signals = _score_arabic_text("السهم ده كارثة وهينهار")
    assert score < -0.5, f"Expected strong bearish, got {score}"
    assert len(signals) > 0
    
    # Moderate bearish
    score, _ = _score_arabic_text("اهرب من السهم ده خطر")
    assert score < -0.3, f"Expected moderate bearish, got {score}"
    
    print("  ✓ test_arabic_bearish_sentiment PASSED")


def test_arabic_negation_handling():
    """Test that Arabic negation inverts sentiment."""
    from tradingagents.dataflows.social_media_sources.sentiment_engine import _score_arabic_text
    
    # "Good" vs "Not good"
    score_positive, _ = _score_arabic_text("السهم كويس")
    score_negated, _ = _score_arabic_text("السهم مش كويس")
    
    # Negated should be lower (more negative) than original
    assert score_negated < score_positive, (
        f"Negation failed: 'كويس'={score_positive}, 'مش كويس'={score_negated}"
    )
    
    print("  ✓ test_arabic_negation_handling PASSED")


def test_english_sentiment():
    """Test English financial sentiment detection."""
    from tradingagents.dataflows.social_media_sources.sentiment_engine import _score_english_text
    
    # Bullish
    score, signals = _score_english_text("This stock is about to breakout! Bullish momentum, buy signal confirmed.")
    assert score > 0.3, f"Expected bullish, got {score}"
    assert len(signals) > 0
    
    # Bearish
    score, signals = _score_english_text("Avoid this stock. Overvalued, bubble territory. Sell immediately.")
    assert score < -0.3, f"Expected bearish, got {score}"
    
    # Neutral
    score, _ = _score_english_text("The company reported quarterly results yesterday.")
    assert -0.3 <= score <= 0.3, f"Expected neutral, got {score}"
    
    print("  ✓ test_english_sentiment PASSED")


def test_english_negation():
    """Test English negation handling."""
    from tradingagents.dataflows.social_media_sources.sentiment_engine import _score_english_text
    
    score_pos, _ = _score_english_text("This is bullish")
    score_neg, _ = _score_english_text("This is not bullish")
    
    assert score_neg < score_pos, (
        f"Negation failed: 'bullish'={score_pos}, 'not bullish'={score_neg}"
    )
    
    print("  ✓ test_english_negation PASSED")


def test_emoji_sentiment():
    """Test emoji sentiment extraction."""
    from tradingagents.dataflows.social_media_sources.sentiment_engine import _score_emojis
    
    assert _score_emojis("🚀🚀🚀") > 0.5, "Rockets should be bullish"
    assert _score_emojis("📉📉") < -0.3, "Chart-down should be bearish"
    assert _score_emojis("Hello world") == 0.0, "No emojis should be neutral"
    
    print("  ✓ test_emoji_sentiment PASSED")


def test_full_sentiment_analysis():
    """Test the complete sentiment analysis pipeline."""
    from tradingagents.dataflows.social_media_sources.aggregator import get_social_media_data
    from tradingagents.dataflows.social_media_sources.sentiment_engine import analyze_social_sentiment
    
    # Collect data
    social_data = get_social_media_data("COMI", "2026-04-07", look_back_days=7)
    
    assert social_data.total_posts > 0, "No posts collected"
    
    # Analyze sentiment
    result = analyze_social_sentiment(social_data)
    
    # Validate output schema
    assert isinstance(result.sentiment_score, float)
    assert -1.0 <= result.sentiment_score <= 1.0
    assert isinstance(result.buzz_score, float)
    assert 0.0 <= result.buzz_score <= 1.0
    assert isinstance(result.confidence, float)
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.total_posts_analyzed, int)
    assert result.total_posts_analyzed > 0
    assert isinstance(result.bullish_signals, int)
    assert isinstance(result.bearish_signals, int)
    assert isinstance(result.platform_sentiment, dict)
    
    # For COMI (popular stock), should have sufficient data
    assert result.data_sufficient, "COMI should have sufficient data"
    
    print(f"  ✓ test_full_sentiment_analysis PASSED")
    print(f"    COMI sentiment: {result.sentiment_score:+.3f}")
    print(f"    Buzz: {result.buzz_score:.2f}, Confidence: {result.confidence:.2f}")
    print(f"    Bullish: {result.bullish_signals}, Bearish: {result.bearish_signals}, Neutral: {result.neutral_signals}")
    print(f"    Themes: {result.key_themes}")


def test_hype_detection():
    """Test hype detection on artificial data."""
    from tradingagents.dataflows.social_media_sources.schema import SocialPost, SocialMediaResult
    from tradingagents.dataflows.social_media_sources.sentiment_engine import analyze_social_sentiment
    
    # Create artificially hyped data
    hyped_posts = [
        SocialPost(
            text=f"سهم COMI صاروخ 🚀🚀🚀 هيطير #{i}",
            timestamp="2026-04-07T10:00:00",
            platform="telegram",
            engagement={"likes": 200, "shares": 50, "comments": 30, "views": 5000},
            ticker="COMI",
            language="ar",
        )
        for i in range(20)  # 20 hyped posts
    ]
    
    social_data = SocialMediaResult(
        ticker="COMI",
        query_date="2026-04-07",
        look_back_days=7,
        posts=hyped_posts,
        platforms_queried=["telegram"],
        platforms_succeeded=["telegram"],
    )
    social_data.compute_metadata()
    
    result = analyze_social_sentiment(social_data)
    
    assert result.hype_detected, "Should detect hype with 20 rocket-emoji posts"
    assert len(result.hype_reasons) >= 2, f"Should have multiple hype reasons, got {result.hype_reasons}"
    assert result.sentiment_score > 0.5, "Hyped posts should still show bullish sentiment"
    
    print(f"  ✓ test_hype_detection PASSED")
    print(f"    Hype detected: {result.hype_detected}")
    print(f"    Reasons: {result.hype_reasons}")


# =============================================================================
# Test 4: Aggregation
# =============================================================================

def test_aggregator_multi_platform():
    """Test multi-platform aggregation."""
    from tradingagents.dataflows.social_media_sources.aggregator import get_social_media_data
    
    result = get_social_media_data("COMI", "2026-04-07", look_back_days=7)
    
    # Should have data from multiple platforms
    assert len(result.platforms_queried) >= 3, f"Expected 3+ platforms, got {result.platforms_queried}"
    assert result.total_posts > 0, "Should have at least some posts"
    
    # Should have both Arabic and English
    assert result.arabic_posts > 0, "Should have Arabic posts"
    assert result.english_posts > 0, "Should have English posts"
    
    # Quality score should be computed
    assert result.data_quality_score > 0, "Quality score should be > 0"
    
    print(f"  ✓ test_aggregator_multi_platform PASSED")
    print(f"    Total posts: {result.total_posts}")
    print(f"    Platforms: {result.platforms_succeeded}")
    print(f"    Arabic: {result.arabic_posts}, English: {result.english_posts}")


def test_aggregator_deduplication():
    """Test that duplicate posts are removed."""
    from tradingagents.dataflows.social_media_sources.aggregator import _deduplicate_posts
    from tradingagents.dataflows.social_media_sources.schema import SocialPost
    
    posts = [
        SocialPost(text="Duplicate post about COMI", timestamp="2026-04-07T10:00:00",
                   platform="twitter", language="en"),
        SocialPost(text="Duplicate post about COMI", timestamp="2026-04-07T11:00:00",
                   platform="telegram", language="en"),
        SocialPost(text="Unique post about HRHO", timestamp="2026-04-07T12:00:00",
                   platform="reddit", language="en"),
    ]
    
    deduped = _deduplicate_posts(posts)
    assert len(deduped) == 2, f"Expected 2 unique posts, got {len(deduped)}"
    
    print("  ✓ test_aggregator_deduplication PASSED")


def test_ticker_extraction():
    """Test ticker extraction from text."""
    from tradingagents.dataflows.social_media_sources.aggregator import _extract_ticker
    
    # English ticker
    assert _extract_ticker("I just bought COMI at a great price", "DEFAULT") == "COMI"
    
    # Arabic company name
    assert _extract_ticker("سهم التجاري الدولي ممتاز", "DEFAULT") == "COMI"
    
    # English company name
    assert _extract_ticker("Elsewedy Electric is expanding", "DEFAULT") == "SWDY"
    
    # Unknown → default
    assert _extract_ticker("Random text with no ticker", "DEFAULT") == "DEFAULT"
    
    print("  ✓ test_ticker_extraction PASSED")


# =============================================================================
# Test 5: Tool Wrappers
# =============================================================================

def test_social_sentiment_tool():
    """Test the get_social_sentiment LangChain tool."""
    from tradingagents.agents.utils.social_media_tools import get_social_sentiment
    
    result_str = get_social_sentiment.invoke({
        "ticker": "COMI",
        "curr_date": "2026-04-07",
        "look_back_days": 7,
    })
    
    # Should return valid JSON
    result = json.loads(result_str)
    
    assert "sentiment_score" in result
    assert "buzz_score" in result
    assert "confidence" in result
    assert "signals" in result
    assert "hype_alert" in result
    assert "platform_sentiment" in result
    assert "language_sentiment" in result
    assert "key_themes" in result
    assert "data_quality" in result
    
    assert -1.0 <= result["sentiment_score"] <= 1.0
    assert 0.0 <= result["buzz_score"] <= 1.0
    assert 0.0 <= result["confidence"] <= 1.0
    
    print(f"  ✓ test_social_sentiment_tool PASSED")
    print(f"    Sentiment: {result['sentiment_score']:+.3f}")
    print(f"    Signals: {result['signals']}")


def test_social_media_posts_tool():
    """Test the get_social_media_posts LangChain tool."""
    from tradingagents.agents.utils.social_media_tools import get_social_media_posts
    from tradingagents.dataflows.social_media_sources.schema import SocialMediaResult, SocialPost
    from unittest.mock import patch
    
    mock_result = SocialMediaResult(
        ticker="COMI",
        query_date="2026-04-07",
        look_back_days=7,
        posts=[
            SocialPost(text="Test post 1", timestamp="2026-04-07", platform="twitter", language="en"),
            SocialPost(text="Test post 2", timestamp="2026-04-07", platform="telegram", language="ar")
        ]
    )
    mock_result.compute_metadata()
    
    with patch('tradingagents.agents.utils.social_media_tools.get_social_media_data', return_value=mock_result):
        result_str = get_social_media_posts.invoke({
            "ticker": "COMI",
            "curr_date": "2026-04-07",
            "look_back_days": 7,
            "platform": "all",
        })
    
    result = json.loads(result_str)
    
    assert "total_posts_found" in result
    assert "posts" in result
    assert isinstance(result["posts"], list)
    assert len(result["posts"]) > 0
    
    # Verify post structure
    post = result["posts"][0]
    assert "text" in post
    assert "platform" in post
    assert "language" in post
    
    print(f"  ✓ test_social_media_posts_tool PASSED")
    print(f"    Posts returned: {len(result['posts'])}")


# =============================================================================
# Test 6: Agent Output Format
# =============================================================================

def test_structured_output_extraction():
    """Test JSON extraction from LLM-like text responses."""
    from tradingagents.agents.analysts.social_media_analyst import _extract_structured_output
    
    # Test with JSON block
    text_with_json = '''
    Here is my analysis of COMI:
    
    ```json
    {"ticker": "COMI", "sentiment_score": 0.65, "buzz_score": 0.8, "confidence": 0.7}
    ```
    
    The stock shows positive momentum.
    '''
    
    result = _extract_structured_output(text_with_json, "COMI")
    assert result["sentiment_score"] == 0.65
    assert result["buzz_score"] == 0.8
    
    # Test with inline text parsing
    text_plain = "Overall sentiment is BULLISH with sentiment_score: 0.45"
    result = _extract_structured_output(text_plain, "COMI")
    assert result["sentiment_score"] == 0.45
    assert result["direction"] == "bullish"
    
    # Test with no extractable data
    text_empty = "This is just a general comment with no structured data."
    result = _extract_structured_output(text_empty, "COMI")
    assert result["ticker"] == "COMI"
    assert result["source"] == "text_parsing_fallback"
    
    print("  ✓ test_structured_output_extraction PASSED")


# =============================================================================
# Test 7: Integration Sanity Check
# =============================================================================

def test_state_has_social_field():
    """Verify AgentState includes social_sentiment_analysis field."""
    from tradingagents.agents.utils.agent_states import AgentState
    
    # Check that the field exists in the type hints
    annotations = AgentState.__annotations__
    assert "social_sentiment_analysis" in annotations, (
        f"social_sentiment_analysis missing from AgentState. Fields: {list(annotations.keys())}"
    )
    assert "sentiment_report" in annotations
    
    print("  ✓ test_state_has_social_field PASSED")


def test_interface_has_social_category():
    """Verify interface.py has social_media_data category."""
    from tradingagents.dataflows.interface import TOOLS_CATEGORIES
    
    assert "social_media_data" in TOOLS_CATEGORIES, (
        f"social_media_data missing from TOOLS_CATEGORIES: {list(TOOLS_CATEGORIES.keys())}"
    )
    
    social_tools = TOOLS_CATEGORIES["social_media_data"]["tools"]
    assert "get_social_sentiment" in social_tools
    assert "get_social_media_posts" in social_tools
    
    print("  ✓ test_interface_has_social_category PASSED")


# =============================================================================
# Test 8: Different Stocks
# =============================================================================

def test_multiple_tickers():
    """Test sentiment analysis across multiple EGX tickers."""
    from tradingagents.dataflows.social_media_sources.aggregator import get_social_media_data
    from tradingagents.dataflows.social_media_sources.sentiment_engine import analyze_social_sentiment
    
    tickers = ["COMI", "HRHO", "EAST", "EFIH", "UNKNOWN_TICKER"]
    
    for ticker in tickers:
        social_data = get_social_media_data(ticker, "2026-04-07", look_back_days=7)
        result = analyze_social_sentiment(social_data)
        
        # Basic validation
        assert -1.0 <= result.sentiment_score <= 1.0
        assert 0.0 <= result.buzz_score <= 1.0
        assert 0.0 <= result.confidence <= 1.0
        assert result.total_posts_analyzed >= 0
        
        print(f"    {ticker}: sentiment={result.sentiment_score:+.3f}, "
              f"buzz={result.buzz_score:.2f}, confidence={result.confidence:.2f}, "
              f"posts={result.total_posts_analyzed}")
    
    print("  ✓ test_multiple_tickers PASSED")


# =============================================================================
# Standalone Runner
# =============================================================================

def run_all_tests():
    """Run all tests and print summary."""
    print("=" * 60)
    print("  EGX Social Media Analyst — Test Suite")
    print("=" * 60)
    
    tests = [
        ("Schema: SocialPost", test_social_post_schema),
        ("Schema: SocialMediaResult", test_social_media_result_schema),
        ("Cached Data: Exists", test_cached_data_exists),
        ("Cached Data: Has Arabic", test_cached_data_has_arabic),
        ("Sentiment: Arabic Bullish", test_arabic_bullish_sentiment),
        ("Sentiment: Arabic Bearish", test_arabic_bearish_sentiment),
        ("Sentiment: Arabic Negation", test_arabic_negation_handling),
        ("Sentiment: English", test_english_sentiment),
        ("Sentiment: English Negation", test_english_negation),
        ("Sentiment: Emoji", test_emoji_sentiment),
        ("Sentiment: Full Pipeline", test_full_sentiment_analysis),
        ("Sentiment: Hype Detection", test_hype_detection),
        ("Aggregator: Multi-Platform", test_aggregator_multi_platform),
        ("Aggregator: Deduplication", test_aggregator_deduplication),
        ("Aggregator: Ticker Extract", test_ticker_extraction),
        ("Tool: get_social_sentiment", test_social_sentiment_tool),
        ("Tool: get_social_media_posts", test_social_media_posts_tool),
        ("Agent: Output Extraction", test_structured_output_extraction),
        ("Integration: AgentState", test_state_has_social_field),
        ("Integration: Interface", test_interface_has_social_category),
        ("Integration: Multiple Tickers", test_multiple_tickers),
    ]
    
    passed = 0
    failed = 0
    errors = []
    
    for name, test_fn in tests:
        try:
            print(f"\n[TEST] {name}")
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, str(e)))
            import traceback
            print(f"  ✗ {name} FAILED: {e}")
            traceback.print_exc()
    
    print("\n" + "=" * 60)
    print(f"  RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)
    
    if errors:
        print("\n  FAILURES:")
        for name, err in errors:
            print(f"    - {name}: {err}")
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
