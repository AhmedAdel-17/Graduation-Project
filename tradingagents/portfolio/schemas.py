"""Canonical data contracts for the Portfolio Assistant subsystem (roadmap P0).

Every model here is the interface between phases: the deterministic core (P1),
the LLM boundary adapters (P2), the copilot service (P3), the API (P4) and the
frontend (P6/P7, via the hand-written mirror in ``portfolioTypes.ts``). Nothing
in this module performs I/O, calls an LLM, or imports a heavy subsystem — it is
imported everywhere, so it must stay light and side-effect-free.

Design notes that apply file-wide
---------------------------------
* **Money is ``float`` EGP, by deliberate choice.** The optimization stack
  (cvxpy / numpy / scikit-learn covariance) is float-native; ``Decimal`` would
  force conversions at every boundary and serialize to JSON strings that break
  the frontend charts. Round at *display* time; never test monetary values for
  exact equality. This is a research/decision-support tool that quotes "≈ EGP".
* **``extra="forbid"`` on every domain model.** This is a wire contract with a
  hand-maintained TypeScript twin; rejecting unknown keys turns schema drift
  into a loud test failure. Genuinely open payloads use explicit ``dict`` fields.
* **Tickers are normalized to the Yahoo ``.CA`` convention** (CLAUDE.md §1).
* **Audit records are immutable by convention.** Snapshots, proposals and
  scenarios are never mutated after persistence; new versions are appended.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Single source of truth for the EGX sector vocabulary. ``sectors.py`` is
# dependency-light (stdlib + text_preprocessor), so importing it here is safe.
from tradingagents.dataflows.social_v2.sectors import SECTORS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    """Timezone-aware UTC timestamp factory (naive datetimes cause audit drift)."""
    return datetime.now(timezone.utc)


def normalize_ticker(raw: str) -> str:
    """Normalize a user/LLM-supplied symbol to the canonical ``SYMBOL.CA`` form.

    Bare symbols (``"COMI"``) get the ``.CA`` suffix; already-suffixed or
    otherwise-dotted symbols are upper-cased and returned as-is. Ticker
    *resolution* (alias → symbol) is the extraction adapter's job against
    ``entities.SYMBOL_REGISTRY``; this only canonicalizes the surface form.
    """
    t = raw.strip().upper()
    if not t:
        raise ValueError("ticker must be non-empty")
    if "." not in t:
        t = f"{t}.CA"
    return t


# Canonical sector codes as an Enum-like Literal, sourced from sectors.SECTORS
# plus the "OTHER" bucket that ``ticker_sector()`` falls back to.
_SECTOR_CODES: frozenset[str] = frozenset(SECTORS) | {"OTHER"}


class EGXIndex(str, Enum):
    """EGX index families. Mirrors ``sentiment.taxonomy.IndexEnum`` deliberately
    (we avoid importing that heavier module here); ``analytics.py`` maps between
    the two so this contract file stays import-light."""

    EGX30 = "EGX30"
    EGX70 = "EGX70"
    EGX100 = "EGX100"


# ---------------------------------------------------------------------------
# Enumerations (the controlled vocabularies the LLM adapters must emit)
# ---------------------------------------------------------------------------

class TradeSide(str, Enum):
    """A proposed rebalancing action. Long-only EGX → no SHORT (CLAUDE.md §6)."""

    BUY = "BUY"
    SELL = "SELL"


class SignalLabel(str, Enum):
    """A per-ticker view sourced from ``TradingAgentsGraph`` (the signal oracle)."""

    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


class SignalSource(str, Enum):
    """Provenance of a ``SignalView`` — drives the honesty/confidence labeling."""

    AGENT = "agent"          # fresh multi-agent pipeline decision
    QUANT_PRIOR = "quant_prior"  # degraded fallback when signal is stale/missing


class HoldingSource(str, Enum):
    """How a holding entered the snapshot — drives the confirmation gate UX."""

    EXTRACTED = "extracted"  # parsed from user text, not yet confirmed
    CONFIRMED = "confirmed"  # user accepted/edited the extraction table
    DERIVED = "derived"      # produced by a scenario patch (never user-stated)


class Objective(str, Enum):
    """Investment objective (design §3.3). Ordered low→high risk appetite."""

    CAPITAL_PRESERVATION = "capital_preservation"
    INCOME = "income"
    BALANCED = "balanced"
    GROWTH = "growth"
    AGGRESSIVE_GROWTH = "aggressive_growth"


class RiskTolerance(str, Enum):
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class Horizon(str, Enum):
    """Investment horizon buckets. ``LT_6M`` triggers the high-cash-floor rule
    in the Policy Compiler — agent theses are multi-month and distrusted at <6m."""

    LT_6M = "lt_6m"
    M6_12 = "6_12m"
    Y1_3 = "1_3y"
    GT_3Y = "gt_3y"


class SolverStatus(str, Enum):
    """Optimizer outcome — surfaced verbatim for honesty (design §6)."""

    OPTIMAL = "optimal"
    INFEASIBLE_RELAXED = "infeasible_relaxed"  # constraints relaxed in documented order
    HEURISTIC_FALLBACK = "heuristic_fallback"  # solver unavailable; rule-based plan


class FlagSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ScenarioStatus(str, Enum):
    ACTIVE = "active"
    PROMOTED = "promoted"    # adopted as a new baseline
    DISCARDED = "discarded"  # soft-deleted (audit doctrine — never hard delete)


class Intent(str, Enum):
    """Router output (design §3). A single message may carry several intents
    (e.g. holdings + objectives in one breath), so the router emits a *set*."""

    DESCRIBE_PORTFOLIO = "describe_portfolio"
    OBJECTIVE = "objective"            # states goals/risk/horizon → Strategy Agent
    OPTIMIZE = "optimize"
    WHAT_IF = "what_if"
    ADOPT_SCENARIO = "adopt_scenario"
    FOLLOW_UP_QA = "follow_up_qa"      # narration over existing state, no recompute
    OFF_TOPIC = "off_topic"


# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------

class _Base(BaseModel):
    """Shared config: reject unknown keys, validate on assignment, keep Enum
    members (str-Enums serialize to their value for the frontend)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# Holdings & snapshots
