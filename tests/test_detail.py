"""Tests for EvaluationDetail."""

from featureflip.detail import EvaluationDetail, EvaluationReason


class TestEvaluationDetail:
    def test_basic_detail(self) -> None:
        detail = EvaluationDetail(
            value=True,
            reason=EvaluationReason.FALLTHROUGH,
        )
        assert detail.value is True
        assert detail.reason == EvaluationReason.FALLTHROUGH
        assert detail.rule_id is None
        assert detail.error is None

    def test_rule_match_detail(self) -> None:
        detail = EvaluationDetail(
            value="pro",
            reason=EvaluationReason.RULE_MATCH,
            rule_id="rule-123",
        )
        assert detail.value == "pro"
        assert detail.reason == EvaluationReason.RULE_MATCH
        assert detail.rule_id == "rule-123"

    def test_error_detail(self) -> None:
        error = ValueError("test error")
        detail = EvaluationDetail(
            value=False,
            reason=EvaluationReason.ERROR,
            error=error,
        )
        assert detail.value is False
        assert detail.reason == EvaluationReason.ERROR
        assert detail.error is error


class TestEvaluationReason:
    def test_all_reasons_exist(self) -> None:
        assert EvaluationReason.FALLTHROUGH.value == "FALLTHROUGH"
        assert EvaluationReason.RULE_MATCH.value == "RULE_MATCH"
        assert EvaluationReason.FLAG_DISABLED.value == "FLAG_DISABLED"
        assert EvaluationReason.FLAG_NOT_FOUND.value == "FLAG_NOT_FOUND"
        assert EvaluationReason.ERROR.value == "ERROR"
