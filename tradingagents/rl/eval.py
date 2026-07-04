"""Off-policy evaluation for the trained CQL meta-policy.

Stage B is offline-only: we cannot run the policy in production at this
point in the pipeline. Three complementary OPE estimators are computed so
the model card can quote multiple numbers and the reviewer doesn't have
to trust any single one:

1. **Direct Method (FQE-style):** the Q-network's own estimate of
   ``E[Q(s, π(s))]`` over the held-out dataset. Has the bias of the
   value function but the lowest variance.

2. **Inverse Propensity Score (IPS) / Self-Normalized IPS (SNIPS):**
   reweights observed rewards by ``π(a|s) / β(a|s)``. β is the behavior
   policy (the existing confidence-scaling rule); π is the learned
   greedy policy. High variance but unbiased.

3. **Behavior-policy mean reward:** the simple no-RL baseline. If we
   can't beat this on held-out data we ship the flag off.

Single-step framing recap: there is no Bellman recursion to bootstrap
because γ=0; FQE collapses to the Q-network's per-state head evaluation.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn.functional as F

from tradingagents.rl.config import DECISION_ACTIONS, N_ACTIONS
from tradingagents.rl.policy import RLDecisionPolicy, RLSizingPolicy
from tradingagents.rl.train import TrainingTensors

logger = logging.getLogger("tradingagents.rl.eval")


# ─────────────────────────────────────────────────────────────────────────────
# Result containers
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class OPEResult:
    n_samples: int
    direct_value: float                   # Q(s, π(s)) averaged
    behavior_mean_reward: float            # committee actions' realized reward
    snips_value: float                     # self-normalized IPS
    snips_effective_sample_size: float     # ess for the importance weights
    action_distribution: Dict[str, float]  # π's action histogram on the eval set
    behavior_action_distribution: Dict[str, float]
    # Headline metric enabled by the counterfactual reward: because we know the
    # realized reward of EVERY action at each state, the policy's value is an
    # exact average (no importance weighting, no bias) — and so is the
    # committee's. ``counterfactual_uplift`` is the difference: how much the
    # learned decisions would have improved on the committee on this set.
    counterfactual_policy_value: float = 0.0
    counterfactual_behavior_value: float = 0.0
    counterfactual_uplift: float = 0.0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _epsilon_greedy_pi(q_values: torch.Tensor, *, epsilon: float = 0.05) -> torch.Tensor:
    """Return π(a|s) for an ε-greedy policy from the Q-values.

    We use a small ε so importance ratios never blow up to infinity when
    the learned policy disagrees with the data. ``epsilon=0.05`` puts a
    floor of 1% on any action's probability.
    """
    batch, n_actions = q_values.shape
    pi = torch.full_like(q_values, epsilon / max(1, n_actions))
    greedy = q_values.argmax(dim=1)
    pi[torch.arange(batch), greedy] += (1.0 - epsilon)
    # Re-normalize defensively
    pi = pi / pi.sum(dim=1, keepdim=True)
    return pi


def _empirical_behavior_distribution(
    behavior_actions: torch.Tensor,
    n_actions: int,
) -> torch.Tensor:
    """β(a) — marginal action distribution observed in the dataset.

    Treating β as state-independent is a coarse approximation but is
    standard for OPE on offline-only RL when we have no β(a|s) model.
    A 1/(2N) Laplace smoother keeps importance ratios finite.
    """
    n = behavior_actions.shape[0]
    counts = torch.bincount(behavior_actions, minlength=n_actions).float()
    smoothed = (counts + 1.0 / (2 * max(1, n))) / (n + n_actions * 1.0 / (2 * max(1, n)))
    return smoothed  # (n_actions,)


def _action_histogram(actions: torch.Tensor, n_actions: int) -> Dict[str, float]:
    n = max(1, actions.shape[0])
    counts = torch.bincount(actions, minlength=n_actions).float()
    return {DECISION_ACTIONS[i]: float(counts[i].item() / n) for i in range(n_actions)}


# ─────────────────────────────────────────────────────────────────────────────
# Top-level OPE
# ─────────────────────────────────────────────────────────────────────────────


def evaluate(
    policy: RLSizingPolicy,
    val: TrainingTensors,
    *,
    epsilon: float = 0.05,
) -> OPEResult:
    """Run all three OPE estimators on a held-out :class:`TrainingTensors`.

    Returns an :class:`OPEResult`. Callers (``scripts/train_rl_policy.py``
    and Stage D's walk-forward harness) write this into the model card.
    """
    if not policy.is_loaded:
        raise RuntimeError("Cannot evaluate an unloaded policy")
    if val.states.shape[0] == 0:
        return OPEResult(
            n_samples=0,
            direct_value=0.0,
            behavior_mean_reward=0.0,
            snips_value=0.0,
            snips_effective_sample_size=0.0,
            action_distribution={},
            behavior_action_distribution={},
            notes=["empty validation set"],
        )

    network = policy.q_network
    assert network is not None
    network.eval()

    behavior_actions = val.behavior_action
    arange = torch.arange(behavior_actions.shape[0])

    with torch.no_grad():
        q = network(val.states)                              # (N, A)
        greedy_actions = q.argmax(dim=1)                     # (N,)
        # Direct value = Q(s, π(s)) averaged over the dataset
        direct_q = q.gather(1, greedy_actions.view(-1, 1)).squeeze(1)
        direct_value = float(direct_q.mean().cpu().item())

    # Realized reward of the committee's action at each state.
    rewards = val.reward_matrix.gather(1, behavior_actions.view(-1, 1)).squeeze(1)
    behavior_mean = float(rewards.mean().cpu().item())

    # ── Counterfactual (exact) values ─────────────────────────────────────
    # We know the realized reward of every action, so the on-policy value of
    # the learned policy is simply the average reward of the action it picks —
    # no importance weighting, no bias. Same for the committee. This is the
    # headline RL-vs-committee comparison.
    cf_policy = float(
        val.reward_matrix.gather(1, greedy_actions.view(-1, 1)).squeeze(1).mean().cpu().item()
    )
    cf_behavior = behavior_mean
    cf_uplift = cf_policy - cf_behavior

    # SNIPS = sum( w_i * r_i ) / sum( w_i ), w_i = π(a_i|s_i) / β(a_i)
    pi = _epsilon_greedy_pi(q, epsilon=epsilon)
    beta = _empirical_behavior_distribution(behavior_actions, N_ACTIONS)
    pi_a = pi[arange, behavior_actions]
    beta_a = beta[behavior_actions]
    weights = pi_a / beta_a.clamp_min(1e-6)
    snips_value = float((weights * rewards).sum().cpu().item() / weights.sum().clamp_min(1e-6).cpu().item())
    ess = float((weights.sum() ** 2 / (weights ** 2).sum().clamp_min(1e-12)).cpu().item())

    notes: List[str] = []
    if ess < 5.0:
        notes.append(
            f"SNIPS effective sample size = {ess:.2f} (< 5); "
            f"importance-weighted value is unstable, trust the counterfactual value"
        )
    if val.states.shape[0] < 30:
        notes.append(
            f"validation set has only {val.states.shape[0]} samples; "
            f"all OPE estimates are illustrative, not statistical evidence"
        )

    return OPEResult(
        n_samples=int(val.states.shape[0]),
        direct_value=direct_value,
        behavior_mean_reward=behavior_mean,
        snips_value=snips_value,
        snips_effective_sample_size=ess,
        action_distribution=_action_histogram(greedy_actions, N_ACTIONS),
        behavior_action_distribution=_action_histogram(behavior_actions, N_ACTIONS),
        counterfactual_policy_value=cf_policy,
        counterfactual_behavior_value=cf_behavior,
        counterfactual_uplift=cf_uplift,
        notes=notes,
    )


def compare_to_baseline(result: OPEResult) -> Dict[str, Any]:
    """Pass/fail flag for the pre-registered Stage D criterion.

    Stage B alone cannot trigger the PASS gate (that needs a walk-forward
    backtest). What we can check here is the necessary condition: the
    Q-network's own value estimate must at minimum exceed the behavior-
    policy reward. If it doesn't, no amount of Stage D evaluation will
    save us — abort early.
    """
    direct_uplift = result.direct_value - result.behavior_mean_reward
    snips_uplift = result.snips_value - result.behavior_mean_reward
    return {
        "direct_value": result.direct_value,
        "behavior_mean_reward": result.behavior_mean_reward,
        "snips_value": result.snips_value,
        "counterfactual_policy_value": result.counterfactual_policy_value,
        "counterfactual_behavior_value": result.counterfactual_behavior_value,
        "counterfactual_uplift": result.counterfactual_uplift,
        "direct_uplift_vs_behavior": direct_uplift,
        "snips_uplift_vs_behavior": snips_uplift,
        # Primary criterion: the exact counterfactual uplift of the learned
        # decisions over the committee on the held-out set.
        "preliminary_ok": result.counterfactual_uplift > 0,
        "snips_ok": snips_uplift > 0,
        "snips_ess": result.snips_effective_sample_size,
    }


def write_report(result: OPEResult, output_path: str | Path) -> Path:
    """Dump the OPE result as JSON for the model card / dashboard."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ope_result": result.to_dict(),
        "summary": compare_to_baseline(result),
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return output_path