# ---------------------------------------------------------------------------

class PortfolioHolding(_Base):
    """One position. ``shares`` and ``weight_pct`` are both optional because
    extraction often yields only one of them (the user says "20% in Telecom" or
    "300 shares of Fawry"); ``analytics.py`` reconciles them against live prices
    and total value. ``name_raw`` preserves the user's surface term for the
    confirmation table and audit."""

    ticker: str = Field(..., description="Canonical SYMBOL.CA")
    shares: Optional[float] = Field(None, ge=0, description="Share count if stated/derived")
    avg_cost: Optional[float] = Field(None, gt=0, description="Avg buy price, EGP/share")
    weight_pct: Optional[float] = Field(
        None, ge=0, le=100, description="Stated/target weight as % of total portfolio"
    )
    source: HoldingSource = HoldingSource.EXTRACTED
    name_raw: Optional[str] = Field(None, description="Original user term (audit/echo)")

    @field_validator("ticker")
    @classmethod
    def _norm_ticker(cls, v: str) -> str:
        return normalize_ticker(v)


class PortfolioSnapshot(_Base):
    """A point-in-time portfolio. The *baseline* snapshot is the confirmed truth;
    scenarios store their own derived snapshots (see ``Scenario``). Weights are
    NOT forced to sum to 100 here — extraction may be partial and cash is tracked
    separately; reconciliation/validation happens in analytics, not the contract.
    """

    snapshot_id: Optional[int] = Field(None, description="Set by the store on persist")
    conversation_id: Optional[str] = None
    version: int = Field(1, ge=1, description="Monotonic baseline version")
    cash_egp: float = Field(0.0, ge=0, description="Investable cash, EGP")
    total_value_egp: Optional[float] = Field(
        None, gt=0, description="User-stated total invested value (excl. cash). Anchors "
        "weight-only holdings so they can be valued/optimized; reconciled to shares at confirm.")
    holdings: list[PortfolioHolding] = Field(default_factory=list)
    confirmed_by_user: bool = False
    promoted_from_scenario: Optional[int] = Field(
        None, description="Scenario id, when this baseline was adopted from a what-if"
    )
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def tickers(self) -> list[str]:
        return [h.ticker for h in self.holdings]


# ---------------------------------------------------------------------------
# Investment policy (Enhancement 1 — Strategy Agent output)
# ---------------------------------------------------------------------------

# Qualitative fields the Strategy Agent infers; used to populate ``inferred_fields``
# when a default is applied so the Narrator can disclose assumptions.
_POLICY_QUALITATIVE_FIELDS = ("objective", "risk_tolerance", "horizon", "income_preference")


