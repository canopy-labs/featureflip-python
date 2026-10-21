"""HTTP client wrapper for Featureflip API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

import httpx
import structlog

from featureflip._version import user_agent
from featureflip.exceptions import UnevaluableEntityError
from featureflip.models import (
    Condition,
    ConditionGroup,
    ConditionLogic,
    ConditionOperator,
    FlagConfiguration,
    FlagType,
    Prerequisite,
    Segment,
    ServeConfig,
    ServeType,
    TargetingRule,
    Variation,
    WeightedVariation,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from featureflip.config import Config

logger = structlog.get_logger()

_T = TypeVar("_T")
_E = TypeVar("_E", ServeType, ConditionLogic)


class HttpClient:
    """HTTP client for communicating with Featureflip API."""

    def __init__(self, sdk_key: str, config: Config) -> None:
        """Initialize the HTTP client.

        Args:
            sdk_key: The SDK key for authentication.
            config: Client configuration options.
        """
        self._sdk_key = sdk_key
        self._config = config
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=httpx.Timeout(
                connect=config.connect_timeout,
                read=config.read_timeout,
                write=config.read_timeout,
                pool=config.connect_timeout,
            ),
            headers={
                "Authorization": sdk_key,
                "User-Agent": user_agent(config.wrapper_name, config.wrapper_version),
            },
        )

    def __enter__(self) -> HttpClient:
        """Enter context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit context manager and close the client."""
        self.close()

    def get_flags(self) -> tuple[list[FlagConfiguration], list[Segment]]:
        """Fetch all flag and segment configurations from the API.

        Returns:
            Tuple of (flags, segments).

        Raises:
            httpx.HTTPStatusError: If the API returns an error response.
            httpx.ConnectError: If the connection fails.
        """
        logger.debug("Fetching flags from API")
        response = self._client.get("/v1/sdk/flags")
        response.raise_for_status()
        flags, segments = self.parse_flags_response(response.json())
        logger.debug("Fetched flags", flag_count=len(flags), segment_count=len(segments))
        return flags, segments

    def parse_flags_response(
        self, data: dict[str, Any]
    ) -> tuple[list[FlagConfiguration], list[Segment]]:
        """Parse a GET /v1/sdk/flags-shaped snapshot into models.

        Reused for the connect-time ``sync`` SSE snapshot, which carries the
        identical payload shape inline (no extra HTTP round-trip).
        """
        flags = self._drop_unevaluable(data.get("flags", []), self._parse_flag, "flag")
        segments = self._drop_unevaluable(
            data.get("segments", []), self._parse_segment, "segment"
        )
        return flags, segments

    @staticmethod
    def _drop_unevaluable(
        entries: list[dict[str, Any]],
        parse: Callable[[dict[str, Any]], _T],
        kind: str,
    ) -> list[_T]:
        """Parse each entry, dropping the ones this build cannot evaluate (#2402).

        Scoped to ``UnevaluableEntityError`` alone. Every other parse failure still
        propagates and fails the payload wholesale, because those are wire-contract
        violations (#2315) where a partially-applied snapshot silently mis-evaluates
        every flag it touches. This one is not a violation at all: the payload is
        well-formed and simply describes behaviour a newer server understands and this
        build does not, so the blast radius is the one entity that carried it.
        """
        parsed: list[_T] = []
        for entry in entries:
            try:
                parsed.append(parse(entry))
            except UnevaluableEntityError as e:
                logger.warning(
                    "Dropping an entity this SDK version cannot evaluate - the rest of "
                    "the configuration was applied. This SDK version may be older than "
                    "the flag configuration.",
                    kind=kind,
                    key=entry.get("key"),
                    reason=str(e),
                )
        return parsed

    def get_flag(self, key: str) -> FlagConfiguration:
        """Fetch a single flag configuration by key.

        Args:
            key: The flag key.

        Returns:
            Parsed FlagConfiguration.

        Raises:
            httpx.HTTPStatusError: If the API returns an error response.
        """
        response = self._client.get(f"/v1/sdk/flags/{key}")
        response.raise_for_status()
        return self._parse_flag(response.json())

    def post_events(self, events: list[dict[str, Any]]) -> None:
        """Send analytics events to the API.

        Args:
            events: List of event dictionaries to send.

        Raises:
            httpx.HTTPStatusError: If the API returns an error response.
            httpx.ConnectError: If the connection fails.
        """
        logger.debug("Posting events to API", count=len(events))
        response = self._client.post("/v1/sdk/events", json={"events": events})
        response.raise_for_status()
        logger.debug("Events posted successfully")

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def _parse_segment(self, data: dict[str, Any]) -> Segment:
        """Parse a segment from JSON.

        Args:
            data: JSON dictionary representing a segment.

        Returns:
            Parsed Segment object.
        """
        return Segment(
            key=data["key"],
            version=data["version"],
            conditions=[
                self._parse_condition(c) for c in data.get("conditions", [])
            ],
            condition_logic=self._parse_condition_logic(
                data.get("conditionLogic", "and"), "conditionLogic"
            ),
        )

    def _parse_flag(self, data: dict[str, Any]) -> FlagConfiguration:
        """Parse a flag configuration from JSON.

        Args:
            data: JSON dictionary representing a flag configuration.

        Returns:
            Parsed FlagConfiguration object.
        """
        return FlagConfiguration(
            key=data["key"],
            version=data["version"],
            type=self._parse_flag_type(data["type"]),
            enabled=data["enabled"],
            variations=[
                Variation(key=v["key"], value=v["value"])
                for v in data["variations"]
            ],
            rules=[self._parse_rule(r) for r in data.get("rules", [])],
            fallthrough=self._parse_serve(data["fallthrough"]),
            off_variation=data["offVariation"],
            prerequisites=[
                Prerequisite(
                    prerequisite_flag_key=p["prerequisiteFlagKey"],
                    expected_variation_key=p["expectedVariationKey"],
                )
                for p in data.get("prerequisites", []) or []
            ],
        )

    def _parse_rule(self, data: dict[str, Any]) -> TargetingRule:
        """Parse a targeting rule from JSON.

        Args:
            data: JSON dictionary representing a targeting rule.

        Returns:
            Parsed TargetingRule object.
        """
        return TargetingRule(
            id=data["id"],
            priority=data["priority"],
            condition_groups=[
                self._parse_condition_group(g)
                for g in data.get("conditionGroups", [])
            ],
            serve=self._parse_serve(data["serve"]),
            segment_key=data.get("segmentKey"),
        )

    def _parse_condition_group(self, data: dict[str, Any]) -> ConditionGroup:
        """Parse a condition group from JSON.

        Args:
            data: JSON dictionary representing a condition group.

        Returns:
            Parsed ConditionGroup object.
        """
        return ConditionGroup(
            operator=self._parse_condition_logic(
                data.get("operator", "And"), "conditionGroup.operator"
            ),
            conditions=[
                self._parse_condition(c) for c in data.get("conditions", [])
            ],
        )

    def _parse_condition(self, data: dict[str, Any]) -> Condition:
        """Parse a condition from JSON.

        Args:
            data: JSON dictionary representing a condition.

        Returns:
            Parsed Condition object.
        """
        return Condition(
            attribute=data["attribute"],
            operator=self._parse_operator(data["operator"]),
            values=data["values"],
            negate=data.get("negate", False),
        )

    @staticmethod
    def _parse_serve_type(raw: Any) -> ServeType:
        """Resolve a wire serve type, dropping the containing entity on an unknown name.

        Unlike ``_parse_flag_type`` (#2401) this value cannot be tolerated and stored:
        ``FlagEvaluator`` dispatches ``serve.type is ServeType.FIXED`` else ROLLOUT, so a
        raw string would take the rollout arm and bucket against weights that may not
        exist. See :class:`UnevaluableEntityError`.
        """
        return HttpClient._resolve_enum(raw, ServeType, "serve.type", "serve type")

    @staticmethod
    def _parse_condition_logic(raw: Any, path: str) -> ConditionLogic:
        """Resolve a wire condition logic, dropping the containing entity on an unknown
        name. ``evaluate_conditions`` dispatches AND else ANY, so an unrecognised value
        would make a segment meant to require ALL of its conditions match ANY of them —
        the rule fails OPEN and over-targets."""
        return HttpClient._resolve_enum(raw, ConditionLogic, path, "condition logic")

    @staticmethod
    def _resolve_enum(raw: Any, enum_cls: type[_E], path: str, label: str) -> _E:
        # `.lower()` on a non-string still raises AttributeError, which is the wire-TYPE
        # violation axis and still fails the payload wholesale (#2315). Deliberately
        # untouched: an integer is the server and SDK disagreeing about the wire format,
        # not a newer server shipping a value this build has not heard of.
        lowered = raw.lower()

        try:
            return enum_cls(lowered)
        except ValueError:
            # An EMPTY value is the field being absent, which is the
            # missing-required-field axis rather than this one, and the SDKs
            # deliberately disagree about it. Re-raise so its handling is unchanged;
            # only a value that is PRESENT and unrecognised drops the entity, which
            # keeps this change purely additive.
            if lowered == "":
                raise
            raise UnevaluableEntityError(
                f'{path} "{raw}" is not a {label} this SDK version understands'
            ) from None

    @staticmethod
    def _parse_flag_type(raw: Any) -> FlagType | str:
        """Resolve a wire flag type, tolerating one this SDK version does not know.

        ``FlagType(raw)`` raises ``ValueError`` on an unrecognised name, and because the
        throw surfaced mid-payload it failed the ENTIRE flags parse: the day a new flag
        type ships server-side, every pinned SDK stops serving EVERY flag, not just the
        one carrying the new type (#2395). That is the same total-outage shape #2372
        fixed for ``ConditionOperator``, triggered by an ordinary additive server change.

        Tolerating it is unconditionally safe here in a way it is NOT for ``ServeType``
        or ``ConditionLogic``: nothing evaluates this field. It is parsed, stored and
        re-exported, and no evaluator reads it — go, ruby and php do not even model it.
        So there is no downstream consumer that could mis-serve on the raw string, and
        equally none that could warn about it, which is why the diagnostic is emitted
        here rather than left to evaluation time.

        A non-string type is a DIFFERENT axis and is not tolerated, exactly as in
        ``_parse_operator``: an integer is the server and SDK disagreeing about the wire
        format (#2279), and coercing it with ``str()`` would turn ``0`` into the flag
        type ``"0"`` -- indistinguishable from a genuine unknown name, and silently wrong
        in the way #2315 exists to prevent.
        """
        if not isinstance(raw, str):
            raise TypeError(
                f"flag.type must be a string, got {type(raw).__name__}: {raw!r}"
            )

        try:
            return FlagType(raw.lower())
        except ValueError:
            logger.warning(
                "Unrecognised flag type - retaining the raw value. This SDK version "
                "may be older than the flag configuration.",
                flag_type=raw,
            )
            return raw

    @staticmethod
    def _parse_operator(raw: Any) -> ConditionOperator | str:
        """Resolve a wire operator, tolerating one this SDK version does not know.

        The wire sends PascalCase operators (e.g. ``"SemverGreaterThanOrEqual"``);
        ``ConditionOperator._missing_`` normalizes them to the snake_case members.

        An unrecognised name raises ``ValueError`` from the enum call. That used to
        propagate out of the whole flags-payload parse, so ONE unknown operator in ONE
        condition of ONE flag failed the ENTIRE fetch and every flag fell back to the
        caller's defaults (#2372). The trigger is ordinary: a new operator shipped
        server-side reaching an SDK a customer has pinned.

        The raw string is returned instead of a member, which is safe because
        ``FlagEvaluator`` fails an unrecognised operator CLOSED (#2262) — its ``case _``
        arm returns None and ``negate`` never inverts it into a match-everyone. Keeping
        the original text (rather than substituting a sentinel) means the evaluator's
        ``unknown_operator`` warning names the operator actually received, which is the
        one detail an operator debugging this needs.

        A non-string operator is a DIFFERENT axis and is not tolerated. Tolerance here
        is for an unknown *name*; an integer is the server and SDK disagreeing about the
        wire format, and coercing it with ``str()`` would turn ``0`` into the operator
        ``"0"`` -- indistinguishable from a genuine unknown name, and silently wrong in
        exactly the way #2315 exists to prevent. csharp's tolerant converter had the
        identical hole (it resolved numbers by ordinal) and closed it the same way; the
        shared ``malformedConfigVectors`` runner pins both.
        """
        if not isinstance(raw, str):
            raise TypeError(
                f"condition.operator must be a string, got {type(raw).__name__}: {raw!r}"
            )

        try:
            return ConditionOperator(raw)
        except ValueError:
            return str(raw)

    def _parse_serve(self, data: dict[str, Any]) -> ServeConfig:
        """Parse a serve config from JSON.

        Args:
            data: JSON dictionary representing a serve configuration.

        Returns:
            Parsed ServeConfig object.
        """
        variations = None
        if "variations" in data:
            variations = [
                WeightedVariation(key=v["key"], weight=v["weight"])
                for v in data["variations"]
            ]
        return ServeConfig(
            type=self._parse_serve_type(data["type"]),
            variation=data.get("variation"),
            bucket_by=data.get("bucketBy"),
            salt=data.get("salt"),
            variations=variations,
        )
