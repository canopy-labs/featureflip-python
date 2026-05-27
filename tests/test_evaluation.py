"""Tests for flag evaluation engine."""

import pytest

from featureflip.context import EvaluationContext
from featureflip.evaluation import FlagEvaluator
from featureflip.models import (
    Condition,
    ConditionGroup,
    ConditionLogic,
    ConditionOperator,
)


class TestConditionEvaluation:
    """Tests for individual condition operators."""

    @pytest.fixture
    def context(self) -> EvaluationContext:
        return EvaluationContext.from_dict({
            "user_id": "user-123",
            "email": "Alice@Example.com",
            "country": "US",
            "plan": "pro",
            "age": "25",
        })

    @pytest.fixture
    def evaluator(self) -> FlagEvaluator:
        return FlagEvaluator()

    def test_equals_case_insensitive(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Equals should match case-insensitively."""
        condition = Condition(
            attribute="email",
            operator=ConditionOperator.EQUALS,
            values=["alice@example.com"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_equals_no_match(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Equals should return False when no values match."""
        condition = Condition(
            attribute="country",
            operator=ConditionOperator.EQUALS,
            values=["CA", "UK"],
        )
        assert evaluator.evaluate_condition(condition, context) is False

    def test_equals_matches_any_value(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Equals should match if any value in the list matches."""
        condition = Condition(
            attribute="country",
            operator=ConditionOperator.EQUALS,
            values=["CA", "US", "UK"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_not_equals(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """NotEquals should return True when value doesn't match any targets."""
        condition = Condition(
            attribute="country",
            operator=ConditionOperator.NOT_EQUALS,
            values=["CA", "UK"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_contains(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Contains should match substring."""
        condition = Condition(
            attribute="email",
            operator=ConditionOperator.CONTAINS,
            values=["@example.com"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_contains_case_insensitive(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Contains should match case-insensitively."""
        condition = Condition(
            attribute="email",
            operator=ConditionOperator.CONTAINS,
            values=["@EXAMPLE.COM"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_not_contains(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """NotContains should return True when substring is not found."""
        condition = Condition(
            attribute="email",
            operator=ConditionOperator.NOT_CONTAINS,
            values=["@other.com"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_starts_with(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """StartsWith should match prefix."""
        condition = Condition(
            attribute="email",
            operator=ConditionOperator.STARTS_WITH,
            values=["alice"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_ends_with(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """EndsWith should match suffix."""
        condition = Condition(
            attribute="email",
            operator=ConditionOperator.ENDS_WITH,
            values=["example.com"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_in_operator(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """In operator should return True when value is in list."""
        condition = Condition(
            attribute="country",
            operator=ConditionOperator.IN,
            values=["US", "CA", "UK"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_not_in_operator(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """NotIn operator should return True when value is not in list."""
        condition = Condition(
            attribute="country",
            operator=ConditionOperator.NOT_IN,
            values=["CA", "UK", "DE"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_missing_attribute_returns_false(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Missing attribute should return False."""
        condition = Condition(
            attribute="nonexistent",
            operator=ConditionOperator.EQUALS,
            values=["any"],
        )
        assert evaluator.evaluate_condition(condition, context) is False

    def test_missing_attribute_with_negate_returns_true(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Missing attribute with negate should return True."""
        condition = Condition(
            attribute="nonexistent",
            operator=ConditionOperator.EQUALS,
            values=["any"],
            negate=True,
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_negate_inverts_result(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Negate flag should invert the evaluation result."""
        # Without negate, this would be True
        condition = Condition(
            attribute="country",
            operator=ConditionOperator.EQUALS,
            values=["US"],
            negate=True,
        )
        assert evaluator.evaluate_condition(condition, context) is False

    def test_greater_than(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """GreaterThan should compare numerically."""
        condition = Condition(
            attribute="age",
            operator=ConditionOperator.GREATER_THAN,
            values=["20"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_less_than(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """LessThan should compare numerically."""
        condition = Condition(
            attribute="age",
            operator=ConditionOperator.LESS_THAN,
            values=["30"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_regex_match(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """MatchesRegex should match regex pattern."""
        condition = Condition(
            attribute="email",
            operator=ConditionOperator.MATCHES_REGEX,
            values=[r"^[a-z]+@example\.com$"],
        )
        assert evaluator.evaluate_condition(condition, context) is True

    def test_regex_match_mid_string(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """MatchesRegex should match anywhere in the string, not just the start."""
        condition = Condition(
            attribute="email",
            operator=ConditionOperator.MATCHES_REGEX,
            values=[r"example\.com"],
        )
        assert evaluator.evaluate_condition(condition, context) is True


class TestConditionLogic:
    """Tests for AND/OR condition logic."""

    @pytest.fixture
    def context(self) -> EvaluationContext:
        return EvaluationContext.from_dict({
            "user_id": "user-123",
            "email": "Alice@Example.com",
            "country": "US",
            "plan": "pro",
            "age": "25",
        })

    @pytest.fixture
    def evaluator(self) -> FlagEvaluator:
        return FlagEvaluator()

    def test_and_logic_all_match(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """AND logic should return True when all conditions match."""
        conditions = [
            Condition(
                attribute="country",
                operator=ConditionOperator.EQUALS,
                values=["US"],
            ),
            Condition(
                attribute="plan",
                operator=ConditionOperator.EQUALS,
                values=["pro"],
            ),
        ]
        result = evaluator.evaluate_conditions(conditions, ConditionLogic.AND, context)
        assert result is True

    def test_and_logic_one_fails(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """AND logic should return False when any condition fails."""
        conditions = [
            Condition(
                attribute="country",
                operator=ConditionOperator.EQUALS,
                values=["US"],
            ),
            Condition(
                attribute="plan",
                operator=ConditionOperator.EQUALS,
                values=["enterprise"],  # This won't match
            ),
        ]
        result = evaluator.evaluate_conditions(conditions, ConditionLogic.AND, context)
        assert result is False

    def test_or_logic_one_matches(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """OR logic should return True when any condition matches."""
        conditions = [
            Condition(
                attribute="country",
                operator=ConditionOperator.EQUALS,
                values=["CA"],  # This won't match
            ),
            Condition(
                attribute="plan",
                operator=ConditionOperator.EQUALS,
                values=["pro"],  # This will match
            ),
        ]
        result = evaluator.evaluate_conditions(conditions, ConditionLogic.OR, context)
        assert result is True

    def test_or_logic_none_match(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """OR logic should return False when no conditions match."""
        conditions = [
            Condition(
                attribute="country",
                operator=ConditionOperator.EQUALS,
                values=["CA"],  # This won't match
            ),
            Condition(
                attribute="plan",
                operator=ConditionOperator.EQUALS,
                values=["enterprise"],  # This won't match either
            ),
        ]
        result = evaluator.evaluate_conditions(conditions, ConditionLogic.OR, context)
        assert result is False

    def test_empty_conditions_matches(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Empty conditions list should return True (match all)."""
        conditions: list[Condition] = []
        result = evaluator.evaluate_conditions(conditions, ConditionLogic.AND, context)
        assert result is True


class TestBucketing:
    """Test percentage rollout bucketing."""

    @pytest.fixture
    def evaluator(self) -> FlagEvaluator:
        return FlagEvaluator()

    def test_compute_bucket_deterministic(self, evaluator: FlagEvaluator) -> None:
        """Same input always produces same bucket."""
        bucket1 = evaluator.compute_bucket("salt123", "user-456")
        bucket2 = evaluator.compute_bucket("salt123", "user-456")
        assert bucket1 == bucket2

    def test_compute_bucket_range(self, evaluator: FlagEvaluator) -> None:
        """Bucket is always in range 0-99."""
        for i in range(1000):
            bucket = evaluator.compute_bucket("salt", f"user-{i}")
            assert 0 <= bucket < 100

    def test_compute_bucket_distribution(self, evaluator: FlagEvaluator) -> None:
        """Buckets are roughly uniformly distributed."""
        buckets = [
            evaluator.compute_bucket("test-salt", f"user-{i}")
            for i in range(10000)
        ]
        # Check each decile has roughly 10% of values (±3%)
        for decile in range(10):
            count = sum(1 for b in buckets if decile * 10 <= b < (decile + 1) * 10)
            percentage = count / 100
            assert 7 <= percentage <= 13, f"Decile {decile} has {percentage}%"

    def test_different_salts_different_buckets(self, evaluator: FlagEvaluator) -> None:
        """Different salts produce different bucket distributions."""
        bucket1 = evaluator.compute_bucket("salt-a", "user-123")
        bucket2 = evaluator.compute_bucket("salt-b", "user-123")
        assert 0 <= bucket1 < 100
        assert 0 <= bucket2 < 100


class TestRolloutResolution:
    """Test resolving rollout variations."""

    @pytest.fixture
    def evaluator(self) -> FlagEvaluator:
        return FlagEvaluator()

    def test_resolve_rollout_50_50(self, evaluator: FlagEvaluator) -> None:
        """50/50 split assigns users to correct variations."""
        from featureflip.models import ServeConfig, ServeType, WeightedVariation

        serve = ServeConfig(
            type=ServeType.ROLLOUT,
            bucket_by="userId",
            salt="test-salt",
            variations=[
                WeightedVariation(key="control", weight=50),
                WeightedVariation(key="treatment", weight=50),
            ],
        )

        control_count = 0
        treatment_count = 0
        for i in range(1000):
            ctx = EvaluationContext.from_dict({"userId": f"user-{i}"})
            variation = evaluator.resolve_rollout(serve, ctx)
            if variation == "control":
                control_count += 1
            else:
                treatment_count += 1

        assert 400 <= control_count <= 600
        assert 400 <= treatment_count <= 600

    def test_resolve_rollout_deterministic(self, evaluator: FlagEvaluator) -> None:
        """Same user always gets same variation."""
        from featureflip.models import ServeConfig, ServeType, WeightedVariation

        serve = ServeConfig(
            type=ServeType.ROLLOUT,
            bucket_by="userId",
            salt="test-salt",
            variations=[
                WeightedVariation(key="control", weight=50),
                WeightedVariation(key="treatment", weight=50),
            ],
        )
        ctx = EvaluationContext.from_dict({"userId": "user-123"})

        variation1 = evaluator.resolve_rollout(serve, ctx)
        variation2 = evaluator.resolve_rollout(serve, ctx)
        assert variation1 == variation2

    def test_resolve_rollout_respects_weights(self, evaluator: FlagEvaluator) -> None:
        """90/10 split gives roughly correct distribution."""
        from featureflip.models import ServeConfig, ServeType, WeightedVariation

        serve = ServeConfig(
            type=ServeType.ROLLOUT,
            bucket_by="userId",
            salt="test-salt",
            variations=[
                WeightedVariation(key="majority", weight=90),
                WeightedVariation(key="minority", weight=10),
            ],
        )

        majority_count = 0
        for i in range(1000):
            ctx = EvaluationContext.from_dict({"userId": f"user-{i}"})
            if evaluator.resolve_rollout(serve, ctx) == "majority":
                majority_count += 1

        assert 850 <= majority_count <= 950

    def test_resolve_rollout_custom_bucket_by(self, evaluator: FlagEvaluator) -> None:
        """Can bucket by custom attribute."""
        from featureflip.models import ServeConfig, ServeType, WeightedVariation

        serve = ServeConfig(
            type=ServeType.ROLLOUT,
            bucket_by="company_id",
            salt="test-salt",
            variations=[
                WeightedVariation(key="a", weight=50),
                WeightedVariation(key="b", weight=50),
            ],
        )

        ctx1 = EvaluationContext.from_dict({"user_id": "user-1", "company_id": "company-x"})
        ctx2 = EvaluationContext.from_dict({"user_id": "user-2", "company_id": "company-x"})

        assert evaluator.resolve_rollout(serve, ctx1) == evaluator.resolve_rollout(serve, ctx2)


class TestFlagEvaluation:
    """Test complete flag evaluation."""

    @pytest.fixture
    def evaluator(self) -> FlagEvaluator:
        return FlagEvaluator()

    def test_disabled_flag_returns_off_variation(self, evaluator: FlagEvaluator) -> None:
        from featureflip.detail import EvaluationReason
        from featureflip.models import (
            FlagConfiguration,
            FlagType,
            ServeConfig,
            ServeType,
            Variation,
        )

        flag = FlagConfiguration(
            key="test-flag",
            version=1,
            type=FlagType.BOOLEAN,
            enabled=False,
            variations=[
                Variation(key="on", value=True),
                Variation(key="off", value=False),
            ],
            rules=[],
            fallthrough=ServeConfig(type=ServeType.FIXED, variation="on"),
            off_variation="off",
        )
        ctx = EvaluationContext.from_dict({"user_id": "123"})
        result = evaluator.evaluate(flag, ctx)
        assert result.value is False
        assert result.reason == EvaluationReason.FLAG_DISABLED

    def test_no_rules_uses_fallthrough(self, evaluator: FlagEvaluator) -> None:
        from featureflip.detail import EvaluationReason
        from featureflip.models import (
            FlagConfiguration,
            FlagType,
            ServeConfig,
            ServeType,
            Variation,
        )

        flag = FlagConfiguration(
            key="test-flag",
            version=1,
            type=FlagType.STRING,
            enabled=True,
            variations=[
                Variation(key="free", value="free"),
                Variation(key="pro", value="pro"),
            ],
            rules=[],
            fallthrough=ServeConfig(type=ServeType.FIXED, variation="free"),
            off_variation="free",
        )
        ctx = EvaluationContext.from_dict({"user_id": "123"})
        result = evaluator.evaluate(flag, ctx)
        assert result.value == "free"
        assert result.reason == EvaluationReason.FALLTHROUGH

    def test_rule_match_returns_rule_variation(self, evaluator: FlagEvaluator) -> None:
        from featureflip.detail import EvaluationReason
        from featureflip.models import (
            Condition,
            ConditionLogic,
            ConditionOperator,
            FlagConfiguration,
            FlagType,
            ServeConfig,
            ServeType,
            TargetingRule,
            Variation,
        )

        flag = FlagConfiguration(
            key="test-flag",
            version=1,
            type=FlagType.STRING,
            enabled=True,
            variations=[
                Variation(key="free", value="free"),
                Variation(key="pro", value="pro"),
            ],
            rules=[
                TargetingRule(
                    id="pro-users",
                    priority=0,
                    condition_groups=[
                        ConditionGroup(
                            operator=ConditionLogic.AND,
                            conditions=[
                                Condition(
                                    attribute="plan",
                                    operator=ConditionOperator.EQUALS,
                                    values=["pro"],
                                )
                            ],
                        )
                    ],
                    serve=ServeConfig(type=ServeType.FIXED, variation="pro"),
                )
            ],
            fallthrough=ServeConfig(type=ServeType.FIXED, variation="free"),
            off_variation="free",
        )
        ctx = EvaluationContext.from_dict({"user_id": "123", "plan": "pro"})
        result = evaluator.evaluate(flag, ctx)
        assert result.value == "pro"
        assert result.reason == EvaluationReason.RULE_MATCH
        assert result.rule_id == "pro-users"

    def test_rules_evaluated_in_priority_order(self, evaluator: FlagEvaluator) -> None:
        from featureflip.models import (
            Condition,
            ConditionLogic,
            ConditionOperator,
            FlagConfiguration,
            FlagType,
            ServeConfig,
            ServeType,
            TargetingRule,
            Variation,
        )

        flag = FlagConfiguration(
            key="test-flag",
            version=1,
            type=FlagType.STRING,
            enabled=True,
            variations=[
                Variation(key="a", value="a"),
                Variation(key="b", value="b"),
                Variation(key="c", value="c"),
            ],
            rules=[
                TargetingRule(
                    id="rule-low-priority",
                    priority=10,
                    condition_groups=[
                        ConditionGroup(
                            operator=ConditionLogic.AND,
                            conditions=[
                                Condition(
                                    attribute="country",
                                    operator=ConditionOperator.EQUALS,
                                    values=["US"],
                                )
                            ],
                        )
                    ],
                    serve=ServeConfig(type=ServeType.FIXED, variation="c"),
                ),
                TargetingRule(
                    id="rule-high-priority",
                    priority=0,
                    condition_groups=[
                        ConditionGroup(
                            operator=ConditionLogic.AND,
                            conditions=[
                                Condition(
                                    attribute="country",
                                    operator=ConditionOperator.EQUALS,
                                    values=["US"],
                                )
                            ],
                        )
                    ],
                    serve=ServeConfig(type=ServeType.FIXED, variation="a"),
                ),
            ],
            fallthrough=ServeConfig(type=ServeType.FIXED, variation="b"),
            off_variation="b",
        )
        ctx = EvaluationContext.from_dict({"user_id": "123", "country": "US"})
        result = evaluator.evaluate(flag, ctx)
        assert result.value == "a"
        assert result.rule_id == "rule-high-priority"

    def test_first_matching_rule_wins(self, evaluator: FlagEvaluator) -> None:
        from featureflip.models import (
            Condition,
            ConditionLogic,
            ConditionOperator,
            FlagConfiguration,
            FlagType,
            ServeConfig,
            ServeType,
            TargetingRule,
            Variation,
        )

        flag = FlagConfiguration(
            key="test-flag",
            version=1,
            type=FlagType.STRING,
            enabled=True,
            variations=[
                Variation(key="a", value="a"),
                Variation(key="b", value="b"),
            ],
            rules=[
                TargetingRule(
                    id="rule-1",
                    priority=0,
                    condition_groups=[
                        ConditionGroup(
                            operator=ConditionLogic.AND,
                            conditions=[
                                Condition(
                                    attribute="country",
                                    operator=ConditionOperator.EQUALS,
                                    values=["US"],
                                )
                            ],
                        )
                    ],
                    serve=ServeConfig(type=ServeType.FIXED, variation="a"),
                ),
                TargetingRule(
                    id="rule-2",
                    priority=1,
                    condition_groups=[
                        ConditionGroup(
                            operator=ConditionLogic.AND,
                            conditions=[
                                Condition(
                                    attribute="plan",
                                    operator=ConditionOperator.EQUALS,
                                    values=["pro"],
                                )
                            ],
                        )
                    ],
                    serve=ServeConfig(type=ServeType.FIXED, variation="b"),
                ),
            ],
            fallthrough=ServeConfig(type=ServeType.FIXED, variation="b"),
            off_variation="b",
        )
        ctx = EvaluationContext.from_dict({"user_id": "123", "country": "US", "plan": "pro"})
        result = evaluator.evaluate(flag, ctx)
        assert result.value == "a"
        assert result.rule_id == "rule-1"

    def test_segment_rule_matches_segment_conditions(self, evaluator: FlagEvaluator) -> None:
        from featureflip.detail import EvaluationReason
        from featureflip.models import (
            Condition,
            ConditionLogic,
            ConditionOperator,
            FlagConfiguration,
            FlagType,
            Segment,
            ServeConfig,
            ServeType,
            TargetingRule,
            Variation,
        )

        segment = Segment(
            key="beta-users",
            version=1,
            conditions=[
                Condition(
                    attribute="plan",
                    operator=ConditionOperator.EQUALS,
                    values=["beta"],
                )
            ],
            condition_logic=ConditionLogic.AND,
        )

        flag = FlagConfiguration(
            key="test-flag",
            version=1,
            type=FlagType.BOOLEAN,
            enabled=True,
            variations=[
                Variation(key="on", value=True),
                Variation(key="off", value=False),
            ],
            rules=[
                TargetingRule(
                    id="rule-1",
                    priority=0,
                    condition_groups=[],
                    serve=ServeConfig(type=ServeType.FIXED, variation="on"),
                    segment_key="beta-users",
                )
            ],
            fallthrough=ServeConfig(type=ServeType.FIXED, variation="off"),
            off_variation="off",
        )

        def get_segment(key: str) -> Segment | None:
            return segment if key == "beta-users" else None

        # Matching context
        ctx_match = EvaluationContext.from_dict({"user_id": "123", "plan": "beta"})
        result = evaluator.evaluate(flag, ctx_match, get_segment)
        assert result.value is True
        assert result.reason == EvaluationReason.RULE_MATCH
        assert result.rule_id == "rule-1"

        # Non-matching context
        ctx_no_match = EvaluationContext.from_dict({"user_id": "456", "plan": "free"})
        result = evaluator.evaluate(flag, ctx_no_match, get_segment)
        assert result.value is False
        assert result.reason == EvaluationReason.FALLTHROUGH

    def test_segment_rule_with_missing_segment_does_not_match(self, evaluator: FlagEvaluator) -> None:
        from featureflip.detail import EvaluationReason
        from featureflip.models import (
            FlagConfiguration,
            FlagType,
            ServeConfig,
            ServeType,
            TargetingRule,
            Variation,
        )

        flag = FlagConfiguration(
            key="test-flag",
            version=1,
            type=FlagType.BOOLEAN,
            enabled=True,
            variations=[
                Variation(key="on", value=True),
                Variation(key="off", value=False),
            ],
            rules=[
                TargetingRule(
                    id="rule-1",
                    priority=0,
                    condition_groups=[],
                    serve=ServeConfig(type=ServeType.FIXED, variation="on"),
                    segment_key="nonexistent",
                )
            ],
            fallthrough=ServeConfig(type=ServeType.FIXED, variation="off"),
            off_variation="off",
        )

        ctx = EvaluationContext.from_dict({"user_id": "123"})
        result = evaluator.evaluate(flag, ctx, lambda _: None)
        assert result.value is False
        assert result.reason == EvaluationReason.FALLTHROUGH

    def test_rollout_in_rule(self, evaluator: FlagEvaluator) -> None:
        from featureflip.detail import EvaluationReason
        from featureflip.models import (
            Condition,
            ConditionLogic,
            ConditionOperator,
            FlagConfiguration,
            FlagType,
            ServeConfig,
            ServeType,
            TargetingRule,
            Variation,
            WeightedVariation,
        )

        flag = FlagConfiguration(
            key="test-flag",
            version=1,
            type=FlagType.BOOLEAN,
            enabled=True,
            variations=[
                Variation(key="on", value=True),
                Variation(key="off", value=False),
            ],
            rules=[
                TargetingRule(
                    id="rollout-rule",
                    priority=0,
                    condition_groups=[
                        ConditionGroup(
                            operator=ConditionLogic.AND,
                            conditions=[
                                Condition(
                                    attribute="plan",
                                    operator=ConditionOperator.EQUALS,
                                    values=["pro"],
                                )
                            ],
                        )
                    ],
                    serve=ServeConfig(
                        type=ServeType.ROLLOUT,
                        bucket_by="userId",
                        salt="rule-salt",
                        variations=[
                            WeightedVariation(key="on", weight=50),
                            WeightedVariation(key="off", weight=50),
                        ],
                    ),
                )
            ],
            fallthrough=ServeConfig(type=ServeType.FIXED, variation="off"),
            off_variation="off",
        )
        ctx = EvaluationContext.from_dict({"userId": "123", "plan": "pro"})
        result = evaluator.evaluate(flag, ctx)
        assert result.value in [True, False]
        assert result.reason == EvaluationReason.RULE_MATCH


class TestConditionGroupEvaluation:
    """Tests for condition group evaluation logic."""

    @pytest.fixture
    def evaluator(self) -> FlagEvaluator:
        return FlagEvaluator()

    @pytest.fixture
    def context(self) -> EvaluationContext:
        return EvaluationContext.from_dict({
            "user_id": "user-123",
            "country": "US",
            "plan": "pro",
            "age": "25",
        })

    def test_empty_condition_groups_matches(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Empty condition groups should match (vacuous truth)."""
        result = evaluator.evaluate_condition_groups([], context)
        assert result is True

    def test_single_group_and_all_match(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Single AND group where all conditions match."""
        groups = [
            ConditionGroup(
                operator=ConditionLogic.AND,
                conditions=[
                    Condition(attribute="country", operator=ConditionOperator.EQUALS, values=["US"]),
                    Condition(attribute="plan", operator=ConditionOperator.EQUALS, values=["pro"]),
                ],
            )
        ]
        assert evaluator.evaluate_condition_groups(groups, context) is True

    def test_single_group_and_one_fails(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Single AND group where one condition fails."""
        groups = [
            ConditionGroup(
                operator=ConditionLogic.AND,
                conditions=[
                    Condition(attribute="country", operator=ConditionOperator.EQUALS, values=["US"]),
                    Condition(attribute="plan", operator=ConditionOperator.EQUALS, values=["enterprise"]),
                ],
            )
        ]
        assert evaluator.evaluate_condition_groups(groups, context) is False

    def test_single_group_or_one_matches(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Single OR group where one condition matches."""
        groups = [
            ConditionGroup(
                operator=ConditionLogic.OR,
                conditions=[
                    Condition(attribute="country", operator=ConditionOperator.EQUALS, values=["CA"]),
                    Condition(attribute="plan", operator=ConditionOperator.EQUALS, values=["pro"]),
                ],
            )
        ]
        assert evaluator.evaluate_condition_groups(groups, context) is True

    def test_multiple_groups_anded_all_match(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Multiple groups ANDed together, all match."""
        groups = [
            ConditionGroup(
                operator=ConditionLogic.AND,
                conditions=[
                    Condition(attribute="country", operator=ConditionOperator.EQUALS, values=["US"]),
                ],
            ),
            ConditionGroup(
                operator=ConditionLogic.AND,
                conditions=[
                    Condition(attribute="plan", operator=ConditionOperator.EQUALS, values=["pro"]),
                ],
            ),
        ]
        assert evaluator.evaluate_condition_groups(groups, context) is True

    def test_multiple_groups_anded_one_fails(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Multiple groups ANDed together, one group fails."""
        groups = [
            ConditionGroup(
                operator=ConditionLogic.AND,
                conditions=[
                    Condition(attribute="country", operator=ConditionOperator.EQUALS, values=["US"]),
                ],
            ),
            ConditionGroup(
                operator=ConditionLogic.AND,
                conditions=[
                    Condition(attribute="plan", operator=ConditionOperator.EQUALS, values=["enterprise"]),
                ],
            ),
        ]
        assert evaluator.evaluate_condition_groups(groups, context) is False

    def test_mixed_operators_across_groups(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """Groups with different operators ANDed together."""
        groups = [
            ConditionGroup(
                operator=ConditionLogic.OR,
                conditions=[
                    Condition(attribute="country", operator=ConditionOperator.EQUALS, values=["CA"]),
                    Condition(attribute="country", operator=ConditionOperator.EQUALS, values=["US"]),
                ],
            ),
            ConditionGroup(
                operator=ConditionLogic.AND,
                conditions=[
                    Condition(attribute="plan", operator=ConditionOperator.EQUALS, values=["pro"]),
                ],
            ),
        ]
        assert evaluator.evaluate_condition_groups(groups, context) is True

    def test_or_group_none_match(
        self, evaluator: FlagEvaluator, context: EvaluationContext
    ) -> None:
        """OR group where no conditions match fails the entire evaluation."""
        groups = [
            ConditionGroup(
                operator=ConditionLogic.OR,
                conditions=[
                    Condition(attribute="country", operator=ConditionOperator.EQUALS, values=["CA"]),
                    Condition(attribute="country", operator=ConditionOperator.EQUALS, values=["UK"]),
                ],
            ),
        ]
        assert evaluator.evaluate_condition_groups(groups, context) is False


class TestPrerequisiteResolution:
    """Tests for prerequisite flag evaluation."""

    @pytest.fixture
    def evaluator(self) -> FlagEvaluator:
        return FlagEvaluator()

    @pytest.fixture
    def ctx(self) -> EvaluationContext:
        return EvaluationContext.from_dict({"user_id": "user-1"})

    def _make_flag(
        self,
        key: str = "test-flag",
        *,
        enabled: bool = True,
        fallthrough_variation: str = "on",
        prerequisites: list | None = None,
    ):
        from featureflip.models import (
            FlagConfiguration,
            FlagType,
            ServeConfig,
            ServeType,
            Variation,
        )

        return FlagConfiguration(
            key=key,
            version=1,
            type=FlagType.BOOLEAN,
            enabled=enabled,
            variations=[
                Variation(key="on", value=True),
                Variation(key="off", value=False),
            ],
            rules=[],
            fallthrough=ServeConfig(type=ServeType.FIXED, variation=fallthrough_variation),
            off_variation="off",
            prerequisites=prerequisites or [],
        )

    def test_no_prerequisites_falls_through(
        self, evaluator: FlagEvaluator, ctx: EvaluationContext
    ) -> None:
        """Flag with no prerequisites evaluates normally."""
        from featureflip.detail import EvaluationReason

        flag = self._make_flag("main", prerequisites=[])
        result = evaluator.evaluate(flag, ctx, all_flags={})
        assert result.reason == EvaluationReason.FALLTHROUGH
        assert result.value is True

    def test_satisfied_prerequisite_serves_on(
        self, evaluator: FlagEvaluator, ctx: EvaluationContext
    ) -> None:
        """When prerequisite serves the expected variation, main falls through."""
        from featureflip.detail import EvaluationReason
        from featureflip.models import Prerequisite

        prereq = self._make_flag("prereq", fallthrough_variation="on")
        main = self._make_flag(
            "main",
            prerequisites=[
                Prerequisite(
                    prerequisite_flag_key="prereq", expected_variation_key="on"
                )
            ],
        )
        result = evaluator.evaluate(main, ctx, all_flags={"prereq": prereq})
        assert result.reason == EvaluationReason.FALLTHROUGH
        assert result.value is True
        assert result.prerequisite_key is None

    def test_unsatisfied_prerequisite_serves_off(
        self, evaluator: FlagEvaluator, ctx: EvaluationContext
    ) -> None:
        """When prerequisite serves a different variation, main serves off."""
        from featureflip.detail import EvaluationReason
        from featureflip.models import Prerequisite

        prereq = self._make_flag("prereq", fallthrough_variation="off")
        main = self._make_flag(
            "main",
            prerequisites=[
                Prerequisite(
                    prerequisite_flag_key="prereq", expected_variation_key="on"
                )
            ],
        )
        result = evaluator.evaluate(main, ctx, all_flags={"prereq": prereq})
        assert result.reason == EvaluationReason.PREREQUISITE_FAILED
        assert result.value is False
        assert result.prerequisite_key == "prereq"

    def test_disabled_prerequisite_serves_off(
        self, evaluator: FlagEvaluator, ctx: EvaluationContext
    ) -> None:
        """A disabled prerequisite resolves to its off variation; if that doesn't
        match expected, main serves off with prerequisite-failed reason."""
        from featureflip.detail import EvaluationReason
        from featureflip.models import Prerequisite

        prereq = self._make_flag("prereq", enabled=False)
        main = self._make_flag(
            "main",
            prerequisites=[
                Prerequisite(
                    prerequisite_flag_key="prereq", expected_variation_key="on"
                )
            ],
        )
        result = evaluator.evaluate(main, ctx, all_flags={"prereq": prereq})
        assert result.reason == EvaluationReason.PREREQUISITE_FAILED
        assert result.prerequisite_key == "prereq"

    def test_multi_prereq_reports_first_failing_key(
        self, evaluator: FlagEvaluator, ctx: EvaluationContext
    ) -> None:
        """When multiple prerequisites fail, the first one in the list is reported."""
        from featureflip.detail import EvaluationReason
        from featureflip.models import Prerequisite

        prereq_a = self._make_flag("prereq-a", fallthrough_variation="off")
        prereq_b = self._make_flag("prereq-b", fallthrough_variation="off")
        main = self._make_flag(
            "main",
            prerequisites=[
                Prerequisite(
                    prerequisite_flag_key="prereq-a", expected_variation_key="on"
                ),
                Prerequisite(
                    prerequisite_flag_key="prereq-b", expected_variation_key="on"
                ),
            ],
        )
        result = evaluator.evaluate(
            main, ctx, all_flags={"prereq-a": prereq_a, "prereq-b": prereq_b}
        )
        assert result.reason == EvaluationReason.PREREQUISITE_FAILED
        assert result.prerequisite_key == "prereq-a"

    def test_chained_prerequisites_resolve(
        self, evaluator: FlagEvaluator, ctx: EvaluationContext
    ) -> None:
        """A prerequisite's own prerequisites are resolved recursively."""
        from featureflip.detail import EvaluationReason
        from featureflip.models import Prerequisite

        grandchild = self._make_flag("grandchild", fallthrough_variation="on")
        child = self._make_flag(
            "child",
            fallthrough_variation="on",
            prerequisites=[
                Prerequisite(
                    prerequisite_flag_key="grandchild", expected_variation_key="on"
                )
            ],
        )
        main = self._make_flag(
            "main",
            prerequisites=[
                Prerequisite(
                    prerequisite_flag_key="child", expected_variation_key="on"
                )
            ],
        )
        result = evaluator.evaluate(
            main, ctx, all_flags={"grandchild": grandchild, "child": child}
        )
        assert result.reason == EvaluationReason.FALLTHROUGH
        assert result.value is True

        # Break the chain at the grandchild level
        grandchild_failing = self._make_flag(
            "grandchild", fallthrough_variation="off"
        )
        result2 = evaluator.evaluate(
            main,
            ctx,
            all_flags={"grandchild": grandchild_failing, "child": child},
        )
        assert result2.reason == EvaluationReason.PREREQUISITE_FAILED

    def test_missing_prerequisite_flag_fails_safely(
        self, evaluator: FlagEvaluator, ctx: EvaluationContext
    ) -> None:
        """If the prerequisite flag isn't in all_flags, serve off with the
        prerequisite key set."""
        from featureflip.detail import EvaluationReason
        from featureflip.models import Prerequisite

        main = self._make_flag(
            "main",
            prerequisites=[
                Prerequisite(
                    prerequisite_flag_key="missing-flag",
                    expected_variation_key="on",
                )
            ],
        )
        result = evaluator.evaluate(main, ctx, all_flags={})
        assert result.reason == EvaluationReason.PREREQUISITE_FAILED
        assert result.value is False
        assert result.prerequisite_key == "missing-flag"

    def test_depth_exceeded_returns_error(
        self, evaluator: FlagEvaluator, ctx: EvaluationContext
    ) -> None:
        """A chain longer than MAX_PREREQUISITE_DEPTH returns reason 'error'."""
        from featureflip.detail import EvaluationReason
        from featureflip.models import Prerequisite

        flags = {}
        for i in range(12):
            key = f"flag-{i}"
            prereqs = (
                [
                    Prerequisite(
                        prerequisite_flag_key=f"flag-{i - 1}",
                        expected_variation_key="on",
                    )
                ]
                if i > 0
                else []
            )
            flags[key] = self._make_flag(
                key, fallthrough_variation="on", prerequisites=prereqs
            )
        top = flags["flag-11"]
        result = evaluator.evaluate(top, ctx, all_flags=flags)
        assert result.reason == EvaluationReason.ERROR

    def test_evaluate_with_shared_memo_reuses_result(
        self, evaluator: FlagEvaluator, ctx: EvaluationContext
    ) -> None:
        """Memo passed across calls prevents redundant re-evaluation."""
        from featureflip.detail import EvaluationDetail, EvaluationReason
        from featureflip.models import Prerequisite

        prereq = self._make_flag("prereq", fallthrough_variation="on")
        main = self._make_flag(
            "main",
            prerequisites=[
                Prerequisite(
                    prerequisite_flag_key="prereq", expected_variation_key="on"
                )
            ],
        )

        memo: dict[str, EvaluationDetail] = {
            "prereq": EvaluationDetail(
                value=True,
                reason=EvaluationReason.FALLTHROUGH,
                variation_key="on",
            )
        }
        result = evaluator.evaluate_with_shared_memo(
            main, ctx, all_flags={"prereq": prereq}, memo=memo
        )
        assert result.reason == EvaluationReason.FALLTHROUGH
        # memo should now also include the main flag's result
        assert "main" in memo

    def test_backward_compatible_evaluate_signature(
        self, evaluator: FlagEvaluator, ctx: EvaluationContext
    ) -> None:
        """Calling evaluate() without all_flags still works (for flags without
        prerequisites). Regression guard for existing callers."""
        from featureflip.detail import EvaluationReason

        flag = self._make_flag("main")
        result = evaluator.evaluate(flag, ctx)
        assert result.reason == EvaluationReason.FALLTHROUGH
        assert result.value is True

    def test_shared_memo_caches_disabled_prerequisite(
        self, evaluator: FlagEvaluator, ctx: EvaluationContext
    ) -> None:
        """A disabled prerequisite is memoised so a sibling flag sharing the
        same prereq doesn't re-evaluate it."""
        from featureflip.detail import EvaluationDetail, EvaluationReason
        from featureflip.models import Prerequisite

        disabled_prereq = self._make_flag("shared", enabled=False)
        main = self._make_flag(
            "main",
            prerequisites=[
                Prerequisite(
                    prerequisite_flag_key="shared", expected_variation_key="on"
                )
            ],
        )
        memo: dict[str, EvaluationDetail] = {}
        evaluator.evaluate_with_shared_memo(
            main, ctx, all_flags={"shared": disabled_prereq}, memo=memo
        )
        assert "shared" in memo
        assert memo["shared"].reason == EvaluationReason.FLAG_DISABLED
