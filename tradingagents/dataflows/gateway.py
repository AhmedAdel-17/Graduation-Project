"""
Data Gateway — Central Data Orchestrator
==========================================
Single entry point for ALL data access in TradingAgents.

Handles:
1. Cache lookup (DiskCache)
2. Primary source fetch with retry
3. Automatic fallback to secondary/tertiary sources
4. Validation via Pydantic schemas
5. Structured logging (no print statements)

Supported data types:
- OHLCV price data (yfinance → EODHD → egxpy)
- Technical signals (TradingView → stockstats)
- Real-time price (TradingView → yfinance)
- News (NewsAPI → RSS feeds → Google News → local CSV)

Usage:
    gateway = DataGateway(config)
    
    # Fetch stock data (tries yfinance → EODHD → egxpy)
    data = gateway.fetch_stock_data("COMI.CA", "2026-01-01", "2026-04-01")
    
    # Fetch technical signals (tries TradingView → local calc)
    signals = gateway.fetch_technical_signals("COMI.CA")
    
    # Fetch news
    news = gateway.fetch_news("COMI", days=7)
"""

import logging
import json
from typing import Any, Dict, Optional
from datetime import datetime

from .cache_manager import CacheManager
from .retry_engine import retry_api_call, fetch_with_fallback
from .schemas import StockDataResponse, TechnicalSignals, NewsResponse, DataQualityReport

logger = logging.getLogger("tradingagents.gateway")


def _record_data_fetch(data_type: str, source: str, status: str, elapsed: float = 0.0) -> None:
    """Best-effort Prometheus counter/histogram increment for data fetches."""
    try:
        from tradingagents.observability.metrics import data_fetch_total, data_fetch_latency_seconds
        data_fetch_total.labels(data_type=data_type, source=source, status=status).inc()
        if elapsed > 0:
            data_fetch_latency_seconds.labels(data_type=data_type, source=source).observe(elapsed)
    except Exception:
        pass