class InvestmentPolicy(_Base):
    """Structured investment profile (design §3.3). Inference (LLM) only — the
    qualitative→quantitative mapping is the Policy Compiler's job (P1), keeping
    allocations auditable. ``source_spans`` cite the user quote behind each
    inferred field; ``notes`` is the escape hatch for anything the enums can't
    capture and feeds the Narrator ONLY, never the optimizer.
    """

    objective: Objective = Objective.BALANCED
    risk_tolerance: RiskTolerance = RiskTolerance.MEDIUM
    horizon: Horizon = Horizon.Y1_3
    income_preference: bool = False

    excluded_sectors: list[str] = Field(default_factory=list)
    excluded_tickers: list[str] = Field(default_factory=list)
    max_position_pct: Optional[float] = Field(
        None, gt=0, le=100, description="Explicit user override only (else compiler default)"
    )
    min_cash_egp: Optional[float] = Field(None, ge=0)

    # Numeric goal (optional) — drives goal-feasibility (goal.py). Qualitative
    # objective/horizon stay primary; these add a concrete target when the user
    # states one ("grow my 50k to 80k in 2 years").
    goal_target_amount_egp: Optional[float] = Field(
        None, gt=0, description="Target total portfolio value the user wants to reach (EGP)"
    )
    goal_horizon_months: Optional[int] = Field(
        None, gt=0, description="Explicit numeric horizon for the goal (months); overrides "
        "the horizon-enum midpoint in the feasibility check"
    )
    monthly_contribution_egp: Optional[float] = Field(
        None, ge=0, description="Planned monthly contribution toward the goal (EGP)"
    )

    notes: str = Field("", description="Free text → Narrator only, never the optimizer")
    source_spans: dict[str, str] = Field(
        default_factory=dict, description="policy field -> user quote that justified it"
    )
    inferred_fields: list[str] = Field(
        default_factory=list, description="Fields set by inference/default vs explicit user input"
    )

    version: int = Field(1, ge=1)
    confirmed_by_user: bool = False
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("excluded_sectors")
    @classmethod
    def _validate_sectors(cls, v: list[str]) -> list[str]:
        out = []
        for s in v:
            code = s.strip().upper()
            if code not in _SECTOR_CODES:
                raise ValueError(f"unknown sector '{s}'; expected one of {sorted(_SECTOR_CODES)}")
            out.append(code)
        return out

    @field_validator("excluded_tickers")
    @classmethod
    def _norm_excluded_tickers(cls, v: list[str]) -> list[str]:
        return [normalize_ticker(t) for t in v]

    @classmethod
    def default_policy(cls) -> "InvestmentPolicy":
        """A balanced, medium-risk, 1–3y starting policy with every qualitative
        field marked as inferred (so the Narrator says "I assumed…"). Used on
        first contact before the user states any objective."""
        return cls(inferred_fields=list(_POLICY_QUALITATIVE_FIELDS))


# ---------------------------------------------------------------------------
# Optimizer params (Policy Compiler output — the deterministic carrier)
# ---------------------------------------------------------------------------

class OptimizerParams(_Base):
    """The compiled, machine-facing form of an ``InvestmentPolicy`` (design §3.4).
    Core numeric fields are *required*: the Policy Compiler must set them
    explicitly so a compiler bug can never hide behind a silent default. This
    object is persisted in the proposal audit blob for reproducibility."""

    risk_aversion: float = Field(..., gt=0, description="Mean-variance λ; higher = more cautious")
    max_position_pct: float = Field(..., gt=0, le=100, description="Per-name cap")
    max_sector_pct: float = Field(..., gt=0, le=100, description="Per-sector cap")
    min_cash_pct: float = Field(..., ge=0, le=100, description="Cash floor")
    view_shrinkage: float = Field(
        ..., ge=0, le=1, description="Shrink agent views toward prior (1=ignore views)"
    )
    turnover_penalty: float = Field(..., ge=0, description="Penalty on Σ|Δweight|")
    transaction_cost_pct: float = Field(..., ge=0, description="Per-side cost, e.g. 0.002")

    vol_target: Optional[float] = Field(None, gt=0, description="Hard annual vol ceiling (preservation)")
    vol_ceiling: Optional[float] = Field(None, gt=0, description="Soft annual vol cap (growth)")
    income_tilt: bool = False

    excluded_tickers: list[str] = Field(default_factory=list)
    excluded_sectors: list[str] = Field(default_factory=list)

    compiler_version: str = Field(..., description="Policy Compiler mapping-table version")
    policy_version: int = Field(..., ge=1, description="Source InvestmentPolicy.version")

    @field_validator("excluded_tickers")
    @classmethod
    def _norm_excluded(cls, v: list[str]) -> list[str]:
        return [normalize_ticker(t) for t in v]


class PolicyFlag(_Base):
    """A deterministic conflict/consistency finding (design §3.4). Rendered as a
    ``policy_flags`` chat block; ``data`` carries machine context the Narrator
    turns into bilingual prose. Flags never silently change the math."""

    code: str = Field(..., description="Stable code, e.g. CONCENTRATION_VS_RISK")
    severity: FlagSeverity = FlagSeverity.WARNING
    detail: str = Field(..., description="English one-liner; Narrator localizes")
    data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Signals (from the TradingAgentsGraph oracle, resolved in P3)
# ---------------------------------------------------------------------------

class SignalView(_Base):
    """A per-ticker view consumed by the optimizer's Black-Litterman blend.
    Carries provenance + freshness so the optimizer can scale view uncertainty
    and the UI can show a ``signal_freshness`` chip honestly."""

    ticker: str
    label: SignalLabel
    confidence: float = Field(..., ge=0, le=1)
    source: SignalSource = SignalSource.AGENT
    session_id: Optional[str] = Field(None, description="analysis_sessions.session_id")
    as_of: Optional[datetime] = Field(None, description="When the signal was produced")
    age_days: Optional[float] = Field(None, ge=0)
    is_stale: bool = False

    @field_validator("ticker")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_ticker(v)


