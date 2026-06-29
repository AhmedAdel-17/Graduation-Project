import os

# EGX ticker universe — from CLAUDE.md §10.
# Format: uppercase with .CA suffix (Yahoo Finance / EGX convention).
EGX_TICKERS: list[str] = [
    # Banks
    "COMI.CA", "ADIB.CA", "CIEB.CA", "EXPA.CA", "HDBK.CA", "QNBA.CA", "SAUD.CA",
    # Real Estate
    "TMGH.CA", "HELI.CA", "PHDC.CA", "OCDI.CA", "ORAS.CA", "EMFD.CA",
    # Industry
    "EAST.CA", "SWDY.CA", "ABUK.CA", "MFPC.CA", "EGAL.CA", "EGCH.CA", "EFIC.CA",
    # ESRS.CA (Ezz Steel) excluded: yfinance returns no income/balance data,
    # Mubasher scraper returned MANUAL_ENTRY_REQUIRED stubs with no values,
    # no EGX Annex 5 PDFs available. Re-add when a data source is secured.
    # See ESRS remediation (2026-05-29).
    # Telecom / Tech
    "ETEL.CA", "FWRY.CA", "EFIH.CA", "RAYA.CA",
    # Financial Services
    "HRHO.CA", "BTFH.CA", "CICH.CA",
    # Food & Beverage
    "JUFO.CA", "EFID.CA", "DOMT.CA",
]

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
    "deep_think_llm": os.getenv("DEEP_THINK_LLM", "deepseek-chat"),
    "quick_think_llm": os.getenv("QUICK_THINK_LLM", "deepseek-chat"),
    "backend_url": os.getenv("LLM_BACKEND_URL", "https://api.deepseek.com"),
    # Separate backend for embeddings (DeepSeek has no embeddings API).
    # Default: local Ollama with `nomic-embed-text` (free, persistent vectors).
    # Override with EMBEDDINGS_BACKEND_URL env var (e.g. https://api.openai.com/v1).
    "embeddings_backend_url": os.environ.get("EMBEDDINGS_BACKEND_URL", "http://localhost:11434/v1"),
    "embeddings_model": os.environ.get("EMBEDDINGS_MODEL", "nomic-embed-text"),
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
    "use_hybrid_fundamental_analyst": True,  # Hybrid (deterministic + 3-stage CoT) for EGX fundamentals; False = deterministic-only (no LLM calls)
    "thesis_cot_mode": "3call",           # "single" = original 1-call H&P, "3call" = competing-hypotheses H&P (default after A/B validation 2026-05-24)
    "egx_risk_free_rate": 0.275,          # CBE policy rate proxy (late 2024); used for earnings_yield_spread

    # ─── Fundamentals pipeline domain constants ─────────────────────────────
    # Filing lag: days after period_end_date before data is assumed public.
    # Used by data_loader.py when publish_date column is unavailable.
    "filing_lag_annual_days": 120,        # Annual financials: ~4 months after fiscal year-end
    "filing_lag_quarterly_days": 45,      # Quarterly financials: ~45 days after quarter-end

    # Leverage alert threshold for non-bank sectors (sector_config.py).
    # D/E above this triggers HIGH_LEVERAGE_ALERT distress flag.
    "leverage_alert_threshold": 5.0,

    # Calibration: D/E above this forces "up" direction (calibration.py).
    # Highly leveraged EGX firms tend to refinance rather than report lower earnings.
    "calibration_max_de_for_down": 4.0,

    # Calibration: confidence assigned when a non-up signal is overridden to "up".
    # Capped at naive-baseline level so Brier score stays competitive with always-up.
    "calibration_up_confidence": 60,

    # Data confidence scoring weights (scoring.py).
    # Must sum to 1.0. Controls relative importance of data quality dimensions.
    "data_confidence_weights": {
        "field_coverage": 0.45,       # Required fields populated (7 fields)
        "optional_coverage": 0.15,    # Optional fields populated
        "staleness": 0.25,            # How recent is the last filing
        "period_depth": 0.15,         # How many annual periods available
    },

    # Manifest-based fundamentals freshness pre-flight check.
    # When False (default): log freshness failures as warnings, do not block.
    # When True: fail fast before analysis starts if manifest freshness fails.
    "enforce_fundamentals_manifest_freshness": False,

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

    # Vector memory backend. ChromaDB is the default stabilization path because
    # it does not require a local Postgres + pgvector service.
    # Set TRADINGAGENTS_MEMORY_BACKEND=postgres only when pgvector is installed
    # and the Postgres schema has been prepared for vector(1536) columns.
    "memory_backend": os.environ.get("TRADINGAGENTS_MEMORY_BACKEND", "chroma").strip().lower(),

    # ChromaDB on-disk persistence path. When set, FinancialSituationMemory
    # uses chromadb.PersistentClient so agent memories survive process
    # restarts. Empty / unset → legacy in-memory client (data lost per run).
    # Directory is gitignored (see .gitignore).
    "chroma_persist_dir": os.environ.get("CHROMA_PERSIST_DIR", "./chroma_db"),

    # Minimum similarity score (cosine, 1 - distance) below which a memory
    # match is dropped from get_memories() results. Range: [0.0, 1.0].
    # 0.0 disables filtering. 0.30 is the conservative default — high enough
    # to keep clearly relevant past lessons out of unrelated prompts, low
    # enough not to wipe out a sparse early store. Override via env or per-call.
    "memory_min_similarity": float(os.environ.get("MEMORY_MIN_SIMILARITY", "0.30")),

    # Redis URL for real-time agent progress streaming to the dashboard WebSocket.
    # Falls back to silent no-op if Redis is not running.
    "redis_url": os.environ.get("REDIS_URL", "redis://localhost:6379"),

    # ─── RL meta-policy (opt-in, Stage C) ────────────────────────────────────
    # Offline-trained Conservative Q-Learning policy that adjusts position
    # size *after* the LLM agents have decided BUY/SELL/HOLD. Default OFF so
    # behavior is identical to main when the flag is unset. The policy can
    # only SHRINK size (never amplify) and the deterministic risk veto still
    # wins. See agent_docs/rl_meta_policy.md (Phase 4) for the architecture.
    "rl_meta_policy_enabled": os.environ.get("RL_META_POLICY_ENABLED", "0").strip() in ("1", "true", "True", "yes"),
    # Path to the trained checkpoint produced by scripts/train_rl_policy.py.
    # Empty string ⇒ fail-closed to identity (size_multiplier=1.0).
    "rl_model_path": os.environ.get("RL_MODEL_PATH", ""),

    # ─── Backtest recording (write-only audit trail) ───────────────────────────
    # When True, each LLM-backed graph node writes a JSON record per invocation
    # to backtest_records_dir. Zero behavior change — record-only, no replay.
    # Enable via backtester --record flag or set in config.
    "backtest_record_outputs": False,
    "record_full_prompts": False,  # save full prompt text (large); False = prompt_hash only
    "backtest_records_dir": "./backtest_records",

    # ─── Pre-fetch optimisation ──────────────────────────────────────────────
    # Pre-fetch data before graph execution (Phase 2a optimisation).
    # When True, DataPrefetcher fetches news and social data in parallel before
    # the graph starts, eliminating tool-calling round-trips for News and Social
    # analysts (~2 LLM calls, ~6K tokens, ~30-60s saved per trade date).
    # Set False only for ablation experiments (ABLATION_NO_PREFETCH) or debugging.
    "prefetch_data": True,

    # ─── Regime-robust improvements (P8) ─────────────────────────────────────
    # All default to OFF — P7 baseline is untouched unless explicitly enabled.

    # Fix A: Anti-churn reversal gating
    "anti_churn_enabled": os.environ.get("ANTI_CHURN_ENABLED", "1").strip() in ("1", "true"),
    "anti_churn_variant": os.environ.get("ANTI_CHURN_VARIANT", "A2"),  # A1|A2|A3|A4
    "anti_churn_min_hold_days": int(os.environ.get("ANTI_CHURN_MIN_HOLD_DAYS", "20")),
    "anti_churn_reversal_confidence_threshold": float(os.environ.get("ANTI_CHURN_REVERSAL_CONF", "0.55")),
    "anti_churn_partial_exit_frac": float(os.environ.get("ANTI_CHURN_PARTIAL_EXIT", "0.50")),

    # Fix B: Confidence decompression — each sub-fix independently toggleable
    "b1_weakest_link_enabled": os.environ.get("B1_WEAKEST_LINK", "0").strip() in ("1", "true"),
    "b2_news_neutral_enabled": os.environ.get("B2_NEWS_NEUTRAL", "0").strip() in ("1", "true"),
    "b3_confidence_floor_enabled": os.environ.get("B3_CONF_FLOOR", "0").strip() in ("1", "true"),
    "b4_sizing_floor_enabled": os.environ.get("B4_SIZING_FLOOR", "0").strip() in ("1", "true"),

    # Fix C: Market breadth overlay (confidence modulation, not hard gate)
    "market_breadth_enabled": os.environ.get("MARKET_BREADTH_ENABLED", "0").strip() in ("1", "true"),
    "market_breadth_lookback_days": int(os.environ.get("BREADTH_LOOKBACK_DAYS", "20")),
    "market_breadth_rally_threshold": float(os.environ.get("BREADTH_RALLY_PCT", "0.70")),
    "market_breadth_downturn_threshold": float(os.environ.get("BREADTH_DOWNTURN_PCT", "0.30")),
    "market_breadth_dampening_factor": float(os.environ.get("BREADTH_DAMPENING", "0.75")),

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
