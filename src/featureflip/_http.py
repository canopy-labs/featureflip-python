"""HTTP client wrapper for Featureflip API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog

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
    from featureflip.config import Config

logger = structlog.get_logger()


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
                "User-Agent": "featureflip-python/0.1.0",
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
        data = response.json()
        flags = [self._parse_flag(f) for f in data.get("flags", [])]
        segments = [self._parse_segment(s) for s in data.get("segments", [])]
        logger.debug("Fetched flags", flag_count=len(flags), segment_count=len(segments))
        return flags, segments

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
            condition_logic=ConditionLogic(data.get("conditionLogic", "and").lower()),
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
            type=FlagType(data["type"].lower()),
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
            operator=ConditionLogic(data.get("operator", "And").lower()),
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
            operator=ConditionOperator(data["operator"].lower()),
            values=data["values"],
            negate=data.get("negate", False),
        )

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
            type=ServeType(data["type"].lower()),
            variation=data.get("variation"),
            bucket_by=data.get("bucketBy"),
            salt=data.get("salt"),
            variations=variations,
        )
