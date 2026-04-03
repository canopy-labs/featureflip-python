"""Tests for EvaluationContext."""

from featureflip.context import EvaluationContext


class TestEvaluationContext:
    def test_create_from_dict(self) -> None:
        ctx = EvaluationContext.from_dict({
            "user_id": "123",
            "email": "alice@example.com",
            "plan": "pro",
        })
        assert ctx.user_id == "123"
        assert ctx.get_attribute("email") == "alice@example.com"
        assert ctx.get_attribute("plan") == "pro"

    def test_user_id_from_attributes(self) -> None:
        ctx = EvaluationContext.from_dict({"user_id": "456"})
        assert ctx.user_id == "456"
        assert ctx.get_attribute("user_id") == "456"

    def test_missing_attribute_returns_none(self) -> None:
        ctx = EvaluationContext.from_dict({"user_id": "123"})
        assert ctx.get_attribute("missing") is None

    def test_empty_context(self) -> None:
        ctx = EvaluationContext.from_dict({})
        assert ctx.user_id is None
        assert ctx.get_attribute("anything") is None

    def test_non_string_user_id_converted(self) -> None:
        ctx = EvaluationContext.from_dict({"user_id": 123})
        assert ctx.user_id == "123"

    def test_attributes_preserved(self) -> None:
        ctx = EvaluationContext.from_dict({
            "user_id": "123",
            "count": 42,
            "active": True,
            "tags": ["a", "b"],
        })
        assert ctx.get_attribute("count") == 42
        assert ctx.get_attribute("active") is True
        assert ctx.get_attribute("tags") == ["a", "b"]
