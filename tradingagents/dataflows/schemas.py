"""
Data Validation Schemas for TradingAgents
==========================================
Pydantic models that define strict contracts for every data type.
All data sources MUST return data conforming to these schemas.

This ensures agents always receive consistent, validated data
regardless of which provider produced it.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


# =============================================================================
# OHLCV / Price Data
# =============================================================================

class OHLCVBar(BaseModel):
    """A single daily price bar."""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class StockDataResponse(BaseModel):
    """
    Unified schema for ALL stock/price data sources.
    
    Every provider (yfinance, EODHD, egxpy, TradingView) must return
    data that can be validated against this schema.
    """
    symbol: str
    market: str = "EGX"
    currency: str = "EGP"
    start_date: str
    end_date: str
    total_records: int = 0
    data: List[OHLCVBar] = Field(default_factory=list)
    avg_daily_volume: float = 0.0
    low_liquidity: bool = False
    volume_missing: bool = False
    source: str = "unknown"
    retrieved_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    errors: Optional[List[str]] = None


# =============================================================================
# Technical Indicators / Signals
# =============================================================================

class TechnicalSignals(BaseModel):
    """
    Unified schema for technical analysis signals.
    
    Can come from TradingView-TA or local stockstats calculation.
    """
    symbol: str
    summary: str = "NEUTRAL"  # "BUY" | "SELL" | "NEUTRAL" | "STRONG_BUY" | "STRONG_SELL"
    moving_averages: Dict[str, Any] = Field(default_factory=dict)
    oscillators: Dict[str, Any] = Field(default_factory=dict)
    indicators: Dict[str, Any] = Field(default_factory=dict)  # Raw indicator values
    source: str = "unknown"
    retrieved_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


# =============================================================================
# Fundamental Data
# =============================================================================

class FundamentalsResponse(BaseModel):
    """Unified schema for fundamental data from any provider."""
    symbol: str
    market: str = "EGX"
    currency: str = "EGP"
    income_statement: Optional[Dict[str, Any]] = None
    balance_sheet: Optional[Dict[str, Any]] = None
    cashflow: Optional[Dict[str, Any]] = None
    key_ratios: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    data_completeness: float = 0.0  # 0.0 to 1.0
    source: str = "unknown"
    retrieved_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    errors: Optional[List[str]] = None


# =============================================================================
# News Data
# =============================================================================

class NewsArticle(BaseModel):
    """A single news article from any source."""
    title: str
    summary: str = ""
    source: str = "unknown"
    published_at: str = ""
    url: Optional[str] = None
    language: str = "en"  # "ar" | "en"
    sentiment: Optional[float] = None  # -1.0 to 1.0 if pre-scored


class NewsResponse(BaseModel):
    """Unified schema for news from all sources."""
    query: str
    total_articles: int = 0
    articles: List[NewsArticle] = Field(default_factory=list)
    sources_queried: List[str] = Field(default_factory=list)
    sources_failed: List[str] = Field(default_factory=list)
    retrieved_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


# =============================================================================
# Data Quality Metadata
# =============================================================================

class DataQualityReport(BaseModel):
    """Tracks data quality for a single fetch operation."""
    source: str
    method: str
    success: bool
    latency_ms: float = 0.0
    records_returned: int = 0
    errors: List[str] = Field(default_factory=list)
    fallback_used: bool = False
    fallback_source: Optional[str] = None
    cached: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
