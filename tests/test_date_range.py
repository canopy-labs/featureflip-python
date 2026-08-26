"""Tests that a date operand outside ``DateTimeOffset``'s range matches nothing.

The engine parses a ``Before``/``After`` operand with ``DateTimeOffset.TryParse``, so
its accepted set is bounded by ``DateTimeOffset``'s range — 0001-01-01T00:00:00Z to
9999-12-31T23:59:59.9999999Z — and it returns false outside it. js, ruby, php, go and
java resolved past both ends and served a wrong variation; python did something worse
and unique to it: :meth:`datetime.astimezone` raises ``OverflowError`` — **not**
``ValueError``, which is the only exception ``_parse_datetime`` caught — so the operand
propagated an exception out of ``evaluate_condition`` instead of returning no-match.

That is the distinction these tests exist for. Everywhere else #2500 is a divergence;
here it is a CRASH, so one saved rule took the calling application down rather than
serving the wrong value. The golden vectors assert the shared no-match contract across
all eight implementations; these assert the python-specific property that getting there
does not raise, which a vector asserting only the boolean cannot express.

The reachable shapes are both a year the grammar admits AND an offset:
``[0-9]{4}`` constrains the WRITTEN year while the offset moves the resolved instant, so
``0001-01-01T00:00:00+05:00`` lands below the floor and ``9999-12-31T23:59:59-05:00``
above the ceiling — which is why the upper bound is reachable at all (#2500).
"""

import pytest

from featureflip.context import EvaluationContext
from featureflip.evaluation import FlagEvaluator
from featureflip.models import Condition, ConditionOperator

# Operands the engine rejects. Year 0 is a REAL proleptic date (0000-02-29 exists, since
# year 0 divides by 400), so it clears both the ISO grammar and #2491's calendar check
# and is excluded on RANGE alone.
OUT_OF_RANGE = [
    "0000-01-01",
    "0000-02-29",
    "0000-12-31",
    "0000-12-31T23:59:59Z",
    "0001-01-01T00:00:00+05:00",
    "0001-01-01T04:59:59+05:00",
    "0001-01-01T13:59:59+14:00",
    "9999-12-31T23:59:59-05:00",
    "9999-12-31T19:00:00-05:00",
]

# The exact ends of the range, which must stay ACCEPTED — a guard that ate its own
# boundary would look identical in a no-match assertion.
IN_RANGE_BOUNDARY = [
    "0001-01-01",
    "0001-01-01T00:00:00Z",
    "0001-01-01T05:00:00+05:00",
    "0001-01-01T14:00:00+14:00",
    "9999-12-31",
    "9999-12-31T23:59:59Z",
    "9999-12-31T18:59:59-05:00",
    "9999-12-31T09:59:59-14:00",
]


class TestDateRange:
    @pytest.mark.parametrize("operand", OUT_OF_RANGE)
    def test_out_of_range_operand_returns_none_rather_than_raising(
        self, operand: str
    ) -> None:
        """The parse itself must fail closed, not raise."""
        assert FlagEvaluator()._parse_datetime(operand) is None

    @pytest.mark.parametrize("operand", IN_RANGE_BOUNDARY)
    def test_boundary_operand_still_parses(self, operand: str) -> None:
        assert FlagEvaluator()._parse_datetime(operand) is not None

    @pytest.mark.parametrize("operand", OUT_OF_RANGE)
    @pytest.mark.parametrize(
        "operator", [ConditionOperator.AFTER, ConditionOperator.BEFORE]
    )
    def test_out_of_range_target_never_matches(
        self, operand: str, operator: ConditionOperator
    ) -> None:
        """Driven through ``evaluate_condition`` — the layer the OverflowError escaped."""
        condition = Condition(
            attribute="attr", operator=operator, values=[operand], negate=False
        )
        context = EvaluationContext(attributes={"attr": "2020-01-01T00:00:00Z"})
        assert FlagEvaluator().evaluate_condition(condition, context) is False

    @pytest.mark.parametrize("attr_value", OUT_OF_RANGE)
    @pytest.mark.parametrize(
        "operator", [ConditionOperator.AFTER, ConditionOperator.BEFORE]
    )
    def test_out_of_range_attribute_never_matches(
        self, attr_value: str, operator: ConditionOperator
    ) -> None:
        """The ATTRIBUTE side goes through the same parser, and is the side a customer
        does not control — an out-of-range value arriving in a context attribute must
        not be able to raise out of evaluation either."""
        condition = Condition(
            attribute="attr",
            operator=operator,
            values=["1970-01-01T00:00:00Z"],
            negate=False,
        )
        context = EvaluationContext(attributes={"attr": attr_value})
        assert FlagEvaluator().evaluate_condition(condition, context) is False

    @pytest.mark.parametrize("operand", OUT_OF_RANGE)
    def test_negate_inverts_the_no_match_rather_than_raising(self, operand: str) -> None:
        """``negate`` inverts an unparseable comparison to True, matching the engine.

        Locked deliberately: this is the #2262 shape, where a default-false arm plus
        ``negate`` silently matched EVERY user. Here it is the engine's own answer, so
        the point is that python reaches it without raising rather than that it is
        surprising."""
        condition = Condition(
            attribute="attr",
            operator=ConditionOperator.AFTER,
            values=[operand],
            negate=True,
        )
        context = EvaluationContext(attributes={"attr": "2020-01-01T00:00:00Z"})
        assert FlagEvaluator().evaluate_condition(condition, context) is True
