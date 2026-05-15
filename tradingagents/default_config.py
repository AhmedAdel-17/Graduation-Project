import os

# Load .env before reading env vars. Safe no-op if python-dotenv is missing or
# .env does not exist. Must run before setdefault() so .env values win.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# API keys — loaded from .env file. setdefault ensures empty-string fallback
# when neither .env nor shell environment provides a value.
os.environ.setdefault("GROQ_API_KEY", "")
os.environ.setdefault("OPENAI_API_KEY", "")
os.environ.setdefault("EODHD_API_KEY", "")
os.environ.setdefault("GOOGLE_API_KEY", "")

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", "./results"),
    "data_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), "dataflows/data_cache")),
    "data_cache_dir": os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
        "dataflows/data_cache",
    ),
    # LLM settings - DeepSeek (OpenAI-compatible)
    "llm_provider": "openai",
    "deep_think_llm": "deepseek-chat",
    "quick_think_llm": "deepseek-chat",
    "backend_url": "https://api.deepseek.com",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "yfinance",        # Yahoo Finance for EGX (.CA suffix)
        "technical_indicators": "yfinance",   # Yahoo Finance indicators
        "fundamental_data": "local",       # Local CSVs for EGX fundamentals
        "news_data": "local",                 # CSV/text files for EGX news
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # "get_stock_data": "eodhd",  # Alternative: EODHD
    },
    
    # =========================================================================
    # EGX (Egyptian Exchange) Market Configuration
    # =========================================================================
    # These settings define institutional trading constraints specific to the
    # Egyptian Exchange (EGX). They enforce regulatory compliance and risk
    # management rules that differ from US equity markets.
    # =========================================================================
    
    # Auto-refresh automation
    "auto_refresh_fundamentals": True,    # Set False to disable EGX auto-download
    "fundamentals_max_age_days": 90,      # Trigger refresh if data is older than this
    "use_fundamental_memory": False,      # Phase 3 memory/reflection is opt-in and local
    "egx_risk_free_rate": 0.275,          # CBE policy rate proxy (late 2024); used for earnings_yield_spread

    # Backtest mode flag — set True when running single-ticker backtests.
    # Relaxes single-stock concentration limits that would otherwise veto most
    # trades when the entire portfolio is allocated to one ticker.
    # Set False for live / multi-stock portfolio trading.
    "backtest_mode": True,

    # ─── Database configuration ──────────────────────────────────────────────
    # PostgreSQL connection URL. Required for persistent agent memories,
    # backtest storage, and audit logs. Falls back to in-memory ChromaDB
    # and local JSON files when not set.
    # Setup:
    #   createdb egx_trading
    #   psql egx_trading -f db_schema.sql
    "postgres_url": os.environ.get("POSTGRES_URL", ""),

    # Vector memory backend (chroma default; postgres opt-in)
    "memory_backend": os.environ.get("TRADINGAGENTS_MEMORY_BACKEND", "chroma").strip().lower(),
    "chroma_persist_dir": os.environ.get("CHROMA_PERSIST_DIR", "./chroma_db"),
    "memory_min_similarity": float(os.environ.get("MEMORY_MIN_SIMILARITY", "0.30")),

    # Redis URL for real-time agent progress streaming to the dashboard WebSocket.
    # Falls back to silent no-op if Redis is not running.
    "redis_url": os.environ.get("REDIS_URL", "redis://localhost:6379"),

    # RL meta-policy (opt-in, off by default)
    "rl_meta_policy_enabled": os.environ.get("RL_META_POLICY_ENABLED", "0").strip() in ("1", "true", "True", "yes"),
    "rl_model_path": os.environ.get("RL_MODEL_PATH", ""),

    # ─── Pre-fetch optimisation ──────────────────────────────────────────────
    # Pre-fetch data before graph execution (Phase 2a optimisation).
    # When True, DataPrefetcher fetches news and social data in parallel before
    # the graph starts, eliminating tool-calling round-trips for News and Social
    # analysts (~2 LLM calls, ~6K tokens, ~30-60s saved per trade date).
    # Set False only for ablation experiments (ABLATION_NO_PREFETCH) or debugging.
    "prefetch_data": True,

    # Target market identifier - determines which market rules apply
    # EGX = Egyptian Exchange, the primary stock exchange in Egypt
    "target_market": "EGX",
    
    # Trading currency - Egyptian Pound (EGP)
    # All position sizes and P&L calculations use this currency
    "trading_currency": "EGP",
    
    # Long-only constraint - EGX institutional accounts typically operate long-only
    # This reflects the limited availability of borrowing mechanisms in Egyptian markets
    "long_only": True,
    
    # Short selling restriction - EGX does not permit short selling for most participants
    # Regulatory framework under the Financial Regulatory Authority (FRA) restricts this
    "allow_short_selling": False,
    
    # Leverage restriction - No margin trading or leveraged positions allowed
    # EGX regulations limit leverage to maintain market stability
    "allow_leverage": False,
    
    # Daily price limit percentage - EGX enforces ±10% daily price movement limits
    # Securities hitting this limit trigger a trading halt (circuit breaker)
    "daily_price_limit_pct": 0.10,
    
    # Investment horizon in months — used by prompts, risk checks, and researchers
    # to frame analysis for the target holding period.
    "trade_horizon_months": 6,

    # Maximum position size as percentage of Average Daily Volume (ADV)
    # Prevents market impact and ensures orderly execution
    # 10% of ADV is a conservative institutional constraint for EGX liquidity
    "max_position_pct_adv": 0.10,
    
    # EGX trading session hours (Egypt Standard Time, UTC+2)
    # Pre-market: 09:30-10:00, Continuous trading: 10:00-14:30
    # These hours are for the main continuous trading session
    "trading_hours": {
        "start": "10:00",  # Market open (continuous session)
        "end": "14:30",    # Market close
    },
}

# EGX ticker universe (CLAUDE.md §10)
EGX_TICKERS = [
    # Banks
    "COMI.CA", "ADIB.CA", "CIEB.CA", "EXPA.CA", "HDBK.CA", "QNBA.CA", "SAUD.CA",
    # Real Estate
    "TMGH.CA", "HELI.CA", "PHDC.CA", "OCDI.CA", "ORAS.CA", "EMFD.CA",
    # Industry
    "EAST.CA", "ESRS.CA", "SWDY.CA", "ABUK.CA", "MFPC.CA", "EGAL.CA", "EGCH.CA", "EFIC.CA",
    # Telecom/Tech
    "ETEL.CA", "FWRY.CA", "EFIH.CA", "RAYA.CA",
    # Financial Services
    "HRHO.CA", "BTFH.CA", "CIch.CA",
    # Food & Beverage
    "JUFO.CA", "EFID.CA", "DOMT.CA",
]
