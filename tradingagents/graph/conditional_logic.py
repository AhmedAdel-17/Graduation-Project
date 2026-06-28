# TradingAgents/graph/conditional_logic.py

from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.dataflows.config import get_config

# =============================================================================
# EGX-Enhanced Conditional Logic
# =============================================================================
# Controls graph flow with EGX-specific considerations:
# - Risk veto stops execution
# - Weak data reduces downstream confidence  
# - Partial conviction propagates correctly
# - No silent overrides
# =============================================================================


class ConditionalLogic:
    """Handles conditional logic for determining graph flow.

    DEAD-CODE NOTE (verified 2026-06-18): only the four ``should_continue_<analyst>``
    tool-loop routers are wired into the compiled graph (``graph/setup.py`` /
    ``ablation/runner.py``). The debate/risk-routing helpers below
    (``should_continue_debate``, ``should_continue_risk_analysis``,
    ``should_execute_after_risk``, ``calculate_conviction_strength``,
    ``apply_confidence_adjustments``) have **no runtime callers** — the debate is now
    a linear ``Bull → Bear → Research Manager`` chain and the risk stage uses the
    deterministic scorer + merged debator. They are retained for reference / external
    callers only. Do not assume they affect graph behaviour."""

    def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds
        
        # Get config for EGX-specific logic
        config = get_config()
        self.is_egx = config.get("target_market", "US") == "EGX"

    def should_continue_market(self, state: AgentState):
        """Determine if market analysis should continue."""
        msgs = state.get("market_messages") or []
        if msgs and msgs[-1].tool_calls:
            return "tools_market"
        return "Msg Clear Market"

    def should_continue_social(self, state: AgentState):
        """Determine if social media analysis should continue."""
        msgs = state.get("social_messages") or []
        if msgs and msgs[-1].tool_calls:
            return "tools_social"
        return "Msg Clear Social"

    def should_continue_news(self, state: AgentState):
        """Determine if news analysis should continue."""
        msgs = state.get("news_messages") or []
        if msgs and msgs[-1].tool_calls:
            return "tools_news"
        return "Msg Clear News"

    def should_continue_fundamentals(self, state: AgentState):
        """Determine if fundamentals analysis should continue."""
        msgs = state.get("fundamentals_messages") or []
        if msgs and msgs[-1].tool_calls:
            return "tools_fundamentals"
        return "Msg Clear Fundamentals"

    def should_continue_debate(self, state: AgentState) -> str:
        """Determine if debate should continue.

        DEPRECATED — no runtime callers (see class docstring).

        NOTE: as of the MEMORY §AA fix the debate is wired as a strict linear
        chain (Bull → Bear → Research Manager) in both ``graph/setup.py`` and
        ``ablation/runner.py``, so this method is no longer used for routing.
        It is retained for reference and any external caller.
        """
        if (
            state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds
        ):  # rounds of back-and-forth between 2 agents
            return "Research Manager"
        if state["investment_debate_state"]["current_response"].startswith("Bull"):
            return "Bear Researcher"
        return "Bull Researcher"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """Determine if risk analysis should continue.

        DEPRECATED — no runtime callers (see class docstring). The 3-agent
        Risky/Safe/Neutral round-robin was replaced by the deterministic Risk
        Scorer + single Merged Risk Debate node.
        """
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # rounds of back-and-forth between 3 agents
            return "Risk Judge"
        if state["risk_debate_state"]["latest_speaker"].startswith("Risky"):
            return "Safe Analyst"
        if state["risk_debate_state"]["latest_speaker"].startswith("Safe"):
            return "Neutral Analyst"
        return "Risky Analyst"
    
    def should_execute_after_risk(self, state: AgentState) -> str:
        """
        Determine if trade should execute after risk assessment.
        Risk VETO stops execution - this is explicit, not silent.

        DEPRECATED — no runtime callers (see class docstring).

        NOTE: This method is available to wire as a conditional edge after
        "Risk Judge" if a post-risk execution node is added to the graph.
        Currently the graph routes Risk Judge → END directly.

        Returns:
            "execute": Proceed with trade execution
            "halt": Stop - risk veto triggered
        """
        # NOTE: Do NOT mutate `state` here. In LangGraph, conditional edge
        # functions receive a read-only snapshot — any writes are silently
        # discarded. risk_veto and final_trade_decision are already set
        # correctly by risk_manager_node before it returns.
        risk_assessment = state.get("risk_assessment", {})

        # Check explicit veto flag set by risk_manager_node
        if state.get("risk_veto", False):
            return "halt"

        # Check if risk manager issued a VETO via approval flag
        if isinstance(risk_assessment, dict):
            if not risk_assessment.get("approved", True):
                return "halt"

            # Check for any remaining critical violations
            violations = risk_assessment.get("violations", [])
            if any(v.get("severity") == "critical" for v in violations):
                return "halt"

        return "execute"
    
    def calculate_conviction_strength(self, state: AgentState) -> str:
        """
        Calculate overall conviction strength from all signals.
        Partial conviction propagates correctly.

        DEPRECATED — no runtime callers (see class docstring).

        Returns:
            "high", "moderate", or "low"
        """
        if not self.is_egx:
            return "moderate"  # Default for non-EGX
        
        # Get bull and bear thesis convictions
        investment_debate = state.get("investment_debate_state", {})
        bull_thesis = investment_debate.get("bull_thesis", {})
        bear_thesis = investment_debate.get("bear_thesis", {})
        
        bull_conviction = bull_thesis.get("conviction_level", "moderate") if bull_thesis else "moderate"
        bear_conviction = bear_thesis.get("conviction_level", "moderate") if bear_thesis else "moderate"
        
        # Map to numeric
        conviction_map = {"high": 3, "moderate": 2, "low": 1}
        bull_score = conviction_map.get(bull_conviction, 2)
        bear_score = conviction_map.get(bear_conviction, 2)
        
        # Get signal alignment
        signal_alignment = None
        if bull_thesis:
            alignment = bull_thesis.get("signal_summary", {}).get("alignment_score", "moderate")
            signal_alignment = conviction_map.get(alignment, 2)
        
        # Get confidence scores
        confidence_scores = state.get("confidence_scores", {})
        avg_confidence = 50.0
        valid_scores = [s for s in confidence_scores.values() if s is not None and isinstance(s, (int, float))]
        if valid_scores:
            avg_confidence = sum(valid_scores) / len(valid_scores)
        
        # Calculate overall conviction
        # Low confidence always reduces conviction
        if avg_confidence < 40:
            return "low"
        elif avg_confidence < 60:
            return "moderate" if bull_score >= 2 or bear_score >= 2 else "low"
        else:
            if signal_alignment and signal_alignment >= 3:
                return "high" if bull_score >= 3 or bear_score >= 3 else "moderate"
            return "moderate"
    
    def apply_confidence_adjustments(self, state: AgentState) -> None:
        """
        Apply confidence adjustments based on data quality.
        Weak data in ANY analyst reduces downstream confidence.
        This method modifies state in place.

        DEPRECATED — no runtime callers (see class docstring).
        """
        if not self.is_egx:
            return
        
        data_quality = state.get("data_quality", {})
        confidence_scores = state.get("confidence_scores", {})
        
        # Apply penalties for incomplete data
        if not data_quality.get("price_data_complete", True):
            if confidence_scores.get("technical"):
                confidence_scores["technical"] *= 0.7
        
        if not data_quality.get("fundamentals_complete", True):
            if confidence_scores.get("fundamental"):
                confidence_scores["fundamental"] *= 0.7
        
        if not data_quality.get("news_available", True):
            if confidence_scores.get("sentiment"):
                confidence_scores["sentiment"] *= 0.6  # Heavier penalty for no news
        
        # Calculate overall confidence (weakest link principle)
        valid_scores = [s for s in confidence_scores.values() if s is not None and isinstance(s, (int, float))]
        if valid_scores:
            min_conf = min(valid_scores)
            avg_conf = sum(valid_scores) / len(valid_scores)
            # Weakest link matters more (60% min, 40% avg)
            confidence_scores["overall"] = round(0.6 * min_conf + 0.4 * avg_conf, 1)
        
        # Update state
        state["confidence_scores"] = confidence_scores

