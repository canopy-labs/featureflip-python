"""Tests for EvaluationContext."""

from decimal import Decimal

import pytest

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

    def test_from_dict_keeps_integer_zero_user_id(self) -> None:
        ctx = EvaluationContext.from_dict({"user_id": 0})
        assert ctx.user_id == "0"

    def test_from_dict_keeps_false_user_id(self) -> None:
        ctx = EvaluationContext.from_dict({"user_id": False})
        assert ctx.user_id == "false"

    def test_from_dict_empty_user_id_falls_back_to_userId(self) -> None:
        ctx = EvaluationContext.from_dict({"user_id": "", "userId": "u"})
        assert ctx.user_id == "u"

    def test_from_dict_user_id_none_uses_userId(self) -> None:
        ctx = EvaluationContext.from_dict({"user_id": None, "userId": "u"})
        assert ctx.user_id == "u"

    def test_from_dict_userId_empty_string_kept(self) -> None:
        ctx = EvaluationContext.from_dict({"userId": ""})
        assert ctx.user_id == ""

    def test_from_dict_user_id_empty_string_alone_is_keyless(self) -> None:
        ctx = EvaluationContext.from_dict({"user_id": ""})
        assert ctx.user_id is None


class TestFromDictCanonicalUserId:
    """#4250: float/Decimal user ids use the canonical bucket spelling, not str()."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1.0, "1"),
            (1e16, "10000000000000000"),
            (2.5e-7, "2.5e-7"),
            (1.5, "1.5"),
            (Decimal("1.0"), "1"),
            # #4246: a huge-exponent Decimal goes through float, not a 100001-digit string.
            (Decimal("1E+100000"), "Infinity"),
            (0, "0"),
            (42, "42"),
            (True, "true"),
            (False, "false"),
            ("abc", "abc"),
        ],
    )
    def test_user_id_is_canonical(self, value: object, expected: str) -> None:
        assert EvaluationContext.from_dict({"user_id": value}).user_id == expected
        assert EvaluationContext.from_dict({"userId": value}).user_id == expected

    def test_none_and_empty_unchanged(self) -> None:
        assert EvaluationContext.from_dict({"user_id": None}).user_id is None
        assert EvaluationContext.from_dict({"user_id": "", "userId": 1.0}).user_id == "1"
        assert EvaluationContext.from_dict({"user_id": ""}).user_id is None
