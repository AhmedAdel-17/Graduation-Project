"""
Signal-quality diagnostics for the EGX Fundamental Analyst.

These helpers do not call an LLM and do not tune prompts. They quantify when
the available fundamentals data is too imbalanced/backward-looking to validate
earnings_direction as a standalone predictive signal.
"""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional


VALID_DIRECTIONS = {"up", "down", "flat"}

MANUAL_DATA_REQUIREMENTS = [
    {
        "category": "forward_context",
        "item": "management guidance / narrative events",
        "why_needed": "Historical accounting data misses one-off events and forward earnings drivers.",
    },
    {
        "category": "market_data",
        "item": "trade-date price, P/E, P/B, dividend yield",
        "why_needed": "Valuation context helps separate cheap recovery setups from deteriorating fundamentals.",
    },
    {
        "category": "quality_of_earnings",
        "item": "operating cash flow, interest expense, one-off gains/losses",
        "why_needed": "Net income direction can be distorted by non-recurring or financing effects.",
    },
    {
        "category": "macro_sector",
        "item": "FX, rates, inflation, commodity and sector-cycle indicators",
        "why_needed": "EGX earnings direction is often driven by macro/sector regimes not visible in statements alone.",
    },
    {
        "category": "labels",
        "item": "larger validated down/flat sample",
        "why_needed": "The annual set is dominated by up outcomes, making always-up a strong baseline.",
    },
]


@dataclass(frozen=True)
class SignalDataDiagnosis:
    n: int
    actual_distribution: Dict[str, int]
    actual_up_rate: float
    actual_non_up_rate: float
    naive_hit_rate: float
    final_hit_rate: Optional[float]
    final_brier_beats_naive: Optional[bool]
    non_up_predictions: int
    true_non_up: int
    false_non_up: int
    non_up_precision: Optional[float]
    non_up_recall: Optional[float]
    root_cause: str
    recommendation: str


def load_case_results(path: str | Path) -> List[Dict[str, str]]:
    """Load saved Phase 2B annual case-level rows."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _direction(row: Mapping[str, str], key: str) -> str:
    return (row.get(key) or "").strip().lower()


def _boolish(value: object) -> Optional[bool]:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def diagnose_saved_cases(rows: Iterable[Mapping[str, str]]) -> SignalDataDiagnosis:
    """
    Diagnose why standalone directional validation is difficult.

    The diagnosis is intentionally conservative: it does not try to prove the
    signal is useless. It distinguishes a technically working analyst from an
    unvalidated standalone prediction label.
    """
    scored = [
        row for row in rows
        if _direction(row, "actual_direction") in VALID_DIRECTIONS
    ]
    n = len(scored)
    actual_counts = Counter(_direction(row, "actual_direction") for row in scored)
    actual_up = actual_counts.get("up", 0)
    actual_non_up = actual_counts.get("down", 0) + actual_counts.get("flat", 0)

    final_key = "earnings_direction"
    final_scored = [
        row for row in scored
        if _direction(row, final_key) in VALID_DIRECTIONS
    ]
    final_correct = sum(
        1 for row in final_scored
        if _direction(row, final_key) == _direction(row, "actual_direction")
    )
    final_hit = (final_correct / len(final_scored)) if final_scored else None

    pred_non_up = [
        row for row in final_scored
        if _direction(row, final_key) in {"down", "flat"}
    ]
    true_non_up = sum(
        1 for row in pred_non_up
        if _direction(row, "actual_direction") in {"down", "flat"}
    )
    false_non_up = sum(
        1 for row in pred_non_up
        if _direction(row, "actual_direction") == "up"
    )
    non_up_precision = true_non_up / len(pred_non_up) if pred_non_up else None
    non_up_recall = true_non_up / actual_non_up if actual_non_up else None

    brier_flag: Optional[bool] = None
    # If the artifact has a row-level final_correct only, Brier is not
    # recoverable here. The validation report remains the Brier source.
    if all("final_correct" in row for row in final_scored):
        brier_flag = None

    up_rate = actual_up / n if n else 0.0
    non_up_rate = actual_non_up / n if n else 0.0
    naive_hit = up_rate

    if up_rate >= 0.70 and actual_non_up < actual_up:
        root_cause = (
            "Class imbalance plus backward-looking evidence: always-up is a "
            "strong baseline, and rare down/flat outcomes are not reliably "
            "separable with the current evidence pack."
        )
    else:
        root_cause = (
            "Standalone direction quality is limited by weak separation between "
            "actual classes in the current case-level evidence."
        )

    recommendation = (
        "Freeze the final direction as conservative/risk-aware, carry the "
        "Fundamental Analyst into downstream multi-agent tests, and add "
        "forward-looking data before another standalone directional gate."
    )

    return SignalDataDiagnosis(
        n=n,
        actual_distribution=dict(actual_counts),
        actual_up_rate=up_rate,
        actual_non_up_rate=non_up_rate,
        naive_hit_rate=naive_hit,
        final_hit_rate=final_hit,
        final_brier_beats_naive=brier_flag,
        non_up_predictions=len(pred_non_up),
        true_non_up=true_non_up,
        false_non_up=false_non_up,
        non_up_precision=non_up_precision,
        non_up_recall=non_up_recall,
        root_cause=root_cause,
        recommendation=recommendation,
    )


def write_manual_data_checklist(path: str | Path) -> None:
    """Write the manual data requirements checklist as CSV."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["category", "item", "why_needed"])
        writer.writeheader()
        writer.writerows(MANUAL_DATA_REQUIREMENTS)