# ---------------------------------------------------------------------------
# Rebalancing proposal (Optimization Engine output)
# ---------------------------------------------------------------------------

class RebalanceAction(_Base):
    """One discrete trade in a proposal (design §6). Quantities are integer
    shares; ``est_value_egp`` is advisory ("≈ EGP"). ``signal_session_id`` links
    the action back to the agent run that justified it — the explainability
    bridge to ``/api/results/{ticker}/{session_id}``."""

    ticker: str
    side: TradeSide
    shares: int = Field(..., gt=0)
    price_used: float = Field(..., gt=0, description="EGP/share at proposal time")
    est_value_egp: float = Field(..., gt=0)
    current_weight_pct: float = Field(..., ge=0, le=100)
    target_weight_pct: float = Field(..., ge=0, le=100)
    rationale: Optional[str] = Field(None, description="One-line human rationale")
    signal_session_id: Optional[str] = None

    @field_validator("ticker")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_ticker(v)


class OptimizationProposal(_Base):
    """The full, self-describing result of an optimization run. Persisted to
    ``pa_optimization_proposals`` and never deleted (audit doctrine). Expected
    returns are explicitly labeled *model views*, not forecasts (design §6).
    Exactly one of ``snapshot_id`` / ``scenario_id`` identifies what was optimized.
    """

    proposal_id: Optional[int] = None
    conversation_id: Optional[str] = None
    snapshot_id: Optional[int] = Field(None, description="Set for baseline runs")
    scenario_id: Optional[int] = Field(None, description="Set for what-if runs")
    policy_version: int = Field(..., ge=1)

    actions: list[RebalanceAction] = Field(default_factory=list)
    current_weights: dict[str, float] = Field(default_factory=dict)
    target_weights: dict[str, float] = Field(default_factory=dict)

    # Metric deltas — all caveated as model views in the UI.
    expected_return_view_annual: Optional[float] = Field(
        None, description="Posterior expected return (MODEL VIEW, not a forecast)"
    )
    expected_vol_before: Optional[float] = Field(None, ge=0)
    expected_vol_after: Optional[float] = Field(None, ge=0)
    hhi_before: Optional[float] = Field(None, ge=0, le=1)
    hhi_after: Optional[float] = Field(None, ge=0, le=1)
    est_total_cost_egp: float = Field(0.0, ge=0)
    est_turnover_pct: float = Field(0.0, ge=0)

    solver_status: SolverStatus
    policy_flags: list[PolicyFlag] = Field(default_factory=list)

    inputs_audit: dict[str, Any] = Field(
        default_factory=dict,
        description="Reproducibility blob: prices, signal session ids+freshness, "
        "OptimizerParams snapshot, covariance hash",
    )
    engine_version: str
    created_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "OptimizationProposal":
        if (self.snapshot_id is None) == (self.scenario_id is None):
            raise ValueError("exactly one of snapshot_id / scenario_id must be set")
        return self


# ---------------------------------------------------------------------------
# Scenario operations (Enhancement 2 — typed what-if vocabulary)
# ---------------------------------------------------------------------------
# Discriminated union on ``op`` (chosen over the design's loose {op, params:dict}):
# it gives whatif.py validation for free and the FE typed chip-builders.

class _Op(_Base):
    """Base for scenario ops; subclasses pin ``op`` and their own params."""


class AddCashOp(_Op):
    op: Literal["ADD_CASH"] = "ADD_CASH"
    amount_egp: float = Field(..., gt=0)


class RemoveCashOp(_Op):
    op: Literal["REMOVE_CASH"] = "REMOVE_CASH"
    amount_egp: float = Field(..., gt=0)


class ClosePositionOp(_Op):
    op: Literal["CLOSE_POSITION"] = "CLOSE_POSITION"
    ticker: str

    @field_validator("ticker")
    @classmethod
    def _n(cls, v: str) -> str:
        return normalize_ticker(v)


class ScalePositionOp(_Op):
    op: Literal["SCALE_POSITION"] = "SCALE_POSITION"
    ticker: str
    factor: float = Field(..., gt=0, description="1.5 = +50% exposure, 0.5 = halve")

    @field_validator("ticker")
    @classmethod
    def _n(cls, v: str) -> str:
        return normalize_ticker(v)


class SetPositionWeightOp(_Op):
    op: Literal["SET_POSITION_WEIGHT"] = "SET_POSITION_WEIGHT"
    ticker: str
    weight_pct: float = Field(..., ge=0, le=100)

    @field_validator("ticker")
    @classmethod
    def _n(cls, v: str) -> str:
        return normalize_ticker(v)


