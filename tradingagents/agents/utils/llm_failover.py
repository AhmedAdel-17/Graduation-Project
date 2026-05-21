"""
LLM Failover & Resilience Utilities
=====================================

This module provides two layers of production-grade LLM reliability:

Layer 1 — `safe_invoke(llm, prompt, *, fallback, agent_name)`
    A thin wrapper around any LangChain `.invoke()` call that:
    - Catches all exceptions (network, auth, token-limit, etc.)
    - Logs the error with agent context
    - Returns a `fallback` value instead of crashing the graph
    - Tracks failure counts for observability

    Usage in any agent node:
        from tradingagents.agents.utils.llm_failover import safe_invoke
        response = safe_invoke(llm, prompt, fallback="HOLD", agent_name="Bull Researcher")

Layer 2 — `ReliableChatModel`
    A LangChain-compatible `BaseChatModel` that wraps multiple provider
    backends and automatically rotates on rate-limit (429) or overload (503)
    errors. Designed to be passed in as a drop-in replacement for ChatOpenAI
    wherever `self.deep_thinking_llm` or `self.quick_thinking_llm` is used.

    Instantiation in TradingAgentsGraph.__init__ when `enable_llm_failover=True`:
        from tradingagents.agents.utils.llm_failover import build_resilient_llm
        self.deep_thinking_llm = build_resilient_llm(self.config, role="deep")
"""

from __future__ import annotations

import logging
import os
import time
import threading
from typing import Any, Dict, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult

logger = logging.getLogger("tradingagents.llm_failover")

# ---------------------------------------------------------------------------
# Layer 1: safe_invoke — lightweight per-call error handler
# ---------------------------------------------------------------------------

# Global failure counter for observability (reset between test runs if needed)
_failure_counts: Dict[str, int] = {}
_failure_lock = threading.Lock()


