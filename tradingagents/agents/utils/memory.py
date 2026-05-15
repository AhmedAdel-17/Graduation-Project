import chromadb
from chromadb.config import Settings
from openai import OpenAI
import logging
import math
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger("tradingagents.memory")

# =============================================================================
# Memory Module with Audit-Grade Logging
# =============================================================================
# Provides:
# 1. FinancialSituationMemory - Vector similarity matching (existing)
# 2. AuditLogger - Append-only, human-readable decision logging (new)
# =============================================================================


class FinancialSituationMemory:
    def __init__(self, name, config):
        self.name = name
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

        # Chroma persistence: when chroma_persist_dir is set we use PersistentClient
        # so agent memories survive process restarts. Empty / None path keeps the
        # legacy in-memory client (useful for unit tests that want a clean store).
        chroma_path = config.get("chroma_persist_dir")
        self.chroma_persist_dir = str(chroma_path) if chroma_path else None
        if self.chroma_persist_dir:
            self.chroma_client = chromadb.PersistentClient(path=self.chroma_persist_dir)
        else:
            self.chroma_client = chromadb.Client(Settings(allow_reset=True))
        self.situation_collection = self.chroma_client.get_or_create_collection(name=name)

        # PR 8: BM25 corpus for embedding-disabled fallback + seed-corpus bootstrap.
        # Stores (document, metadata) tuples in-memory. Rebuilt lazily on query.
        self._bm25_corpus: List[tuple] = []
        self._bm25_index = None  # rank_bm25.BM25Okapi, lazily constructed
        self._load_seed_corpus(config)

    def get_embedding(self, text):
        """Get OpenAI embedding for a text"""
        if not self.embeddings_enabled:
            return None
        response = self.client.embeddings.create(
            model=self.embedding, input=text
        )
        return response.data[0].embedding

    # Canonical metadata keys persisted alongside each memory row. Used by
    # filtered retrieval (`get_memories(where=...)`) and surfaced back to the
    # caller in the returned dict.
    METADATA_KEYS = (
        "ticker",
        "trade_date",
        "memory_type",  # one of: thesis | execution | risk_decision | reflection
        "agent_name",
        "outcome",  # optional JSON-serializable summary of realised result
        "confidence",  # optional float in [0, 1]
    )

    def _load_seed_corpus(self, config: Dict[str, Any]) -> None:
        """Bootstrap empty collections with the hand-curated EGX seed corpus.

        PR 8 / MEMORY.md §K fix. The seeds are always loaded into the in-memory
        BM25 corpus so embedding-disabled configs have something to retrieve.
        When the Chroma collection is empty AND embeddings are enabled, the
        seeds are also written into Chroma so vector similarity has signal too.

        Seeds are filtered by ``agent_name`` so each collection only sees rows
        relevant to its agent. Disable with ``config["disable_seed_memories"]=True``.
        """
        if config.get("disable_seed_memories"):
            return
        try:
            from tradingagents.agents.utils.seed_memories import get_seeds_for_agent
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("Seed corpus import failed: %s", exc)
            return

        seeds = get_seeds_for_agent(self.name)
        if not seeds:
            return

        # Always populate the in-memory BM25 corpus.
        for entry in seeds:
            metadata = {
                k: v for k, v in entry.items()
                if k in self.METADATA_KEYS and v is not None
            }
            metadata["recommendation"] = entry.get("recommendation", "")
            self._bm25_corpus.append((entry["situation"], metadata))
        self._bm25_index = None  # invalidate; rebuild on first query

        # If Chroma is fresh AND embeddings are enabled, also persist seeds
        # into Chroma so vector similarity benefits too. Each Chroma row carries
        # the same metadata block, identified by id="seed_<i>" so they can be
        # distinguished from reflection-written rows.
        if not self.embeddings_enabled:
            logger.debug(
                "Seed corpus loaded into BM25 only for %s (embeddings disabled, %d entries)",
                self.name, len(seeds),
            )
            return
        if self.situation_collection.count() > 0:
            return  # already populated this run / prior run

        try:
            ids = [f"seed_{i}" for i in range(len(seeds))]
            documents = [e["situation"] for e in seeds]
            metadatas = []
            for e in seeds:
                meta = {k: e[k] for k in self.METADATA_KEYS if k in e and e[k] is not None}
                meta["recommendation"] = e.get("recommendation", "")
                metadatas.append(meta)
            embeddings = [self.get_embedding(doc) for doc in documents]
            self.situation_collection.add(
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
                ids=ids,
            )
            logger.info("Seeded %d entries into Chroma collection '%s'", len(seeds), self.name)
        except Exception as exc:
            logger.warning("Seed load into Chroma failed for '%s': %s", self.name, exc)

    def _rebuild_bm25_index(self) -> None:
        """Tokenize the BM25 corpus and build a fresh index. Idempotent."""
        if not self._bm25_corpus:
            self._bm25_index = None
            return
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.warning("rank_bm25 not installed; BM25 fallback disabled")
            self._bm25_index = None
            return
        tokenized = [doc.lower().split() for doc, _ in self._bm25_corpus]
        self._bm25_index = BM25Okapi(tokenized)

    def _bm25_search(
        self,
        query: str,
        n_matches: int,
        where: Optional[Dict] = None,
        min_similarity: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Keyword retrieval over the in-memory BM25 corpus.

        Used when embeddings are disabled (DeepSeek / Groq backends) and as a
        graceful fallback when the Chroma collection is empty even though
        embeddings are configured.

        BM25 raw scores are unbounded; we squash with ``tanh(score / 5)`` so
        the returned ``similarity_score`` lives in the same [0, 1] range as
        the cosine-similarity path — letting ``min_similarity`` thresholds
        behave consistently across backends.
        """
        if self._bm25_index is None:
            self._rebuild_bm25_index()
        if self._bm25_index is None or not self._bm25_corpus:
            return []

        tokens = query.lower().split() if query else []
        if not tokens:
            return []

        scores = self._bm25_index.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        results: List[Dict[str, Any]] = []
        for idx, raw_score in ranked:
            if len(results) >= n_matches:
                break
            if raw_score <= 0:
                continue  # no token overlap at all
            doc, meta = self._bm25_corpus[idx]
            if where:
                if not all(meta.get(k) == v for k, v in where.items()):
                    continue
            similarity = math.tanh(float(raw_score) / 5.0)
            if min_similarity is not None and similarity < min_similarity:
                continue
            results.append(
                {
                    "matched_situation": doc,
                    "recommendation": meta.get("recommendation", ""),
                    "similarity_score": similarity,
                    "metadata": {
                        k: meta.get(k)
                        for k in self.METADATA_KEYS
                        if meta.get(k) is not None
                    },
                }
            )
        return results

    def _build_metadata(self, recommendation, per_item, default):
        """Merge default + per-item metadata + recommendation. Chroma rejects
        ``None`` values in metadatas, so we drop unset keys defensively."""
        merged = {}
        if default:
            merged.update({k: v for k, v in default.items() if v is not None})
        if per_item:
            merged.update({k: v for k, v in per_item.items() if v is not None})
        # Keep only the canonical keys plus recommendation, in case callers
        # passed extras we don't want polluting the collection.
        whitelisted = {
            k: v for k, v in merged.items() if k in self.METADATA_KEYS and v is not None
        }
        whitelisted["recommendation"] = recommendation
        return whitelisted

    def add_situations(
        self,
        situations_and_advice,
        metadatas=None,
        *,
        default_metadata=None,
    ):
        """Add financial situations and their advice, with optional metadata.

        Args:
            situations_and_advice: list of (situation, recommendation) tuples.
            metadatas: optional list of dicts parallel to ``situations_and_advice``;
                per-row metadata merged onto ``default_metadata``.
            default_metadata: optional dict applied to every row in the batch
                (e.g. ``{"ticker": "COMI.CA", "agent_name": "bull_memory",
                "memory_type": "reflection"}``). Recognized keys are listed in
                ``FinancialSituationMemory.METADATA_KEYS``; unrecognized keys
                are dropped.

        When embeddings are disabled (DeepSeek / Groq backends), rows are
        appended to the in-memory BM25 corpus only — Chroma vector add is
        skipped because there's no embedding to attach.
        """
        if not situations_and_advice:
            return

        if metadatas is not None and len(metadatas) != len(situations_and_advice):
            raise ValueError(
                "metadatas length must equal situations_and_advice length"
            )

        # Always append to BM25 corpus so retrieval works even when embeddings
        # are disabled or vector search misses (e.g. cold-start).
        for i, (situation, recommendation) in enumerate(situations_and_advice):
            per_item = metadatas[i] if metadatas else None
            meta = self._build_metadata(recommendation, per_item, default_metadata)
            self._bm25_corpus.append((situation, meta))
        self._bm25_index = None  # invalidate

        if not self.embeddings_enabled:
            return

        situations = []
        ids = []
        embeddings = []
        built_metadatas = []

        offset = self.situation_collection.count()

        for i, (situation, recommendation) in enumerate(situations_and_advice):
            per_item = metadatas[i] if metadatas else None
            situations.append(situation)
            ids.append(str(offset + i))
            embeddings.append(self.get_embedding(situation))
            built_metadatas.append(
                self._build_metadata(recommendation, per_item, default_metadata)
            )

        self.situation_collection.add(
            documents=situations,
            metadatas=built_metadatas,
            embeddings=embeddings,
            ids=ids,
        )

    def get_memories(
        self,
        current_situation,
        n_matches=1,
        *,
        where=None,
        min_similarity=None,
    ):
        """Find matching recommendations using vector similarity, with BM25
        keyword fallback for embedding-disabled backends or empty stores.

        Args:
            current_situation: free-text query.
            n_matches: top-k.
            where: optional Chroma metadata filter, e.g. ``{"ticker": "COMI.CA"}``.
                Rows lacking the filtered key are excluded by Chroma automatically.
            min_similarity: optional float in [0, 1]. Matches with
                ``similarity_score`` strictly below this threshold are dropped.
                ``None`` disables the filter; ``0.0`` keeps everything.

        Returns:
            list of dicts: ``{matched_situation, recommendation, similarity_score,
            metadata}`` — ``metadata`` carries the canonical keys persisted with
            the row (ticker, trade_date, memory_type, …).

        Telemetry: emits ``logger.info("memory.empty_return …")`` whenever
        retrieval returns ``[]`` so we can measure cold-start frequency.
        """
        # PR 8: BM25 fallback when embeddings disabled (DeepSeek / Groq backends).
        if not self.embeddings_enabled:
            results = self._bm25_search(
                current_situation,
                n_matches=n_matches,
                where=where,
                min_similarity=min_similarity,
            )
            if not results:
                logger.info(
                    "memory.empty_return (collection=%s, backend=bm25, where=%s, threshold=%s)",
                    self.name, where, min_similarity,
                )
            return results

        # Vector path (embeddings enabled). When Chroma is empty, fall back to
        # BM25 over the seed corpus so we never silently return [] just because
        # no reflection has run yet.
        if self.situation_collection.count() == 0:
            results = self._bm25_search(
                current_situation,
                n_matches=n_matches,
                where=where,
                min_similarity=min_similarity,
            )
            if not results:
                logger.info(
                    "memory.empty_return (collection=%s, backend=bm25_fallback, "
                    "where=%s, threshold=%s)",
                    self.name, where, min_similarity,
                )
            return results

        query_embedding = self.get_embedding(current_situation)

        query_kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": n_matches,
            "include": ["metadatas", "documents", "distances"],
        }
        if where:
            query_kwargs["where"] = where

        results = self.situation_collection.query(**query_kwargs)

        # When Chroma returns nothing matching the filter, "documents" can be
        # [[]]; defend against that.
        docs = results.get("documents") or [[]]
        metas = results.get("metadatas") or [[]]
        dists = results.get("distances") or [[]]
        if not docs or not docs[0]:
            logger.info(
                "memory.empty_return (collection=%s, backend=chroma, where=%s, threshold=%s)",
                self.name, where, min_similarity,
            )
            return []

        matched_results = []
        for i in range(len(docs[0])):
            meta = metas[0][i] if metas and metas[0] else {}
            similarity = 1 - dists[0][i] if dists and dists[0] else 0.0
            if min_similarity is not None and similarity < min_similarity:
                continue
            matched_results.append(
                {
                    "matched_situation": docs[0][i],
                    "recommendation": meta.get("recommendation", ""),
                    "similarity_score": similarity,
                    "metadata": {
                        k: meta.get(k)
                        for k in self.METADATA_KEYS
                        if meta.get(k) is not None
                    },
                }
            )

        if not matched_results:
            logger.info(
                "memory.empty_return (collection=%s, backend=chroma, where=%s, "
                "threshold=%s, raw_hits=%d)",
                self.name, where, min_similarity, len(docs[0]),
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

