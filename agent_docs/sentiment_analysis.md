# 📰 Sentiment Analysis Agents (News & Social)

## Overview
These agents parse unstructured text—financial news and multi-platform social media posts—to return quantitative sentiment scores `[-1.0 to 1.0]` alongside qualitative chain-of-thought sub-signals.

## Tech Stack & Libraries
- **Transformers / Pre-trained LLMs**: For specialized Arabic sequence classification or text-generation (e.g., MARBERT or fine-tuned Llama).
- **BeautifulSoup4 / Playwright**: For robust document retrieval from news websites.
- **Tweepy / Telethon**: For social media firehose consumption.

## Implementation Guidelines
1. **Dialect and Slang Mastery**:
   - The EGX retail sentiment is predominantly driven in the Egyptian dialect (العامية المصرية). 
   - You MUST normalize inputs and utilize a slang-mapper before LLM inference. Mapping examples: 'هامور' (Whale/Institutional Buyer), 'تجميع' (Accumulation/Buy pressure), 'تصريف' (Distribution/Sell pressure), 'بامب' (Pump).
2. **Noise and Bot Reduction**:
   - Social media (especially Telegram and Twitter) contains severe spam. You must implement a preliminary heuristic filter (or lightweight NLP classifier) to discard spam before sending tokens to an expensive LLM.
3. **Hype Detection Engine**:
   - Social sentiment isn't just up/down; it has velocity. Include a `hype_index` `[0.0 to 1.0]` representing the frequency and emotional intensity of mentions over the sliding window. High hype often indicates extreme volatility or imminent dumps.
4. **Resilience & Fallbacks**:
   - Scrapers are fragile and IP bans are frequent. Implement strict `try...except` logic, explicit timeout parameters, and exponential backoff.
   - If an agent completely fails to fetch data, it must return a neutral score `(0.0)` rather than crashing the LangGraph pipeline.
