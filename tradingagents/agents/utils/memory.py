import chromadb
from chromadb.config import Settings
from openai import OpenAI
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

# =============================================================================
# Memory Module with Audit-Grade Logging
# =============================================================================
# Provides:
# 1. FinancialSituationMemory - Vector similarity matching (existing)
# 2. AuditLogger - Append-only, human-readable decision logging (new)
# =============================================================================


class FinancialSituationMemory:
    def __init__(self, name, config):
        backend_url = config.get("backend_url", "")
        if "localhost:11434" in backend_url:
            self.embedding = "nomic-embed-text"
            self.embeddings_enabled = True
        elif "openai.com" in backend_url:
            self.embedding = "text-embedding-3-small"
            self.embeddings_enabled = True
        else:
            # Groq and other providers don't support embeddings — disable memory
            self.embedding = None
            self.embeddings_enabled = False

        self.client = OpenAI(base_url=backend_url) if self.embeddings_enabled else None
        self.chroma_client = chromadb.Client(Settings(allow_reset=True))
        self.situation_collection = self.chroma_client.get_or_create_collection(name=name)

    def get_embedding(self, text):
        """Get OpenAI embedding for a text"""
        if not self.embeddings_enabled:
            return None
        response = self.client.embeddings.create(
            model=self.embedding, input=text
        )
        return response.data[0].embedding

    def add_situations(self, situations_and_advice):
        """Add financial situations and their corresponding advice. Parameter is a list of tuples (situation, rec)"""
        if not self.embeddings_enabled:
            return

        situations = []
        advice = []
        ids = []
        embeddings = []

        offset = self.situation_collection.count()

        for i, (situation, recommendation) in enumerate(situations_and_advice):
            situations.append(situation)
            advice.append(recommendation)
            ids.append(str(offset + i))
            embeddings.append(self.get_embedding(situation))

        self.situation_collection.add(
            documents=situations,
            metadatas=[{"recommendation": rec} for rec in advice],
            embeddings=embeddings,
            ids=ids,
        )

    def get_memories(self, current_situation, n_matches=1):
        """Find matching recommendations using OpenAI embeddings"""
        if not self.embeddings_enabled or self.situation_collection.count() == 0:
            return []

        query_embedding = self.get_embedding(current_situation)

        results = self.situation_collection.query(
            query_embeddings=[query_embedding],
            n_results=n_matches,
            include=["metadatas", "documents", "distances"],
        )

        matched_results = []
        for i in range(len(results["documents"][0])):
            matched_results.append(
                {
                    "matched_situation": results["documents"][0][i],
                    "recommendation": results["metadatas"][0][i]["recommendation"],
                    "similarity_score": 1 - results["distances"][0][i],
                }
            )

        return matched_results


# =============================================================================
# AUDIT-GRADE LOGGING
# =============================================================================
# Append-only, human-readable logging for compliance and review
# Records: Agent opinions, Trade justifications, Risk decisions, Final actions
# =============================================================================