class ExcludeSectorOp(_Op):
    op: Literal["EXCLUDE_SECTOR"] = "EXCLUDE_SECTOR"
    sector: str

    @field_validator("sector")
    @classmethod
    def _v(cls, v: str) -> str:
        code = v.strip().upper()
        if code not in _SECTOR_CODES:
            raise ValueError(f"unknown sector '{v}'")
        return code


class ExcludeTickerOp(_Op):
    op: Literal["EXCLUDE_TICKER"] = "EXCLUDE_TICKER"
    ticker: str

    @field_validator("ticker")
    @classmethod
    def _n(cls, v: str) -> str:
        return normalize_ticker(v)


class OverridePolicyOp(_Op):
    """Override one ``InvestmentPolicy`` field for the scenario (e.g. "more
    aggressive" → risk_tolerance=high). ``value`` is heterogeneous by field;
    whatif.py validates it against the target field's type."""

    op: Literal["OVERRIDE_POLICY"] = "OVERRIDE_POLICY"
    field: str = Field(..., description="InvestmentPolicy field name")
    value: Union[str, float, bool] = Field(...)


class TargetRiskDeltaOp(_Op):
    """"Reduce my risk by 20%" → target portfolio vol = (1 + delta/100) ×
    reference vol. The single documented definition of an otherwise-ambiguous
    request; the Narrator restates it every time (design §4.2)."""

    op: Literal["TARGET_RISK_DELTA"] = "TARGET_RISK_DELTA"
    vol_delta_pct: float = Field(..., description="-20 = target 80% of reference vol")


ScenarioOp = Annotated[
    Union[
        AddCashOp, RemoveCashOp, ClosePositionOp, ScalePositionOp,
        SetPositionWeightOp, ExcludeSectorOp, ExcludeTickerOp,
        OverridePolicyOp, TargetRiskDeltaOp,
    ],
    Field(discriminator="op"),
]


class ScenarioPatch(_Base):
    """A hypothetical: one or more ops + what they are measured against
    (design §4.2). ``reference="active"`` composes on the current branch
    ("and what if I *also*…"); ``"baseline"`` compares to the original portfolio."""

    ops: list[ScenarioOp] = Field(..., min_length=1)
    reference: Literal["active", "baseline"] = "active"
    label: str = Field("", description="Human label, e.g. 'Sell all FWRY' (auto-derived)")


class PinnedInputSet(_Base):
    """Frozen inputs a scenario was computed against so branch diffs are honest
    (design §4.3): the same prices/signals/covariance, never "prices moved since
    lunch". Refreshing prices is an explicit action that recomputes the tree."""

    price_asof: datetime
    price_source: str = Field("", description="e.g. yfinance / gateway-cache")
    signal_session_ids: dict[str, Optional[str]] = Field(default_factory=dict)
    covariance_hash: str = ""


class Scenario(_Base):
    """A node in the workspace scenario tree (Enhancement 2). Stores the patch,
    the *pinned* derived state, and the inputs it was computed against. Baseline
    is never mutated by a hypothetical — scenarios are forks."""

    scenario_id: Optional[int] = None
    conversation_id: Optional[str] = None
    parent_scenario_id: Optional[int] = Field(
        None, description="None = forked from baseline"
    )
    base_snapshot_id: Optional[int] = None
    patch: ScenarioPatch
    derived_snapshot: PortfolioSnapshot = Field(
        ..., description="Pinned post-patch portfolio state"
    )
    derived_policy: Optional[InvestmentPolicy] = Field(
        None, description="Set when the patch contained OVERRIDE_POLICY"
    )
    input_set: Optional[PinnedInputSet] = None
    proposal_id: Optional[int] = None
    status: ScenarioStatus = ScenarioStatus.ACTIVE
    label: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Workspace (the v2 centerpiece — persistent copilot state)
# ---------------------------------------------------------------------------

