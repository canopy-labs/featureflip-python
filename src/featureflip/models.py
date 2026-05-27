"""Core data models for Featureflip SDK.

This module contains all the data models used to represent feature flag
configurations, variations, targeting rules, and conditions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FlagType(Enum):
    """Type of feature flag value."""

    BOOLEAN = "boolean"
    STRING = "string"
    NUMBER = "number"
    JSON = "json"


class ConditionOperator(Enum):
    """Operators for evaluating conditions against context attributes."""

    # Equality
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"

    # String operations
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    MATCHES_REGEX = "matches_regex"

    # Set operations
    IN = "in"
    NOT_IN = "not_in"

    # Numeric comparisons
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"

    # Date/time comparisons
    BEFORE = "before"
    AFTER = "after"


class ConditionLogic(Enum):
    """Logic for combining multiple conditions in a rule."""

    AND = "and"
    OR = "or"


class ServeType(Enum):
    """Type of serving strategy for a variation."""

    FIXED = "fixed"
    ROLLOUT = "rollout"


@dataclass(frozen=True)
class Variation:
    """A variation of a feature flag.

    Attributes:
        key: Unique identifier for the variation within the flag.
        value: The value returned when this variation is served.
               Can be bool, str, int, float, dict, or list.
    """

    key: str
    value: Any


@dataclass(frozen=True)
class WeightedVariation:
    """A variation with a weight for percentage-based rollouts.

    Attributes:
        key: Reference to a Variation key.
        weight: Percentage weight (0-100) for this variation.
    """

    key: str
    weight: int


@dataclass(frozen=True)
class Condition:
    """A condition for targeting rules.

    Attributes:
        attribute: The context attribute to evaluate (e.g., "country", "user_id").
        operator: The comparison operator to use.
        values: The values to compare against.
        negate: If True, the condition result is inverted.
    """

    attribute: str
    operator: ConditionOperator
    values: list[Any]
    negate: bool = False


@dataclass(frozen=True)
class ServeConfig:
    """Configuration for how to serve a variation.

    For FIXED type, only `variation` is needed.
    For ROLLOUT type, `bucket_by`, `salt`, and `variations` are used.

    Attributes:
        type: The serving strategy type.
        variation: For FIXED, the variation key to serve.
        bucket_by: For ROLLOUT, the context attribute to use for bucketing.
        salt: For ROLLOUT, a salt string for consistent hashing.
        variations: For ROLLOUT, list of weighted variations.
    """

    type: ServeType
    variation: str | None = None
    bucket_by: str | None = None
    salt: str | None = None
    variations: tuple[WeightedVariation, ...] | None = None

    def __init__(
        self,
        type: ServeType,
        variation: str | None = None,
        bucket_by: str | None = None,
        salt: str | None = None,
        variations: list[WeightedVariation] | tuple[WeightedVariation, ...] | None = None,
    ) -> None:
        """Initialize ServeConfig, converting variations list to tuple for immutability."""
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "variation", variation)
        object.__setattr__(self, "bucket_by", bucket_by)
        object.__setattr__(self, "salt", salt)
        # Convert list to tuple for immutability
        if variations is not None:
            object.__setattr__(self, "variations", tuple(variations))
        else:
            object.__setattr__(self, "variations", None)


@dataclass(frozen=True)
class ConditionGroup:
    """A group of conditions combined with a logical operator.

    Groups are ANDed together at the rule level; within each group,
    conditions are combined using the group's operator.

    Attributes:
        operator: How to combine conditions within this group (AND/OR).
        conditions: List of conditions in this group.
    """

    operator: ConditionLogic
    conditions: tuple[Condition, ...]

    def __init__(
        self,
        operator: ConditionLogic,
        conditions: list[Condition] | tuple[Condition, ...],
    ) -> None:
        """Initialize ConditionGroup, converting conditions list to tuple for immutability."""
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "conditions", tuple(conditions))


@dataclass(frozen=True)
class TargetingRule:
    """A targeting rule that determines which variation to serve.

    Rules are evaluated in priority order. The first rule whose conditions
    match will determine the served variation.

    Condition groups are ANDed together; within each group, conditions
    use the group's operator (AND/OR).

    Attributes:
        id: Unique identifier for the rule.
        priority: Evaluation order (lower = higher priority).
        condition_groups: List of condition groups, ANDed together.
        serve: Configuration for which variation to serve.
        segment_key: Optional key referencing a user segment for targeting.
    """

    id: str
    priority: int
    condition_groups: tuple[ConditionGroup, ...]
    serve: ServeConfig
    segment_key: str | None = None

    def __init__(
        self,
        id: str,
        priority: int,
        condition_groups: list[ConditionGroup] | tuple[ConditionGroup, ...],
        serve: ServeConfig,
        segment_key: str | None = None,
    ) -> None:
        """Initialize TargetingRule, converting condition_groups list to tuple for immutability."""
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "condition_groups", tuple(condition_groups))
        object.__setattr__(self, "serve", serve)
        object.__setattr__(self, "segment_key", segment_key)


@dataclass(frozen=True)
class Segment:
    """A user segment with conditions for targeting.

    Attributes:
        key: Unique identifier for the segment.
        version: Version number of the segment configuration.
        conditions: Conditions that define segment membership.
        condition_logic: How to combine conditions (AND/OR).
    """

    key: str
    version: int
    conditions: tuple[Condition, ...]
    condition_logic: ConditionLogic

    def __init__(
        self,
        key: str,
        version: int,
        conditions: list[Condition] | tuple[Condition, ...],
        condition_logic: ConditionLogic,
    ) -> None:
        """Initialize Segment, converting conditions list to tuple for immutability."""
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "conditions", tuple(conditions))
        object.__setattr__(self, "condition_logic", condition_logic)


@dataclass(frozen=True)
class Prerequisite:
    """A dependency on another flag's served variation.

    A flag with prerequisites only evaluates its rules and fallthrough when
    every prerequisite resolves to the expected variation. Otherwise the
    off variation is served.

    Attributes:
        prerequisite_flag_key: The key of the flag that must be satisfied.
        expected_variation_key: The variation key the prerequisite must serve.
    """

    prerequisite_flag_key: str
    expected_variation_key: str


@dataclass(frozen=True)
class FlagConfiguration:
    """Complete configuration for a feature flag.

    Attributes:
        key: Unique identifier for the flag.
        version: Version number of the configuration.
        type: The type of value this flag returns.
        enabled: Whether the flag is enabled for evaluation.
        variations: All possible variations for this flag.
        rules: Targeting rules evaluated in priority order.
        fallthrough: Serve config when no rules match (and flag is enabled).
        off_variation: Variation key to serve when flag is disabled.
        prerequisites: Other flags that must serve specific variations before
            this flag's rules and fallthrough are evaluated.
    """

    key: str
    version: int
    type: FlagType
    enabled: bool
    variations: tuple[Variation, ...]
    rules: tuple[TargetingRule, ...]
    fallthrough: ServeConfig
    off_variation: str
    prerequisites: tuple[Prerequisite, ...] = ()
    _variations_by_key: dict[str, Variation] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __init__(
        self,
        key: str,
        version: int,
        type: FlagType,
        enabled: bool,
        variations: list[Variation] | tuple[Variation, ...],
        rules: list[TargetingRule] | tuple[TargetingRule, ...],
        fallthrough: ServeConfig,
        off_variation: str,
        prerequisites: list[Prerequisite] | tuple[Prerequisite, ...] = (),
    ) -> None:
        """Initialize FlagConfiguration with internal lookup index."""
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "variations", tuple(variations))
        object.__setattr__(self, "rules", tuple(rules))
        object.__setattr__(self, "fallthrough", fallthrough)
        object.__setattr__(self, "off_variation", off_variation)
        object.__setattr__(self, "prerequisites", tuple(prerequisites))
        # Build lookup index
        variations_by_key = {v.key: v for v in variations}
        object.__setattr__(self, "_variations_by_key", variations_by_key)

    def get_variation(self, key: str) -> Variation | None:
        """Get a variation by its key.

        Args:
            key: The variation key to look up.

        Returns:
            The Variation if found, None otherwise.
        """
        return self._variations_by_key.get(key)