class AuditLogger:
    """
    Audit-grade logging for trading decisions.
    
    Features:
    - Append-only (no modifications to historical records)
    - Human-readable format (suitable for graduation defense)
    - JSON-structured for programmatic access
    - Markdown summary for quick review
    """
    
    def __init__(self, config: Dict[str, Any], ticker: str = "UNKNOWN"):
        """
        Initialize the audit logger.
        
        Args:
            config: Configuration dictionary
            ticker: Stock ticker being analyzed
        """
        self.config = config
        self.ticker = ticker
        self.target_market = config.get("target_market", "US")
        
        # Create log directory
        project_dir = config.get("project_dir", ".")
        self.log_dir = Path(project_dir) / "audit_logs" / ticker
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Log files
        self.json_log_path = self.log_dir / "audit_log.jsonl"  # Append-only JSON Lines
        self.markdown_log_path = self.log_dir / "audit_summary.md"  # Human-readable
        
        # Session identifier
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    def _append_json(self, entry: Dict[str, Any]) -> None:
        """Append a JSON entry to the log file (append-only)."""
        entry["_session_id"] = self.session_id
        entry["_logged_at"] = datetime.now().isoformat()
        
        with open(self.json_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    
    def _append_markdown(self, content: str) -> None:
        """Append markdown content to the summary file."""
        with open(self.markdown_log_path, "a", encoding="utf-8") as f:
            f.write(content + "\n")
    
    def log_session_start(self, trade_date: str) -> None:
        """Log the start of a trading session."""
        entry = {
            "event": "SESSION_START",
            "ticker": self.ticker,
            "trade_date": trade_date,
            "market": self.target_market,
            "timestamp": datetime.now().isoformat(),
        }
        self._append_json(entry)
        
        # Markdown header
        md_content = f"""
---

# 📊 Trading Session: {self.ticker}

**Date**: {trade_date}  
**Market**: {self.target_market}  
**Session ID**: {self.session_id}  
**Started**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---
"""
        self._append_markdown(md_content)
    
    def log_agent_opinion(
        self,
        agent_name: str,
        opinion_type: str,
        opinion_summary: str,
        confidence_score: Optional[float] = None,
        structured_output: Optional[Dict] = None,
        raw_output: Optional[str] = None
    ) -> None:
        """
        Log an agent's opinion/analysis.
        
        Args:
            agent_name: Name of the agent (e.g., "Technical Analyst", "Accountant")
            opinion_type: Type of opinion (e.g., "bullish", "bearish", "neutral")
            opinion_summary: Brief summary of the opinion
            confidence_score: Confidence level (0-100)
            structured_output: Structured JSON output from agent
            raw_output: Raw text output (truncated if too long)
        """
        entry = {
            "event": "AGENT_OPINION",
            "agent_name": agent_name,
            "opinion_type": opinion_type,
            "opinion_summary": opinion_summary,
            "confidence_score": confidence_score,
            "structured_output": structured_output,
            "raw_output_length": len(raw_output) if raw_output else 0,
        }
        self._append_json(entry)
        
        # Markdown entry
        confidence_str = f" (Confidence: {confidence_score}%)" if confidence_score else ""
        emoji = "🐂" if "bull" in opinion_type.lower() else "🐻" if "bear" in opinion_type.lower() else "➖"
        
        md_content = f"""
## {emoji} {agent_name}

**Opinion**: {opinion_type.upper()}{confidence_str}

> {opinion_summary}

"""
        if structured_output:
            md_content += f"<details>\n<summary>Structured Output</summary>\n\n```json\n{json.dumps(structured_output, indent=2, ensure_ascii=False, default=str)}\n```\n</details>\n"
        
        self._append_markdown(md_content)
    
    def log_trade_justification(
        self,
        decision: str,
        justification: str,
        bull_thesis_summary: Optional[str] = None,
        bear_thesis_summary: Optional[str] = None,
        signal_alignment: Optional[str] = None,
        execution_plan: Optional[Dict] = None
    ) -> None:
        """
        Log the trade justification from the researcher/trader.
        
        Args:
            decision: BUY, SELL, or HOLD
            justification: Detailed justification for the decision
            bull_thesis_summary: Summary of bull case
            bear_thesis_summary: Summary of bear case
            signal_alignment: How well signals aligned (strong/moderate/weak)
            execution_plan: Structured execution plan
        """
        entry = {
            "event": "TRADE_JUSTIFICATION",
            "decision": decision,
            "justification": justification,
            "bull_thesis_summary": bull_thesis_summary,
            "bear_thesis_summary": bear_thesis_summary,
            "signal_alignment": signal_alignment,
            "has_execution_plan": execution_plan is not None,
        }
        self._append_json(entry)
        
        # Markdown entry
        decision_emoji = "🟢" if decision.upper() == "BUY" else "🔴" if decision.upper() == "SELL" else "🟡"
        
        md_content = f"""
## 💼 Trade Justification

### {decision_emoji} Decision: **{decision.upper()}**

**Signal Alignment**: {signal_alignment or "Not assessed"}

#### Justification
{justification}

"""
        if bull_thesis_summary:
            md_content += f"#### 🐂 Bull Case\n{bull_thesis_summary}\n\n"
        if bear_thesis_summary:
            md_content += f"#### 🐻 Bear Case\n{bear_thesis_summary}\n\n"
        
        if execution_plan:
            md_content += f"""
#### 📋 Execution Plan
```json
{json.dumps(execution_plan, indent=2, ensure_ascii=False, default=str)}
```
"""
        
        self._append_markdown(md_content)
    
    def log_risk_decision(
        self,
        approved: bool,
        violations: List[Dict] = None,
        risk_assessment: Optional[Dict] = None,
        veto_explanation: Optional[str] = None
    ) -> None:
        """
        Log the risk manager's decision.
        
        Args:
            approved: Whether the trade was approved
            violations: List of risk violations
            risk_assessment: Full risk assessment
            veto_explanation: Explanation if vetoed
        """
        entry = {
            "event": "RISK_DECISION",
            "approved": approved,
            "violation_count": len(violations) if violations else 0,
            "violations": violations,
            "veto_explanation": veto_explanation,
        }
        self._append_json(entry)
        
        # Markdown entry
        status_emoji = "✅" if approved else "⛔"
        
        md_content = f"""
## 🛡️ Risk Assessment

### {status_emoji} Status: {"APPROVED" if approved else "VETOED"}

"""
        if not approved and veto_explanation:
            md_content += f"**Veto Reason**:\n{veto_explanation}\n\n"
        
        if violations:
            md_content += "### Violations\n\n"
            for v in violations:
                severity_emoji = "🔴" if v.get("severity") == "critical" else "🟡" if v.get("severity") == "high" else "🟢"
                md_content += f"- {severity_emoji} **{v.get('rule', 'Unknown')}** ({v.get('severity', 'unknown')})\n"
                md_content += f"  - {v.get('explanation', 'No explanation')}\n"
                md_content += f"  - Remediation: {v.get('remediation', 'None')}\n\n"
        
        self._append_markdown(md_content)
    
    def log_final_action(
        self,
        final_decision: str,
        risk_veto: bool = False,
        confidence_scores: Optional[Dict] = None,
        data_quality: Optional[Dict] = None,
        execution_details: Optional[Dict] = None
    ) -> None:
        """
        Log the final action taken.
        
        Args:
            final_decision: The final decision text
            risk_veto: Whether risk veto was applied
            confidence_scores: Confidence scores from all analysts
            data_quality: Data quality indicators
            execution_details: Details of execution if applicable
        """
        entry = {
            "event": "FINAL_ACTION",
            "final_decision": final_decision[:500],  # Truncate for storage
            "risk_veto": risk_veto,
            "confidence_scores": confidence_scores,
            "data_quality": data_quality,
            "execution_details": execution_details,
        }
        self._append_json(entry)
        
        # Markdown entry
        veto_note = " (VETOED BY RISK)" if risk_veto else ""
        
        md_content = f"""
## 🎯 Final Action{veto_note}

"""
        if confidence_scores:
            md_content += "### Confidence Summary\n\n"
            md_content += "| Analyst | Confidence |\n|---------|------------|\n"
            for analyst, score in confidence_scores.items():
                if score is not None:
                    md_content += f"| {analyst.title()} | {score}% |\n"
        
        if data_quality:
            completeness = data_quality.get("data_completeness_score", 100)
            md_content += f"\n**Data Completeness**: {completeness}%\n"
        
        # Extract decision from text
        decision_upper = final_decision.upper()
        if "BUY" in decision_upper and "HOLD" not in decision_upper:
            final_action = "BUY"
            emoji = "🟢"
        elif "SELL" in decision_upper:
            final_action = "SELL"
            emoji = "🔴"
        else:
            final_action = "HOLD"
            emoji = "🟡"
        
        md_content += f"""
### {emoji} Final Decision: **{final_action}**

<details>
<summary>Full Decision Text</summary>

{final_decision[:2000]}{"..." if len(final_decision) > 2000 else ""}

</details>

---
**Session Ended**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

"""
        self._append_markdown(md_content)
    
    def log_session_end(self, returns_if_known: Optional[float] = None) -> None:
        """Log the end of a trading session with optional returns."""
        entry = {
            "event": "SESSION_END",
            "ticker": self.ticker,
            "returns_if_known": returns_if_known,
            "timestamp": datetime.now().isoformat(),
        }
        self._append_json(entry)
    
    def create_audit_from_state(self, state: Dict[str, Any]) -> None:
        """
        Create a complete audit log from the final graph state.
        
        Args:
            state: The final state from TradingAgentsGraph
        """
        trade_date = state.get("trade_date", "Unknown")
        
        # Start session
        self.log_session_start(trade_date)
        
        # Log technical analyst opinion
        tech_analysis = state.get("technical_analysis", {})
        if tech_analysis:
            self.log_agent_opinion(
                agent_name="Technical Analyst (Chartist)",
                opinion_type=tech_analysis.get("trend_direction", "neutral"),
                opinion_summary=f"Trend: {tech_analysis.get('trend_direction', 'N/A')}, Signals: {tech_analysis.get('indicator_signals', {})}",
                confidence_score=tech_analysis.get("confidence_score"),
                structured_output=tech_analysis,
            )
        
        # Log fundamental analyst opinion
        fund_analysis = state.get("fundamental_analysis", {})
        if fund_analysis:
            health = fund_analysis.get("financial_health", {}).get("overall", "unknown")
            self.log_agent_opinion(
                agent_name="Fundamental Analyst (Accountant)",
                opinion_type=health,
                opinion_summary=f"Financial Health: {health}, Valuation: {fund_analysis.get('valuation_range', {})}",
                confidence_score=fund_analysis.get("confidence_score"),
                structured_output=fund_analysis,
            )
        
        # Log sentiment analyst opinion
        sent_analysis = state.get("sentiment_analysis", {})
        if sent_analysis:
            self.log_agent_opinion(
                agent_name="News Analyst (Journalist)",
                opinion_type=sent_analysis.get("sentiment", "neutral"),
                opinion_summary=sent_analysis.get("explanation", "No explanation provided"),
                confidence_score=sent_analysis.get("confidence_score"),
                structured_output=sent_analysis,
            )
        
        # Log trade justification
        investment_debate = state.get("investment_debate_state", {})
        bull_thesis = investment_debate.get("bull_thesis", {})
        bear_thesis = investment_debate.get("bear_thesis", {})
        execution_plan = state.get("execution_plan", {})
        
        if execution_plan:
            exec_plan_inner = execution_plan.get("execution_plan", execution_plan)
            decision = exec_plan_inner.get("decision", "HOLD")
            alignment = bull_thesis.get("signal_summary", {}).get("alignment_score") if bull_thesis else None
            
            self.log_trade_justification(
                decision=decision,
                justification=state.get("investment_plan", "No justification provided"),
                bull_thesis_summary=str(bull_thesis.get("key_catalysts", [])) if bull_thesis else None,
                bear_thesis_summary=str(bear_thesis.get("key_risks", [])) if bear_thesis else None,
                signal_alignment=alignment,
                execution_plan=exec_plan_inner,
            )
        
        # Log risk decision
        risk_assessment = state.get("risk_assessment", {})
        if risk_assessment:
            self.log_risk_decision(
                approved=risk_assessment.get("approved", True),
                violations=risk_assessment.get("violations", []),
                risk_assessment=risk_assessment,
                veto_explanation=risk_assessment.get("veto_explanation"),
            )
        
        # Log final action
        self.log_final_action(
            final_decision=state.get("final_trade_decision", "No decision"),
            risk_veto=state.get("risk_veto", False),
            confidence_scores=state.get("confidence_scores", {}),
            data_quality=state.get("data_quality", {}),
            execution_details=execution_plan.get("execution_plan") if execution_plan else None,
        )
        
        # End session
        self.log_session_end()


if __name__ == "__main__":
    # Example usage
    matcher = FinancialSituationMemory()

    # Example data
    example_data = [
        (
            "High inflation rate with rising interest rates and declining consumer spending",
            "Consider defensive sectors like consumer staples and utilities. Review fixed-income portfolio duration.",
        ),
        (
            "Tech sector showing high volatility with increasing institutional selling pressure",
            "Reduce exposure to high-growth tech stocks. Look for value opportunities in established tech companies with strong cash flows.",
        ),
        (
            "Strong dollar affecting emerging markets with increasing forex volatility",
            "Hedge currency exposure in international positions. Consider reducing allocation to emerging market debt.",
        ),
        (
            "Market showing signs of sector rotation with rising yields",
            "Rebalance portfolio to maintain target allocations. Consider increasing exposure to sectors benefiting from higher rates.",
        ),
    ]

    # Add the example situations and recommendations
    matcher.add_situations(example_data)

    # Example query
    current_situation = """
    Market showing increased volatility in tech sector, with institutional investors 
    reducing positions and rising interest rates affecting growth stock valuations
    """

    try:
        recommendations = matcher.get_memories(current_situation, n_matches=2)

        for i, rec in enumerate(recommendations, 1):
            print(f"\nMatch {i}:")
            print(f"Similarity Score: {rec['similarity_score']:.2f}")
            print(f"Matched Situation: {rec['matched_situation']}")
            print(f"Recommendation: {rec['recommendation']}")

    except Exception as e:
        print(f"Error during recommendation: {str(e)}")

