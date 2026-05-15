import os
import sys
import json
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

# Path setup
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Load environment variables (APIFY_API_TOKEN, OPENAI_API_KEY)
load_dotenv(os.path.join(_ROOT, ".env"))

# Import pipeline components
try:
    from scripts.twitter_pipeline.v2.sources import facebook_apify
    from tradingagents.utils.sentiment_engine import SentimentEngine
    from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst
    from langchain_openai import ChatOpenAI
    from tradingagents.default_config import DEFAULT_CONFIG
except ImportError as e:
    print(f"❌ Import Error: {e}")
    sys.exit(1)

# Configure logging to be very verbose for this test
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("test_workflow")

def main():
    # Use UTF-8 for console output
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ticker = "COMI" # Commercial International Bank
    trade_date = datetime.now().strftime("%Y-%m-%d")

    print("="*80)
    print(f"🧪 EGX SENTIMENT WORKFLOW TEST: {ticker} | {trade_date}")
    print("="*80)

    # --- STEP 1: FETCH DATA ---
    print("\n[STEP 1] FETCHING SOCIAL MEDIA DATA (Facebook Primary)...")
    print("Connecting to Apify...")
    try:
        # Fetch a small number of results per group for testing purposes
        raw_posts = facebook_apify.scrape(results_per_group=5)
        if not raw_posts:
            print("⚠️ No posts were scraped. Check your Apify token or group URLs.")
            return
            
        print(f"✅ Successfully fetched {len(raw_posts)} posts.")
        for i, post in enumerate(raw_posts[:3]):
            print(f"  Post {i+1} [{post.platform}]: {post.text[:120].replace('\\n', ' ')}...")
    except Exception as e:
        print(f"❌ Error fetching data: {e}")
        return

    # --- STEP 2: SENTIMENT ENGINE (Transformer Models) ---
    print("\n[STEP 2] RUNNING TRANSFORMER SENTIMENT ENGINE...")
    print("Loading models (FinBERT, CAMeLBERT, XLM-R)...")
    try:
        engine = SentimentEngine.get_instance()
        texts = [p.text for p in raw_posts]
        results = engine.analyze_batch(texts, preprocess=True)
        
        print(f"✅ Analyzed {len(results)} posts with SentimentEngine.")
        for i, res in enumerate(results[:3]):
            # Emphasize the label with an emoji
            emoji = "🟢" if res.label == "bullish" else "🔴" if res.label == "bearish" else "⚪"
            print(f"  Post {i+1} Sentiment: {emoji} {res.label.upper()} | Score: {res.score:+.3f} | Conf: {res.confidence:.2f} | Model: {res.model_used}")
        
        aggregated = SentimentEngine.aggregate_scores(results)
        agg_emoji = "🟢" if aggregated.label == "bullish" else "🔴" if aggregated.label == "bearish" else "⚪"
        print(f"📈 Aggregated Transformer Result: {agg_emoji} {aggregated.label.upper()} (score: {aggregated.score:+.3f}, conf: {aggregated.confidence:.2f})")
    except Exception as e:
        print(f"❌ Error in Sentiment Engine: {e}")
        return

    # --- STEP 3: SOCIAL MEDIA ANALYST AGENT (LLM Reasoning) ---
    print("\n[STEP 3] RUNNING SOCIAL MEDIA ANALYST AGENT (LLM Reasoning)...")
    try:
        # Initialize LLM using project config
        config = DEFAULT_CONFIG.copy()
        
        # Check for API Key
        if not os.getenv("OPENAI_API_KEY") and not os.getenv("DEEPSEEK_API_KEY"):
             print("❌ ERROR: No LLM API key found in .env!")
             return

        print(f"Initializing LLM: {config['deep_think_llm']} via {config['backend_url']}")
        llm = ChatOpenAI(
            model=config["deep_think_llm"], 
            base_url=config["backend_url"], 
            api_key=os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY"),
            temperature=0
        )
        
        # Create agent node
        analyst_node = create_social_media_analyst(llm)
        
        # Prepare state (prefetch mode to avoid tool calls in this test)
        # This simulates the DataPrefetcher having already worked.
        prefetched_posts_list = []
        for post in raw_posts:
            prefetched_posts_list.append({
                "text": post.text,
                "platform": post.platform,
                "timestamp": post.timestamp,
                "url": post.url,
                "source": post.source
            })
            
        # Agent expects strings or dicts depending on implementation, 
        # but social_media_analyst.py line 231 uses bool(prefetched_posts) 
        # and then passes it to the prompt.
        
        state = {
            "company_of_interest": ticker,
            "trade_date": trade_date,
            "prefetched_social_posts": json.dumps(prefetched_posts_list, ensure_ascii=False, indent=2),
            "prefetched_social_sentiment": json.dumps({
                "score": aggregated.score,
                "label": aggregated.label,
                "confidence": aggregated.confidence,
                "models_used": aggregated.model_used
            }, ensure_ascii=False, indent=2)
        }
        
        print("🤖 Agent is processing the data and reasoning...")
        agent_result = analyst_node(state)
        
        print("\n✅ AGENT ANALYSIS COMPLETE")
        print("="*80)
        print("1. QUALITATIVE REPORT (Excerpt):")
        report = agent_result.get("sentiment_report", "No report generated.")
        # Show first 800 chars and last 200 chars to see the JSON block
        if len(report) > 1000:
            print(report[:800])
            print("\n... [middle content omitted] ...\n")
            print(report[-300:])
        else:
            print(report)
            
        print("\n" + "="*80)
        print("2. STRUCTURED OUTPUT & BLENDED SIGNAL:")
        
        analysis_json = agent_result.get("social_sentiment_analysis", "{}")
        analysis = json.loads(analysis_json)
        
        combined = analysis.get("combined_sentiment", {})
        label = combined.get("label", "N/A").upper()
        emoji = "🟢" if label == "BULLISH" else "🔴" if label == "BEARISH" else "⚪"
        
        print(f"🏆 FINAL SIGNAL: {emoji} {label}")
        print(f"   Blended Score: {combined.get('score', 0):+.4f} (Transformer + LLM)")
        print(f"   Confidence:    {combined.get('confidence', 0):.4f}")
        print(f"   Hype Detected: {analysis.get('hype_detected', False)}")
        print(f"   Key Themes:    {', '.join(analysis.get('key_themes', []))}")
        
    except Exception as e:
        print(f"❌ Error in Social Media Analyst Agent: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*80)
    print("✨ TEST COMPLETE")
    print("="*80)

if __name__ == "__main__":
    main()
