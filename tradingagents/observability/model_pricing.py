"""
LLM model pricing lookup for cost estimation.

Provides estimated $/token rates for known models. Unknown models return 0.0
(tokens are still counted, but cost is not estimated).

Pricing is best-effort — rates change frequently. The PRICING_SOURCE_DATE
indicates when these were last verified against provider documentation.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Date these prices were last verified against provider documentation.
PRICING_SOURCE_DATE = "2026-06-01"

# Model name → {input_per_token: $/token, output_per_token: $/token}
MODEL_PRICING = {
    # DeepSeek (primary provider for this project)
    "deepseek-chat": {"input_per_token": 0.14e-6, "output_per_token": 0.28e-6},
    "deepseek-reasoner": {"input_per_token": 0.55e-6, "output_per_token": 2.19e-6},
    # OpenAI (fallback/alternative)
    "gpt-4o": {"input_per_token": 2.50e-6, "output_per_token": 10.0e-6},
    "gpt-4o-mini": {"input_per_token": 0.15e-6, "output_per_token": 0.60e-6},
    "gpt-4-turbo": {"input_per_token": 10.0e-6, "output_per_token": 30.0e-6},
    # Anthropic
    "claude-sonnet-4-20250514": {"input_per_token": 3.0e-6, "output_per_token": 15.0e-6},
    "claude-haiku-4-5-20251001": {"input_per_token": 0.80e-6, "output_per_token": 4.0e-6},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Estimate cost in USD for a single LLM call.

    Returns 0.0 if model pricing is unknown. Tokens are still tracked
    via separate counters regardless of whether cost can be estimated.
    """
    prices = MODEL_PRICING.get(model)
    if prices is None:
        logger.debug("model_pricing: unknown model %r, cost recorded as 0", model)
        return 0.0
    return (
        input_tokens * prices["input_per_token"]
        + output_tokens * prices["output_per_token"]
    )