class PortfolioWorkspace(_Base):
    """The durable state the copilot operates over (design §2/§5.2). Persisted in
    Postgres (or JSON fallback); the service is stateless per turn and reloads
    this each turn, so any instance can pick up a conversation."""

    conversation_id: str
    user_id: Optional[str] = Field(None, description="'local' in single-user demo mode")
    language: Literal["en", "ar", "auto"] = "auto"

    baseline: Optional[PortfolioSnapshot] = Field(
        None, description="Latest confirmed baseline portfolio"
    )
    policy: InvestmentPolicy = Field(default_factory=InvestmentPolicy.default_policy)
    scenarios: list[Scenario] = Field(default_factory=list)
    active_ref: str = Field("baseline", description="'baseline' | str(scenario_id)")
    last_proposal_id: Optional[int] = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def active_scenario(self) -> Optional[Scenario]:
        """The scenario the active_ref points at, or None when on the baseline."""
        if self.active_ref == "baseline":
            return None
        for s in self.scenarios:
            if str(s.scenario_id) == self.active_ref:
                return s
        return None

    def digest(self) -> "WorkspaceDigest":
        """Token-bounded summary handed to the router/interpreter prompts instead
        of raw chat history (design §4.4) — keeps LLM context constant regardless
        of conversation length."""
        bl = self.baseline
        baseline_summary = (
            f"{len(bl.holdings)} holdings, {bl.cash_egp:.0f} EGP cash"
            if bl else "no portfolio described yet"
        )
        return WorkspaceDigest(
            baseline_summary=baseline_summary,
            baseline_tickers=bl.tickers if bl else [],
            policy=self.policy,
            scenarios=[
                ScenarioRef(
                    scenario_id=s.scenario_id, label=s.label,
                    parent_scenario_id=s.parent_scenario_id, status=s.status,
                )
                for s in self.scenarios if s.status == ScenarioStatus.ACTIVE
            ],
            active_ref=self.active_ref,
            last_proposal_id=self.last_proposal_id,
        )


class ScenarioRef(_Base):
    """Compact scenario reference for the digest and the FE scenario tabs."""

    scenario_id: Optional[int] = None
    label: str = ""
    parent_scenario_id: Optional[int] = None
    status: ScenarioStatus = ScenarioStatus.ACTIVE


class WorkspaceDigest(_Base):
    """Compact, LLM-prompt-safe view of the workspace (see ``digest()``)."""

    baseline_summary: str
    baseline_tickers: list[str] = Field(default_factory=list)
    policy: InvestmentPolicy
    scenarios: list[ScenarioRef] = Field(default_factory=list)
    active_ref: str = "baseline"
    last_proposal_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Analytics (Analytics Engine output)
# ---------------------------------------------------------------------------

class HoldingAnalytics(_Base):
    """Per-holding computed view backing the holdings table + treemap nodes."""

    ticker: str
    shares: float = Field(..., ge=0)
    price: float = Field(..., gt=0)
    market_value_egp: float = Field(..., ge=0)
    weight_pct: float = Field(..., ge=0, le=100)
    avg_cost: Optional[float] = None
    unrealized_pnl_egp: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    sector: str = "OTHER"
    indices: list[EGXIndex] = Field(default_factory=list)
    signal: Optional[SignalView] = None


class PortfolioAnalytics(_Base):
    """Deterministic analytics for a snapshot at live prices (design §2). Every
    number here is injected verbatim into the Narrator prompt as ground truth."""

    total_value_egp: float = Field(..., ge=0)
    invested_egp: float = Field(..., ge=0)
    cash_egp: float = Field(..., ge=0)
    cash_drag_pct: float = Field(..., ge=0, le=100)

    holdings: list[HoldingAnalytics] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    hhi: float = Field(..., ge=0, le=1, description="Herfindahl concentration index")
    sector_exposure: dict[str, float] = Field(default_factory=dict)
    index_exposure: dict[str, float] = Field(default_factory=dict)

    portfolio_beta: Optional[float] = Field(None, description="vs EGX30")
    beta_is_proxy: bool = Field(
        False, description="True when ^CASE30 was unavailable and a constituent "
        "proxy basket was used (roadmap finding #3)"
    )
    annual_vol: Optional[float] = Field(None, ge=0)

    min_history_excluded: list[str] = Field(
        default_factory=list,
        description="Tickers dropped from risk math for insufficient price history",
    )
    price_asof: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Chat blocks (the visualization contract — design §9/§10)
# ---------------------------------------------------------------------------
# Engines compute these payloads; React renders them; the LLM never draws.
# Discriminated union on ``type``. ``is_hypothetical`` + ``scenario_id`` on the
# base fold the design's HypotheticalFrame wrapper into a flag any block honors.

class _BlockBase(_Base):
    is_hypothetical: bool = Field(
        False, description="Render in scenario styling (dashed/flask/watermark)"
    )
    scenario_id: Optional[int] = None


class AllocationSlice(_Base):
    label: str
    ticker: Optional[str] = None
    value_egp: float = Field(..., ge=0)
    weight_pct: float = Field(..., ge=0, le=100)
    is_cash: bool = False


class AllocationDonutData(_Base):
    slices: list[AllocationSlice] = Field(default_factory=list)
    total_egp: float = Field(..., ge=0)


class AllocationDonutBlock(_BlockBase):
    type: Literal["allocation_donut"] = "allocation_donut"
    title: Optional[str] = None
    data: AllocationDonutData


class TreemapNode(_Base):
    label: str
    ticker: Optional[str] = None
    sector: str
    value_egp: float = Field(..., ge=0)
    weight_pct: float = Field(..., ge=0, le=100)
    signal_tone: Optional[float] = Field(
        None, ge=-1, le=1, description="Blended agent signal for color (red→green)"
    )


