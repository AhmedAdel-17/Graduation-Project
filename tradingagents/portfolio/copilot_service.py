"""PortfolioCopilotService — the stateless turn handler (roadmap P3, design §5.2).

Wires the deterministic core (P1) and the LLM boundary adapters (P2) into one
service that turns a user message into a stream of events + persisted workspace
state. There is **no LangGraph** here (design §5.2): a turn is
``router → switch on intent → sequential calls into pure services``. The service
is *stateless per turn* — it reloads the whole ``PortfolioWorkspace`` from the
store each message, so any instance can pick up a conversation and killing the
process mid-conversation loses nothing.

Two event streams come out of a turn:

* **``ctx.events``** — user-facing ``schemas.ServerEvent``s the API streams to
  the browser over WS (status, extraction table, policy update, assistant
  message with chart blocks…).
* **domain trace events** — the audit/observability record (``events.PAEvent``)
  persisted to ``pa_events`` + published on ``pa:<conversation_id>`` (finding #1).

Disciplines retained from the dropped graph framing (design §5.2): a typed
``TurnContext`` threads through handlers; LLM calls are confined to the four
boundary adapters (this orchestrator never formats a prompt); every step emits a
domain trace event. The **confirmation gate** is enforced here: no optimization
runs against an unconfirmed snapshot or an unconfirmed policy *change*.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Sequence

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.portfolio import ENGINE_VERSION
from tradingagents.portfolio import scenarios as sc
from tradingagents.portfolio import schemas as s
from tradingagents.portfolio.analytics import compute_analytics
from tradingagents.portfolio.events import PAEvent, PortfolioEventEmitter
from tradingagents.portfolio.extraction import ExtractionResult, PortfolioExtractionAgent
from tradingagents.portfolio.narrator import AdvisorNarrator
from tradingagents.portfolio.optimizer import optimize as run_optimize
from tradingagents.portfolio.policy_compiler import compile_policy
from tradingagents.portfolio.router import IntentRouter
from tradingagents.portfolio.signals import SignalResolver
from tradingagents.portfolio.strategy import PortfolioStrategyAgent
from tradingagents.portfolio.whatif import WhatIfInterpreter
from tradingagents.portfolio.workspace_store import WorkspaceStore, get_workspace_store

logger = logging.getLogger("tradingagents.portfolio.copilot")

PriceProvider = Callable[[Sequence[str]], dict[str, float]]
#: ticker list -> daily-returns DataFrame (columns = tickers). Empty on failure.
ReturnsProvider = Callable[[Sequence[str]], "pd.DataFrame"]
#: ticker list -> (market-cap weight per ticker summing to 1, imputed tickers).
MarketCapProvider = Callable[[Sequence[str]], "tuple[dict[str, float], list[str]]"]
#: ticker list -> {ticker: {ratio_name: value}} for the evidence-based view engine.
RatiosProvider = Callable[[Sequence[str]], "dict[str, dict[str, float]]"]
#: ticker list -> {sector_code: sentiment in [-1,1]} for the view engine's sentiment leg.
SentimentProvider = Callable[[Sequence[str]], "dict[str, float]"]


def _default_price_provider(tickers: Sequence[str]) -> dict[str, float]:
    """Best-effort live prices via the market-data layer (P1)."""
    from tradingagents.portfolio.market_data import get_live_prices
    return get_live_prices(tickers)


def _default_returns_provider(tickers: Sequence[str]) -> "pd.DataFrame":
    """Best-effort daily-return history (≈2y) via the market-data layer.
    Empty DataFrame on failure → optimizer falls back to a diagonal prior."""
    from datetime import datetime, timezone
    from tradingagents.portfolio.market_data import get_return_history
    return get_return_history(tickers, datetime.now(timezone.utc).strftime("%Y-%m-%d"))


def _default_market_cap_provider(tickers: Sequence[str]) -> "tuple[dict[str, float], list[str]]":
    """Best-effort market-cap weights for the BL equilibrium prior (local CSVs)."""
    from tradingagents.portfolio.market_data import get_market_cap_weights
    return get_market_cap_weights(tickers)


def _default_ratios_provider(tickers: Sequence[str]) -> "dict[str, dict[str, float]]":
    """Local EGX fundamental ratios for the evidence-based view engine (offline)."""
    from tradingagents.portfolio.market_data import get_fundamental_ratios
    return get_fundamental_ratios(tickers)


def social_v2_sector_sentiment(
    tickers: Sequence[str], *, curr_date: Optional[str] = None, allow_pipeline: bool = False,
) -> dict[str, float]:
    """EGX sector sentiment from the social_v2 layer for the view engine's sentiment
    leg, keyed by the same sector codes the view engine uses (``sectors.ticker_sector``).

    **Cache-only by default** (``allow_pipeline=False``): reads only signals the
    social pipeline already cached (e.g. from a prior analyst run or the demo
    warm-up) — it never triggers a scrape inside an optimize turn. Returns ``{}``
    when nothing is cached, so the sentiment leg is simply inert until warmed.
    Set ``allow_pipeline=True`` (operator/offline) to force a live run. Per CLAUDE.md
    §8, EGX social is sparse, so this is the weakest, lowest-weighted view source."""
    from datetime import datetime, timezone
    try:
        from tradingagents.dataflows.social_v2 import cache as v2_cache
        from tradingagents.dataflows.social_v2 import signal_adapter as sa
        from tradingagents.dataflows.social_v2.sectors import ticker_sector
    except Exception:  # pragma: no cover — social_v2 import edge
        return {}
    curr_date = curr_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_sector: dict[str, list[float]] = {}
    for t in tickers:
        try:
            payload = v2_cache.signal_get(f"signal:{t.upper()}:{curr_date}:7")
            if payload is None and allow_pipeline:
                payload = sa.fetch_v2_signal(t, curr_date, use_cache=True)
            ss = (payload or {}).get("sector_sentiment") or {}
            if ss.get("status") == "SIGNAL" and ss.get("score") is not None:
                by_sector.setdefault(ticker_sector(t), []).append(float(ss["score"]))
        except Exception:  # pragma: no cover — per-ticker best-effort
            continue
    return {sec: sum(v) / len(v) for sec, v in by_sector.items() if v}


def _default_sentiment_provider(tickers: Sequence[str]) -> dict[str, float]:
    """Cache-only social_v2 sector sentiment (safe inside an optimize turn)."""
    return social_v2_sector_sentiment(tickers, allow_pipeline=False)


def _has_arabic(text: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in text)


# EN + Egyptian-Arabic cues that the user wants NEW names proposed, not just a
# reshuffle of current holdings (the opt-in "suggest opportunities" universe).
_NEW_OPP_CUES = (
    "buy", "new stock", "new name", "new position", "opportun", "suggest",
    "what can i buy", "what should i buy", "add a stock", "deploy", "diversify into",
    "اشتري", "أشتري", "اشترى", "جديد", "فرص", "فرصة", "اقتراح", "رشح", "أرشح",
    "استثمر", "أستثمر", "ضيف سهم", "أضيف سهم", "اشتري ايه", "أشتري إيه",
)


def _wants_new_opportunities(text: str) -> bool:
    """Deterministic opt-in detector for the candidate-universe mode (design
    decision: holdings-only by default, user can ask to surface new EGX names)."""
    low = (text or "").lower()
    return any(cue in low for cue in _NEW_OPP_CUES)


def _policy_is_uninformed(policy: s.InvestmentPolicy) -> bool:
    """True when the user has never stated any objective/risk/horizon, so the policy
    is still all-default. Drives the proactive completeness nudge on optimize —
    the assistant asks for the goal rather than silently optimizing on guesses."""
    quals = {"objective", "risk_tolerance", "horizon", "income_preference"}
    return (policy.version == 1 and not policy.confirmed_by_user
            and quals.issubset(set(policy.inferred_fields)) and not policy.source_spans)


# ---------------------------------------------------------------------------
# Chat-block builders (deterministic — engines compute, React renders, §9/§10)
# ---------------------------------------------------------------------------

def _signal_tone(view: Optional[s.SignalView]) -> Optional[float]:
    if view is None:
        return None
    return {s.SignalLabel.BUY: view.confidence, s.SignalLabel.SELL: -view.confidence,
            s.SignalLabel.HOLD: 0.0}[view.label]


def _allocation_donut(
    analytics: s.PortfolioAnalytics, *, is_hypothetical: bool = False,
    scenario_id: Optional[int] = None,
) -> s.AllocationDonutBlock:
    slices = [
        s.AllocationSlice(
            label=h.ticker, ticker=h.ticker, value_egp=h.market_value_egp,
            weight_pct=h.weight_pct, is_cash=False,
        )
        for h in analytics.holdings
    ]
    if analytics.cash_egp > 0:
        slices.append(s.AllocationSlice(
            label="Cash", value_egp=analytics.cash_egp,
            weight_pct=analytics.cash_drag_pct, is_cash=True,
        ))
    return s.AllocationDonutBlock(
        is_hypothetical=is_hypothetical, scenario_id=scenario_id,
        data=s.AllocationDonutData(slices=slices, total_egp=analytics.total_value_egp),
    )


def _sector_treemap(
    analytics: s.PortfolioAnalytics, *, is_hypothetical: bool = False,
    scenario_id: Optional[int] = None,
) -> s.SectorTreemapBlock:
    nodes = [
        s.TreemapNode(
            label=h.ticker, ticker=h.ticker, sector=h.sector,
            value_egp=h.market_value_egp, weight_pct=h.weight_pct,
            signal_tone=_signal_tone(h.signal),
        )
        for h in analytics.holdings
    ]
    return s.SectorTreemapBlock(
        is_hypothetical=is_hypothetical, scenario_id=scenario_id,
        data=s.SectorTreemapData(nodes=nodes),
    )


def _before_after(
    proposal: s.OptimizationProposal, *, is_hypothetical: bool = False,
    scenario_id: Optional[int] = None,
) -> s.BeforeAfterBlock:
    tickers = list(dict.fromkeys([*proposal.current_weights, *proposal.target_weights]))
    entries = []
    for t in tickers:
        before = float(proposal.current_weights.get(t, 0.0))
        after = float(proposal.target_weights.get(t, 0.0))
        entries.append(s.BeforeAfterEntry(
            ticker=t, before_pct=min(max(before, 0.0), 100.0),
            after_pct=min(max(after, 0.0), 100.0), delta_pct=after - before,
        ))
    return s.BeforeAfterBlock(
        is_hypothetical=is_hypothetical, scenario_id=scenario_id,
        data=s.BeforeAfterData(entries=entries),
    )


def _rebalance_actions(
    proposal: s.OptimizationProposal, *, is_hypothetical: bool = False,
    scenario_id: Optional[int] = None,
) -> s.RebalanceActionsBlock:
    return s.RebalanceActionsBlock(
        is_hypothetical=is_hypothetical, scenario_id=scenario_id,
        data=s.RebalanceActionsData(
            actions=list(proposal.actions),
            est_total_cost_egp=proposal.est_total_cost_egp,
            est_turnover_pct=proposal.est_turnover_pct,
        ),
    )


def _risk_panel(
    proposal: s.OptimizationProposal, analytics: s.PortfolioAnalytics, *,
    is_hypothetical: bool = False, scenario_id: Optional[int] = None,
) -> s.RiskPanelBlock:
    max_before = max(proposal.current_weights.values(), default=0.0)
    max_after = max(proposal.target_weights.values(), default=0.0)
    cash_after = max(0.0, 100.0 - sum(proposal.target_weights.values()))
    return s.RiskPanelBlock(
        is_hypothetical=is_hypothetical, scenario_id=scenario_id,
        data=s.RiskPanelData(
            hhi_before=proposal.hhi_before, hhi_after=proposal.hhi_after,
            vol_before=proposal.expected_vol_before, vol_after=proposal.expected_vol_after,
            beta_before=analytics.portfolio_beta,
            max_position_before=max_before, max_position_after=max_after,
            cash_pct_before=analytics.cash_drag_pct, cash_pct_after=cash_after,
            beta_is_proxy=analytics.beta_is_proxy,
        ),
    )


def _proposal_blocks(
    analytics: s.PortfolioAnalytics, proposal: s.OptimizationProposal,
    signals: dict[str, s.SignalView], *, is_hypothetical: bool = False,
    scenario_id: Optional[int] = None,
) -> list[s.ChatBlock]:
    blocks: list[s.ChatBlock] = [
        _allocation_donut(analytics, is_hypothetical=is_hypothetical, scenario_id=scenario_id),
        _sector_treemap(analytics, is_hypothetical=is_hypothetical, scenario_id=scenario_id),
        _before_after(proposal, is_hypothetical=is_hypothetical, scenario_id=scenario_id),
        _rebalance_actions(proposal, is_hypothetical=is_hypothetical, scenario_id=scenario_id),
        _risk_panel(proposal, analytics, is_hypothetical=is_hypothetical, scenario_id=scenario_id),
    ]
    if signals:
        blocks.append(s.SignalFreshnessBlock(
            is_hypothetical=is_hypothetical, scenario_id=scenario_id,
            signals=list(signals.values()),
        ))
    if proposal.policy_flags:
        blocks.append(s.PolicyFlagsBlock(
            is_hypothetical=is_hypothetical, scenario_id=scenario_id,
            flags=list(proposal.policy_flags),
        ))
    return blocks


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class PortfolioCopilotService:
    """Stateless per-turn orchestrator for the Portfolio Assistant."""

    def __init__(
        self,
        store: WorkspaceStore,
        *,
        router: Optional[IntentRouter] = None,
        extraction: Optional[PortfolioExtractionAgent] = None,
        strategy: Optional[PortfolioStrategyAgent] = None,
        whatif: Optional[WhatIfInterpreter] = None,
        narrator: Optional[AdvisorNarrator] = None,
        signal_resolver: Optional[SignalResolver] = None,
        price_provider: Optional[PriceProvider] = None,
        returns_provider: Optional[ReturnsProvider] = None,
        market_cap_provider: Optional[MarketCapProvider] = None,
        ratios_provider: Optional[RatiosProvider] = None,
        sentiment_provider: Optional[SentimentProvider] = None,
        config: Optional[dict[str, Any]] = None,
        enable_redis: bool = True,
    ) -> None:
        self.store = store
        self.config = config or DEFAULT_CONFIG
        self.router = router or IntentRouter(config=self.config)
        self.extraction = extraction or PortfolioExtractionAgent(config=self.config)
        self.strategy = strategy or PortfolioStrategyAgent(config=self.config)
        self.whatif = whatif or WhatIfInterpreter(config=self.config)
        self.narrator = narrator or AdvisorNarrator(config=self.config)
        self.signal_resolver = signal_resolver or SignalResolver(config=self.config)
        self.price_provider = price_provider or _default_price_provider
        # Risk-model inputs (injectable so golden-turn tests stay offline). The
        # real providers reach yfinance / local fundamentals CSVs.
        self.returns_provider = returns_provider or _default_returns_provider
        self.market_cap_provider = market_cap_provider or _default_market_cap_provider
        self.ratios_provider = ratios_provider or _default_ratios_provider
        self.sentiment_provider = sentiment_provider or _default_sentiment_provider
        self._enable_redis = enable_redis

    @classmethod
    def build(cls, *, config: Optional[dict[str, Any]] = None, **kwargs: Any) -> "PortfolioCopilotService":
        """Production wiring: real store + self-constructing boundary adapters."""
        return cls(get_workspace_store(), config=config, **kwargs)

    # --- public turn entry -------------------------------------------------
    def handle_turn(
        self, conversation_id: str, text: str, *, language: str = "auto",
    ) -> s.TurnContext:
        """Process one user message; return the populated ``TurnContext``."""
        workspace = self.store.load_workspace(conversation_id)
        if workspace is None:
            raise KeyError(f"unknown conversation: {conversation_id}")

        message_id = self.store.add_message(conversation_id, "user", text=text)
        emitter = self._emitter(conversation_id, message_id)
        lang = self._resolve_language(language, workspace, text)
        ctx = s.TurnContext(
            conversation_id=conversation_id, user_id=workspace.user_id,
            message_id=message_id, message_text=text, language=lang, workspace=workspace,
        )
        emitter.emit(PAEvent.TURN_START, payload={"text": text})

        try:
            intents = self.router.classify(text, workspace.digest())
            ctx.intents = sorted(intents, key=lambda i: i.value)
            emitter.emit(PAEvent.ROUTED, payload={"intents": [i.value for i in ctx.intents]})
            self._dispatch(ctx, emitter, lang)
        except Exception as exc:  # pragma: no cover — defensive turn boundary
            logger.exception("copilot turn failed for %s", conversation_id)
            ctx.emit(s.ErrorEvent(message=str(exc), recoverable=True))
            emitter.emit(PAEvent.ERROR, payload={"error": str(exc)})

        self._persist_assistant(ctx)
        emitter.emit(PAEvent.TURN_DONE, payload={"events": len(ctx.events)})
        return ctx

    def _dispatch(self, ctx: s.TurnContext, emitter: PortfolioEventEmitter, lang: str) -> None:
        intents = set(ctx.intents)
        # facts/objectives can co-occur with each other and run first
        if s.Intent.DESCRIBE_PORTFOLIO in intents:
            self._handle_describe(ctx, emitter, lang)
        if s.Intent.OBJECTIVE in intents:
            self._handle_objective(ctx, emitter, lang)
        # then exactly one primary action
        if s.Intent.WHAT_IF in intents:
            self._handle_what_if(ctx, emitter, lang)
        elif s.Intent.ADOPT_SCENARIO in intents:
            self._adopt(ctx, emitter, lang, scenario_id=None)
        elif s.Intent.OPTIMIZE in intents:
            self._handle_optimize(ctx, emitter, lang)
        elif s.Intent.FOLLOW_UP_QA in intents:
            self._handle_qa(ctx, emitter, lang)
        elif not (s.Intent.DESCRIBE_PORTFOLIO in intents or s.Intent.OBJECTIVE in intents):
            self._handle_off_topic(ctx, emitter, lang)

    # --- direct (non-routed) operations the API layer calls ----------------
    def confirm_snapshot(
        self, conversation_id: str, snapshot: s.PortfolioSnapshot, *, language: str = "auto",
    ) -> s.TurnContext:
        """Persist the user-confirmed/edited extraction table as the baseline."""
        workspace = self.store.load_workspace(conversation_id)
        if workspace is None:
            raise KeyError(conversation_id)
        emitter = self._emitter(conversation_id)
        lang = self._resolve_language(language, workspace, "")
        ctx = s.TurnContext(conversation_id=conversation_id, user_id=workspace.user_id,
                            language=lang, workspace=workspace)
        confirmed = snapshot.model_copy(update={
            "conversation_id": conversation_id, "confirmed_by_user": True,
            "holdings": [h.model_copy(update={"source": s.HoldingSource.CONFIRMED})
                         for h in snapshot.holdings],
        })
        confirmed = self._reconcile_shares(confirmed, emitter)
        sid = self.store.save_baseline_snapshot(conversation_id, confirmed)
        emitter.emit(PAEvent.SNAPSHOT_CONFIRMED,
                     payload={"snapshot_id": sid, "holdings": len(confirmed.holdings)})
        msg = ("Portfolio confirmed — you can now ask me to optimize it or explore a what-if."
               if lang == "en" else
               "تم تأكيد المحفظة — تقدر تطلب مني أحسّن توزيعها أو نجرّب سيناريو افتراضي.")
        ctx.emit(s.AssistantMessageEvent(text=msg))
        self._persist_assistant(ctx)
        return ctx

    def update_policy(
        self, conversation_id: str, *, updates: Optional[dict[str, Any]] = None,
        confirm: bool = True, language: str = "auto",
    ) -> s.InvestmentPolicy:
        """Apply a manual policy edit from the UI panel (bumps version, confirms)."""
        workspace = self.store.load_workspace(conversation_id)
        if workspace is None:
            raise KeyError(conversation_id)
        data = workspace.policy.model_dump()
        data.update(updates or {})
        data["version"] = workspace.policy.version + 1
        data["confirmed_by_user"] = confirm
        policy = s.InvestmentPolicy.model_validate(data)
        self.store.save_policy(conversation_id, policy)
        emitter = self._emitter(conversation_id)
        emitter.emit(PAEvent.POLICY_INFERRED,
                     payload={"version": policy.version, "confirmed": confirm, "manual": True})
        return policy

    def adopt_scenario(
        self, conversation_id: str, scenario_id: int, *, language: str = "auto",
    ) -> s.TurnContext:
        """Promote a scenario to the new baseline (API + ``adopt`` intent path)."""
        workspace = self.store.load_workspace(conversation_id)
        if workspace is None:
            raise KeyError(conversation_id)
        emitter = self._emitter(conversation_id)
        lang = self._resolve_language(language, workspace, "")
        ctx = s.TurnContext(conversation_id=conversation_id, user_id=workspace.user_id,
                            language=lang, workspace=workspace)
        self._adopt(ctx, emitter, lang, scenario_id=scenario_id)
        self._persist_assistant(ctx)
        return ctx

    def apply_what_if_patch(
        self, conversation_id: str, patch: s.ScenarioPatch, *, language: str = "auto",
    ) -> s.TurnContext:
        """Run a UI-built scenario patch (skips the interpreter LLM)."""
        workspace = self.store.load_workspace(conversation_id)
        if workspace is None:
            raise KeyError(conversation_id)
        emitter = self._emitter(conversation_id)
        lang = self._resolve_language(language, workspace, patch.label)
        ctx = s.TurnContext(conversation_id=conversation_id, user_id=workspace.user_id,
                            language=lang, workspace=workspace)
        if not self._require_confirmed_baseline(ctx, emitter, lang):
            self._persist_assistant(ctx)
            return ctx
        self._run_what_if(ctx, emitter, lang, patch)
        self._persist_assistant(ctx)
        return ctx

    # --- intent handlers ---------------------------------------------------
    def _handle_describe(self, ctx: s.TurnContext, emitter: PortfolioEventEmitter, lang: str) -> None:
        result = self.extraction.extract(ctx.message_text, conversation_id=ctx.conversation_id, language=lang)
        result = self._price_extracted_holdings(result, lang)
        emitter.emit(PAEvent.EXTRACTED, payload={
            "holdings": len(result.snapshot.holdings),
            "unresolved": result.block.data.unresolved_names,
        })
        ctx.emit(s.ExtractionEvent(blocks=[result.block]))
        if result.clarification is not None:
            ctx.emit(result.clarification)
            emitter.emit(PAEvent.CLARIFICATION, payload={"missing": result.clarification.missing})

    def _handle_objective(self, ctx: s.TurnContext, emitter: PortfolioEventEmitter, lang: str) -> None:
        ws = ctx.workspace
        policy = self.strategy.infer_policy(ctx.message_text, current_policy=ws.policy)
        self.store.save_policy(ctx.conversation_id, policy, source_message_id=ctx.message_id)
        emitter.emit(PAEvent.POLICY_INFERRED,
                     payload={"version": policy.version, "inferred": policy.inferred_fields})
        ctx.emit(s.PolicyUpdateEvent(
            policy=policy, inferred_fields=policy.inferred_fields, requires_confirm=True))

    def _handle_optimize(self, ctx: s.TurnContext, emitter: PortfolioEventEmitter, lang: str) -> None:
        ws = ctx.workspace
        if not self._require_confirmed_baseline(ctx, emitter, lang):
            return
        if ws.policy.version > 1 and not ws.policy.confirmed_by_user:
            emitter.emit(PAEvent.GATE_BLOCKED, payload={"reason": "policy_unconfirmed"})
            ctx.emit(s.AssistantMessageEvent(text=self._t(
                lang,
                "Please confirm the updated investment profile before I re-optimize.",
                "من فضلك أكّد ملف الاستثمار المُحدَّث قبل ما أعيد التحسين.")))
            return
        include_candidates = _wants_new_opportunities(ctx.message_text)
        try:
            analytics, proposal, signals = self._optimize(
                ws.baseline, ws.policy, emitter=emitter,
                snapshot_id=ws.baseline.snapshot_id, include_candidates=include_candidates)
        except ValueError as exc:
            self._emit_optimize_error(ctx, emitter, lang, exc)
            return
        proposal.conversation_id = ctx.conversation_id
        # Goal feasibility: if the user stated a numeric target, judge it against the
        # proposal's model-view return/vol and attach the verdict as a policy flag.
        goal_text = self._apply_goal_feasibility(ws.policy, analytics, proposal, emitter, lang)
        proposal_id = self.store.save_proposal(proposal)
        self.store.set_last_proposal_id(ctx.conversation_id, proposal_id)
        emitter.emit(PAEvent.PROPOSAL_READY,
                     payload={"proposal_id": proposal_id, "solver": proposal.solver_status.value})
        blocks = _proposal_blocks(analytics, proposal, signals)
        narration = self.narrator.narrate(analytics=analytics, proposal=proposal, blocks=blocks, language=lang)
        text = (narration.text + ("\n\n" + goal_text if goal_text else "")).strip()
        # Completeness nudge: if the user never stated a goal, the proposal ran on
        # disclosed defaults — ask one consolidated question so the next run is tailored.
        if _policy_is_uninformed(ws.policy):
            emitter.emit(PAEvent.CLARIFICATION, payload={"missing": ["objective", "horizon", "risk_tolerance"]})
            text = self._t(
                lang,
                ("Heads up: I used a default profile (balanced, medium risk, 1–3 years) because you "
                 "haven't told me your goal yet. For a plan tailored to you, tell me: (1) your goal "
                 "(grow capital, income, or preserve), (2) your time horizon, and (3) how much risk "
                 "you're comfortable with.\n\n"),
                ("تنبيه: استخدمت ملفًّا افتراضيًّا (متوازن، مخاطرة متوسطة، 1–3 سنين) لأنك لسه ما قلتش هدفك. "
                 "عشان أعملك خطة على مقاسك، قوللي: (1) هدفك (تنمية رأس المال، دخل، أو الحفاظ على المال)، "
                 "(2) المدة الزمنية، (3) مستوى المخاطرة المريح ليك.\n\n")) + text
        ctx.emit(s.AssistantMessageEvent(text=text, blocks=narration.blocks))

    def _handle_what_if(self, ctx: s.TurnContext, emitter: PortfolioEventEmitter, lang: str) -> None:
        ws = ctx.workspace
        if not self._require_confirmed_baseline(ctx, emitter, lang):
            return
        res = self.whatif.interpret(ctx.message_text, ws.digest(), language=lang)
        if res.patch is None:
            if res.clarification is not None:
                ctx.emit(res.clarification)
                emitter.emit(PAEvent.CLARIFICATION, payload={"missing": res.clarification.missing})
            return
        self._run_what_if(ctx, emitter, lang, res.patch)

    def _handle_qa(self, ctx: s.TurnContext, emitter: PortfolioEventEmitter, lang: str) -> None:
        ws = ctx.workspace
        proposal = self.store.get_proposal(ws.last_proposal_id) if ws.last_proposal_id else None
        narration = self.narrator.narrate(proposal=proposal, language=lang)
        emitter.emit(PAEvent.QA, payload={"proposal_id": ws.last_proposal_id})
        text = narration.text or self._t(
            lang, "I can describe your portfolio, optimize it, or run a what-if — what would you like?",
            "أقدر أوصّف محفظتك أو أحسّنها أو نجرّب سيناريو — تحب نعمل إيه؟")
        ctx.emit(s.AssistantMessageEvent(text=text))

    def _handle_off_topic(self, ctx: s.TurnContext, emitter: PortfolioEventEmitter, lang: str) -> None:
        emitter.emit(PAEvent.OFF_TOPIC)
        ctx.emit(s.AssistantMessageEvent(text=self._t(
            lang,
            "I'm a decision-support assistant for your EGX portfolio — I can analyze "
            "holdings, propose a rebalance, or explore a what-if. How can I help?",
            "أنا مساعد لدعم القرار في محفظتك بالبورصة المصرية — أقدر أحلل أسهمك أو أقترح "
            "إعادة توازن أو نجرّب سيناريو. أساعدك بإيه؟")))

    # --- shared sub-flows --------------------------------------------------
    def _run_what_if(
        self, ctx: s.TurnContext, emitter: PortfolioEventEmitter, lang: str, patch: s.ScenarioPatch,
    ) -> None:
        ws = ctx.workspace
        base_scn = ws.active_scenario() if patch.reference == "active" else None
        if base_scn is not None:
            base_snap, base_pol = base_scn.derived_snapshot, base_scn.derived_policy or ws.policy
            parent_id, base_snapshot_id = base_scn.scenario_id, base_scn.base_snapshot_id
        else:
            base_snap, base_pol = ws.baseline, ws.policy
            parent_id, base_snapshot_id = None, ws.baseline.snapshot_id

        try:
            prices = self._prices(base_snap.tickers)
            scen = sc.build_scenario(
                patch, base_snap, base_pol, prices=prices,
                parent_scenario_id=parent_id, base_snapshot_id=base_snapshot_id)
            scen_id = self.store.save_scenario(ctx.conversation_id, scen)
            scen.scenario_id = scen_id
            emitter.emit(PAEvent.SCENARIO_FORKED, payload={"scenario_id": scen_id, "label": scen.label})
            ctx.emit(s.ScenarioCreatedEvent(scenario=s.ScenarioRef(
                scenario_id=scen_id, label=scen.label, parent_scenario_id=parent_id)))

            scen_pol = scen.derived_policy or base_pol
            directives = sc.extract_directives(patch)
            # reference proposal (same inputs, not persisted) for an honest diff
            _, ref_prop, _ = self._optimize(base_snap, base_pol, emitter=emitter,
                                            snapshot_id=base_snapshot_id or 0, emit_trace=False)
            scen_analytics, scen_prop, scen_signals = self._optimize(
                scen.derived_snapshot, scen_pol, emitter=emitter, scenario_id=scen_id,
                target_vol_mult=directives.get("target_vol_mult"),
                reference_vol=ref_prop.expected_vol_after)
        except ValueError as exc:
            self._emit_optimize_error(ctx, emitter, lang, exc)
            return

        scen_prop.conversation_id = ctx.conversation_id
        proposal_id = self.store.save_proposal(scen_prop)
        self.store.set_last_proposal_id(ctx.conversation_id, proposal_id)
        self.store.set_active_ref(ctx.conversation_id, str(scen_id))
        emitter.emit(PAEvent.PROPOSAL_READY,
                     payload={"proposal_id": proposal_id, "scenario_id": scen_id})

        compare = sc.diff_proposals(ref_prop, scen_prop, reference=patch.reference, scenario_label=scen.label)
        blocks: list[s.ChatBlock] = [s.ScenarioCompareBlock(
            is_hypothetical=True, scenario_id=scen_id, data=compare)]
        blocks.extend(_proposal_blocks(scen_analytics, scen_prop, scen_signals,
                                       is_hypothetical=True, scenario_id=scen_id))
        narration = self.narrator.narrate(proposal=scen_prop, diff=compare, blocks=blocks, language=lang)
        ctx.emit(s.AssistantMessageEvent(text=narration.text, blocks=narration.blocks, scenario_id=scen_id))

    def _adopt(
        self, ctx: s.TurnContext, emitter: PortfolioEventEmitter, lang: str,
        *, scenario_id: Optional[int],
    ) -> None:
        ws = ctx.workspace
        if scenario_id is None:
            active = ws.active_scenario()
            scenario_id = active.scenario_id if active else None
        if scenario_id is None:
            ctx.emit(s.AssistantMessageEvent(text=self._t(
                lang, "There's no active scenario to adopt yet — try a what-if first.",
                "مفيش سيناريو نشط لاعتماده — جرّب سيناريو افتراضي الأول.")))
            return
        scen = self.store.get_scenario(scenario_id)
        if scen is None:
            ctx.emit(s.AssistantMessageEvent(text=self._t(
                lang, "I couldn't find that scenario.", "مش لاقي السيناريو ده.")))
            return
        next_version = (ws.baseline.version + 1) if ws.baseline else 1
        new_baseline = sc.promote_to_baseline(scen, version=next_version)
        new_baseline.conversation_id = ctx.conversation_id
        new_id = self.store.save_baseline_snapshot(ctx.conversation_id, new_baseline)
        self.store.update_scenario_status(scenario_id, s.ScenarioStatus.PROMOTED)
        for sibling in self.store.get_scenarios(ctx.conversation_id, statuses=[s.ScenarioStatus.ACTIVE]):
            if sibling.scenario_id != scenario_id:
                self.store.update_scenario_status(sibling.scenario_id, s.ScenarioStatus.DISCARDED)
        self.store.set_active_ref(ctx.conversation_id, "baseline")
        emitter.emit(PAEvent.SCENARIO_ADOPTED,
                     payload={"scenario_id": scenario_id, "new_snapshot_id": new_id, "version": next_version})
        ctx.emit(s.AssistantMessageEvent(text=self._t(
            lang,
            f"Adopted '{scen.label}' as your new baseline (v{next_version}).",
            f"تم اعتماد '{scen.label}' كأساس جديد للمحفظة (نسخة {next_version}).")))

    def _optimize(
        self, snapshot: s.PortfolioSnapshot, policy: s.InvestmentPolicy, *,
        emitter: PortfolioEventEmitter, snapshot_id: Optional[int] = None,
        scenario_id: Optional[int] = None, emit_trace: bool = True,
        target_vol_mult: Optional[float] = None, reference_vol: Optional[float] = None,
        include_candidates: bool = False,
    ) -> tuple[s.PortfolioAnalytics, s.OptimizationProposal, dict[str, s.SignalView]]:
        """Run the full deterministic optimize chain on one (snapshot, policy).

        Returns ``(analytics, proposal, signals)``. Raises ``ValueError`` when the
        snapshot can't be priced/anchored (caller turns it into a clarification).

        ``include_candidates`` opts the universe into EGX30 names the user does
        not yet hold, so the optimizer can propose *new* positions (the
        "suggest opportunities" mode). The risk model (Ledoit-Wolf covariance)
        and the CAPM-equilibrium prior (market-cap weights) are built over the
        whole universe; both degrade gracefully to the prior behaviour offline.
        """
        tickers = snapshot.tickers
        if not tickers:
            raise ValueError("portfolio has no holdings to optimize")

        candidates = self._candidate_universe(tickers) if include_candidates else []
        prices = self._prices(list(dict.fromkeys([*tickers, *candidates])))
        missing = [t for t in tickers if t not in prices]
        if missing:
            raise ValueError(f"no live price for: {missing}")
        priced_candidates = [c for c in candidates if c in prices]
        universe = list(dict.fromkeys([*tickers, *priced_candidates]))

        signals = self.signal_resolver.resolve(universe)
        if emit_trace:
            emitter.emit(PAEvent.SIGNALS_RESOLVED, payload={
                t: {"label": v.label.value, "source": v.source.value, "stale": v.is_stale}
                for t, v in signals.items()})

        # --- risk-model inputs: real Ledoit-Wolf Σ + CAPM-equilibrium prior ---
        covariance, returns, excluded = self._covariance(universe)
        market_weights, imputed_caps = self._market_weights(universe)

        # --- evidence-based views (value/quality/momentum + fresh agent signal) ---
        views, view_provenance = self._build_views(universe, returns, signals)
        if emit_trace:
            emitter.emit(PAEvent.SIGNALS_RESOLVED, payload={
                "_risk_model": {
                    "covariance": "ledoit_wolf" if covariance is not None else "diagonal_fallback",
                    "prior": "market_cap_equilibrium" if market_weights else "current_weights",
                    "views": {t: round(sc, 3) for t, (sc, _c) in views.items()},
                    "candidates": priced_candidates, "cov_excluded": excluded,
                    "cap_imputed": imputed_caps}})

        analytics = compute_analytics(snapshot, prices, signals=signals, returns=returns)

        params, flags = compile_policy(policy, analytics)
        if target_vol_mult is not None and reference_vol:
            params = params.model_copy(update={"vol_target": reference_vol * target_vol_mult})
        if emit_trace:
            emitter.emit(PAEvent.POLICY_COMPILED, payload={
                "compiler_version": params.compiler_version,
                "flags": [f.code for f in flags]})

        proposal = run_optimize(
            snapshot, prices, params, signals=signals,
            views=views or None, view_provenance=view_provenance or None,
            covariance=covariance, market_weights=market_weights or None,
            candidate_tickers=priced_candidates or None,
            conversation_id=None, snapshot_id=snapshot_id, scenario_id=scenario_id,
            policy_flags=flags)
        return analytics, proposal, signals

    def _build_views(
        self, universe: Sequence[str], returns, signals: dict[str, s.SignalView],
    ) -> tuple[dict[str, tuple[float, float]], dict[str, Any]]:
        """Build evidence-based BL views (views.py) over ``universe``. Returns
        ``(views_map, provenance)`` where views_map is ticker→(score, confidence)
        and provenance is the per-ticker evidence trail for the audit/narrator.
        Best-effort: any failure yields empty views (optimizer uses pure prior)."""
        from tradingagents.portfolio.views import build_views
        try:
            ratios = self.ratios_provider(list(universe))
        except Exception as exc:  # pragma: no cover — provider best-effort
            logger.warning("ratios fetch failed: %s", exc)
            ratios = {}
        try:
            sector_sentiment = self.sentiment_provider(list(universe))
        except Exception as exc:  # pragma: no cover — provider best-effort
            logger.warning("sentiment fetch failed: %s", exc)
            sector_sentiment = {}
        tviews = build_views(universe, ratios=ratios, returns=returns,
                             agent_signals=signals, sector_sentiment=sector_sentiment or None)
        views_map = {t: v.as_tuple() for t, v in tviews.items()}
        provenance = {t: v.provenance() for t, v in tviews.items()}
        return views_map, provenance

    def _apply_goal_feasibility(
        self, policy: s.InvestmentPolicy, analytics: s.PortfolioAnalytics,
        proposal: s.OptimizationProposal, emitter: PortfolioEventEmitter, lang: str,
    ) -> str:
        """Assess the policy's numeric goal vs the proposal's model-view outlook,
        attach a GOAL_FEASIBILITY policy flag, and return a localized one-liner
        (empty when no numeric goal was stated)."""
        from tradingagents.portfolio.goal import assess_goal_feasibility, goal_flag
        fb = assess_goal_feasibility(
            policy, current_value_egp=analytics.total_value_egp,
            expected_return_annual=proposal.expected_return_view_annual,
            expected_vol_annual=proposal.expected_vol_after)
        if not fb.has_goal:
            return ""
        flag = goal_flag(fb)
        if flag is not None:
            proposal.policy_flags = list(proposal.policy_flags) + [flag]
        emitter.emit(PAEvent.POLICY_COMPILED, payload={
            "goal_feasibility": fb.verdict,
            "required_return_annual": fb.required_return_annual,
            "expected_return_annual": fb.expected_return_annual})
        return fb.message_ar if str(lang).lower().startswith("ar") else fb.message_en

    def _candidate_universe(self, held: Sequence[str]) -> list[str]:
        """EGX30 constituents the user does not yet hold — the candidate buy set
        for the 'suggest opportunities' mode (overridable via EGX30_CONSTITUENTS)."""
        from tradingagents.sentiment.taxonomy import IndexEnum, members_of_index
        held_set = {t.upper() for t in held}
        out: list[str] = []
        for sym in sorted(members_of_index(IndexEnum.EGX30)):
            cand = s.normalize_ticker(sym)
            if cand not in held_set:
                out.append(cand)
        return out

    def _covariance(self, universe: Sequence[str]):
        """Best-effort Ledoit-Wolf covariance over ``universe``. Returns
        ``(covariance_df_or_None, returns_df, excluded_tickers)``; never raises."""
        from tradingagents.portfolio.market_data import build_covariance
        try:
            returns = self.returns_provider(list(universe))
        except Exception as exc:  # pragma: no cover — provider best-effort
            logger.warning("returns fetch failed: %s", exc)
            return None, None, list(universe)
        if returns is None or getattr(returns, "empty", True):
            return None, None, list(universe)
        cov, excluded, _shrink = build_covariance(returns, min_history_days=60)
        return cov, returns, excluded

    def _market_weights(self, universe: Sequence[str]) -> tuple[dict[str, float], list[str]]:
        """Best-effort market-cap equilibrium weights over ``universe``."""
        try:
            return self.market_cap_provider(list(universe))
        except Exception as exc:  # pragma: no cover — provider best-effort
            logger.warning("market-cap fetch failed: %s", exc)
            return {}, list(universe)

    # --- read-only helpers the API layer calls -----------------------------
    def compute_snapshot_analytics(
        self, snapshot: s.PortfolioSnapshot, *,
        signals: Optional[dict[str, s.SignalView]] = None,
    ) -> s.PortfolioAnalytics:
        """Recompute analytics for a snapshot at live prices (GET .../analytics).

        Uses the injected price provider so it stays offline-testable. Raises
        ``ValueError`` when a holding cannot be priced (the route maps to 503).
        """
        tickers = snapshot.tickers
        prices = self._prices(tickers)
        missing = [t for t in tickers if t not in prices]
        if missing:
            raise ValueError(f"no live price for: {missing}")
        return compute_analytics(snapshot, prices, signals=signals)

    # --- gates / helpers ---------------------------------------------------
    def _require_confirmed_baseline(
        self, ctx: s.TurnContext, emitter: PortfolioEventEmitter, lang: str,
    ) -> bool:
        ws = ctx.workspace
        if ws.baseline is not None and ws.baseline.confirmed_by_user:
            return True
        emitter.emit(PAEvent.GATE_BLOCKED, payload={"reason": "snapshot_unconfirmed"})
        ctx.emit(s.AssistantMessageEvent(text=self._t(
            lang,
            "First tell me what you hold (and confirm the table) — then I can optimize "
            "or run a what-if.",
            "الأول قوللي محفظتك فيها إيه (وأكّد الجدول) — بعدها أقدر أحسّن أو نجرّب سيناريو.")))
        return False

    def _emit_optimize_error(
        self, ctx: s.TurnContext, emitter: PortfolioEventEmitter, lang: str, exc: Exception,
    ) -> None:
        emitter.emit(PAEvent.ERROR, payload={"error": str(exc), "stage": "optimize"})
        ctx.emit(s.AssistantMessageEvent(text=self._t(
            lang,
            "I couldn't price every holding to optimize right now — please re-check the "
            "tickers and share counts.",
            "مش قادر أسعّر كل الأسهم دلوقتي عشان أحسّن — راجع الرموز وعدد الأسهم من فضلك.")))

    def _price_extracted_holdings(self, result: ExtractionResult, lang: str) -> ExtractionResult:
        """Fill ``shares`` + ``avg_cost`` on the extraction confirmation table from
        live prices, so a weight-described portfolio ("60% Telecom Egypt … my
        portfolio is 50k") shows derived share counts and a per-share price the
        user can sanity-check *before* confirming — not bare em-dashes.

        ``avg_cost`` is set to the most recent market price as a proxy when the
        user did not state a cost basis (the column then reads as "current price").
        ``shares`` = weight%/100 × stated total ÷ price. Best-effort: any pricing
        failure leaves that row untouched (still confirmable; reconciled at confirm)."""
        snapshot = result.snapshot
        holdings = snapshot.holdings
        total = snapshot.total_value_egp
        need_price = [h for h in holdings if h.avg_cost is None or (h.shares is None and h.weight_pct is not None)]
        if not holdings or not need_price:
            return result
        try:
            prices = self._prices([h.ticker for h in need_price])
        except Exception as exc:  # pragma: no cover — pricing best-effort
            logger.warning("extraction pricing skipped (pricing failed): %s", exc)
            return result

        new_holdings: list[s.PortfolioHolding] = []
        unpriced: list[str] = []
        priced = 0
        for h in holdings:
            px = prices.get(h.ticker)
            update: dict[str, Any] = {}
            if px and px > 0:
                if h.avg_cost is None:
                    update["avg_cost"] = round(px, 2)
                if h.shares is None and h.weight_pct is not None and total:
                    update["shares"] = float(round(h.weight_pct / 100.0 * total / px))
            elif h.shares is None:
                unpriced.append(h.ticker)
            if update:
                priced += 1
                new_holdings.append(h.model_copy(update=update))
            else:
                new_holdings.append(h)

        if not priced and not unpriced:
            return result

        warnings = list(result.block.data.warnings)
        if unpriced:
            warnings.append(self._t(
                lang,
                "Couldn't fetch a live price for: " + ", ".join(unpriced) + " — shares left blank.",
                "متعذّر جلب سعر حالي لـ: " + "، ".join(unpriced) + " — عدد الأسهم متروك فاضي."))
        new_snapshot = snapshot.model_copy(update={"holdings": new_holdings})
        new_data = result.block.data.model_copy(update={"holdings": new_holdings, "warnings": warnings})
        new_block = result.block.model_copy(update={"data": new_data})
        return result.model_copy(update={"snapshot": new_snapshot, "block": new_block})

    def _reconcile_shares(
        self, snapshot: s.PortfolioSnapshot, emitter: PortfolioEventEmitter,
    ) -> s.PortfolioSnapshot:
        """Turn weight-only holdings into integer share counts using the stated
        total value + live prices, so a percentage-described portfolio (e.g.
        "60% Telecom Egypt … my portfolio is 50k") can actually be valued and
        optimized. No-op when there's no stated total or nothing to reconcile."""
        total = snapshot.total_value_egp
        needs = [h for h in snapshot.holdings if h.shares is None and h.weight_pct is not None]
        if not total or not needs:
            return snapshot
        try:
            prices = self._prices([h.ticker for h in needs])
        except Exception as exc:  # pragma: no cover — pricing best-effort
            logger.warning("share reconciliation skipped (pricing failed): %s", exc)
            return snapshot
        reconciled = 0
        new_holdings = []
        for h in snapshot.holdings:
            px = prices.get(h.ticker)
            if h.shares is None and h.weight_pct is not None and px and px > 0:
                shares = round(h.weight_pct / 100.0 * total / px)
                new_holdings.append(h.model_copy(update={"shares": float(shares)}))
                reconciled += 1
            else:
                new_holdings.append(h)
        if reconciled:
            emitter.emit(PAEvent.SNAPSHOT_CONFIRMED,
                         payload={"reconciled_holdings": reconciled, "from_total_value_egp": total})
        return snapshot.model_copy(update={"holdings": new_holdings})

    def _prices(self, tickers: Sequence[str]) -> dict[str, float]:
        raw = self.price_provider(list(tickers)) or {}
        return {k: float(v) for k, v in raw.items()}

    def _persist_assistant(self, ctx: s.TurnContext) -> None:
        """Persist the assistant's turn (text + blocks) for thread reconstruction."""
        text = ""
        blocks: list[s.ChatBlock] = []
        scenario_id: Optional[int] = None
        for ev in ctx.events:
            if isinstance(ev, s.AssistantMessageEvent):
                text = ev.text or text
                blocks = list(ev.blocks) or blocks
                scenario_id = ev.scenario_id if ev.scenario_id is not None else scenario_id
            elif isinstance(ev, s.ExtractionEvent) and not blocks:
                blocks = list(ev.blocks)
            elif isinstance(ev, s.ClarificationEvent) and not text:
                text = ev.question
        if text or blocks:
            self.store.add_message(
                ctx.conversation_id, "assistant",
                text=text or None, blocks=blocks or None, scenario_id=scenario_id)

    def _emitter(self, conversation_id: str, message_id: Optional[int] = None) -> PortfolioEventEmitter:
        return PortfolioEventEmitter(
            conversation_id, store=self.store, message_id=message_id,
            enable_redis=self._enable_redis)

    def _resolve_language(self, language: str, workspace: s.PortfolioWorkspace, text: str) -> str:
        lang = language if language in ("en", "ar") else (
            workspace.language if workspace.language in ("en", "ar") else "auto")
        if lang == "auto":
            lang = "ar" if _has_arabic(text) else "en"
        return lang

    @staticmethod
    def _t(lang: str, en: str, ar: str) -> str:
        return ar if lang == "ar" else en


__all__ = ["PortfolioCopilotService", "ENGINE_VERSION"]
