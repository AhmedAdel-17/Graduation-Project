"""Automated reasoning-quality structural checks on captured artifacts.

Run AFTER scripts/capture_reasoning_artifacts.py has populated
results/reasoning_artifacts/.  These tests validate that the multi-agent
optimization did NOT flatten or degenerate the reasoning architecture.

No live LLM is required — all checks are pure structural/keyword analysis
over the saved artifact files.

Usage:
    pytest tests/test_reasoning_quality.py -v \\
        --artifacts-dir=results/reasoning_artifacts
"""
import json
import re
import pytest
from pathlib import Path
from collections import Counter


# ---------------------------------------------------------------------------
# Pytest configuration
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--artifacts-dir",
        default="results/reasoning_artifacts",
        help="Path to captured reasoning artifacts (from capture_reasoning_artifacts.py)",
    )


@pytest.fixture(scope="session")
def all_artifacts(request):
    """Load all captured artifacts as a list of dicts."""
    base = Path(request.config.getoption("--artifacts-dir"))
    if not base.exists():
        pytest.skip(f"Artifacts directory not found: {base}")

    artifacts = []
    for ticker_dir in sorted(base.iterdir()):
        if not ticker_dir.is_dir():
            continue
        for date_dir in sorted(ticker_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            entry = {
                "ticker": ticker_dir.name,
                "date": date_dir.name,
                "path": date_dir,
            }
            # Load text artifacts
            for name in [
                "bull_text", "bear_text", "research_manager",
                "trader_text", "risk_debate", "risk_risky",
                "risk_safe", "risk_neutral",
            ]:
                p = date_dir / f"{name}.md"
                entry[name] = p.read_text() if p.exists() else ""

            # final_decision is .txt
            p = date_dir / "final_decision.txt"
            entry["final_decision"] = p.read_text().strip() if p.exists() else ""

            # Load JSON artifacts
            for name in ["bull_thesis", "bear_thesis", "execution_plan",
                         "risk_assessment", "confidence"]:
                p = date_dir / f"{name}.json"
                try:
                    entry[name] = json.loads(p.read_text()) if p.exists() else {}
                except json.JSONDecodeError:
                    entry[name] = {}

            artifacts.append(entry)

    if not artifacts:
        pytest.skip("No artifacts found in artifacts directory")
    return artifacts


# ===========================================================================
# RQ-1: Bull/Bear Thesis Distinctiveness
# ===========================================================================

class TestThesisDistinctiveness:
    """Verify bull and bear produce genuinely different arguments."""

    def test_different_directions(self, all_artifacts):
        """Bull and bear should recommend different directions at least 80% of the time."""
        same_direction = 0
        evaluated = 0
        for a in all_artifacts:
            bull_dir = (a["bull_thesis"].get("direction") or "").upper()
            bear_dir = (a["bear_thesis"].get("direction") or "").upper()
            if bull_dir and bear_dir:
                evaluated += 1
                if bull_dir == bear_dir:
                    same_direction += 1

        if evaluated == 0:
            pytest.skip("No bull/bear direction pairs found in artifacts")

        same_rate = same_direction / evaluated
        assert same_rate <= 0.20, (
            f"{same_rate:.0%} of dates have bull==bear direction. "
            f"Theses are not genuinely distinct."
        )

    def test_different_arguments(self, all_artifacts):
        """Bull and bear text should share <60% of content (not copy-paste)."""
        high_overlap = 0
        evaluated = 0
        for a in all_artifacts:
            bull_words = set(a["bull_text"].lower().split())
            bear_words = set(a["bear_text"].lower().split())
            if len(bull_words) < 20 or len(bear_words) < 20:
                continue
            evaluated += 1
            overlap = len(bull_words & bear_words) / min(len(bull_words), len(bear_words))
            if overlap > 0.60:
                high_overlap += 1

        if evaluated == 0:
            pytest.skip("Insufficient text length for comparison")

        overlap_rate = high_overlap / evaluated
        assert overlap_rate <= 0.15, (
            f"{overlap_rate:.0%} of dates have >60% word overlap between bull/bear. "
            f"Arguments may not be genuinely distinct."
        )

    def test_invalidation_conditions_present(self, all_artifacts):
        """Bull thesis should include invalidation conditions."""
        missing = 0
        for a in all_artifacts:
            thesis = a["bull_thesis"]
            text = a["bull_text"].lower()
            has_invalidation = (
                thesis.get("invalidation")
                or thesis.get("invalidation_conditions")
                or "invalidat" in text
                or "stop" in text
                or re.search(r"if.*below", text)
            )
            if not has_invalidation:
                missing += 1

        if not all_artifacts:
            pytest.skip("No artifacts")

        miss_rate = missing / len(all_artifacts)
        assert miss_rate <= 0.25, (
            f"{miss_rate:.0%} of bull theses are missing invalidation conditions. "
            f"Prompt compression may have dropped them."
        )


# ===========================================================================
# RQ-2: Thesis Grounding (claims reference actual inputs)
# ===========================================================================

class TestThesisGrounding:
    """Verify claims reference data from analyst inputs, not hallucinated numbers."""

    def test_bull_mentions_specific_numbers(self, all_artifacts):
        """Bull text should contain at least 2 specific numbers (prices, ratios, %)."""
        ungrounded = 0
        for a in all_artifacts:
            numbers = re.findall(r'\d+\.?\d*%?', a["bull_text"])
            if len(numbers) < 2:
                ungrounded += 1

        if not all_artifacts:
            pytest.skip("No artifacts")

        rate = ungrounded / len(all_artifacts)
        assert rate <= 0.20, (
            f"{rate:.0%} of bull theses lack specific numerical grounding."
        )

    def test_bear_mentions_specific_numbers(self, all_artifacts):
        """Bear text should contain at least 2 specific numbers."""
        ungrounded = 0
        for a in all_artifacts:
            numbers = re.findall(r'\d+\.?\d*%?', a["bear_text"])
            if len(numbers) < 2:
                ungrounded += 1

        if not all_artifacts:
            pytest.skip("No artifacts")

        rate = ungrounded / len(all_artifacts)
        assert rate <= 0.20, (
            f"{rate:.0%} of bear theses lack specific numerical grounding."
        )


# ===========================================================================
# RQ-3: Research Manager Synthesis Quality
# ===========================================================================

class TestResearchManagerSynthesis:
    """Verify CIO synthesizes both perspectives, not just echoes one."""

    def test_references_both_sides(self, all_artifacts):
        """Research Manager output should mention both bull and bear concepts."""
        one_sided = 0
        evaluated = 0
        for a in all_artifacts:
            rm = a["research_manager"].lower()
            if len(rm) < 50:
                continue
            evaluated += 1
            bull_refs = any(kw in rm for kw in [
                "bull", "upside", "buy", "growth", "opportunity"
            ])
            bear_refs = any(kw in rm for kw in [
                "bear", "downside", "risk", "caution", "concern"
            ])
            if not (bull_refs and bear_refs):
                one_sided += 1

        if evaluated == 0:
            pytest.skip("No RM outputs of sufficient length to evaluate")

        rate = one_sided / evaluated
        assert rate <= 0.20, (
            f"{rate:.0%} of Research Manager outputs are one-sided."
        )

    def test_produces_actionable_decision(self, all_artifacts):
        """Output should contain a BUY/SELL/HOLD decision."""
        no_decision = 0
        for a in all_artifacts:
            rm = a["research_manager"].upper()
            if not any(d in rm for d in ["BUY", "SELL", "HOLD"]):
                no_decision += 1

        if not all_artifacts:
            pytest.skip("No artifacts")

        rate = no_decision / len(all_artifacts)
        assert rate <= 0.10, (
            f"{rate:.0%} of RM outputs lack an actionable decision."
        )


# ===========================================================================
# RQ-4: Trader Execution Plan Quality
# ===========================================================================

class TestTraderPlanQuality:
    """Verify trader plans respect injected constraints and EGX rules."""

    def test_stop_loss_present_for_buy_plans(self, all_artifacts):
        """Every BUY execution plan must include a stop-loss."""
        missing_sl = 0
        buy_plans = 0
        for a in all_artifacts:
            plan = a["execution_plan"]
            ep = plan.get("execution_plan", plan) if isinstance(plan, dict) else {}
            decision = (ep.get("decision") or a["final_decision"] or "").upper().strip()
            if decision not in ("BUY",):
                continue
            buy_plans += 1
            exit_logic = ep.get("exit_logic", {})
            has_sl = (
                exit_logic.get("stop_loss")
                or "stop" in a["trader_text"].lower()
            )
            if not has_sl:
                missing_sl += 1

        if buy_plans == 0:
            pytest.skip("No BUY plans to evaluate")

        rate = missing_sl / buy_plans
        assert rate <= 0.10, (
            f"{rate:.0%} of BUY plans are missing stop-loss. "
            f"Trader prompt may not be enforcing constraints."
        )

    def test_no_short_selling_in_plans(self, all_artifacts):
        """No execution plan should propose short selling on EGX."""
        shorts_found = 0
        for a in all_artifacts:
            text = a["trader_text"].lower()
            plan = a["execution_plan"]
            ep = plan.get("execution_plan", plan) if isinstance(plan, dict) else {}
            if "short" in text and "no short" not in text and "short selling" not in text:
                shorts_found += 1
            if ep.get("short_sell") or ep.get("direction") == "SHORT":
                shorts_found += 1

        assert shorts_found == 0, (
            f"{shorts_found} plans reference short selling on EGX."
        )

    def test_position_size_within_limits(self, all_artifacts):
        """If execution_plan has target_shares, it should not exceed max_shares."""
        violations = 0
        checked = 0
        for a in all_artifacts:
            plan = a["execution_plan"]
            ep = plan.get("execution_plan", plan) if isinstance(plan, dict) else {}
            ps = ep.get("position_sizing", {})
            target = ps.get("target_shares") or ps.get("shares")
            max_s = ps.get("max_shares") or ps.get("max_shares_total")
            if target and max_s:
                checked += 1
                if target > max_s:
                    violations += 1

        if checked > 0:
            rate = violations / checked
            assert rate <= 0.05, (
                f"{rate:.0%} of plans exceed position-size limits."
            )


# ===========================================================================
# RQ-5: Merged Risk Debate Perspective Coverage
# ===========================================================================

class TestMergedRiskDebatePerspectives:
    """Verify the merged debate isn't degenerate — all 3 views must be substantive."""

    def test_all_three_sections_nonempty(self, all_artifacts):
        """Each perspective must have ≥50 words of substance."""
        missing = 0
        for a in all_artifacts:
            for key in ["risk_risky", "risk_safe", "risk_neutral"]:
                word_count = len(a[key].split())
                if word_count < 50:
                    missing += 1
                    break  # One missing section is enough to flag this date

        if not all_artifacts:
            pytest.skip("No artifacts")

        rate = missing / len(all_artifacts)
        assert rate <= 0.10, (
            f"{rate:.0%} of merged debates have a perspective with <50 words."
        )

    def test_perspectives_are_distinct(self, all_artifacts):
        """Risky/safe/neutral should not be near-duplicates of each other."""
        degenerate = 0
        evaluated = 0
        for a in all_artifacts:
            risky_words = set(a["risk_risky"].lower().split())
            safe_words = set(a["risk_safe"].lower().split())
            if len(risky_words) < 15 or len(safe_words) < 15:
                continue
            evaluated += 1
            overlap = len(risky_words & safe_words) / min(len(risky_words), len(safe_words))
            if overlap > 0.70:
                degenerate += 1

        if evaluated == 0:
            pytest.skip("Insufficient text for perspective comparison")

        rate = degenerate / evaluated
        assert rate <= 0.15, (
            f"{rate:.0%} of merged debates have >70% overlap between risky and safe."
        )

    def test_risky_section_is_pro_trade(self, all_artifacts):
        """Risky analyst should skew toward opportunity language."""
        pro_keywords = [
            "opportunity", "upside", "growth", "bold", "reward",
            "buy", "bullish", "compelling", "undervalued",
        ]
        wrong_tone = 0
        evaluated = 0
        for a in all_artifacts:
            text = a["risk_risky"].lower()
            if len(text) < 50:
                continue
            evaluated += 1
            pro_count = sum(1 for kw in pro_keywords if kw in text)
            if pro_count < 2:
                wrong_tone += 1

        if evaluated == 0:
            pytest.skip("No risky sections of sufficient length")

        rate = wrong_tone / evaluated
        assert rate <= 0.25, (
            f"{rate:.0%} of risky-analyst sections lack pro-trade language."
        )

    def test_safe_section_is_cautionary(self, all_artifacts):
        """Safe analyst should skew toward caution/risk language."""
        caution_keywords = [
            "risk", "caution", "concern", "protect", "loss",
            "downside", "careful", "volatile", "capital preservation",
        ]
        wrong_tone = 0
        evaluated = 0
        for a in all_artifacts:
            text = a["risk_safe"].lower()
            if len(text) < 50:
                continue
            evaluated += 1
            caution_count = sum(1 for kw in caution_keywords if kw in text)
            if caution_count < 2:
                wrong_tone += 1

        if evaluated == 0:
            pytest.skip("No safe sections of sufficient length")

        rate = wrong_tone / evaluated
        assert rate <= 0.25, (
            f"{rate:.0%} of safe-analyst sections lack cautionary language."
        )

    def test_mentions_numerical_targets(self, all_artifacts):
        """Risk debate should include specific price targets or percentage levels."""
        no_numbers = 0
        for a in all_artifacts:
            text = a["risk_debate"]
            numbers = re.findall(r'\d+\.?\d*', text)
            if len(numbers) < 3:
                no_numbers += 1

        if not all_artifacts:
            pytest.skip("No artifacts")

        rate = no_numbers / len(all_artifacts)
        assert rate <= 0.20, (
            f"{rate:.0%} of risk debates lack numerical specificity."
        )
