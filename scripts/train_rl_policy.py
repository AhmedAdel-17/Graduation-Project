"""Train the offline RL meta-policy on a parquet dataset.

Usage::

    python scripts/train_rl_policy.py \\
        --dataset data/rl/training_v1.parquet \\
        --output  models/rl_meta_v1.pt \\
        --epochs 200 --seed 42 --cql-alpha 1.0

Writes three artifacts:

- ``<output>``                    — torch checkpoint (state_dict + config)
- ``<output>.card.json``          — human-readable model card
- ``<output>.eval.json``          — OPE results on the held-out split

The script is deterministic given ``--seed``. It refuses to overwrite an
existing checkpoint unless ``--force`` is passed (cheap guardrail against
clobbering a known-good model during a careless retrain).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root so we can ``from tradingagents.rl ...`` regardless of CWD.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tradingagents.rl.config import DEFAULT_TRAINING_CONFIG, TrainingConfig
from tradingagents.rl.eval import compare_to_baseline, evaluate, write_report
from tradingagents.rl.train import time_aware_split, materialize_dataset, train

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("rl.train_cli")


def _config_from_args(args: argparse.Namespace) -> TrainingConfig:
    """Apply CLI overrides on top of the default config."""
    base = DEFAULT_TRAINING_CONFIG.as_dict()
    # Drop derived fields the dataclass doesn't accept
    base.pop("size_tiers", None)
    base.pop("n_actions", None)

    if args.epochs is not None:
        base["n_epochs"] = int(args.epochs)
    if args.batch_size is not None:
        base["batch_size"] = int(args.batch_size)
    if args.learning_rate is not None:
        base["learning_rate"] = float(args.learning_rate)
    if args.cql_alpha is not None:
        base["cql_alpha"] = float(args.cql_alpha)
    if args.seed is not None:
        base["seed"] = int(args.seed)
    if args.val_fraction is not None:
        base["val_fraction"] = float(args.val_fraction)
    if args.split_mode is not None:
        base["split_mode"] = str(args.split_mode)

    if "hidden_dims" in base and isinstance(base["hidden_dims"], list):
        base["hidden_dims"] = tuple(base["hidden_dims"])
    return TrainingConfig(**base)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="Path to training parquet/csv")
    parser.add_argument("--output", required=True, help="Output .pt path")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing model")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--learning-rate", type=float, default=None)
    parser.add_argument("--cql-alpha", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--val-fraction", type=float, default=None)
    parser.add_argument("--split-mode", choices=("time", "random"), default=None)
    parser.add_argument("--keep-hold-rows", action="store_true",
                        help="Do not drop HOLD rows (debug only — distorts action histogram)")
    args = parser.parse_args(argv)

    config = _config_from_args(args)
    output = Path(args.output)
    if output.exists() and not args.force:
        logger.error("rl-train-cli: %s exists. Pass --force to overwrite.", output)
        return 1

    logger.info("rl-train-cli: starting train run with config:\n%s",
                json.dumps(config.as_dict(), indent=2))

    try:
        result = train(
            args.dataset,
            config=config,
            drop_pending=True,
            drop_hold=not args.keep_hold_rows,
        )
    except (FileNotFoundError, ValueError) as exc:
        logger.error("rl-train-cli: training failed: %s", exc)
        return 2

    logger.info(
        "rl-train-cli: trained on %d / val %d (best_val_td=%.6f at epoch %d, %.1fs wall)",
        result.n_train, result.n_val, result.best_val_td_loss,
        result.best_epoch, result.wall_clock_seconds,
    )

    # Build OPE on the same held-out split so the eval and the card match.
    full = materialize_dataset(
        args.dataset, drop_pending=True, drop_hold=not args.keep_hold_rows,
    )
    _train_t, val_t = time_aware_split(
        full,
        val_fraction=config.val_fraction,
        mode=config.split_mode,
        seed=config.seed,
    )
    ope_result = evaluate(result.policy, val_t)
    summary = compare_to_baseline(ope_result)
    logger.info("rl-train-cli: OPE summary:\n%s",
                json.dumps(summary, indent=2, default=str))

    # Persist artefacts
    card_extra = {
        "training": result.to_card_dict(),
        "ope_summary": summary,
        "trained_at_utc": datetime.utcnow().isoformat() + "Z",
        "command_line_args": vars(args),
    }
    result.policy.save(output, extra_metadata=card_extra)
    eval_path = write_report(ope_result, output.with_suffix(output.suffix + ".eval.json"))
    logger.info("rl-train-cli: wrote eval report to %s", eval_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
