# 📊 Data Pipeline & Ingestion Guidelines

## Overview
The Data Pipeline is the backbone of the agentic system. It is responsible for fetching, cleaning, and standardizing all quantitative data for the EGX, including OHLCV prices, volume profiles, and fundamental corporate metrics.

## Tech Stack & Libraries
- **Pandas / NumPy**: Vectorized core data manipulation.
- **pandas-ta / TA-Lib**: Technical indicator computation.
- **Requests & aiohttp**: Sync and async API interactions for market data.

## Implementation Guidelines
1. **Timezone Enforcement**: 
   - All historical and real-time timestamps MUST be strictly localized to `Africa/Cairo`.
   - Consider the EGX market logic: trading sessions usually run from 10:00 AM to 2:30 PM local time. Filter out-of-hours noise carefully.
2. **Missing Data Strategies**:
   - Never drop `NaN` rows abruptly. Use structured forward-filling (`ffill()`) for standard price gaps (e.g., weekends, non-trading holidays).
   - If a ticker is highly illiquid (frequent in smaller-cap EGX stocks), trigger a liquidity warning flag metadata attribute in the dataset before passing to the agents.
3. **Caching Layer**:
   - Because EGX data sources limit requests, aggressively cache history locally. Use Parquet for efficient storage of DataFrames or SQLite for rapid relational reads.
   - Separate raw ingested data from cleaned/processed features to avoid losing original state during logic refactors.
4. **Resiliency and Backoff**:
   - Use exponential backoff for REST requests. Implement a circuit breaker if the source provider goes fully offline.
