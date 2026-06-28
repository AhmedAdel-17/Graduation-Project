import os

# EGX ticker universe — from CLAUDE.md §10.
# Format: uppercase with .CA suffix (Yahoo Finance / EGX convention).
# This is the single source of truth for the dashboard ticker picker (live + backtest
# screens, via /api/test/egx-tickers). Names cross-referenced against
# data/egx30_fundamentals/_slug_map.json. Keep in sync with
# dashboard/src/data/egxTickerMeta.ts and dashboard/src/hooks/useTickers.ts.
EGX_TICKERS: list[str] = [
    # Banks
    "COMI.CA", "ADIB.CA",
    # Real Estate
    "TMGH.CA", "HELI.CA", "PHDC.CA", "ORAS.CA", "EMFD.CA", "ORHD.CA",
    # Industry & Materials
    "ABUK.CA", "EAST.CA", "EGAL.CA", "EGCH.CA", "ORWE.CA", "AMOC.CA",
    "MCQE.CA", "ARCC.CA", "ISPH.CA", "RMDA.CA", "GBCO.CA",
    # Telecom / Tech
    "ETEL.CA", "FWRY.CA", "EFIH.CA", "RAYA.CA", "OIH.CA",
    # Financial Services
    "HRHO.CA", "BTFH.CA", "CCAP.CA", "VLMR.CA",
    # Food & Beverage
    "JUFO.CA", "EFID.CA",
]

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", "./results"),
    "data_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), "dataflows/data_cache")),
    "data_cache_dir": os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
        "dataflows/data_cache",
    ),
    # LLM settings - NVIDIA Build / DeepSeek-V4-Pro (primary, OpenAI-compatible)
    # Fallback chain: nvidia -> deepseek -> google (see llm_failover_priority below).
    "llm_provider": "openai",
    "deep_think_llm": os.getenv("DEEP_THINK_LLM", "deepseek-ai/deepseek-v4-pro"),
    "quick_think_llm": os.getenv("QUICK_THINK_LLM", "deepseek-ai/deepseek-v4-pro"),
    "backend_url": os.getenv("LLM_BACKEND_URL", "https://integrate.api.nvidia.com/v1"),
    # Portfolio Assistant conversational boundary model. This is separate from
    # the TradingAgentsGraph deep/quick roles: it handles bilingual extraction,
    # strategy, what-if interpretation, routing, and narration. NVIDIA Build is
    # an OpenAI-compatible host, so the existing NVIDIA_API_KEY reaches the whole
    # catalog. Default is openai/gpt-oss-120b — markedly faster + more reliable
    # at the bilingual structured-extraction (ticker) task than the prior Qwen
    # endpoint, which was slow and frequently truncated. DeepSeek-V4-Pro stays on
    # the agent graph (deep/quick roles) for the heavy analysis/optimization task.
    "conversational_provider": os.getenv("CONVERSATIONAL_PROVIDER", "nvidia"),
    "conversational_llm": os.getenv("CONVERSATIONAL_LLM", "openai/gpt-oss-120b"),
    "conversational_backend_url": os.getenv(
        "CONVERSATIONAL_BACKEND_URL", "https://integrate.api.nvidia.com/v1"
    ),
    # Completion-token budget for the conversational boundary adapters. NVIDIA
    # Build endpoints default this low enough to truncate structured JSON
    # mid-token. gpt-oss is a reasoning model whose hidden reasoning shares this
    # budget, so keep generous headroom (raised from 2048) so the JSON answer
    # always closes after the reasoning trace.
    "conversational_max_tokens": int(os.getenv("CONVERSATIONAL_MAX_TOKENS", "3072")),
    # Reasoning effort for the conversational model when it is a reasoning model
    # (e.g. gpt-oss). "low" keeps boundary adapters (extraction/router/narrate)
    # fast and prevents the reasoning trace from eating the completion budget.
    # Set "none"/"" to omit the parameter entirely (e.g. for non-reasoning models).
    "conversational_reasoning_effort": os.getenv("CONVERSATIONAL_REASONING_EFFORT", "low"),
    # ─── Portfolio Assistant copilot (subsystem P3) ─────────────────────────
    # Freshness window for agent signals consumed by the optimizer's BL views.
    # A cached analysis_sessions decision newer than this is used as-is; older
    # (or missing) ⇒ enqueue a fresh TradingAgentsGraph run or degrade to a
    # neutral quant-prior with a low_confidence flag (design §6, roadmap P3).
    "pa_signal_max_age_days": int(os.getenv("PA_SIGNAL_MAX_AGE_DAYS", "7")),
    # Hard cap on concurrent signal-refresh graph runs the copilot may launch,
    # so a multi-holding refresh cannot self-DoS the LLM quota (roadmap finding
    # #2). Enforced by a process-global semaphore in signals.SignalResolver.
    "pa_max_concurrent_runs": int(os.getenv("PA_MAX_CONCURRENT_RUNS", "2")),
    # Master toggle for mounting the /api/portfolio/* router (P4). Default on;
    # documents the no-auth blocker (MEMORY.md) — portfolios are personal data.
    "pa_enabled": os.getenv("PA_ENABLED", "1") == "1",
    # Provider failover order used by build_resilient_llm(). Primary first; each
    # next provider is tried automatically on rate-limit (429) / overload (503/504).
    # "nvidia"   -> NVIDIA Build (DeepSeek-V4-Pro, NVIDIA_API_KEY)   [PRIMARY]
    # "google"   -> Gemini       (GOOGLE_API_KEY)                    [fallback #1]
    # "deepseek" -> DeepSeek direct (deepseek-chat, DEEPSEEK_API_KEY)[fallback #2]
    # "groq"     -> Groq Llama   (GROQ_API_KEY)                      [fallback #3]
    # "openrouter" -> OpenRouter (openai/gpt-4o-mini, OR_API_KEY)      [fallback #4]
    #
    # NOTE: With NVIDIA primary, live runs keep DeepSeek-V4-Pro quality and fallback
    # on rate-limit/overload. Google is fallback #1 and DeepSeek-direct is fallback #2.
    # See MEMORY.md §FF / §GG.
    "llm_failover_priority": os.getenv(
        "LLM_FAILOVER_PRIORITY", "nvidia,google,deepseek,groq,openrouter"
    ).split(","),
    # Fallback provider models (override via env).
    "google_model": os.getenv("GOOGLE_MODEL", "gemini-2.0-flash"),
    "groq_model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
    # Determinism: every LLM is built with temperature=0; this seed is also
    # pinned on providers that support it (OpenAI/DeepSeek, Google) so backtest
    # runs and audit trails are reproducible. Override with LLM_SEED.
    "llm_seed": int(os.getenv("LLM_SEED", "42")),
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
    "egx_risk_free_rate": 0.275,          # CBE policy rate proxy (late 2024); used for earnings_yield_spread

    # Backtest mode flag — set True when running single-ticker backtests.
    # Relaxes single-stock concentration limits that would otherwise veto most
    # trades when the entire portfolio is allocated to one ticker.
    # Set False for live / multi-stock portfolio trading.
    "backtest_mode": True,

    # ─── Cost-aware BUY gate ──────────────────────────────────────────────────
    # A BUY is only worth taking if its expected upside clears the EGX round-trip
    # cost by a margin (estimation error means a thesis that barely beats costs is
    # net-negative in expectation). The deterministic final gate downgrades BUY →
    # HOLD when the bull-case base upside < `min_edge_cost_multiple` × round-trip
    # cost. Default 2.0 (expected move must be at least 2× the cost hurdle).
    # Set 0 to disable the gate. Override via MIN_EDGE_COST_MULTIPLE.
    "min_edge_cost_multiple": float(os.environ.get("MIN_EDGE_COST_MULTIPLE", "2.0")),

    # ─── Confidence-driven position sizing (FROZEN by default) ────────────────
    # When True, the executor scales target position size by the LLM-emitted
    # `confidence` scalar. This is OFF by default because that confidence is an
    # uncalibrated number produced by the same text model that wrote the thesis —
    # it is NOT a calibrated probability and must not size real capital until the
    # calibration work (remediation Phase 3) validates it (Brier / reliability).
    # Re-enable only after calibration, via CONFIDENCE_SIZING_ENABLED=1.
    "confidence_sizing_enabled": os.environ.get(
        "CONFIDENCE_SIZING_ENABLED", "0"
    ).strip() in ("1", "true", "True", "yes"),

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