class SectorTreemapData(_Base):
    nodes: list[TreemapNode] = Field(default_factory=list)


class SectorTreemapBlock(_BlockBase):
    type: Literal["sector_treemap"] = "sector_treemap"
    data: SectorTreemapData


class HoldingRow(_Base):
    ticker: str
    shares: float
    avg_cost: Optional[float] = None
    price: float
    market_value_egp: float
    weight_pct: float
    unrealized_pnl_egp: Optional[float] = None
    signal_label: Optional[SignalLabel] = None


class HoldingsTableBlock(_BlockBase):
    type: Literal["holdings_table"] = "holdings_table"
    rows: list[HoldingRow] = Field(default_factory=list)


class ExtractedPortfolioTableData(_Base):
    """The editable confirmation table — the human-in-the-loop safety gate."""

    holdings: list[PortfolioHolding] = Field(default_factory=list)
    cash_egp: float = Field(0.0, ge=0)
    total_value_egp: Optional[float] = Field(
        None, gt=0, description="User-stated total invested value (excl. cash), when given")
    unresolved_names: list[str] = Field(
        default_factory=list, description="User terms not matched to a ticker"
    )
    warnings: list[str] = Field(default_factory=list, description="e.g. weights sum > 100%")


class ExtractedPortfolioTableBlock(_BlockBase):
    type: Literal["extracted_portfolio_table"] = "extracted_portfolio_table"
    data: ExtractedPortfolioTableData


class BeforeAfterEntry(_Base):
    ticker: str
    before_pct: float = Field(..., ge=0, le=100)
    after_pct: float = Field(..., ge=0, le=100)
    delta_pct: float


class BeforeAfterData(_Base):
    entries: list[BeforeAfterEntry] = Field(default_factory=list)


class BeforeAfterBlock(_BlockBase):
    type: Literal["before_after"] = "before_after"
    data: BeforeAfterData


class ScenarioCompareData(_Base):
    reference: Literal["active", "baseline"] = "baseline"
    scenario_label: str = ""
    metric_deltas: dict[str, float] = Field(
        default_factory=dict, description="e.g. {'vol': -0.03, 'hhi': -0.15, 'cash_pct': +5}"
    )


class ScenarioCompareBlock(_BlockBase):
    type: Literal["scenario_compare"] = "scenario_compare"
    data: ScenarioCompareData


class RebalanceActionsData(_Base):
    actions: list[RebalanceAction] = Field(default_factory=list)
    est_total_cost_egp: float = Field(0.0, ge=0)
    est_turnover_pct: float = Field(0.0, ge=0)


class RebalanceActionsBlock(_BlockBase):
    type: Literal["rebalance_actions"] = "rebalance_actions"
    data: RebalanceActionsData


class RiskPanelData(_Base):
    hhi_before: Optional[float] = None
    hhi_after: Optional[float] = None
    vol_before: Optional[float] = None
    vol_after: Optional[float] = None
    beta_before: Optional[float] = None
    beta_after: Optional[float] = None
    max_position_before: Optional[float] = None
    max_position_after: Optional[float] = None
    cash_pct_before: Optional[float] = None
    cash_pct_after: Optional[float] = None
    beta_is_proxy: bool = False


class RiskPanelBlock(_BlockBase):
    type: Literal["risk_panel"] = "risk_panel"
    data: RiskPanelData


class PolicyFlagsBlock(_BlockBase):
    type: Literal["policy_flags"] = "policy_flags"
    flags: list[PolicyFlag] = Field(default_factory=list)


class SignalFreshnessBlock(_BlockBase):
    type: Literal["signal_freshness"] = "signal_freshness"
    signals: list[SignalView] = Field(default_factory=list)


