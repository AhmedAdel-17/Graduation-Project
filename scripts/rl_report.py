"""Thesis-ready report for the RL decision policy.

Turns a trained policy's sidecar JSONs into a defense-ready Markdown table +
figures, the same way ``scripts/thesis_report.py`` does for the backtests. It
does NO new modelling — it only renders what ``scripts/train_rl_policy.py``
already wrote:

  * ``<model>.eval.json``  — off-policy + counterfactual evaluation
  * ``<model>.card.json``  — training config + sample counts + feature version
  * (optional) ``<dataset>.parquet.summary.json`` — corpus size / action mix

Outputs into ``thesis_results/``:

  * ``rl_summary_<ts>.md``                  — model + OPE table + caveats
  * ``charts/rl_counterfactual_<ts>.png``   — policy vs committee value (uplift)
  * ``charts/rl_action_dist_<ts>.png``      — action mix: committee vs policy

The headline number for the thesis is the **counterfactual uplift**: because the
realized reward of every action (BUY/HOLD/SELL) is known at each held-out
decision, the policy's value and the committee's value are both exact averages,
so their difference is an unbiased estimate of how much the learned decisions
would have improved on the committee.

Usage
-----
    python scripts/rl_report.py --model models/rl_decision.pt
    python scripts/rl_report.py --model models/rl_decision.pt \\
        --dataset-summary data/rl/training_20260701.parquet.summary.json
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DECISION_ACTIONS = ("BUY", "HOLD", "SELL")


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"rl-report: cannot read {path}: {exc}")
        return None


def _fmt(x: Any, nd: int = 4) -> str:
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "—"


# ─────────────────────────────────────────────────────────────────────────────
# Charts
# ─────────────────────────────────────────────────────────────────────────────


def _chart_counterfactual(ope: Dict[str, Any], out: Path) -> None:
    committee = float(ope.get("counterfactual_behavior_value") or 0.0)
    policy = float(ope.get("counterfactual_policy_value") or 0.0)
    fig, ax = plt.subplots(figsize=(5.0, 4.0))
    bars = ax.bar(["Committee", "RL policy"], [committee, policy],
                  color=["#9aa7b8", "#2e75b6"])
    ax.set_ylabel("Mean realized reward (held-out decisions)")
    ax.set_title("Counterfactual value: RL policy vs committee")
    ax.axhline(0.0, color="#444", linewidth=0.8)
    for b, v in zip(bars, [committee, policy]):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                f"{v:.4f}", ha="center",
                va="bottom" if v >= 0 else "top", fontsize=9)
    uplift = policy - committee
    ax.annotate(f"uplift = {uplift:+.4f}", xy=(0.5, 0.95), xycoords="axes fraction",
                ha="center", fontsize=10, color="#2e7d32" if uplift >= 0 else "#c62828")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def _chart_action_dist(ope: Dict[str, Any], out: Path) -> None:
    pol = ope.get("action_distribution") or {}
    com = ope.get("behavior_action_distribution") or {}
    x = range(len(DECISION_ACTIONS))
    pol_v = [float(pol.get(a, 0.0)) for a in DECISION_ACTIONS]
    com_v = [float(com.get(a, 0.0)) for a in DECISION_ACTIONS]
    w = 0.38
    fig, ax = plt.subplots(figsize=(5.5, 4.0))
    ax.bar([i - w / 2 for i in x], com_v, width=w, label="Committee", color="#9aa7b8")
    ax.bar([i + w / 2 for i in x], pol_v, width=w, label="RL policy", color="#2e75b6")
    ax.set_xticks(list(x))
    ax.set_xticklabels(DECISION_ACTIONS)
    ax.set_ylabel("Share of held-out decisions")
    ax.set_title("Decision mix: committee vs RL policy")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Markdown
# ─────────────────────────────────────────────────────────────────────────────


def _build_markdown(
    *,
    model_path: Path,
    card: Dict[str, Any],
    evalj: Dict[str, Any],
    dataset_summary: Optional[Dict[str, Any]],
    chart_paths: Dict[str, Path],
) -> str:
    ope = evalj.get("ope_result", {}) or {}
    summ = evalj.get("summary", {}) or {}
    cfg = card.get("config", {}) or {}
    fp = card.get("model_fingerprint", {}) or {}
    # Sample counts + best loss live in the training block / fingerprint.
    training = (card.get("extra_metadata", {}) or {}).get("training", {}) or {}
    n_train = training.get("n_train", fp.get("trained_on_n_samples", "—"))
    n_val = training.get("n_val", "—")
    best_loss = training.get("best_val_td_loss", fp.get("best_val_td_loss"))
    best_epoch = training.get("best_epoch", fp.get("best_epoch", "—"))

    L: List[str] = []
    L.append("# RL Decision Policy — Evaluation Report")
    L.append("")
    L.append(f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} from "
             f"`{model_path.name}` sidecars._")
    L.append("")
    L.append("The policy is an offline Conservative Q-Learning decision-calibration "
             "policy over the actions {BUY, HOLD, SELL}, trained on the counterfactual "
             "per-action reward of the committee's historical decisions. It runs as a "
             "parallel decision arm; the figures below quantify how its decisions "
             "compare to the committee on a held-out, look-ahead-free set.")
    L.append("")

    # Model summary
    L.append("## Model")
    L.append("")
    L.append("| Field | Value |")
    L.append("|---|---|")
    L.append(f"| Algorithm | {cfg.get('algorithm', '—')} ({cfg.get('algorithm_version', '—')}) |")
    L.append(f"| Feature version | {card.get('feature_version', '—')} |")
    L.append(f"| Train / val samples | {n_train} / {n_val} |")
    L.append(f"| CQL alpha (conservatism) | {cfg.get('cql_alpha', '—')} |")
    L.append(f"| Hidden dims | {cfg.get('hidden_dims', '—')} |")
    L.append(f"| Best val loss (epoch) | {_fmt(best_loss, 6)} ({best_epoch}) |")
    L.append(f"| Weights fingerprint | `{fp.get('weights_sha256_16', '—')}` |")
    if dataset_summary:
        L.append(f"| Corpus size | {dataset_summary.get('n_samples', '—')} samples, "
                 f"{dataset_summary.get('n_tickers', '—')} ticker(s) |")
        ac = dataset_summary.get("action_counts")
        if ac:
            L.append(f"| Committee action mix | {ac} |")
    L.append("")

    # Headline result
    L.append("## Headline result — counterfactual uplift")
    L.append("")
    uplift = summ.get("counterfactual_uplift", ope.get("counterfactual_uplift"))
    L.append("| Metric | Value |")
    L.append("|---|---|")
    L.append(f"| Held-out decisions | {ope.get('n_samples', '—')} |")
    L.append(f"| Committee value (mean realized reward) | {_fmt(ope.get('counterfactual_behavior_value'))} |")
    L.append(f"| **RL policy value** | **{_fmt(ope.get('counterfactual_policy_value'))}** |")
    L.append(f"| **Counterfactual uplift (policy − committee)** | **{_fmt(uplift)}** |")
    L.append(f"| Uplift positive? | {'✅ yes' if (uplift or 0) > 0 else '❌ no'} |")
    L.append("")
    L.append(f"![Counterfactual value](charts/{chart_paths['cf'].name})")
    L.append("")

    # Supporting OPE
    L.append("## Supporting off-policy estimates")
    L.append("")
    L.append("| Estimator | Value | Uplift vs committee |")
    L.append("|---|---|---|")
    L.append(f"| Direct (FQE-style Q) | {_fmt(ope.get('direct_value'))} | "
             f"{_fmt(summ.get('direct_uplift_vs_behavior'))} |")
    L.append(f"| SNIPS (importance-weighted) | {_fmt(ope.get('snips_value'))} | "
             f"{_fmt(summ.get('snips_uplift_vs_behavior'))} |")
    L.append(f"| SNIPS effective sample size | {_fmt(ope.get('snips_effective_sample_size'), 2)} | — |")
    L.append("")
    L.append("## Decision mix")
    L.append("")
    L.append(f"![Action distribution](charts/{chart_paths['dist'].name})")
    L.append("")

    # Caveats — verbatim from the eval notes (honest framing for the defense)
    notes = ope.get("notes") or []
    L.append("## Caveats")
    L.append("")
    if notes:
        for n in notes:
            L.append(f"- {n}")
    else:
        L.append("- (none reported)")
    L.append("- The counterfactual value is exact given the realized reward of every action, "
             "but its *generalisation* is bounded by the held-out sample size above. "
             "Treat small-sample uplift as illustrative until a larger multi-ticker corpus is used.")
    L.append("- The policy is deployed as a parallel arm; these numbers describe an offline "
             "comparison, not live trading P&L.")
    L.append("")
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="models/rl_decision.pt",
                        help="Trained policy .pt path (reads its .eval.json + .card.json sidecars).")
    parser.add_argument("--dataset-summary", default=None,
                        help="Optional <dataset>.parquet.summary.json for corpus stats.")
    parser.add_argument("--out-dir", default="thesis_results",
                        help="Output directory. Default thesis_results.")
    args = parser.parse_args(argv)

    model_path = Path(args.model)
    eval_path = Path(str(model_path) + ".eval.json")
    card_path = Path(str(model_path) + ".card.json")

    evalj = _load_json(eval_path)
    card = _load_json(card_path) or {}
    if evalj is None:
        print(f"rl-report: no eval report at {eval_path}. Train first with "
              f"scripts/train_rl_policy.py (or scripts/rl_pipeline.py).")
        return 1

    dataset_summary = None
    if args.dataset_summary:
        dataset_summary = _load_json(Path(args.dataset_summary))

    out_dir = Path(args.out_dir)
    charts_dir = out_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    ope = evalj.get("ope_result", {}) or {}
    chart_paths = {
        "cf": charts_dir / f"rl_counterfactual_{ts}.png",
        "dist": charts_dir / f"rl_action_dist_{ts}.png",
    }
    _chart_counterfactual(ope, chart_paths["cf"])
    _chart_action_dist(ope, chart_paths["dist"])

    md = _build_markdown(
        model_path=model_path, card=card, evalj=evalj,
        dataset_summary=dataset_summary, chart_paths=chart_paths,
    )
    md_path = out_dir / f"rl_summary_{ts}.md"
    md_path.write_text(md, encoding="utf-8")

    print(f"rl-report: wrote {md_path}")
    print(f"rl-report: wrote {chart_paths['cf']}")
    print(f"rl-report: wrote {chart_paths['dist']}")
    uplift = (evalj.get("summary", {}) or {}).get("counterfactual_uplift",
                                                  ope.get("counterfactual_uplift"))
    print(f"rl-report: counterfactual uplift = {_fmt(uplift)} "
          f"over {ope.get('n_samples', '?')} held-out decisions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
