"""
Fundamental Analyst module for EGX market.

Structure:
  schemas.py            — Pydantic output schema (FundamentalAnalysisReport)
  financial_calculator.py — True ratio computation (no thresholds, no labels)
  statement_standardizer.py — Preprocessing: common-size, YoY/QoQ, growth, directions
  sector_config.py      — 4-sector profiles, metric applicability, safety floors
  scoring.py            — data_confidence, signal_coherence, financial_health heuristic
  data_loader.py        — Multi-period CSV loader with filing-lag filter

Phase 2A:
  data_cot.py           — Stage 1: evidence pack assembly (deterministic)
  concept_cot.py        — Stage 2: quick_thinking_llm scoped interpretation
  thesis_cot.py         — Stage 3: deep_thinking_llm H&P investment thesis
  pipeline.py           — Orchestrator with fallback chain

Phase 3 additions (after gate):
  memory_manager.py     — 2-tier period-based memory
"""
from .schemas import FundamentalAnalysisReport
from .financial_calculator import FinancialCalculator
from .statement_standardizer import StatementStandardizer
from .sector_config import SectorConfig, classify_sector
from .pipeline import run_cot_pipeline

__all__ = [
    "FundamentalAnalysisReport",
    "FinancialCalculator",
    "StatementStandardizer",
    "SectorConfig",
    "classify_sector",
    "run_cot_pipeline",
]
