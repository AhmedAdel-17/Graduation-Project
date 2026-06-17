"""Portfolio Optimization Assistant subsystem (design v3).

A stateful copilot — orchestrated by ``PortfolioCopilotService`` (not a LangGraph)
— that turns free-form bilingual (English + Egyptian Arabic) portfolio
descriptions into analytics and constraint-respecting rebalancing *proposals*
for human review. LLMs live only at the boundaries (extraction, strategy,
what-if, narration); every number a user sees is produced by deterministic code.

This package is decision-support only. It never executes orders.

See ``docs/PORTFOLIO_ASSISTANT_DESIGN.md`` (architecture, frozen v3) and
``docs/PORTFOLIO_ASSISTANT_ROADMAP.md`` (implementation phases).
"""

from __future__ import annotations

#: Bump when the optimizer / analytics math changes in a way that invalidates
#: previously stored proposals. Persisted on every ``OptimizationProposal`` so
#: audit rows are reproducible against a known code version (roadmap P0/P1).
ENGINE_VERSION = "0.1.0"

#: Bump when ``schemas.py`` changes shape. Mirrored by the hand-written TS types
#: in ``dashboard/src/services/api/portfolioTypes.ts``; the fixture round-trip
#: tests fail if the two drift.
SCHEMA_VERSION = "0.1.0"

__all__ = ["ENGINE_VERSION", "SCHEMA_VERSION"]
