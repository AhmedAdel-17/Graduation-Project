#!/usr/bin/env python
"""Incremental (online) RL update for the position-sizing meta-policy.

This is the runnable online-learning loop that complements the one-shot offline
trainer (``scripts/train_rl_policy.py`` / ``tradingagents.rl.train``). Run it
whenever new trade outcomes have matured (their 20-day reward horizon elapsed):
it ingests those outcomes, takes a few guard-railed gradient steps on the
existing Q-network, and writes an updated, audit-stamped checkpoint.

Sources of matured outcomes (both honour the no-look-ahead horizon):
  * Postgres audit DB (``analysis_sessions`` + ``backtest_trades``) — preferred,
    because it recovers the REAL state features per decision.
  * Backtester JSON reports — fallback; features are sparse on this path.

Examples
--------
First time (warm-start from the offline model, ingest the audit DB)::

    python scripts/online_rl_update.py \
        --offline models/rl_sizing.pt \
        --out models/rl_sizing_online.pt \
        --postgres

Subsequent runs (resume the online checkpoint, ingest new JSON reports)::

    python scripts/online_rl_update.py \
        --resume models/rl_sizing_online.pt \
        --out models/rl_sizing_online.pt \
        --reports results/backtest_*.json

The script is fail-safe: if no new matured outcomes are found it makes no
gradient step and leaves the checkpoint unchanged.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
from pathlib import Path

# Ensure repo root on path when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingagents.rl import dataset  # noqa: E402
from tradingagents.rl.online import OnlineConfig, OnlineRLTrainer  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("online_rl_update")


def _load_trainer(args) -> OnlineRLTrainer:
    online_cfg = OnlineConfig(
        learning_rate=args.lr,
        updates_per_call=args.steps,
        min_buffer_to_update=args.min_buffer,
        seed=args.seed,
    )
    if args.resume:
        log.info("Resuming online checkpoint: %s", args.resume)
        t = OnlineRLTrainer.load(args.resume)
        # Apply CLI overrides to the resumed trainer's online config.
        t.online = online_cfg
        return t
    if args.offline:
        log.info("Warm-starting from offline policy: %s", args.offline)
        return OnlineRLTrainer.from_offline_policy(args.offline, online_config=online_cfg)
    log.warning("No --offline / --resume given — COLD starting a fresh Q-network.")
    return OnlineRLTrainer.cold_start(online_config=online_cfg)


def _collect_samples(args):
    samples = []
    if args.postgres:
        log.info("Ingesting matured samples from Postgres ...")
        try:
            samples += dataset.build_from_postgres(horizon_days=args.horizon)
        except Exception as exc:  # pragma: no cover - depends on live DB
            log.warning("Postgres ingestion failed: %s", exc)
    report_paths = []
    for pattern in args.reports or []:
        report_paths += [Path(p) for p in glob.glob(pattern)]
    if report_paths:
        log.info("Ingesting %d JSON report(s) ...", len(report_paths))
        samples += dataset.build_from_json_reports(report_paths, horizon_days=args.horizon)
    return samples


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_argument_group("warm start (choose one)")
    src.add_argument("--offline", help="Path to a saved offline RLSizingPolicy (.pt) to warm-start from")
    src.add_argument("--resume", help="Path to a previously-saved online checkpoint (.pt) to continue")

    data = ap.add_argument_group("matured outcomes (one or both)")
    data.add_argument("--postgres", action="store_true", help="Ingest from the Postgres audit DB")
    data.add_argument("--reports", nargs="*", help="Glob(s) of backtester JSON reports")

    ap.add_argument("--out", required=True, help="Where to write the updated online checkpoint")
    ap.add_argument("--horizon", type=int, default=dataset.DEFAULT_REWARD_HORIZON_DAYS,
                    help="Reward horizon in days (no-look-ahead gate)")
    ap.add_argument("--lr", type=float, default=1e-4, help="Online learning rate")
    ap.add_argument("--steps", type=int, default=8, help="Gradient steps per update")
    ap.add_argument("--min-buffer", type=int, default=16, help="Min buffer size before updating")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--report-out", help="Optional path to write a JSON run summary")
    args = ap.parse_args()

    trainer = _load_trainer(args)
    samples = _collect_samples(args)
    log.info("Collected %d candidate samples (PENDING rows are dropped).", len(samples))

    n_new = trainer.ingest_samples(samples)
    log.info("Committed %d NEW transitions to the replay buffer (size=%d).",
             n_new, len(trainer.buffer))

    result = trainer.update()
    log.info("Update: %s", result.reason)

    out_path = trainer.save(args.out)
    summary = {
        "out": str(out_path),
        "n_new_transitions": n_new,
        "buffer_size": len(trainer.buffer),
        "update": result.to_dict(),
        "fingerprint": trainer.model_fingerprint,
    }
    if args.report_out:
        Path(args.report_out).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        log.info("Wrote run summary to %s", args.report_out)

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
