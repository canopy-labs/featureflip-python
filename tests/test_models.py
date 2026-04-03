"""Tests for data models."""

from featureflip.models import (
    Condition,
    ConditionGroup,
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


class TestVariation:
    def test_variation_with_boolean_value(self) -> None:
        variation = Variation(key="on", value=True)
        assert variation.key == "on"
        assert variation.value is True

    def test_variation_with_string_value(self) -> None:
        variation = Variation(key="treatment", value="pro")
        assert variation.key == "treatment"
        assert variation.value == "pro"

    def test_variation_with_dict_value(self) -> None:
        variation = Variation(key="config", value={"theme": "dark"})
        assert variation.key == "config"
        assert variation.value == {"theme": "dark"}


class TestCondition:
    def test_condition_creation(self) -> None:
        condition = Condition(
            attribute="country",
            operator=ConditionOperator.EQUALS,
            values=["US", "CA"],
            negate=False,
        )
        assert condition.attribute == "country"
        assert condition.operator == ConditionOperator.EQUALS
        assert condition.values == ["US", "CA"]
        assert condition.negate is False

    def test_condition_defaults(self) -> None:
        condition = Condition(
            attribute="email",
            operator=ConditionOperator.CONTAINS,
            values=["@example.com"],
        )
        assert condition.negate is False


class TestServeConfig:
    def test_fixed_serve(self) -> None:
        serve = ServeConfig(type=ServeType.FIXED, variation="on")
        assert serve.type == ServeType.FIXED
        assert serve.variation == "on"
        assert serve.bucket_by is None

    def test_rollout_serve(self) -> None:
        serve = ServeConfig(
            type=ServeType.ROLLOUT,
            bucket_by="user_id",
            salt="abc123",
            variations=[
                WeightedVariation(key="control", weight=50),
                WeightedVariation(key="treatment", weight=50),
            ],
        )
        assert serve.type == ServeType.ROLLOUT
        assert serve.bucket_by == "user_id"
        assert serve.salt == "abc123"
        assert len(serve.variations) == 2


class TestConditionGroup:
    def test_condition_group_creation(self) -> None:
        group = ConditionGroup(
            operator=ConditionLogic.AND,
            conditions=[
                Condition(
                    attribute="plan",
                    operator=ConditionOperator.EQUALS,
                    values=["pro"],
                )
            ],
        )
        assert group.operator == ConditionLogic.AND
        assert len(group.conditions) == 1

    def test_condition_group_or(self) -> None:
        group = ConditionGroup(
            operator=ConditionLogic.OR,
            conditions=[
                Condition(
                    attribute="country",
                    operator=ConditionOperator.EQUALS,
                    values=["US"],
                ),
                Condition(
                    attribute="country",
                    operator=ConditionOperator.EQUALS,
                    values=["CA"],
                ),
            ],
        )
        assert group.operator == ConditionLogic.OR
        assert len(group.conditions) == 2


class TestTargetingRule:
    def test_rule_creation(self) -> None:
        rule = TargetingRule(
            id="rule-1",
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
            serve=ServeConfig(type=ServeType.FIXED, variation="on"),
        )
        assert rule.id == "rule-1"
        assert rule.priority == 0
        assert len(rule.condition_groups) == 1
        assert rule.condition_groups[0].operator == ConditionLogic.AND


class TestFlagConfiguration:
    def test_flag_creation(self) -> None:
        flag = FlagConfiguration(
            key="new-checkout",
            version=1,
            type=FlagType.BOOLEAN,
            enabled=True,
            variations=[
                Variation(key="on", value=True),
                Variation(key="off", value=False),
            ],
            rules=[],
            fallthrough=ServeConfig(type=ServeType.FIXED, variation="off"),
            off_variation="off",
        )
        assert flag.key == "new-checkout"
        assert flag.enabled is True
        assert len(flag.variations) == 2

    def test_get_variation_by_key(self) -> None:
        flag = FlagConfiguration(
            key="test",
            version=1,
            type=FlagType.BOOLEAN,
            enabled=True,
            variations=[
                Variation(key="on", value=True),
                Variation(key="off", value=False),
            ],
            rules=[],
            fallthrough=ServeConfig(type=ServeType.FIXED, variation="off"),
            off_variation="off",
        )
        assert flag.get_variation("on").value is True
        assert flag.get_variation("off").value is False
        assert flag.get_variation("missing") is None
