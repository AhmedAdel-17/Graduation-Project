from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json
import re
import logging
from typing import Dict, Any, List, Optional
from tradingagents.agents.utils.agent_utils import get_news, get_global_news
from tradingagents.agents.utils.news_data_tools import get_egx_company_news, get_egx_market_news
from tradingagents.dataflows.config import get_config

logger = logging.getLogger("tradingagents.news_analyst")

# =============================================================================
# News & Sentiment Analyst ("Journalist") for EGX Market
# =============================================================================
# Analyzes Arabic and English news for Egyptian stocks.
# Uses transformer-based sentiment as PRIMARY signal, LLM as reasoning layer.
# Silence (no news) reduces confidence — it's a signal, not absence of signal.
# =============================================================================

# Confidence adjustment factors
NO_NEWS_CONFIDENCE_PENALTY = 0.40
SPARSE_NEWS_PENALTY = 0.20
SINGLE_SOURCE_PENALTY = 0.15
ARABIC_ONLY_ADJUSTMENT = 0.0


def _extract_headlines_from_report(report: str) -> List[Dict[str, str]]:
    """
    Extract individual news headlines/articles from the LLM report text.

    The LLM report typically contains quoted headlines, bullet points,
    or sections describing individual articles. We extract these for
    individual transformer scoring.
    """
    headlines = []

    # Pattern 1: Quoted headlines
    quoted = re.findall(r'["""«](.+?)["""»]', report)
    for h in quoted:
        if len(h) > 15:
            headlines.append({"text": h, "source": "quoted_in_report"})

    # Pattern 2: Bullet-point items (- or • or *)
    bullets = re.findall(r'(?:^|\n)\s*[•\-\*]\s*(.+?)(?:\n|$)', report)
    for b in bullets:
        clean = b.strip()
        if len(clean) > 20 and clean not in [h["text"] for h in headlines]:
            headlines.append({"text": clean, "source": "bullet_in_report"})

    # Pattern 3: Headlines from JSON block if present
    try:
        json_str = None
        json_match = re.search(r'```json\s*(.*?)\s*```', report, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            fallback_match = re.search(r'\{.*\}', report, re.DOTALL)
            if fallback_match:
                json_str = fallback_match.group(0)
                
        if json_str:
            data = json.loads(json_str)
            for item in data.get("key_headlines", []):
                h_text = item.get("headline", "")
                if h_text and len(h_text) > 15:
                    headlines.append({
                        "text": h_text,
                        "source": item.get("source", "json_block"),
                    })
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass

    return headlines


def _run_transformer_sentiment(headlines: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Run transformer-based sentiment on extracted headlines.

    Returns aggregated transformer sentiment alongside per-headline breakdown.
    Falls back gracefully if the engine is unavailable.
    """
    if not headlines:
        return {
            "transformer_score": 0.0,
            "transformer_label": "neutral",
            "transformer_confidence": 0.1,
            "per_headline": [],
            "model_status": "no_headlines",
        }

    try:
        from tradingagents.utils.sentiment_engine import SentimentEngine

        engine = SentimentEngine.get_instance()
        texts = [h["text"] for h in headlines]
        results = engine.analyze_batch(texts, preprocess=True, deduplicate=True)

        # Build per-headline breakdown
        per_headline = []
        for h, r in zip(headlines, results):
            per_headline.append({
                "headline": h["text"][:100],
                "source": h.get("source", "unknown"),
                "score": r.score,
                "label": r.label,
                "confidence": r.confidence,
                "model": r.model_used,
            })

        # Aggregate
        aggregated = SentimentEngine.aggregate_scores(results)

        return {
            "transformer_score": aggregated.score,
            "transformer_label": aggregated.label,
            "transformer_confidence": aggregated.confidence,
            "per_headline": per_headline,
            "model_status": "ok",
            "models_used": aggregated.model_used,
        }

    except Exception as e:
        logger.warning("Transformer sentiment failed, returning neutral: %s", e)
        return {
            "transformer_score": 0.0,
            "transformer_label": "neutral",
            "transformer_confidence": 0.1,
            "per_headline": [],
            "model_status": f"error: {str(e)[:80]}",
        }


def create_news_analyst(llm):
    """
    Create the News & Sentiment Analyst ("Journalist") agent for EGX.

    Pipeline:
      1. LLM uses tools to fetch news articles
      2. LLM produces a reasoning report with headlines
      3. Headlines are extracted and scored by transformer models
      4. Both LLM analysis and transformer scores are returned
    """

    def news_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        config = get_config()
        target_market = config.get("target_market", "US")

        if target_market == "EGX":
            tools = [get_egx_company_news, get_egx_market_news]
            market_context = "EGX (Egyptian Exchange)"
        else:
            tools = [get_news, get_global_news]
            market_context = "US Markets"

        # ── Phase 2b: Pre-fetched data mode ──────────────────────────────────
        # If DataPrefetcher has already fetched the news before the graph ran,
        # skip the tool-calling round-trip entirely. The LLM still reasons over
        # the data; it just doesn't need to call tools to get it.
        prefetched_company_news = state.get("prefetched_company_news", "")
        prefetched_market_news = state.get("prefetched_market_news", "")
        has_prefetched = bool(prefetched_company_news or prefetched_market_news)

        if has_prefetched:
            # Build a direct (non-tool) prompt with the pre-fetched data injected
            prefetch_prompt = f"""You are a News & Sentiment Analyst ("Journalist") specializing in {market_context}.

## Pre-Fetched News Data
The following news data has been retrieved for you. Analyze it directly without calling any tools.

### Company News for {ticker} (as of {current_date})
{prefetched_company_news or "No company-specific news found."}

### Market News (as of {current_date})
{prefetched_market_news or "No market news found."}

## Your Analysis Task
Analyze the above news for {ticker}. You MUST:
1. Interpret BOTH Arabic and English text natively
2. Note that SILENCE (no news) REDUCES confidence — it is a signal, not neutral
3. End with a JSON block in exactly this format:

```json
{{
    "sentiment": "bullish|bearish|neutral",
    "sentiment_strength": "strong|moderate|weak",
    "confidence_score": 0-100,
    "confidence_adjustments": ["list of factors affecting confidence"],
    "explanation": "2-3 sentence summary of sentiment reasoning",
    "key_headlines": [
        {{"headline": "...", "source": "...", "language": "ar|en", "impact": "positive|negative|neutral"}}
    ],
    "news_coverage": {{
        "total_articles": 0,
        "sources_count": 0,
        "languages": ["arabic", "english"],
        "date_range": "start to end"
    }},
    "risks_from_news": ["any risk factors identified in news"],
    "catalysts_from_news": ["any positive catalysts identified"]
}}
```

Current date: {current_date} | Company: {ticker} | Market: {market_context}"""

            from langchain_core.messages import AIMessage
            result = llm.invoke(prefetch_prompt)
        else:
            # Standard tool-calling path (fallback when no prefetch available)
            system_message = f"""You are a News & Sentiment Analyst ("Journalist") specializing in {market_context}.

## Your Role
Analyze news and market sentiment for stocks. You must interpret BOTH Arabic and English news sources and provide structured sentiment analysis.

## Language Handling
- You MUST analyze Arabic text natively — do not dismiss or ignore it
- Arabic news often contains critical local market intelligence
- Report language breakdown in your analysis

## Sentiment Analysis Rules
1. SILENCE IS A SIGNAL: If no news is found, this REDUCES confidence (not neutral by default)
2. Single-source news reduces confidence
3. Mixed signals reduce confidence but should be explained
4. Official company disclosures carry more weight than opinion pieces

## Required Output Format
After your analysis, you MUST end with a JSON block in this exact format:

```json
{{{{
    "sentiment": "bullish|bearish|neutral",
    "sentiment_strength": "strong|moderate|weak",
    "confidence_score": 0-100,
    "confidence_adjustments": ["list of factors affecting confidence"],
    "explanation": "2-3 sentence summary of sentiment reasoning",
    "key_headlines": [
        {{{{"headline": "...", "source": "...", "language": "ar|en", "impact": "positive|negative|neutral"}}}}
    ],
    "news_coverage": {{{{
        "total_articles": number,
        "sources_count": number,
        "languages": ["arabic", "english"],
        "date_range": "start to end"
    }}}},
    "risks_from_news": ["any risk factors identified in news"],
    "catalysts_from_news": ["any positive catalysts identified"]
}}}}
```

First use the tools to retrieve news, then provide your analysis with the JSON summary."""

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a News & Sentiment Analyst (Journalist) for {market} stocks, collaborating with other analysts."
                        " Use the provided tools to gather news data and provide structured sentiment analysis."
                        " You MUST analyze Arabic text natively — it contains critical market intelligence."
                        " If you cannot fully answer, another assistant will help."
                        " Your analysis must end with a structured JSON block as specified."
                        " You have access to the following tools: {tool_names}.\n{system_message}"
                        "\n\nFor your reference:"
                        "\n- Current date: {current_date}"
                        "\n- Company: {ticker}"
                        "\n- Market: {market}"
                        "\n- IMPORTANT: No news = low confidence, not neutral sentiment",
                    ),
                    MessagesPlaceholder(variable_name="messages"),
                ]
            )

            prompt = prompt.partial(system_message=system_message)
            prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
            prompt = prompt.partial(current_date=current_date)
            prompt = prompt.partial(ticker=ticker)
            prompt = prompt.partial(market=market_context)

            chain = prompt | llm.bind_tools(tools)
            result = chain.invoke(
                state.get("news_messages") or [("human", ticker)]
            )

        report = ""
        sentiment_analysis = None

        if len(result.tool_calls) == 0:
            report = result.content

            # ── Step 1: Extract LLM's structured analysis ──
            try:
                json_str = None
                json_match = re.search(r'```json\s*(.*?)\s*```', report, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    # Even with double braces {{ ... }}, we can match the block
                    fallback_match = re.search(r'\{.*\}', report, re.DOTALL)
                    if fallback_match:
                        json_str = fallback_match.group(0)
                        
                if json_str:
                    json_str = json_str.replace("{{", "{").replace("}}", "}")
                    sentiment_analysis = json.loads(json_str)
            except (json.JSONDecodeError, AttributeError):
                sentiment_analysis = None

            if sentiment_analysis is None:
                sentiment_analysis = {
                    "sentiment": "neutral",
                    "sentiment_strength": "weak",
                    "confidence_score": 30,
                    "confidence_adjustments": ["Failed to extract structured analysis"],
                    "explanation": "Unable to parse news analysis. Manual review recommended.",
                    "key_headlines": [],
                    "news_coverage": {"total_articles": 0, "sources_count": 0},
                    "risks_from_news": ["Analysis incomplete"],
                    "catalysts_from_news": [],
                }

            # ── Step 2: Extract headlines and run transformer sentiment ──
            headlines = _extract_headlines_from_report(report)
            transformer_result = _run_transformer_sentiment(headlines)

            # ── Step 3: Merge transformer scores into the analysis ──
            sentiment_analysis["transformer_sentiment"] = {
                "score": transformer_result["transformer_score"],
                "label": transformer_result["transformer_label"],
                "confidence": transformer_result["transformer_confidence"],
                "model_status": transformer_result["model_status"],
                "per_headline_scores": transformer_result["per_headline"],
            }

            # ── Step 4: Compute final combined score ──
            # Transformer is primary signal; LLM confidence modulates it
            llm_conf = sentiment_analysis.get("confidence_score", 50) / 100.0
            t_score = transformer_result["transformer_score"]
            t_conf = transformer_result["transformer_confidence"]

            # If transformer has data, it's the primary signal
            if transformer_result["model_status"] == "ok" and headlines:
                # Map LLM sentiment to numeric for blending
                llm_sentiment_map = {"bullish": 0.6, "bearish": -0.6, "neutral": 0.0}
                llm_score = llm_sentiment_map.get(
                    sentiment_analysis.get("sentiment", "neutral"), 0.0
                )

                # Weighted blend: 65% transformer, 35% LLM
                combined_score = t_score * 0.65 + llm_score * 0.35
                combined_conf = t_conf * 0.60 + llm_conf * 0.40
            else:
                # No transformer data — use LLM only with reduced confidence
                llm_sentiment_map = {"bullish": 0.5, "bearish": -0.5, "neutral": 0.0}
                combined_score = llm_sentiment_map.get(
                    sentiment_analysis.get("sentiment", "neutral"), 0.0
                )
                combined_conf = llm_conf * 0.7  # Penalty for no transformer validation

            combined_score = max(-1.0, min(1.0, combined_score))
            combined_conf = max(0.05, min(1.0, combined_conf))

            if combined_score > 0.15:
                combined_label = "bullish"
            elif combined_score < -0.15:
                combined_label = "bearish"
            else:
                combined_label = "neutral"

            sentiment_analysis["combined_sentiment"] = {
                "score": round(combined_score, 4),
                "label": combined_label,
                "confidence": round(combined_conf, 4),
            }

        return {
            "news_messages": [result],
            "news_report": report,
            "sentiment_analysis": sentiment_analysis,
        }

    return news_analyst_node
