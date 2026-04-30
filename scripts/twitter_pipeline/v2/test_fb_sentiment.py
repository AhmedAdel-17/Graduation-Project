import os
import sys
import json
from datetime import datetime, timezone

# Add necessary paths to sys.path so we can import from the project
_HERE = os.path.dirname(os.path.abspath(__file__))
_V2 = _HERE
_V1 = os.path.abspath(os.path.join(_HERE, ".."))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))

for path in (_ROOT, _V1, _V2):
    if path not in sys.path:
        sys.path.insert(0, path)

# Load environment variables (APIFY_API_TOKEN)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except ImportError:
    print("python-dotenv not installed, assuming env variables are set.")

# Import the Facebook Apify scraper
from sources import facebook_apify

# Import the Sentiment Engine wrapper
from sentiment import analyze_egx

# Import the entity extractor to show which stocks were found
from entities import extract as extract_entities

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("==================================================")
    print("🧪 EGX Facebook Scraping & Sentiment Test Script 🧪")
    print("==================================================")
    
    # Check if Apify token is set
    if not os.getenv("APIFY_API_TOKEN"):
        print("❌ ERROR: APIFY_API_TOKEN is not set in your .env file!")
        print("Please add it to run the Facebook scraper.")
        sys.exit(1)

    print("\n1️⃣  Scraping Data from Facebook Groups...")
    # Fetch a small number of results per group for testing purposes
    # By default, facebook_apify targets 3 predefined EGX groups
    posts = facebook_apify.scrape(results_per_group=15)
    
    if not posts:
        print("⚠️ No posts were scraped. Check your Apify token or group URLs.")
        sys.exit(0)
        
    print(f"\n✅ Scraped {len(posts)} valid posts from Facebook after initial filtering.")
    print("\n2️⃣  Analyzing Sentiment & Extracting Entities...")
    
    results = []
    
    for i, post in enumerate(posts, 1):
        print(f"\n--- Post {i}/{len(posts)} ---")
        print(f"URL: {post.url}")
        # Print a snippet of the text
        text_snippet = post.text[:100].replace('\n', ' ') + ('...' if len(post.text) > 100 else '')
        print(f"Text Snippet: {text_snippet}")
        
        # 1. Extract stock entities (which stock is this about?)
        mentions = extract_entities(post.text)
        symbols = [m.symbol for m in mentions]
        print(f"Mentioned Stocks: {symbols if symbols else 'Market Overall / None'}")
        
        # 2. Perform Sentiment Analysis
        sentiment_output = analyze_egx(post.text)
        
        score = sentiment_output.get("score", 0.0)
        label = sentiment_output.get("label", "neutral")
        conf = sentiment_output.get("confidence", 0.0)
        model = sentiment_output.get("model_used", "unknown")
        
        # Emphasize the label with an emoji
        emoji = "🟢" if label == "bullish" else "🔴" if label == "bearish" else "⚪"
        print(f"Sentiment: {emoji} {label.upper()} | Score: {score:+.3f} | Confidence: {conf:.2f} | Model: {model}")
        
        # Store for JSON output
        results.append({
            "url": post.url,
            "text": post.text,
            "platform": post.platform,
            "group": post.source,
            "mentions": [m.to_dict() for m in mentions],
            "sentiment": sentiment_output
        })

    # Save results to a JSON file
    log_dir = os.path.join(_HERE, "logs")
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_file = os.path.join(log_dir, f"test_fb_results_{stamp}.json")
    
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
        
    print("\n==================================================")
    print(f"💾 Saved full results and text to: {out_file}")
    print("==================================================")

if __name__ == "__main__":
    main()