def write_fix_plan(path: str | Path, diagnosis: SignalDataDiagnosis) -> None:
    """Write a human-readable fix plan for the Fundamental Agent."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Fundamental Agent Fix Plan\n",
        "\n",
        "This is a no-LLM, no-prompt-tuning diagnosis of the current Fundamental Agent signal issue.\n",
        "\n",
        "## What Is Fixed In Code\n",
        "\n",
        "- Keep Phase B v2 conservative final-direction calibration.\n",
        "- Treat final `earnings_direction` as a cautious public signal, not proof of standalone edge.\n",
        "- Preserve richer fields for downstream agents: raw direction, outlook, downside risk, confidence, and calibration notes.\n",
        "- Use this diagnostic before any future standalone rerun to avoid repeating the same threshold-tuning loop.\n",
        "\n",
        "## Current Evidence\n",
        "\n",
        f"- Scored annual cases: {diagnosis.n}\n",
        f"- Actual distribution: {diagnosis.actual_distribution}\n",
        f"- Actual up rate / naive always-up hit rate: {diagnosis.naive_hit_rate:.1%}\n",
        f"- Final hit rate: {diagnosis.final_hit_rate:.1%}\n" if diagnosis.final_hit_rate is not None else "- Final hit rate: unavailable\n",
        f"- Final non-up predictions: {diagnosis.non_up_predictions}\n",
        f"- True non-up caught: {diagnosis.true_non_up}\n",
        f"- False non-up calls: {diagnosis.false_non_up}\n",
        f"- Non-up precision: {diagnosis.non_up_precision:.1%}\n" if diagnosis.non_up_precision is not None else "- Non-up precision: n/a\n",
        f"- Non-up recall: {diagnosis.non_up_recall:.1%}\n" if diagnosis.non_up_recall is not None else "- Non-up recall: n/a\n",
        "\n",
        "## Root Cause\n",
        "\n",
        f"{diagnosis.root_cause}\n",
        "\n",
        "## What You Need To Do Manually\n",
        "\n",
        "Add or verify these data sources before trying to prove standalone direction accuracy again:\n",
        "\n",
    ]
    for item in MANUAL_DATA_REQUIREMENTS:
        lines.append(f"- {item['item']}: {item['why_needed']}\n")
    lines.extend([
        "\n",
        "## Next Project Action\n",
        "\n",
        f"{diagnosis.recommendation}\n",
        "\n",
        "Do not keep tuning the final direction threshold on the same 113 cases. That repeats the loop without adding new information.\n",
    ])
    out.write_text("".join(lines), encoding="utf-8")
