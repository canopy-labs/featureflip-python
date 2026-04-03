"""Flag evaluation engine."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

import structlog

from featureflip.detail import EvaluationDetail, EvaluationReason

if TYPE_CHECKING:
    from collections.abc import Callable

    from featureflip.context import EvaluationContext
from featureflip.models import (
    Condition,
    ConditionGroup,
    ConditionLogic,
    ConditionOperator,
    FlagConfiguration,
    Segment,
    ServeConfig,
    ServeType,
)

logger = structlog.get_logger()


class FlagEvaluator:
    """Evaluates feature flags against context."""

    def evaluate_condition(self, condition: Condition, context: EvaluationContext) -> bool:
        """Evaluate a single condition against context.

        Args:
            condition: The condition to evaluate.
            context: The evaluation context with user attributes.

        Returns:
            True if the condition matches, False otherwise.
        """
        # Get attribute value
        attr_value = context.get_attribute(condition.attribute)

        # Missing attribute = fail (unless negated)
        if attr_value is None:
            return condition.negate

        # Convert to lowercase string for comparison
        str_value = str(attr_value).lower()

        result = self._evaluate_operator(
            condition.operator,
            str_value,
            [str(v).lower() for v in condition.values],
        )

        return not result if condition.negate else result

    def _evaluate_operator(
        self, operator: ConditionOperator, value: str, targets: list[str]
    ) -> bool:
        """Evaluate an operator against a value and targets.

        Args:
            operator: The comparison operator.
            value: The context attribute value (lowercase).
            targets: The target values to compare against (lowercase).

        Returns:
            True if the operator condition is satisfied, False otherwise.
        """
        match operator:
            case ConditionOperator.EQUALS:
                return any(value == t for t in targets)
            case ConditionOperator.NOT_EQUALS:
                return all(value != t for t in targets)
            case ConditionOperator.CONTAINS:
                return any(t in value for t in targets)
            case ConditionOperator.NOT_CONTAINS:
                return all(t not in value for t in targets)
            case ConditionOperator.STARTS_WITH:
                return any(value.startswith(t) for t in targets)
            case ConditionOperator.ENDS_WITH:
                return any(value.endswith(t) for t in targets)
            case ConditionOperator.IN:
                return value in targets
            case ConditionOperator.NOT_IN:
                return value not in targets
            case ConditionOperator.MATCHES_REGEX:
                return any(
                    re.search(t, value, re.IGNORECASE) is not None for t in targets
                )
            case ConditionOperator.GREATER_THAN:
                return self._compare_numeric(value, targets[0], ">")
            case ConditionOperator.LESS_THAN:
                return self._compare_numeric(value, targets[0], "<")
            case ConditionOperator.GREATER_THAN_OR_EQUAL:
                return self._compare_numeric(value, targets[0], ">=")
            case ConditionOperator.LESS_THAN_OR_EQUAL:
                return self._compare_numeric(value, targets[0], "<=")
            case ConditionOperator.BEFORE:
                return value < targets[0]  # ISO string comparison
            case ConditionOperator.AFTER:
                return value > targets[0]  # ISO string comparison
            case _:
                logger.warning("unknown_operator", operator=operator)
                return False

    def _compare_numeric(self, value: str, target: str, op: str) -> bool:
        """Compare two values as numbers.

        Args:
            value: The first value (as string).
            target: The second value (as string).
            op: The comparison operator (">", "<", ">=", "<=").

        Returns:
            True if the comparison is satisfied, False otherwise.
        """
        try:
            val = float(value)
            tgt = float(target)
            match op:
                case ">":
                    return val > tgt
                case "<":
                    return val < tgt
                case ">=":
                    return val >= tgt
                case "<=":
                    return val <= tgt
                case _:
                    return False
        except ValueError:
            return False

    def evaluate_conditions(
        self,
        conditions: list[Condition],
        logic: ConditionLogic,
        context: EvaluationContext,
    ) -> bool:
        """Evaluate multiple conditions with AND/OR logic.

        Args:
            conditions: List of conditions to evaluate.
            logic: How to combine condition results (AND/OR).
            context: The evaluation context with user attributes.

        Returns:
            True if conditions are satisfied according to the logic, False otherwise.
        """
        if not conditions:
            return True

        results = [self.evaluate_condition(c, context) for c in conditions]

        if logic == ConditionLogic.AND:
            return all(results)
        else:  # OR
            return any(results)

    def evaluate_condition_groups(
        self,
        condition_groups: list[ConditionGroup] | tuple[ConditionGroup, ...],
        context: EvaluationContext,
    ) -> bool:
        """Evaluate condition groups (ANDed together).

        Each group has its own operator (AND/OR) for combining its conditions.
        Groups are ANDed together: all groups must match for the result to be True.

        Args:
            condition_groups: List of condition groups to evaluate.
            context: The evaluation context with user attributes.

        Returns:
            True if all condition groups are satisfied, False otherwise.
        """
        if not condition_groups:
            return True

        for group in condition_groups:
            group_result = self.evaluate_conditions(
                list(group.conditions), group.operator, context
            )
            if not group_result:
                return False
        return True

    def compute_bucket(self, salt: str, value: str) -> int:
        """Compute deterministic bucket (0-99) for a value.

        Uses MD5 hashing for consistency with other SDKs.
        Formula: int(md5(salt:value).digest()[:4], 'little') % 100

        Args:
            salt: Salt string for the hash (typically flag-specific).
            value: The value to hash (typically user identifier).

        Returns:
            An integer bucket in the range [0, 99].
        """
        input_str = f"{salt}:{value}"
        hash_bytes = hashlib.md5(input_str.encode()).digest()
        hash_int = int.from_bytes(hash_bytes[:4], "little", signed=False)
        return hash_int % 100

    def resolve_rollout(self, serve: ServeConfig, context: EvaluationContext) -> str:
        """Resolve which variation to serve for a rollout.

        Args:
            serve: The serve configuration with rollout settings.
            context: The evaluation context with user attributes.

        Returns:
            The variation key to serve based on the bucket assignment.
        """
        bucket_by = serve.bucket_by or "userId"
        bucket_value = context.get_attribute(bucket_by)
        bucket_value_str = str(bucket_value) if bucket_value is not None else ""

        bucket = self.compute_bucket(serve.salt or "", bucket_value_str)

        cumulative = 0
        for variation in serve.variations or []:
            cumulative += variation.weight
            if bucket < cumulative:
                return variation.key

        # Fallback to last variation
        if serve.variations:
            return serve.variations[-1].key
        return ""

    def evaluate(
        self,
        flag: FlagConfiguration,
        context: EvaluationContext,
        get_segment: Callable[[str], Segment | None] | None = None,
    ) -> EvaluationDetail:
        """Evaluate a flag against context.

        Evaluation order:
        1. If flag disabled, return off variation
        2. Evaluate rules in priority order (0 = highest)
        3. First matching rule wins
        4. If no rules match, use fallthrough

        Args:
            flag: The flag configuration to evaluate.
            context: The evaluation context with user attributes.
            get_segment: Optional callable to look up segments by key.

        Returns:
            An EvaluationDetail containing the value, reason, and optional rule_id.
        """
        # Step 1: Check if flag is disabled
        if not flag.enabled:
            variation = flag.get_variation(flag.off_variation)
            value = variation.value if variation else None
            return EvaluationDetail(value=value, reason=EvaluationReason.FLAG_DISABLED)

        # Step 2: Evaluate rules in priority order
        sorted_rules = sorted(flag.rules, key=lambda r: r.priority)
        for rule in sorted_rules:
            # If rule references a segment, evaluate segment conditions
            if rule.segment_key and get_segment:
                segment = get_segment(rule.segment_key)
                if segment is None:
                    conditions_match = False
                else:
                    conditions_match = self.evaluate_conditions(
                        list(segment.conditions), segment.condition_logic, context
                    )
            else:
                conditions_match = self.evaluate_condition_groups(
                    rule.condition_groups, context
                )

            if conditions_match:
                variation_key = self._resolve_serve(rule.serve, context)
                variation = flag.get_variation(variation_key)
                value = variation.value if variation else None
                return EvaluationDetail(
                    value=value, reason=EvaluationReason.RULE_MATCH, rule_id=rule.id
                )

        # Step 3: No rules matched, use fallthrough
        variation_key = self._resolve_serve(flag.fallthrough, context)
        variation = flag.get_variation(variation_key)
        value = variation.value if variation else None
        return EvaluationDetail(value=value, reason=EvaluationReason.FALLTHROUGH)

    def _resolve_serve(self, serve: ServeConfig, context: EvaluationContext) -> str:
        """Resolve which variation key to serve.

        Args:
            serve: The serve configuration (FIXED or ROLLOUT).
            context: The evaluation context with user attributes.

        Returns:
            The variation key to serve.
        """
        if serve.type == ServeType.FIXED:
            return serve.variation or ""
        else:  # ROLLOUT
            return self.resolve_rollout(serve, context)
