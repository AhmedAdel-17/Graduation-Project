import json
import logging
import sys
import os

# Add the project root to the Python path so imports work correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the pipeline components from the project
from tradingagents.utils.text_preprocessor import preprocess_single
from scripts.twitter_pipeline.relevance import classify as check_relevance
from scripts.twitter_pipeline.v2.intent import detect as detect_intent
from scripts.twitter_pipeline.v2.content_type import classify as classify_content
from scripts.twitter_pipeline.v2.entities import extract as extract_entities
from scripts.twitter_pipeline.v2.quality_gate import evaluate as quality_gate
from tradingagents.utils.sentiment_engine import SentimentEngine

# Configure logging to hide noisy warnings unless they are critical
logging.basicConfig(level=logging.ERROR)

def test_pipeline(raw_text: str):
    print("=" * 80)
    print(f"🔍 INPUT TEXT: {raw_text}")
    print("=" * 80)

    # ---------------------------------------------------------
    # 1. Preprocessing
    # ---------------------------------------------------------
    print("\n[STEP 1] PREPROCESSING & LANGUAGE DETECTION")
    processed = preprocess_single(raw_text, check_spam=True)
    print(f"  > Cleaned Text : {processed.cleaned}")
    print(f"  > Language     : {processed.language}")
    print(f"  > Spam/Valid?  : {'✅ Valid' if processed.is_valid else f'❌ Rejected ({processed.rejection_reason})'}")

    # ---------------------------------------------------------
    # 2. Relevance (Is it EGX + Finance?)
    # ---------------------------------------------------------
    print("\n[STEP 2] STRICT RELEVANCE CLASSIFIER")
    is_relevant, rel_debug = check_relevance(raw_text)
    print(f"  > Passes Gate? : {'✅ Yes' if is_relevant else '❌ No'}")
    print(f"  > Finance Hits : {rel_debug.get('finance_hits', [])}")
    print(f"  > EGX Hits     : {rel_debug.get('egx_hits', [])}")

    # ---------------------------------------------------------
    # 3. Trading Intent Detection
    # ---------------------------------------------------------
    print("\n[STEP 3] TRADING INTENT DETECTION")
    intent_res = detect_intent(processed.cleaned)
    print(f"  > Intents      : {intent_res.intents}")
    print(f"  > Evidence     : {intent_res.evidence}")
    print(f"  > Net Score    : {intent_res.score} (-1.0 to 1.0)")

    # ---------------------------------------------------------
    # 4. Content Type Classification
    # ---------------------------------------------------------
    print("\n[STEP 4] CONTENT TYPE CLASSIFICATION")
    content_res = classify_content(processed.cleaned)
    print(f"  > Class        : {content_res.label}")
    print(f"  > Weight       : {content_res.weight}x")
    print(f"  > Reasons      : {content_res.reasons}")

    # ---------------------------------------------------------
    # 5. Entity Extraction (Which stock?)
    # ---------------------------------------------------------
    print("\n[STEP 5] EGX ENTITY EXTRACTION")
    mentions = extract_entities(processed.cleaned)
    if mentions:
        for m in mentions:
            print(f"  > Entity Found : {m.symbol} (Confidence: {m.confidence:.2f}) -> {m.evidence}")
    else:
        print("  > ❌ No EGX entities found in text.")

    # ---------------------------------------------------------
    # 6. Quality Gate
    # ---------------------------------------------------------
    print("\n[STEP 6] SIGNAL QUALITY GATE")
    q_eval = quality_gate(processed.cleaned, intent_res.to_dict(), content_res.to_dict())
    print(f"  > Action       : {q_eval['bucket'].upper()}")
    print(f"  > Keep Post?   : {'✅ Yes' if q_eval['keep'] else '❌ No'}")
    print(f"  > Reason       : {q_eval['reason']}")

    # ---------------------------------------------------------
    # 7. Sentiment Scoring (Transformer AI)
    # ---------------------------------------------------------
    print("\n[STEP 7] AI SENTIMENT SCORING (Transformer)")
    if not processed.is_valid or not is_relevant:
        print("  > ⚠️ SKIPPED: Text was rejected by earlier gates (Spam or Not Relevant).")
    else:
        engine = SentimentEngine.get_instance()
        # We pass preprocess=False because we already cleaned it in Step 1
        sentiment_res = engine.analyze(processed.cleaned, language=processed.language, preprocess=False)
        print(f"  > Final Label  : {sentiment_res.label.upper()}")
        print(f"  > AI Score     : {sentiment_res.score} (-1.0 to +1.0)")
        print(f"  > AI Confidence: {sentiment_res.confidence:.2f}")
        print(f"  > Model Used   : {sentiment_res.model_used}")

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    print("Loading AI Models... (this takes a few seconds to load the Transformers into memory)")
    # Force the engine to initialize so the interactive loop is fast
    _ = SentimentEngine.get_instance()
    
    print("\n--- RUNNING BUILT-IN TEST CASES ---")
    
    # Test Case 1: Strong Bullish Arabic Post
    test_pipeline("سهم التجاري الدولي هيطلع الصاروخ بكرا تجميع قوي جدا 🚀 $COMI")
    
    # Test Case 2: Strong Bearish English Post
    test_pipeline("CIB just reported terrible earnings. Sold my entire position, this is dumping hard. COMI.CA")
    
    # Test Case 3: Spam / Off-topic
    test_pipeline("Join my free telegram channel for 100% guaranteed profit in crypto! t.me/scam")
    
    print("--- INTERACTIVE TESTER READY ---")
    while True:
        try:
            user_input = input("\nEnter a post to test (or type 'quit' to exit): \n> ")
            if user_input.lower() in ['q', 'quit', 'exit']:
                break
            if user_input.strip():
                test_pipeline(user_input)
        except KeyboardInterrupt:
            break