class DataGateway:
    """
    Central data orchestrator. ALL data access should go through here.
    
    Architecture:
        Tool (@tool wrapper) → DataGateway → CacheManager → Provider
        
    The gateway:
    - Checks cache first
    - On miss, tries providers in priority order
    - Validates responses with Pydantic schemas
    - Logs data quality metrics
    - Never raises to the caller — returns empty/error responses
    """

    def __init__(self, config: dict):
        """
        Initialize the DataGateway.
        
        Args:
            config: The DEFAULT_CONFIG dict from default_config.py
        """
        self.config = config
        cache_dir = config.get("data_cache_dir", config.get("data_dir", "./data_cache"))
        self.cache = CacheManager(cache_dir)
        self._quality_log: list = []  # Running log of data quality reports
        
        logger.info("DataGateway initialized (cache_dir: %s)", cache_dir)

    # =========================================================================
    # OHLCV / Price Data
    # =========================================================================

    def fetch_stock_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Fetch OHLCV stock data with caching and fallback.
        
        Fallback chain: yfinance → EODHD → egxpy
        
        Args:
            symbol: Ticker symbol (e.g., "COMI.CA" or "COMI")
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            
        Returns:
            Dict matching StockDataResponse schema (always returns, never raises)
        """
        # Normalize symbol
        symbol_normalized = self._normalize_symbol(symbol)
        cache_key = f"ohlcv:{symbol_normalized}:{start_date}:{end_date}"

        # Try cache first
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.info("Cache HIT for stock data: %s", cache_key)
            return cached

        # Build provider chain
        providers = self._build_ohlcv_providers(symbol_normalized, start_date, end_date)

        import time as _time
        _fetch_start = _time.perf_counter()
        try:
            # A provider only "succeeds" if it returns a dict with non-empty
            # data. yfinance returns an empty dict (no exception) for thin /
            # delisted EGX names — without this predicate the EODHD + local-CSV
            # fallbacks would never be reached.
            result, source_name = fetch_with_fallback(
                providers=providers,
                method_name=f"stock_data({symbol_normalized})",
                is_valid=lambda r: isinstance(r, dict) and bool(r.get("data")),
            )
            _elapsed = _time.perf_counter() - _fetch_start

            # Validate with schema
            if isinstance(result, dict):
                result["source"] = source_name
                try:
                    validated = StockDataResponse(**result)
                    result = validated.model_dump()
                except Exception as e:
                    logger.warning("Schema validation warning for %s: %s", symbol_normalized, e)
                    # Still return raw result — partial data is better than no data

            # Cache it
            self.cache.set(cache_key, result, data_type="ohlcv")

            # Log quality
            self._log_quality("stock_data", source_name, True, len(result.get("data", [])))
            _record_data_fetch("ohlcv", source_name, "success", _elapsed)

            return result

        except RuntimeError as e:
            _elapsed = _time.perf_counter() - _fetch_start
            logger.error("All providers failed for stock data: %s", e)
            self._log_quality("stock_data", "none", False, 0, str(e))
            _record_data_fetch("ohlcv", "none", "error", _elapsed)
            
            # Return empty response (never crash the agent pipeline)
            return StockDataResponse(
                symbol=symbol_normalized,
                start_date=start_date,
                end_date=end_date,
                source="none",
                errors=[f"All providers failed: {e}"],
            ).model_dump()

    def _build_ohlcv_providers(self, symbol: str, start_date: str, end_date: str) -> list:
        """Build ordered list of OHLCV providers."""
        providers = []

        # LOCAL-ONLY mode (backtests): use ONLY the pre-built per-ticker CSVs in
        # data/egx30_ohlcv — no network/API calls for price data. Set via
        # config['ohlcv_local_only']=True (the scenario/event-study backtester
        # does this). Returns just the local provider so yfinance/EODHD/egxpy are
        # never contacted.
        if self.config.get("ohlcv_local_only"):
            try:
                from .local_ohlcv import get_local_ohlcv_data
                return [(
                    "local_csv",
                    lambda s=symbol, sd=start_date, ed=end_date: get_local_ohlcv_data(s, sd, ed),
                )]
            except ImportError:
                logger.error("ohlcv_local_only set but local_ohlcv unavailable!")
                return []

        # Provider 1: yfinance (always available)
        try:
            from .y_finance import get_YFin_data_online
            providers.append((
                "yfinance",
                lambda s=symbol, sd=start_date, ed=end_date: get_YFin_data_online(s, sd, ed),
            ))
        except ImportError:
            logger.warning("yfinance provider not available")

        # Provider 2: EODHD (requires API key)
        if self.config.get("data_vendors", {}).get("core_stock_apis") != "yfinance" or True:
            try:
                from .eodhd import get_stock_data_eodhd
                import os
                if os.getenv("EODHD_API_KEY"):
                    providers.append((
                        "eodhd",
                        lambda s=symbol, sd=start_date, ed=end_date: get_stock_data_eodhd(s, sd, ed),
                    ))
            except ImportError:
                pass

        # Provider 3: egxpy (if installed)
        try:
            from .egxpy_wrapper import get_stock_data_egxpy, is_egxpy_available
            if is_egxpy_available():
                providers.append((
                    "egxpy",
                    lambda s=symbol, sd=start_date, ed=end_date: get_stock_data_egxpy(s, sd, ed),
                ))
        except ImportError:
            pass

        # Provider 4: local CSV cache (LAST RESORT — offline, delayed). Only ever
        # reached when every live source above returns empty/raises. This keeps a
        # backtest producing real per-date decisions (instead of all-HOLD / zero
        # returns with blank reasoning) for thin or yfinance-dead EGX names. The
        # window filter is end-EXCLUSIVE there, so it is look-ahead-safe.
        try:
            from .local_ohlcv import get_local_ohlcv_data
            providers.append((
                "local_csv",
                lambda s=symbol, sd=start_date, ed=end_date: get_local_ohlcv_data(s, sd, ed),
            ))
        except ImportError:
            pass

        if not providers:
            logger.error("No OHLCV providers available!")

        return providers

    # =========================================================================
    # Technical Indicators / Signals
    # =========================================================================

    def fetch_technical_signals(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        look_back_days: int = 90,
    ) -> Dict[str, Any]:
        """
        Fetch technical analysis with caching and fallback.
        
        Fallback chain: TradingView-TA → local stockstats calculation
        
        Args:
            symbol: Ticker symbol
            start_date: Start date for indicator calculation
            end_date: End date (also used as cache key date)
            look_back_days: Days of data for indicator calc
            
        Returns:
            Dict matching TechnicalSignals schema
        """
        symbol_normalized = self._normalize_symbol(symbol)
        date_key = end_date or datetime.now().strftime("%Y-%m-%d")
        cache_key = f"technical:{symbol_normalized}:{date_key}"

        # Try cache
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.info("Cache HIT for technical signals: %s", cache_key)
            return cached

        # Build provider chain
        providers = self._build_technical_providers(
            symbol_normalized, start_date, end_date, look_back_days
        )

        try:
            result, source_name = fetch_with_fallback(
                providers=providers,
                method_name=f"technical_signals({symbol_normalized})",
            )

            # Normalize result to dict
            if hasattr(result, "model_dump"):
                result_dict = result.model_dump()
            elif isinstance(result, dict):
                result_dict = result
            else:
                # stockstats returns a string — wrap it
                result_dict = TechnicalSignals(
                    symbol=symbol_normalized,
                    summary="NEUTRAL",
                    indicators={"raw": str(result)[:2000]},
                    source=source_name,
                ).model_dump()

            # Cache it
            self.cache.set(cache_key, result_dict, data_type="technical_signals")
            self._log_quality("technical_signals", source_name, True)
            
            return result_dict

        except RuntimeError as e:
            logger.error("All providers failed for technical signals: %s", e)
            self._log_quality("technical_signals", "none", False, 0, str(e))
            
            return TechnicalSignals(
                symbol=symbol_normalized,
                source="none",
            ).model_dump()

    def _build_technical_providers(
        self, symbol: str, start_date, end_date, look_back_days: int
    ) -> list:
        """Build ordered list of technical signal providers."""
        providers = []

        # Provider 1: TradingView-TA (real-time, no API key)
        try:
            from .tradingview_provider import get_tradingview_signals
            providers.append((
                "tradingview",
                lambda s=symbol: get_tradingview_signals(s),
            ))
        except ImportError:
            logger.info("TradingView provider not available")

        # Provider 2: Local stockstats calculation (always available)
        try:
            from .y_finance import get_stock_stats_indicators_window
            sd = start_date or "2025-01-01"
            ed = end_date or datetime.now().strftime("%Y-%m-%d")
            providers.append((
                "stockstats",
                lambda s=symbol, sd_=sd, ed_=ed, lb=look_back_days: get_stock_stats_indicators_window(s, sd_, ed_, lb),
            ))
        except ImportError:
            pass

        return providers

    # =========================================================================
    # Real-time Price (replaces broken Mubasher scraper)
    # =========================================================================

    def fetch_realtime_price(self, symbol: str) -> Dict[str, Any]:
        """
        Get near-real-time price. Primary: TradingView, Fallback: yfinance.
        
        Returns dict with {price, change, volume, source} — never raises.
        """
        symbol_normalized = self._normalize_symbol(symbol)
        cache_key = f"realtime:{symbol_normalized}"

        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        providers = []

        # Provider 1: TradingView (near real-time, no API key)
        try:
            from .tradingview_provider import get_tradingview_price
            providers.append(("tradingview", lambda s=symbol_normalized: get_tradingview_price(s)))
        except ImportError:
            pass

        # Provider 2: yfinance fast_info (delayed ~15min)
        try:
            import yfinance as yf
            def _yf_price(s):
                ticker = yf.Ticker(s)
                info = ticker.fast_info
                return {
                    "price": info.get("lastPrice", 0),
                    "change": 0,
                    "volume": info.get("lastVolume", 0),
                    "source": "yfinance (delayed ~15min)",
                }
            providers.append(("yfinance", lambda s=symbol_normalized: _yf_price(s)))
        except ImportError:
            pass

        try:
            result, source = fetch_with_fallback(providers, f"realtime_price({symbol_normalized})")
            self.cache.set(cache_key, result, data_type="realtime_price")
            return result
        except RuntimeError:
            return {"price": 0, "change": 0, "volume": 0, "source": "unavailable"}

    # =========================================================================
    # News Data (Phase 2)
    # =========================================================================

    def fetch_news(
        self,
        ticker: str,
        days: int = 7,
        max_articles: int = 15,
        include_global: bool = True,
    ) -> Dict[str, Any]:
        """
        Fetch news with caching and multi-source aggregation.
        
        Fallback chain: NewsAPI → RSS feeds → Google News → local CSV
        
        Args:
            ticker: EGX ticker (e.g., "COMI" or "COMI.CA")
            days: Number of days to look back
            max_articles: Maximum articles to return
            include_global: Include general EGX market news
            
        Returns:
            Dict matching NewsResponse schema (always returns, never raises)
        """
        ticker_clean = ticker.upper().replace(".CA", "").strip()
        # Include the effective trade date so a live-run cache entry is never
        # reused for a different historical backtest date.
        from tradingagents.dataflows.config import get_config as _get_cfg
        _trade_date = _get_cfg().get("trade_date", "live")
        cache_key = f"news:{ticker_clean}:{_trade_date}:{days}d"

        # Try cache (30-min TTL for news)
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.info("Cache HIT for news: %s", cache_key)
            return cached

        try:
            from .news_providers.aggregator import fetch_aggregated_news
            
            result = fetch_aggregated_news(
                ticker=ticker_clean,
                days=days,
                max_articles=max_articles,
                include_global=include_global,
            )

            # Cache it
            self.cache.set(cache_key, result, data_type="news")
            
            # Log quality
            total = result.get("total_articles", 0)
            sources = result.get("sources_queried", [])
            self._log_quality(
                "news", 
                ",".join(sources) if sources else "none",
                total > 0,
                total,
            )
            
            return result

        except Exception as e:
            logger.error("News fetch failed: %s", e)
            self._log_quality("news", "none", False, 0, str(e))
            
            # Return empty response
            return NewsResponse(
                query=ticker_clean,
                sources_failed=[str(e)],
            ).model_dump()

    # =========================================================================
    # Social Media Sentiment (Phase 3)
    # =========================================================================

    def fetch_social_sentiment(
        self,
        ticker: str,
        curr_date: str = None,
        look_back_days: int = 7,
    ) -> Dict[str, Any]:
        """
        Fetch social media sentiment with caching.
        
        Sources: Twitter (web search) → Telegram (public channels) → 
                 Reddit → cached data (last resort, flagged).
        
        Args:
            ticker: EGX ticker (e.g., "COMI" or "COMI.CA")
            curr_date: Current date (defaults to today)
            look_back_days: Days of social data to analyze
            
        Returns:
            Dict with social media data + sentiment scores + quality metadata.
            Always returns — never raises.
        """
        ticker_clean = ticker.upper().replace(".CA", "").strip()
        curr_date = curr_date or datetime.now().strftime("%Y-%m-%d")
        cache_key = f"social:{ticker_clean}:{curr_date}:{look_back_days}d"

        # Try cache (1-hour TTL for social media)
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.info("Cache HIT for social sentiment: %s", cache_key)
            return cached

        try:
            from .social_media_sources.aggregator import get_social_media_data
            from .social_media_sources.sentiment_engine import analyze_social_sentiment

            # Collect posts from all platforms
            social_data = get_social_media_data(
                ticker_clean, curr_date, look_back_days
            )

            # Analyze sentiment
            sentiment = analyze_social_sentiment(social_data)

            result = {
                "ticker": ticker_clean,
                "analysis_date": curr_date,
                "look_back_days": look_back_days,
                # Core scores
                "sentiment_score": sentiment.sentiment_score,
                "buzz_score": sentiment.buzz_score,
                "momentum_score": sentiment.momentum_score,
                "confidence": sentiment.confidence,
                # Signal counts
                "total_posts": sentiment.total_posts_analyzed,
                "bullish_signals": sentiment.bullish_signals,
                "bearish_signals": sentiment.bearish_signals,
                "neutral_signals": sentiment.neutral_signals,
                # Hype detection
                "hype_detected": sentiment.hype_detected,
                "hype_reasons": sentiment.hype_reasons,
                # Platform breakdown
                "platform_sentiment": sentiment.platform_sentiment,
                # Language breakdown
                "arabic_sentiment": sentiment.arabic_sentiment,
                "english_sentiment": sentiment.english_sentiment,
                # Quality indicators
                "data_sufficient": sentiment.data_sufficient,
                "data_quality_note": sentiment.data_quality_note,
                "platforms_succeeded": social_data.platforms_succeeded,
                "platforms_failed": list(social_data.platforms_failed.keys()),
                "data_quality_score": social_data.data_quality_score,
                # Source metadata
                "source": "live" if social_data.platforms_succeeded else "cached",
            }

            # Cache it
            self.cache.set(cache_key, result, data_type="social_media")
            
            self._log_quality(
                "social_sentiment",
                ",".join(social_data.platforms_succeeded) or "cached",
                sentiment.total_posts_analyzed > 0,
                sentiment.total_posts_analyzed,
            )

            return result

        except Exception as e:
            logger.error("Social sentiment fetch failed: %s", e)
            self._log_quality("social_sentiment", "none", False, 0, str(e))
            
            # Return empty response
            return {
                "ticker": ticker_clean,
                "analysis_date": curr_date,
                "sentiment_score": 0.0,
                "buzz_score": 0.0,
                "confidence": 0.0,
                "total_posts": 0,
                "data_sufficient": False,
                "data_quality_note": f"All sources failed: {e}",
                "source": "none",
            }

    # =========================================================================
    # Utilities
    # =========================================================================

    def _normalize_symbol(self, symbol: str) -> str:
        """Ensure EGX symbol has .CA suffix (canonical helper, MEMORY.md §H).

        Index tickers (starting with ^ or $) are left unchanged — they are
        not EGX equity symbols and should not receive the .CA suffix.
        """
        symbol = symbol.strip()
        # Index tickers like ^EGX30, $TASI — pass through unchanged
        if symbol.startswith("^") or symbol.startswith("$"):
            return symbol.upper()
        from tradingagents.dataflows.symbol_utils import ensure_ca_suffix
        return ensure_ca_suffix(symbol)

    def _log_quality(
        self,
        method: str,
        source: str,
        success: bool,
        records: int = 0,
        error: str = "",
    ):
        """Log a data quality report."""
        report = DataQualityReport(
            source=source,
            method=method,
            success=success,
            records_returned=records,
            errors=[error] if error else [],
        )
        self._quality_log.append(report.model_dump())
        
        # Keep only last 100 entries
        if len(self._quality_log) > 100:
            self._quality_log = self._quality_log[-100:]

    def get_quality_report(self) -> list:
        """Return the data quality log for debugging."""
        return self._quality_log

    def get_cache_stats(self) -> dict:
        """Return cache statistics."""
        return self.cache.stats()
