import pytest

from featureflip._canonical_number import canonical_number


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "1"),
        (1.5, "1.5"),
        (0.1, "0.1"),
        (0.30000000000000004, "0.30000000000000004"),
        (-0.0, "0"),
        (0.0, "0"),
        (100.25, "100.25"),
        (1e21, "1e+21"),
        (1.5e21, "1.5e+21"),
        (1e20, "100000000000000000000"),
        (1e-7, "1e-7"),
        (0.000001, "0.000001"),
        (2.5e-7, "2.5e-7"),
        (123456.789, "123456.789"),
        (-1.5, "-1.5"),
        (-1e-7, "-1e-7"),
        (100.0, "100"),
        (float("nan"), "NaN"),
        (float("inf"), "Infinity"),
        (float("-inf"), "-Infinity"),
        (5e-324, "5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
    ],
)
def test_canonical_number(value: float, expected: str) -> None:
    assert canonical_number(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.0", "1"),
        ("100", "100"),
        ("1E+2", "100"),
        ("-0", "0"),
        ("-5.00", "-5"),
        ("12345678901234567890", "12345678901234567890"),
        # Exact digits stop below 1e21; at and above it the value goes through float (#4246),
        # so a tiny Decimal with a huge exponent never expands to its full digit string.
        ("999999999999999999999", "999999999999999999999"),
        ("-999999999999999999999", "-999999999999999999999"),
        ("1E+21", "1e+21"),
        ("-1E+21", "-1e+21"),
        ("1000000000000000000000", "1e+21"),
        ("1E+30", "1e+30"),
        ("1E+400", "Infinity"),
        ("-1E+100000", "-Infinity"),
        ("1.5", "1.5"),
        ("0.1", "0.1"),
        ("1E-7", "1e-7"),
        ("NaN", "NaN"),
        ("Infinity", "Infinity"),
        ("-Infinity", "-Infinity"),
    ],
)
def test_canonical_decimal(value: str, expected: str) -> None:
    from decimal import Decimal

    from featureflip._canonical_number import canonical_decimal

    assert canonical_decimal(Decimal(value)) == expected


def test_decimal_bucket_value_hashes_like_its_canonical_text() -> None:
    from decimal import Decimal

    from featureflip.context import EvaluationContext
    from featureflip.evaluation import FlagEvaluator
    from featureflip.models import ServeConfig, ServeType, WeightedVariation

    serve = ServeConfig(
        type=ServeType.ROLLOUT,
        bucket_by="id",
        salt="s",
        variations=[WeightedVariation(key="a", weight=50), WeightedVariation(key="b", weight=50)],
    )
    ev = FlagEvaluator()
    for dec, text in [(Decimal("1.0"), "1"), (Decimal("2.50"), "2.5"), (Decimal("7"), "7")]:
        ctx = EvaluationContext.from_dict({"id": dec})
        ref = EvaluationContext.from_dict({"id": text})
        assert ev.resolve_rollout(serve, ctx) == ev.resolve_rollout(serve, ref)


def test_bool_bucket_value_hashes_as_lowercase_json_spelling() -> None:
    from featureflip.context import EvaluationContext
    from featureflip.evaluation import FlagEvaluator
    from featureflip.models import ServeConfig, ServeType, WeightedVariation

    # Salt "sb5": "true" buckets to a and "True" to b; "false" to b and "False" to a.
    serve = ServeConfig(
        type=ServeType.ROLLOUT,
        bucket_by="id",
        salt="sb5",
        variations=[WeightedVariation(key="a", weight=50), WeightedVariation(key="b", weight=50)],
    )
    ev = FlagEvaluator()
    assert ev.resolve_rollout(serve, EvaluationContext.from_dict({"id": True})) == "a"
    assert ev.resolve_rollout(serve, EvaluationContext.from_dict({"id": False})) == "b"
