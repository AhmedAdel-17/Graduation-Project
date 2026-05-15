"""PR 9 test suite — Sentiment surfacing helpers.

47 tests across 9 test classes.  No LLM, no I/O.
All functions under test are pure (tradingagents/sentiment/surfacing.py).

Regression gates: must stay green alongside PR 1-8 suites.
"""

import pytest
from tradingagents.sentiment.surfacing import (
    _extract_key_from_audit,
    _is_no_signal_layer_c,
    _safe_float,
    build_sentiment_context_event,
    extract_sentiment_audit_record,
    format_sentiment_for_api,
    format_sentiment_for_cli,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

def _make_state(
    *,
    layer_c_status="SIGNAL",
    overall_status="OK",
    conf_mult=0.85,
    size_mult=0.75,
    audit="macro=RISK_OFF|market=FEAR|sector=banks",
    narrative="Market is bearish.",
    cited_post_ids=None,
    sentiment_report="Social data shows fear.",
    blend_result_key="sentiment_blend_result",
) -> dict:
    """Build a minimal final_state dict that mimics the graph output."""
    blend: dict = {}
    if conf_mult is not None:
        blend["confidence_multiplier"] = conf_mult
    if size_mult is not None:
        blend["position_size_multiplier"] = size_mult
    if audit is not None:
        blend["audit"] = audit

    return {
        blend_result_key: blend,
        "confidence_scores": {"overall_status": overall_status},
        "social_sentiment_analysis": {
            "layer_c_status": layer_c_status,
            "llm_narrative": narrative,
            "cited_post_ids": cited_post_ids or ["p1", "p2"],
        },
        "sentiment_report": sentiment_report,
    }


_EMPTY_STATE: dict = {}

_NO_SIGNAL_STATE = _make_state(
    layer_c_status="NO_SIGNAL: n_strong_mentions gate failed (gate=n_strong_mentions, k=2, threshold=5)",
    overall_status="INSUFFICIENT_DATA",
    conf_mult=None,
    size_mult=None,
    audit=None,
    narrative=None,
    cited_post_ids=None,
)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

class TestIsNoSignalLayerC:
    def test_none_returns_true(self):
        assert _is_no_signal_layer_c(None) is True

    def test_empty_string_returns_true(self):
        assert _is_no_signal_layer_c("") is True

    def test_no_signal_prefix(self):
        assert _is_no_signal_layer_c("NO_SIGNAL: gate failed") is True

    def test_no_signal_lowercase(self):
        assert _is_no_signal_layer_c("no_signal: something") is True

    def test_signal_string(self):
        assert _is_no_signal_layer_c("SIGNAL") is False

    def test_insufficient_in_status(self):
        assert _is_no_signal_layer_c("insufficient data") is True

    def test_unknown_string_is_no_signal(self):
        # "unknown" doesn't contain marker → not no-signal
        assert _is_no_signal_layer_c("ACTIVE") is False


class TestSafeFloat:
    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_int_coerced(self):
        assert _safe_float(1) == 1.0

    def test_str_coerced(self):
        assert _safe_float("0.85") == pytest.approx(0.85)

    def test_invalid_returns_none(self):
        assert _safe_float("abc") is None

    def test_list_returns_none(self):
        assert _safe_float([1, 2]) is None


class TestExtractKeyFromAudit:
    def test_pipe_separated(self):
        audit = "macro=RISK_OFF|market=FEAR|sector=banks"
        assert _extract_key_from_audit(audit, "macro") == "RISK_OFF"
        assert _extract_key_from_audit(audit, "market") == "FEAR"
        assert _extract_key_from_audit(audit, "sector") == "banks"

    def test_comma_separated(self):
        audit = "macro=NEUTRAL,market=PANIC"
        assert _extract_key_from_audit(audit, "macro") == "NEUTRAL"
        assert _extract_key_from_audit(audit, "market") == "PANIC"

    def test_key_not_present(self):
        assert _extract_key_from_audit("macro=RISK_OFF", "sector") is None

    def test_none_audit(self):
        assert _extract_key_from_audit(None, "macro") is None

    def test_empty_audit(self):
        assert _extract_key_from_audit("", "macro") is None


# ──────────────────────────────────────────────────────────────────────────────
# 2. extract_sentiment_audit_record
# ──────────────────────────────────────────────────────────────────────────────

class TestExtractSentimentAuditRecord:
    def test_full_state_all_keys_present(self):
        state = _make_state()
        rec = extract_sentiment_audit_record(state)
        assert rec["layer_c_status"] == "SIGNAL"
        assert rec["overall_status"] == "OK"
        assert rec["is_no_signal"] is False
        assert rec["confidence_multiplier"] == pytest.approx(0.85)
        assert rec["position_size_multiplier"] == pytest.approx(0.75)
        assert rec["blend_audit"] == "macro=RISK_OFF|market=FEAR|sector=banks"
        assert rec["llm_narrative"] == "Market is bearish."
        assert rec["cited_post_ids"] == ["p1", "p2"]
        assert rec["sentiment_report_snippet"] == "Social data shows fear."
        assert rec["macro_direction"] == "RISK_OFF"
        assert rec["market_regime"] == "FEAR"
        assert rec["sector_tilt"] == "banks"

    def test_empty_state_never_raises(self):
        rec = extract_sentiment_audit_record(_EMPTY_STATE)
        assert isinstance(rec, dict)
        assert rec["is_no_signal"] is True  # unknown → conservative default

    def test_no_signal_state(self):
        rec = extract_sentiment_audit_record(_NO_SIGNAL_STATE)
        assert rec["is_no_signal"] is True
        assert rec["confidence_multiplier"] is None
        assert rec["position_size_multiplier"] is None
        assert rec["overall_status"] == "INSUFFICIENT_DATA"

    def test_snippet_truncated_at_200(self):
        long_report = "x" * 500
        state = _make_state(sentiment_report=long_report)
        rec = extract_sentiment_audit_record(state)
        assert len(rec["sentiment_report_snippet"]) == 200

    def test_none_sentiment_report(self):
        state = _make_state(sentiment_report=None)
        rec = extract_sentiment_audit_record(state)
        assert rec["sentiment_report_snippet"] is None

    def test_missing_blend_result_key(self):
        state = {"confidence_scores": {"overall_status": "OK"}}
        rec = extract_sentiment_audit_record(state)
        assert rec["confidence_multiplier"] is None
        assert rec["position_size_multiplier"] is None

    def test_non_dict_blend_result_safe(self):
        state = {"sentiment_blend_result": "garbage"}
        rec = extract_sentiment_audit_record(state)
        assert rec["confidence_multiplier"] is None


# ──────────────────────────────────────────────────────────────────────────────
# 3. format_sentiment_for_api
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatSentimentForApi:
    def test_full_signal_has_blend_modifiers(self):
        state = _make_state()
        api = format_sentiment_for_api(state)
        assert api["is_no_signal"] is False
        assert api["blend_modifiers"] is not None
        assert api["blend_modifiers"]["confidence"] == pytest.approx(0.85)
        assert api["blend_modifiers"]["position_size"] == pytest.approx(0.75)
        assert api["blend_modifiers"]["audit"] is not None

    def test_no_signal_blend_modifiers_is_none(self):
        api = format_sentiment_for_api(_NO_SIGNAL_STATE)
        assert api["is_no_signal"] is True
        assert api["blend_modifiers"] is None

    def test_empty_state_safe(self):
        api = format_sentiment_for_api(_EMPTY_STATE)
        assert isinstance(api, dict)
        assert api["is_no_signal"] is True
        assert api["blend_modifiers"] is None

    def test_macro_market_sector_forwarded(self):
        state = _make_state()
        api = format_sentiment_for_api(state)
        assert api["macro_direction"] == "RISK_OFF"
        assert api["market_regime"] == "FEAR"
        assert api["sector_tilt"] == "banks"

    def test_narrative_forwarded(self):
        state = _make_state(narrative="Bullish sentiment detected.")
        api = format_sentiment_for_api(state)
        assert api["narrative"] == "Bullish sentiment detected."

    def test_cited_post_ids_forwarded(self):
        state = _make_state(cited_post_ids=["abc", "def"])
        api = format_sentiment_for_api(state)
        assert api["cited_post_ids"] == ["abc", "def"]


# ──────────────────────────────────────────────────────────────────────────────
# 4. format_sentiment_for_cli
# ──────────────────────────────────────────────────────────────────────────────

class TestFormatSentimentForCli:
    def test_returns_string(self):
        result = format_sentiment_for_cli(_make_state())
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_layer_c_status(self):
        result = format_sentiment_for_cli(_make_state())
        assert "SIGNAL" in result

    def test_contains_blend_multipliers(self):
        result = format_sentiment_for_cli(_make_state(conf_mult=0.70, size_mult=0.50))
        assert "0.70" in result
        assert "0.50" in result

    def test_no_signal_shows_pass_through(self):
        result = format_sentiment_for_cli(_NO_SIGNAL_STATE)
        assert "pass-through" in result.lower() or "no modifiers" in result.lower()

    def test_narrative_included_when_present(self):
        state = _make_state(narrative="Strong buying interest in banks.")
        result = format_sentiment_for_cli(state)
        assert "Strong buying interest" in result

    def test_narrative_truncated_at_300(self):
        state = _make_state(narrative="y" * 500)
        result = format_sentiment_for_cli(state)
        assert "…" in result

    def test_empty_state_never_raises(self):
        result = format_sentiment_for_cli(_EMPTY_STATE)
        assert isinstance(result, str)

    def test_context_line_present_when_audit_has_keys(self):
        result = format_sentiment_for_cli(_make_state())
        assert "macro=" in result.lower() or "RISK_OFF" in result


# ──────────────────────────────────────────────────────────────────────────────
# 5. build_sentiment_context_event
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildSentimentContextEvent:
    def _event(self, state=None):
        return build_sentiment_context_event(
            session_id="abc12345",
            ticker="COMI",
            trade_date="2026-05-02",
            final_state=state or _make_state(),
            logged_at="2026-05-02T12:00:00Z",
        )

    def test_event_type(self):
        ev = self._event()
        assert ev["event"] == "SENTIMENT_CONTEXT"

    def test_required_fields_present(self):
        ev = self._event()
        for key in ("_session_id", "ticker", "trade_date", "_logged_at", "agent_name"):
            assert key in ev

    def test_agent_name_is_blender(self):
        ev = self._event()
        assert ev["agent_name"] == "SentimentBlender"

    def test_session_id_forwarded(self):
        ev = self._event()
        assert ev["_session_id"] == "abc12345"

    def test_blend_fields_populated(self):
        ev = self._event()
        assert ev["confidence_multiplier"] == pytest.approx(0.85)
        assert ev["position_size_multiplier"] == pytest.approx(0.75)

    def test_no_signal_state_event(self):
        ev = self._event(_NO_SIGNAL_STATE)
        assert ev["is_no_signal"] is True
        assert ev["confidence_multiplier"] is None

    def test_empty_state_never_raises(self):
        ev = self._event(_EMPTY_STATE)
        assert ev["event"] == "SENTIMENT_CONTEXT"

    def test_all_structured_fields_in_event(self):
        ev = self._event()
        for key in (
            "layer_c_status",
            "overall_status",
            "is_no_signal",
            "confidence_multiplier",
            "position_size_multiplier",
            "blend_audit",
            "macro_direction",
            "market_regime",
            "sector_tilt",
            "llm_narrative",
            "cited_post_ids",
        ):
            assert key in ev, f"Missing key: {key}"


# ──────────────────────────────────────────────────────────────────────────────
# 6. Determinism & idempotency
# ──────────────────────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_extract_is_idempotent(self):
        state = _make_state()
        r1 = extract_sentiment_audit_record(state)
        r2 = extract_sentiment_audit_record(state)
        assert r1 == r2

    def test_format_api_is_idempotent(self):
        state = _make_state()
        assert format_sentiment_for_api(state) == format_sentiment_for_api(state)

    def test_format_cli_is_idempotent(self):
        state = _make_state()
        assert format_sentiment_for_cli(state) == format_sentiment_for_cli(state)

    def test_state_not_mutated(self):
        state = _make_state()
        import copy
        original = copy.deepcopy(state)
        extract_sentiment_audit_record(state)
        assert state == original