def safe_invoke(
    llm: Any,
    prompt: Any,
    *,
    fallback: Any = None,
    agent_name: str = "unknown_agent",
    max_retries: int = 0,          # Additional retries beyond what LangChain does
    retry_delay_s: float = 2.0,
) -> Any:
    """
    Invoke `llm.invoke(prompt)` with full exception safety.

    Parameters
    ----------
    llm         : Any LangChain-compatible LLM/chain with an `.invoke()` method.
    prompt      : The prompt (str, list[Message], dict, etc.) to pass to `.invoke()`.
    fallback    : Value returned if all attempts fail. If callable, it is called
                  with the exception as its single argument: `fallback(exc)`.
    agent_name  : Human-readable name for logging and failure tracking.
    max_retries : How many additional retries to attempt on *any* exception
                  (separate from LangChain's built-in max_retries on the LLM).
    retry_delay_s: Seconds between manual retries.

    Returns
    -------
    The LLM response on success, or `fallback` (or `fallback(exc)`) on failure.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1 + max_retries):
        try:
            return llm.invoke(prompt)
        except Exception as exc:
            last_exc = exc
            with _failure_lock:
                _failure_counts[agent_name] = _failure_counts.get(agent_name, 0) + 1
            logger.error(
                "[%s] LLM invoke failed (attempt %d/%d): %s — %s",
                agent_name,
                attempt + 1,
                1 + max_retries,
                type(exc).__name__,
                exc,
            )
            if attempt < max_retries:
                time.sleep(retry_delay_s)

    # All attempts exhausted — return fallback
    if callable(fallback):
        return fallback(last_exc)
    return fallback


def get_failure_counts() -> Dict[str, int]:
    """Return a snapshot of per-agent LLM failure counts."""
    with _failure_lock:
        return dict(_failure_counts)


def reset_failure_counts() -> None:
    """Reset all failure counters (useful between test runs)."""
    with _failure_lock:
        _failure_counts.clear()


# ---------------------------------------------------------------------------
# Layer 2: ReliableChatModel — multi-provider failover
# ---------------------------------------------------------------------------

class ReliableChatModel(BaseChatModel):
    """
    A LangChain-compatible ChatModel with automatic provider failover.

    On rate-limit (429), overload (503), or resource-exhausted errors, it
    rotates to the next configured provider. Non-retryable errors (auth
    failures, malformed requests) are raised immediately.

    Provider list is configured via ``config["llm_failover_priority"]``.
    Each entry is tried in order; if all fail, RuntimeError is raised.

    Thread-safety: provider rotation uses an index protected by a Lock.
    """

    # Pydantic v2 model config
    model_config = {"arbitrary_types_allowed": True}

    # LangChain uses PrivateAttr for non-serialized fields in BaseChatModel.
    # We store providers and state as plain instance attributes instead of
    # PrivateAttr to avoid the copy() serialization issue in the original impl.

    def __init__(
        self,
        providers: List[BaseChatModel],
        **kwargs: Any,
    ) -> None:
        """
        Parameters
        ----------
        providers : List of initialised BaseChatModel instances, in priority order.
        """
        super().__init__(**kwargs)
        object.__setattr__(self, "_failover_providers", list(providers))
        object.__setattr__(self, "_failover_idx", 0)
        object.__setattr__(self, "_failover_lock", threading.Lock())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _providers(self) -> List[BaseChatModel]:
        return object.__getattribute__(self, "_failover_providers")

    @property
    def _current_idx(self) -> int:
        return object.__getattribute__(self, "_failover_idx")

    def _rotate(self) -> None:
        with object.__getattribute__(self, "_failover_lock"):
            new_idx = (self._current_idx + 1) % len(self._providers)
            object.__setattr__(self, "_failover_idx", new_idx)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        err = str(exc).lower()
        return any(
            tag in err
            for tag in (
                "rate_limit", "rate limit",
                "429",
                "503",
                "overloaded",
                "resource_exhausted",
                "quota exceeded",
                "payload too large",
                "413",
            )
        )

    # ------------------------------------------------------------------
    # LangChain BaseChatModel interface
    # ------------------------------------------------------------------

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        errors: List[str] = []
        for _ in range(len(self._providers)):
            provider = self._providers[self._current_idx]
            pname = provider.__class__.__name__
            try:
                logger.info("ReliableChatModel: invoking %s (idx=%d)", pname, self._current_idx)
                return provider._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as exc:
                if self._is_retryable(exc):
                    logger.warning(
                        "ReliableChatModel: %s rate-limited/overloaded (%s), rotating...",
                        pname, exc,
                    )
                    errors.append(f"{pname}: {exc}")
                    self._rotate()
                    time.sleep(1.5)
                else:
                    logger.error("ReliableChatModel: %s non-retryable error: %s", pname, exc)
                    raise

        raise RuntimeError(
            f"ReliableChatModel: all {len(self._providers)} providers exhausted. "
            f"Errors: {'; '.join(errors)}"
        )

    def bind_tools(self, tools: List[Any], **kwargs: Any) -> "ReliableChatModel":
        """
        Bind tools to every underlying provider and return a new ReliableChatModel.
        Uses object.__setattr__ to avoid Pydantic's immutability on private attrs.
        """
        bound_providers = [p.bind_tools(tools, **kwargs) for p in self._providers]
        return ReliableChatModel(providers=bound_providers)

    @property
    def _llm_type(self) -> str:
        return "reliable_failover_model"


# ---------------------------------------------------------------------------
# Factory: build_resilient_llm
# ---------------------------------------------------------------------------

def build_resilient_llm(
    config: Dict[str, Any],
    role: str = "deep",
    seed: int = 42,
    max_retries_per_provider: int = 0,
) -> ReliableChatModel:
    """
    Build a ReliableChatModel from the project config.

    Parameters
    ----------
    config  : The DEFAULT_CONFIG dict (or a copy with overrides).
    role    : "deep" → uses ``deep_think_llm``; "quick" → uses ``quick_think_llm``.
    seed    : Seed value passed to providers that support it (OpenAI, Google).

    Returns a ReliableChatModel with providers in the order specified by
    ``config["llm_failover_priority"]`` (default: ["primary", "openrouter", "groq"]).
    """
    model_key = "deep_think_llm" if role == "deep" else "quick_think_llm"
    model_name = config[model_key]

    priority = config.get("llm_failover_priority", ["primary", "openrouter", "groq"])
    providers: List[BaseChatModel] = []

    for p_name in priority:
        try:
            if p_name == "primary":
                # Use the configured primary provider
                from langchain_openai import ChatOpenAI
                providers.append(ChatOpenAI(
                    model=model_name,
                    base_url=config.get("backend_url", "https://api.deepseek.com"),
                    api_key=config.get("DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY"),
                    temperature=0,
                    seed=seed,
                    max_retries=max_retries_per_provider,
                ))

            elif p_name == "openrouter":
                api_key = config.get("OPENROUTER_API_KEY") or config.get("openrouter_api_key")
                if not api_key:
                    logger.debug("build_resilient_llm: skipping openrouter — no API key")
                    continue
                from langchain_openai import ChatOpenAI
                providers.append(ChatOpenAI(
                    model=config.get("openrouter_model", "openai/gpt-4o-mini"),
                    base_url="https://openrouter.ai/api/v1",
                    api_key=api_key,
                    temperature=0,
                    seed=seed,
                    max_retries=max_retries_per_provider,
                ))

            elif p_name == "groq":
                api_key = config.get("GROQ_API_KEY") or config.get("groq_api_key")
                if not api_key:
                    logger.debug("build_resilient_llm: skipping groq — no API key")
                    continue
                from langchain_openai import ChatOpenAI
                providers.append(ChatOpenAI(
                    model=config.get("groq_model", "llama-3.3-70b-versatile"),
                    base_url="https://api.groq.com/openai/v1",
                    api_key=api_key,
                    temperature=0,
                    max_retries=max_retries_per_provider,
                ))

            elif p_name == "google":
                api_key = config.get("GOOGLE_API_KEY") or config.get("google_api_key")
                if not api_key:
                    logger.debug("build_resilient_llm: skipping google — no API key")
                    continue
                from langchain_google_genai import ChatGoogleGenerativeAI
                providers.append(ChatGoogleGenerativeAI(
                    model=config.get("google_model", "gemini-1.5-flash"),
                    temperature=0,
                    google_api_key=api_key,
                ))

        except Exception as exc:
            logger.warning("build_resilient_llm: failed to init provider %s: %s", p_name, exc)

    if not providers:
        raise ValueError(
            "build_resilient_llm: no valid providers could be initialised. "
            "Check API keys and llm_failover_priority config."
        )

    logger.info(
        "build_resilient_llm: %d provider(s) ready for role=%s: %s",
        len(providers),
        role,
        [p.__class__.__name__ for p in providers],
    )
    return ReliableChatModel(providers=providers)
