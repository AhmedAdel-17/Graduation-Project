"""Cross-sectional factor engine (remediation Track B).

The PM/quant review concluded that single-name LLM narratives are the wrong shape
for a thin market like EGX, and that cross-sectional factor ranking
(value / quality / momentum / low-vol) is the more defensible primary signal. This
package is that engine: rank the whole universe on robust factors, go long the top
quantile, and let the LLM/fundamentals become overlays rather than the driver.

The core (``core``) is pure and point-in-time: it takes already-fetched data and
computes factors + cross-sectional ranks, so it is fully unit-testable without
network and cannot leak future information.
"""
from tradingagents.factors.core import (  # noqa: F401
    ATOMIC_FACTORS,
    DEFAULT_FACTOR_WEIGHTS,
    FactorInputs,
    FactorScore,
    momentum_score,
    realized_vol,
    rank_universe,
    rank_to_decisions,
)
from tradingagents.factors.fundamentals_adapter import (  # noqa: F401
    build_factor_inputs,
    fundamentals_for_factor,
    ratios_to_factor_fields,
    sector_map_for,
)
from tradingagents.factors.portfolio_bridge import (  # noqa: F401
    build_factor_portfolio,
    factor_scores_to_views,
)
