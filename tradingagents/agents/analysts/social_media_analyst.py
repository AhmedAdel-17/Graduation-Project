"""
Social Media Sentiment Analyst for EGX
========================================
Analyzes social media posts (Twitter/X, Telegram, Reddit) for Egyptian stocks.

Pipeline:
  1. LLM uses tools to fetch social posts + pre-aggregated sentiment data
  2. LLM produces a qualitative report
  3. Individual posts are scored by transformer models (XLM-R primary,
     CAMeLBERT fallback for pure Arabic) via SentimentEngine
  4. Transformer score (70%) is blended with LLM interpretation (30%)
  5. Output is compatible with the [-1, 1] scoring contract
"""

import re
import json
import logging
import time
from typing import Any, Dict, List, Optional

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.social_media_tools import (
    get_social_sentiment,
    get_social_media_posts,
)
from tradingagents.dataflows.config import get_config

logger = logging.getLogger("tradingagents.social_media_analyst")

# ---------------------------------------------------------------------------
# Confidence tuning constants
# ---------------------------------------------------------------------------
NO_POSTS_CONFIDENCE_PENALTY = 0.45
SPARSE_POSTS_PENALTY = 0.20
HYPE_BOOST_MULTIPLIER = 0.80   # Reduce confidence when hype is detected


# =============================================================================
# Post extraction helper
# =============================================================================

def _extract_posts_from_report(report: str) -> List[str]:
    """
    Pull individual post snippets from the LLM's social media report.

    Three strategies (mirrors news_analyst.py headline extraction):
    1. Quoted text ("..." or «...»)
    2. Bullet-point items
    3. JSON block's post_excerpts array (if LLM included one)
    """
    posts: List[str] = []

    # 1. Quoted text
    quoted = re.findall(r'["\u201c\u201d\u00ab](.+?)["\u201d\u00bb]', report)
    for q in quoted:
        if len(q) > 10:
            posts.append(q)

    # 2. Bullet points
    bullets = re.findall(r'(?:^|\n)\s*[•\-\*]\s*(.+?)(?:\n|$)', report)
    for b in bullets:
        clean = b.strip()
        if len(clean) > 10 and clean not in posts:
            posts.append(clean)

    # 3. JSON block
    try:
        json_match = re.search(r'```json\s*(.*?)\s*```', report, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(1))
            for item in data.get("post_excerpts", []):
                text = item if isinstance(item, str) else item.get("text", "")
                if text and len(text) > 10:
                    posts.append(text)
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass

    return posts


# =============================================================================
# Transformer scoring
# =============================================================================

def _run_transformer_sentiment(posts: List[str]) -> Dict[str, Any]:
    """
    Score social posts through the shared SentimentEngine.

    Social posts are almost always mixed Arabic/English/Egyptian dialect,
    so XLM-R is the most appropriate model and will be auto-selected for
    mixed-language texts.  CAMeLBERT handles pure-Arabic posts.

    Returns a summary dict compatible with what news_analyst.py produces.
    """
    if not posts:
        return {
            "transformer_score": 0.0,
            "transformer_label": "neutral",
            "transformer_confidence": 0.1,
            "per_post": [],
            "model_status": "no_posts",
        }

    try:
        from tradingagents.utils.sentiment_engine import SentimentEngine

        engine = SentimentEngine.get_instance()
        results = engine.analyze_batch(posts, preprocess=True, deduplicate=True)

        per_post = []
        for post, r in zip(posts, results):
            per_post.append({
                "post": post[:100],
                "score": r.score,
                "label": r.label,
                "confidence": r.confidence,
                "model": r.model_used,
            })

        aggregated = SentimentEngine.aggregate_scores(results)

        return {
            "transformer_score": aggregated.score,
            "transformer_label": aggregated.label,
            "transformer_confidence": aggregated.confidence,
            "per_post": per_post,
            "model_status": "ok",
            "models_used": aggregated.model_used,
        }

    except Exception as e:
        logger.warning("Transformer sentiment (social) failed, returning neutral: %s", e)
        return {
            "transformer_score": 0.0,
            "transformer_label": "neutral",
            "transformer_confidence": 0.1,
            "per_post": [],
            "model_status": f"error: {str(e)[:80]}",
        }


# =============================================================================
# Structured output extraction
# =============================================================================

