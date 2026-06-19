"""Flag evaluation engine."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog

from featureflip._semver import compare_semver
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

MAX_PREREQUISITE_DEPTH = 10

# Operators that must receive the original-case operands instead of the
# up-front-folded lowercase ones. Semver compares prerelease identifiers
# case-sensitively (semver §11, ASCII order) — folding would flip precedence vs
# the engine's SemverComparer (#1454). MATCHES_REGEX matches case-sensitively
# (the engine uses RegexOptions.None) (#1453). Date Before/After parse ISO-8601,
# whose "T"/"Z" designators are case-sensitive — folding would break parsing
# (#1455). Every other operator matches case-insensitively, so casing is folded
# up front only for those.
_CASE_SENSITIVE_OPERATORS = frozenset({
    ConditionOperator.SEMVER_EQUALS,
    ConditionOperator.SEMVER_GREATER_THAN,
    ConditionOperator.SEMVER_GREATER_THAN_OR_EQUAL,
    ConditionOperator.SEMVER_LESS_THAN,
    ConditionOperator.SEMVER_LESS_THAN_OR_EQUAL,
    ConditionOperator.MATCHES_REGEX,
    ConditionOperator.BEFORE,
    ConditionOperator.AFTER,
})

# Equality-family operators that get type-aware numeric coercion when the
# attribute value is a native number. Mirrors the .NET engine: when the
# attribute is numeric, Equals/NotEquals/In/NotIn compare numerically against
# each condition value parsed strictly as a number (so 1.0 == "1"), instead of
# stringifying. Contains/StartsWith/EndsWith are NOT coerced (#1458).
_NUMERIC_COERCIBLE_OPERATORS = frozenset({
    ConditionOperator.EQUALS,
    ConditionOperator.NOT_EQUALS,
    ConditionOperator.IN,
    ConditionOperator.NOT_IN,
})


def _parse_numeric(value: str) -> float | None:
    """Strictly parse a string literal as a float, or ``None`` if it isn't one.

    Strict: ``float("1abc")`` raises ``ValueError`` -> ``None`` (no coercion of
    partly-numeric strings). Mirrors the engine's invariant-culture double parse
    used for the relational operators.
    """
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


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

        # Type-aware numeric coercion (#1458): when the attribute is a native
        # number, the equality-family operators compare numerically instead of
        # stringifying, so 1.0 matches "1" and 1 matches "1.0" — mirroring the
        # .NET engine. Python's bool is a subclass of int and MUST be excluded so
        # True/False keep the string path (str(True) == "True", not "1").
        if (
            isinstance(attr_value, (int, float))
            and not isinstance(attr_value, bool)
            and condition.operator in _NUMERIC_COERCIBLE_OPERATORS
        ):
            return self._evaluate_numeric_equality(condition, float(attr_value))

        # Case-sensitive operators — semver (§11), MatchesRegex (engine uses
        # RegexOptions.None), and date Before/After (ISO "T"/"Z") — must see the
        # original casing; every other operator folds case here so it can match
        # case-insensitively.
        str_value = str(attr_value)
        values = [str(v) for v in condition.values]
        if condition.operator not in _CASE_SENSITIVE_OPERATORS:
            str_value = str_value.lower()
            values = [v.lower() for v in values]

        result = self._evaluate_operator(condition.operator, str_value, values)

        return not result if condition.negate else result

    def _evaluate_numeric_equality(self, condition: Condition, attr_value: float) -> bool:
        """Numerically compare a native-numeric attribute for the equality family.

        Mirrors the .NET engine: any condition value that parses strictly as a
        number and equals ``attr_value`` is a match. Equals/In match on any equal
        value; NotEquals/NotIn invert it. ``negate`` is then applied on top.

        Args:
            condition: The condition (operator, values, negate).
            attr_value: The attribute value as a float.

        Returns:
            True if the condition matches, False otherwise.
        """
        any_equal = any(
            (parsed := _parse_numeric(str(v))) is not None and parsed == attr_value
            for v in condition.values
        )
        result = (
            any_equal
            if condition.operator in (ConditionOperator.EQUALS, ConditionOperator.IN)
            else not any_equal
        )
        return not result if condition.negate else result

    @staticmethod
    def _search_regex(pattern: str, value: str) -> bool:
        """Run a single MatchesRegex pattern, failing safe to no-match.

        An invalid/uncompilable pattern returns ``False`` rather than raising
        ``re.error``, mirroring the engine's ``EvaluateMatchesRegex`` (which
        catches ``ArgumentException``) and every other SDK (#1460).

        ReDoS note (#1460): the engine bounds catastrophic backtracking with a
        100ms regex timeout. Python's ``re`` has no per-match timeout —
        ``signal``-based alarms are main-thread/Unix-only and too intrusive for
        a library, and the third-party ``regex`` module's timeout would add a
        dependency to this lightweight SDK — so a pathological config pattern
        can still be slow here.
        """
        try:
            return re.search(pattern, value) is not None
        except re.error:
            return False

    def _evaluate_operator(
        self, operator: ConditionOperator, value: str, targets: list[str]
    ) -> bool:
        """Evaluate an operator against a value and targets.

        Args:
            operator: The comparison operator.
            value: The context attribute value (lowercased for case-insensitive
                operators; original case for semver/regex operators).
            targets: The target values to compare against (same casing as
                ``value``).

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
                # Case-sensitive (engine uses RegexOptions.None): MATCHES_REGEX is
                # in _CASE_SENSITIVE_OPERATORS, so value/targets arrive in original
                # case and there is no re.IGNORECASE. Case-insensitivity is opt-in
                # via the (?i) inline flag in the pattern. _search_regex fails safe
                # to no-match for an invalid pattern (see its ReDoS note).
                return any(self._search_regex(t, value) for t in targets)
            # Relational operators match if the value satisfies the comparison
            # against ANY condition value (mirrors the server engine + semver
            # below), not just targets[0]. any() over [] yields False, so empty
            # values returns False without an IndexError.
            case ConditionOperator.GREATER_THAN:
                return any(self._compare_numeric(value, t, ">") for t in targets)
            case ConditionOperator.LESS_THAN:
                return any(self._compare_numeric(value, t, "<") for t in targets)
            case ConditionOperator.GREATER_THAN_OR_EQUAL:
                return any(self._compare_numeric(value, t, ">=") for t in targets)
            case ConditionOperator.LESS_THAN_OR_EQUAL:
                return any(self._compare_numeric(value, t, "<=") for t in targets)
            case ConditionOperator.BEFORE:
                return any(self._compare_datetime(value, t, "<") for t in targets)
            case ConditionOperator.AFTER:
                return any(self._compare_datetime(value, t, ">") for t in targets)
            case ConditionOperator.SEMVER_EQUALS:
                return any(compare_semver(value, t, "=") for t in targets)
            case ConditionOperator.SEMVER_GREATER_THAN:
                return any(compare_semver(value, t, ">") for t in targets)
            case ConditionOperator.SEMVER_GREATER_THAN_OR_EQUAL:
                return any(compare_semver(value, t, ">=") for t in targets)
            case ConditionOperator.SEMVER_LESS_THAN:
                return any(compare_semver(value, t, "<") for t in targets)
            case ConditionOperator.SEMVER_LESS_THAN_OR_EQUAL:
                return any(compare_semver(value, t, "<=") for t in targets)
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
        val = _parse_numeric(value)
        tgt = _parse_numeric(target)
        if val is None or tgt is None:
            return False
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

    def _parse_datetime(self, s: str) -> datetime | None:
        """Parse a string into a timezone-aware UTC datetime.

        Mirrors the server engine's ``TryParseDateTime``:

        1. Parse as an ISO-8601 date-time. If the string carries a timezone
           offset (``+05:00`` or ``Z``) it is honoured; if it has no offset the
           value is assumed to be UTC. The result is normalised to UTC.
        2. If ISO parsing fails and the string is a bare integer, treat it as
           Unix time in **seconds**. Out-of-range values fail.
        3. Otherwise return ``None`` (no match) — never a lexical fallback.

        Note: ``Before``/``After`` are in ``_CASE_SENSITIVE_OPERATORS``, so
        operands reach here with their original case (ISO ``T``/``Z`` intact).
        The trailing ``Z`` is still rewritten to ``+00:00`` because
        ``datetime.fromisoformat`` only accepts ``Z`` on Python 3.11+ and this
        SDK supports 3.10.

        Args:
            s: The candidate date-time string.

        Returns:
            A timezone-aware UTC ``datetime``, or ``None`` if unparseable.
        """
        raw = s.strip()

        # datetime.fromisoformat only accepts a trailing "Z" on Python 3.11+;
        # rewrite it to an explicit offset so 3.10 parses it too.
        iso = raw
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"

        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            # Unix timestamp fallback (seconds since epoch).
            if re.fullmatch(r"-?\d+", raw):
                try:
                    return datetime.fromtimestamp(int(raw), tz=timezone.utc)
                except (OverflowError, OSError, ValueError):
                    return None
            return None

        # Naive datetimes are assumed UTC; aware ones are converted to UTC.
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _compare_datetime(self, value: str, target: str, op: str) -> bool:
        """Compare two values as UTC date-time instants.

        Both operands are parsed via :meth:`_parse_datetime`. If either fails to
        parse there is no match (the engine skips unparseable operands rather
        than falling back to a lexical comparison).

        Args:
            value: The first value (as string).
            target: The second value (as string).
            op: The comparison operator ("<" for Before, ">" for After).

        Returns:
            True if both parse and the comparison is satisfied, False otherwise.
        """
        val = self._parse_datetime(value)
        if val is None:
            return False
        tgt = self._parse_datetime(target)
        if tgt is None:
            return False
        if op == "<":
            return val < tgt
        if op == ">":
            return val > tgt
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

        # A Rollout serve can arrive with no weighted variations — env-level PercentageRollout
        # has nowhere to store per-variation weights, so the mapper emits type=Rollout with no
        # variations (#1469). Degrade to the default fixed variation instead of returning an
        # empty key. Mirrors the engine + C#/Java SDK evaluators.
        if not serve.variations:
            return serve.variation or ""

        # Keyless user contexts can't be bucketed. Rather than hashing the empty
        # value into an arbitrary salt-dependent bucket, serve the control (first)
        # variation deterministically. The engine assigns a random GUID per eval
        # (spreading anonymous users over HTTP); local SDK eval is deterministic, so
        # parity is guaranteed only for keyed contexts (#1457).
        if bucket_value_str == "" and bucket_by in ("userId", "user_id") and serve.variations:
            return serve.variations[0].key

        bucket = self.compute_bucket(serve.salt or "", bucket_value_str)

        cumulative = 0
        for variation in serve.variations or []:
            cumulative += variation.weight
            if bucket < cumulative:
                return variation.key

        # Fallback to last variation. Unlike the JS/Go/Ruby SDKs (which return unconditionally
        # here), the explicit re-check is kept because mypy --strict loses the non-None narrowing
        # of serve.variations across the compute_bucket call above. The no-variations case already
        # returned the default at the top of this method, so the final `return ""` is unreachable.
        if serve.variations:
            return serve.variations[-1].key
        return ""

    def evaluate(
        self,
        flag: FlagConfiguration,
        context: EvaluationContext,
        get_segment: Callable[[str], Segment | None] | None = None,
        all_flags: dict[str, FlagConfiguration] | None = None,
    ) -> EvaluationDetail:
        """Evaluate a flag against context.

        Evaluation order:
        1. If flag disabled, return off variation
        2. Resolve prerequisites; if any fail, serve off variation
        3. Evaluate rules in priority order (0 = highest)
        4. First matching rule wins
        5. If no rules match, use fallthrough

        Args:
            flag: The flag configuration to evaluate.
            context: The evaluation context with user attributes.
            get_segment: Optional callable to look up segments by key.
            all_flags: Map of all flags in the environment, keyed by flag key.
                Required for prerequisite resolution; if omitted, only flags
                without prerequisites can be evaluated correctly.

        Returns:
            An EvaluationDetail containing the value, reason, and optional rule_id.
        """
        memo: dict[str, EvaluationDetail] = {}
        return self._evaluate_internal(
            flag, context, get_segment, all_flags or {}, depth=0, memo=memo
        )

    def evaluate_with_shared_memo(
        self,
        flag: FlagConfiguration,
        context: EvaluationContext,
        all_flags: dict[str, FlagConfiguration],
        memo: dict[str, EvaluationDetail],
        get_segment: Callable[[str], Segment | None] | None = None,
    ) -> EvaluationDetail:
        """Evaluate a flag while sharing a memoisation map with other calls.

        Use this when evaluating multiple flags in a single batch so shared
        prerequisite flags are evaluated only once across the batch.

        Args:
            flag: The flag configuration to evaluate.
            context: The evaluation context.
            all_flags: Map of all flags in the environment, keyed by flag key.
            memo: Memoisation dict shared across evaluations in the batch.
            get_segment: Optional callable to look up segments by key.

        Returns:
            An EvaluationDetail with the result. The memo is also updated.
        """
        return self._evaluate_internal(
            flag, context, get_segment, all_flags, depth=0, memo=memo
        )

    def _evaluate_internal(
        self,
        flag: FlagConfiguration,
        context: EvaluationContext,
        get_segment: Callable[[str], Segment | None] | None,
        all_flags: dict[str, FlagConfiguration],
        depth: int,
        memo: dict[str, EvaluationDetail],
    ) -> EvaluationDetail:
        # Guard against runaway recursion. Cycle detection happens at write
        # time on the server, so reaching this branch is defensive.
        if depth > MAX_PREREQUISITE_DEPTH:
            result = self._serve_off(flag, EvaluationReason.ERROR)
            memo[flag.key] = result
            return result

        # Step 1: Check if flag is disabled
        if not flag.enabled:
            result = self._serve_off(flag, EvaluationReason.FLAG_DISABLED)
            memo[flag.key] = result
            return result

        # Step 2: Resolve prerequisites
        for prereq in flag.prerequisites:
            prereq_result = memo.get(prereq.prerequisite_flag_key)
            if prereq_result is None:
                prereq_flag = all_flags.get(prereq.prerequisite_flag_key)
                if prereq_flag is None:
                    # Stale reference (delete-blocking should have prevented this — defensive)
                    result = self._serve_off(
                        flag,
                        EvaluationReason.PREREQUISITE_FAILED,
                        prerequisite_key=prereq.prerequisite_flag_key,
                    )
                    memo[flag.key] = result
                    return result
                prereq_result = self._evaluate_internal(
                    prereq_flag,
                    context,
                    get_segment,
                    all_flags,
                    depth + 1,
                    memo,
                )
                memo[prereq.prerequisite_flag_key] = prereq_result

            # Propagate depth-exceeded / internal errors upward
            if prereq_result.reason == EvaluationReason.ERROR:
                result = self._serve_off(flag, EvaluationReason.ERROR)
                memo[flag.key] = result
                return result

            if prereq_result.variation_key != prereq.expected_variation_key:
                result = self._serve_off(
                    flag,
                    EvaluationReason.PREREQUISITE_FAILED,
                    prerequisite_key=prereq.prerequisite_flag_key,
                )
                memo[flag.key] = result
                return result

        # Step 3: Evaluate rules in priority order
        sorted_rules = sorted(flag.rules, key=lambda r: r.priority)
        for rule in sorted_rules:
            # If rule references a segment, evaluate segment conditions. A
            # non-empty segment_key must resolve its segment to match: if the
            # segment source isn't wired (get_segment is None), or the segment
            # can't be found, fail closed (no match) -- mirroring the engine +
            # C# SDK -- rather than falling through to the rule's condition
            # groups (which match unconditionally when empty). See #1459.
            if rule.segment_key:
                if get_segment is None:
                    conditions_match = False
                else:
                    segment = get_segment(rule.segment_key)
                    if segment is None:
                        conditions_match = False
                    else:
                        conditions_match = self.evaluate_conditions(
                            list(segment.conditions),
                            segment.condition_logic,
                            context,
                        )
            else:
                conditions_match = self.evaluate_condition_groups(
                    rule.condition_groups, context
                )

            if conditions_match:
                variation_key = self._resolve_serve(rule.serve, context)
                variation = flag.get_variation(variation_key)
                value = variation.value if variation else None
                result = EvaluationDetail(
                    value=value,
                    reason=EvaluationReason.RULE_MATCH,
                    rule_id=rule.id,
                    variation_key=variation_key,
                )
                memo[flag.key] = result
                return result

        # Step 4: No rules matched, use fallthrough
        variation_key = self._resolve_serve(flag.fallthrough, context)
        variation = flag.get_variation(variation_key)
        value = variation.value if variation else None
        result = EvaluationDetail(
            value=value,
            reason=EvaluationReason.FALLTHROUGH,
            variation_key=variation_key,
        )
        memo[flag.key] = result
        return result

    def _serve_off(
        self,
        flag: FlagConfiguration,
        reason: EvaluationReason,
        *,
        prerequisite_key: str | None = None,
    ) -> EvaluationDetail:
        """Build an EvaluationDetail that serves the flag's off variation."""
        variation = flag.get_variation(flag.off_variation)
        value = variation.value if variation else None
        return EvaluationDetail(
            value=value,
            reason=reason,
            variation_key=flag.off_variation,
            prerequisite_key=prerequisite_key,
        )

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