ChatBlock = Annotated[
    Union[
        AllocationDonutBlock, SectorTreemapBlock, HoldingsTableBlock,
        ExtractedPortfolioTableBlock, BeforeAfterBlock, ScenarioCompareBlock,
        RebalanceActionsBlock, RiskPanelBlock, PolicyFlagsBlock,
        SignalFreshnessBlock,
    ],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# WebSocket protocol (design §9) — client → server and server → client
# ---------------------------------------------------------------------------

# Inbound
class UserMessageIn(_Base):
    type: Literal["user_message"] = "user_message"
    text: str
    language: Literal["en", "ar", "auto"] = "auto"


class WhatIfIn(_Base):
    """UI-built scenario (chip/control) — skips the interpreter LLM."""

    type: Literal["what_if"] = "what_if"
    patch: ScenarioPatch


class AdoptScenarioIn(_Base):
    type: Literal["adopt_scenario"] = "adopt_scenario"
    scenario_id: int


ClientEvent = Annotated[
    Union[UserMessageIn, WhatIfIn, AdoptScenarioIn],
    Field(discriminator="type"),
]


# Outbound
class StatusEvent(_Base):
    type: Literal["status"] = "status"
    stage: str  # extracting | signals | optimizing | ...
    detail: Optional[str] = None


class ClarificationEvent(_Base):
    type: Literal["clarification"] = "clarification"
    question: str
    missing: list[str] = Field(default_factory=list)


class ExtractionEvent(_Base):
    type: Literal["extraction"] = "extraction"
    blocks: list[ChatBlock] = Field(default_factory=list)


class PolicyUpdateEvent(_Base):
    type: Literal["policy_update"] = "policy_update"
    policy: InvestmentPolicy
    inferred_fields: list[str] = Field(default_factory=list)
    requires_confirm: bool = True


class PolicyFlagsEvent(_Base):
    type: Literal["policy_flags"] = "policy_flags"
    flags: list[PolicyFlag] = Field(default_factory=list)


class ScenarioCreatedEvent(_Base):
    type: Literal["scenario_created"] = "scenario_created"
    scenario: ScenarioRef


class AssistantMessageEvent(_Base):
    type: Literal["assistant_message"] = "assistant_message"
    text: str = ""
    scenario_id: Optional[int] = None
    blocks: list[ChatBlock] = Field(default_factory=list)


class DoneEvent(_Base):
    type: Literal["done"] = "done"
    proposal_id: Optional[int] = None


class ErrorEvent(_Base):
    type: Literal["error"] = "error"
    message: str
    recoverable: bool = True


ServerEvent = Annotated[
    Union[
        StatusEvent, ClarificationEvent, ExtractionEvent, PolicyUpdateEvent,
        PolicyFlagsEvent, ScenarioCreatedEvent, AssistantMessageEvent,
        DoneEvent, ErrorEvent,
    ],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Turn context (the orchestrator's working state — §5.2 disciplines)
# ---------------------------------------------------------------------------

class TurnContext(_Base):
    """The typed state object threaded through ``PortfolioCopilotService`` handlers
    for one message (design §5.2). NOT a wire model and NOT persisted directly —
    it is mutable working state; the durable record is the ``PortfolioWorkspace``
    and the ``pa_messages`` rows. ``events`` accumulates what gets streamed out."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    conversation_id: str
    user_id: Optional[str] = None
    message_id: Optional[int] = None
    message_text: str = ""
    language: Literal["en", "ar", "auto"] = "auto"

    intents: list[Intent] = Field(default_factory=list)
    workspace: Optional[PortfolioWorkspace] = None
    events: list[ServerEvent] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=_utcnow)

    def emit(self, event: ServerEvent) -> None:
        """Append an outbound event (the API layer streams ``events`` over WS)."""
        self.events.append(event)


__all__ = [
    # helpers / constants
    "normalize_ticker", "EGXIndex",
    # enums
    "TradeSide", "SignalLabel", "SignalSource", "HoldingSource", "Objective",
    "RiskTolerance", "Horizon", "SolverStatus", "FlagSeverity", "ScenarioStatus",
    "Intent",
    # holdings / snapshots
    "PortfolioHolding", "PortfolioSnapshot",
    # policy
    "InvestmentPolicy", "OptimizerParams", "PolicyFlag",
    # signals
    "SignalView",
    # proposal
    "RebalanceAction", "OptimizationProposal",
    # scenarios
    "AddCashOp", "RemoveCashOp", "ClosePositionOp", "ScalePositionOp",
    "SetPositionWeightOp", "ExcludeSectorOp", "ExcludeTickerOp",
    "OverridePolicyOp", "TargetRiskDeltaOp", "ScenarioOp", "ScenarioPatch",
    "PinnedInputSet", "Scenario",
    # workspace
    "PortfolioWorkspace", "ScenarioRef", "WorkspaceDigest",
    # analytics
    "HoldingAnalytics", "PortfolioAnalytics",
    # chat blocks
    "AllocationSlice", "AllocationDonutData", "AllocationDonutBlock",
    "TreemapNode", "SectorTreemapData", "SectorTreemapBlock",
    "HoldingRow", "HoldingsTableBlock",
    "ExtractedPortfolioTableData", "ExtractedPortfolioTableBlock",
    "BeforeAfterEntry", "BeforeAfterData", "BeforeAfterBlock",
    "ScenarioCompareData", "ScenarioCompareBlock",
    "RebalanceActionsData", "RebalanceActionsBlock",
    "RiskPanelData", "RiskPanelBlock",
    "PolicyFlagsBlock", "SignalFreshnessBlock", "ChatBlock",
    # ws protocol
    "UserMessageIn", "WhatIfIn", "AdoptScenarioIn", "ClientEvent",
    "StatusEvent", "ClarificationEvent", "ExtractionEvent", "PolicyUpdateEvent",
    "PolicyFlagsEvent", "ScenarioCreatedEvent", "AssistantMessageEvent",
    "DoneEvent", "ErrorEvent", "ServerEvent",
    # turn context
    "TurnContext",
]