def _extract_structured_output(text: str, ticker: str) -> dict:
    """
    Extract structured sentiment data from LLM text responses.

    Tries three strategies in order:
    1. Parse a ```json ... ``` fenced block
    2. Regex-extract key fields from plain text (sentiment_score, direction, etc.)
    3. Return a minimal fallback dict
    """
    # Strategy 1: fenced JSON block
    json_match = re.search(r"```json\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 2: inline key-value extraction
    result = {"ticker": ticker}
    found_any = False

    score_match = re.search(r"sentiment_score[:\s]+(-?[\d.]+)", text)
    if score_match:
        result["sentiment_score"] = float(score_match.group(1))
        found_any = True

    buzz_match = re.search(r"buzz_score[:\s]+(-?[\d.]+)", text)
    if buzz_match:
        result["buzz_score"] = float(buzz_match.group(1))
        found_any = True

    conf_match = re.search(r"confidence[:\s]+(-?[\d.]+)", text)
    if conf_match:
        result["confidence"] = float(conf_match.group(1))
        found_any = True

    text_lower = text.lower()
    if "bullish" in text_lower:
        result["direction"] = "bullish"
        found_any = True
    elif "bearish" in text_lower:
        result["direction"] = "bearish"
        found_any = True

    if found_any:
        result["source"] = "text_parsing"
        return result

    # Strategy 3: fallback
    return {
        "ticker": ticker,
        "source": "text_parsing_fallback",
        "raw_text": text[:500],
    }


# =============================================================================
# Agent factory
# =============================================================================

def create_social_media_analyst(llm):
    """
    Create the Social Media Sentiment Analyst agent for EGX.

    Pipeline:
      1. LLM fetches social posts via tools
      2. LLM writes qualitative report
      3. Posts extracted → scored by transformers
      4. Scores blended into final [-1, 1] social sentiment
    """

    def social_media_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        tools = [
            get_social_sentiment,
            get_social_media_posts,
        ]

        # ── Phase 2c: Pre-fetched data mode ──────────────────────────────────
        # If DataPrefetcher has already fetched social data, skip tool calls.
        # The LLM still reasons qualitatively; it just doesn't need to call tools.
        prefetched_sentiment = state.get("prefetched_social_sentiment", "")
        prefetched_posts = state.get("prefetched_social_posts", "")
        has_prefetched = bool(prefetched_sentiment or prefetched_posts)

        if has_prefetched:
            prefetch_prompt = (
                f"You are a Social Media Sentiment Analyst for the Egyptian Stock Exchange (EGX). "
                f"Your task is to analyze social media posts, retail investor sentiment, and public "
                f"perception for {ticker}. You specialize in bilingual content "
                f"(Arabic — including Egyptian dialect — and English).\n\n"
                f"## Pre-Fetched Social Data\n"
                f"The following data has already been retrieved. Analyze it directly.\n\n"
                f"### Sentiment Scores & Aggregates\n"
                f"{prefetched_sentiment or 'No structured sentiment data available.'}\n\n"
                f"### Social Media Posts\n"
                f"{prefetched_posts or 'No posts retrieved.'}\n\n"
                f"## Your Report Must Include\n"
                f"1. Overall sentiment direction and score\n"
                f"2. Buzz/volume analysis\n"
                f"3. Hype detection\n"
                f"4. Platform breakdown\n"
                f"5. Arabic vs English sentiment comparison\n"
                f"6. Key discussion themes\n"
                f"7. Confidence assessment\n\n"
                f"End with a JSON block:\n\n"
                f"```json\n"
                f"{{\n"
                f'    "sentiment": "bullish|bearish|neutral",\n'
                f'    "sentiment_score": <float -1 to 1>,\n'
                f'    "confidence": <float 0 to 1>,\n'
                f'    "buzz_score": <float 0 to 1>,\n'
                f'    "hype_detected": <true|false>,\n'
                f'    "direction": "bullish|bearish|neutral",\n'
                f'    "post_excerpts": ["post text 1", "post text 2"],\n'
                f'    "key_themes": ["theme1", "theme2"],\n'
                f'    "platform_breakdown": {{"twitter": "...", "telegram": "...", "reddit": "..."}},\n'
                f'    "language_breakdown": {{"arabic": <pct>, "english": <pct>}}\n'
                f"}}\n"
                f"```\n\n"
                f"Current date: {current_date} | Company: {ticker}"
            )
            result = llm.invoke(prefetch_prompt)
        else:
            # Standard tool-calling path
            system_message = (
                "You are a Social Media Sentiment Analyst for the Egyptian Stock Exchange (EGX). "
                "Your task is to analyze social media posts, retail investor sentiment, and public "
                "perception for a specific EGX stock. You specialize in understanding bilingual content "
                "(Arabic — including Egyptian dialect العامية المصرية — and English).\n\n"
                "Use the get_social_sentiment tool to get structured sentiment scores (overall sentiment, "
                "buzz, momentum, hype detection, platform breakdown, language breakdown). "
                "Use the get_social_media_posts tool to see actual post content for deeper qualitative analysis.\n\n"
                "Your report MUST include:\n"
                "1. Overall sentiment direction and score\n"
                "2. Buzz/volume analysis — is this stock being talked about more than usual?\n"
                "3. Hype detection — are posts genuine analysis or retail hype/pump?\n"
                "4. Platform breakdown — do Twitter, Telegram, and Reddit agree or diverge?\n"
                "5. Arabic vs English sentiment comparison\n"
                "6. Key discussion themes (dividends, earnings, technical, currency, etc.)\n"
                "7. Confidence assessment — how reliable is this social signal?\n\n"
                "After your analysis you MUST end with a JSON block:\n\n"
                "```json\n"
                "{\n"
                '    "sentiment": "bullish|bearish|neutral",\n'
                '    "sentiment_score": <float -1 to 1>,\n'
                '    "confidence": <float 0 to 1>,\n'
                '    "buzz_score": <float 0 to 1>,\n'
                '    "hype_detected": <true|false>,\n'
                '    "direction": "bullish|bearish|neutral",\n'
                '    "post_excerpts": ["post text 1", "post text 2", ...],\n'
                '    "key_themes": ["theme1", "theme2"],\n'
                '    "platform_breakdown": {"twitter": "...", "telegram": "...", "reddit": "..."},\n'
                '    "language_breakdown": {"arabic": <pct>, "english": <pct>}\n'
                "}\n"
                "```\n\n"
                "Provide fine-grained, actionable analysis. Do not simply state 'sentiment is mixed'. "
                "Identify specific bullish and bearish signals from the data.\n\n"
                "Append a Markdown summary table at the end of your report."
            )

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a helpful AI assistant, collaborating with other assistants."
                        " Use the provided tools to progress towards answering the question."
                        " If you are unable to fully answer, that's OK; another assistant with different tools"
                        " will help where you left off. Execute what you can to make progress."
                        " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                        " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                        " You have access to the following tools: {tool_names}.\n{system_message}"
                        "\nFor your reference, the current date is {current_date}. "
                        "The current EGX company we want to analyze is {ticker}.",
                    ),
                    MessagesPlaceholder(variable_name="messages"),
                ]
            )

            prompt = prompt.partial(system_message=system_message)
            prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
            prompt = prompt.partial(current_date=current_date)
            prompt = prompt.partial(ticker=ticker)

            chain = prompt | llm.bind_tools(tools)
            result = chain.invoke(
                state.get("social_messages") or [("human", ticker)]
            )

        report = ""
        social_analysis = {}

        if len(result.tool_calls) == 0:
            report = result.content

            # ── Step 1: Extract LLM structured analysis ──
            structured = _extract_structured_output(report, ticker)

            # ── Step 2: Extract post snippets → transformer scoring ──
            posts = _extract_posts_from_report(report)

            # Also use post_excerpts from LLM's own JSON if available
            if isinstance(structured, dict):
                llm_excerpts = structured.get("post_excerpts", [])
                if isinstance(llm_excerpts, list):
                    for p in llm_excerpts:
                        if isinstance(p, str) and p not in posts:
                            posts.append(p)

            transformer_result = _run_transformer_sentiment(posts)

            # ── Step 3: Blend transformer + LLM scores ──
            llm_direction_map = {"bullish": 0.6, "bearish": -0.6, "neutral": 0.0}

            # LLM score from structured output
            raw_llm_score = structured.get("sentiment_score", None)
            if raw_llm_score is not None:
                try:
                    llm_num_score = float(raw_llm_score)
                except (ValueError, TypeError):
                    llm_num_score = llm_direction_map.get(
                        structured.get("direction", "neutral"), 0.0
                    )
            else:
                llm_num_score = llm_direction_map.get(
                    structured.get("direction", structured.get("sentiment", "neutral")), 0.0
                )

            llm_conf = float(structured.get("confidence", 0.4))

            t_score = transformer_result["transformer_score"]
            t_conf = transformer_result["transformer_confidence"]

            # Hype detected → reduce confidence in social signal
            hype = bool(structured.get("hype_detected", False))

            if transformer_result["model_status"] == "ok" and posts:
                # 70% transformer, 30% LLM for social (transformer more reliable here)
                combined_score = t_score * 0.70 + llm_num_score * 0.30
                combined_conf = t_conf * 0.65 + llm_conf * 0.35
            else:
                # No transformer data — LLM only, further reduced confidence
                combined_score = llm_num_score
                combined_conf = llm_conf * 0.65

            # Hype penalty
            if hype:
                combined_conf *= HYPE_BOOST_MULTIPLIER

            combined_score = max(-1.0, min(1.0, combined_score))
            combined_conf = max(0.05, min(1.0, combined_conf))

            if combined_score > 0.15:
                combined_label = "bullish"
            elif combined_score < -0.15:
                combined_label = "bearish"
            else:
                combined_label = "neutral"

            # ── Step 4: Assemble final analysis dict ──
            social_analysis = {
                **structured,
                "transformer_sentiment": {
                    "score": transformer_result["transformer_score"],
                    "label": transformer_result["transformer_label"],
                    "confidence": transformer_result["transformer_confidence"],
                    "model_status": transformer_result["model_status"],
                    "per_post_scores": transformer_result["per_post"],
                },
                "combined_sentiment": {
                    "score": round(combined_score, 4),
                    "label": combined_label,
                    "confidence": round(combined_conf, 4),
                    "hype_adjusted": hype,
                },
            }

        return {
            "social_messages": [result],
            "sentiment_report": report,
            "social_sentiment_analysis": json.dumps(
                social_analysis, ensure_ascii=False, default=str
            ),
        }

    return social_media_analyst_node
