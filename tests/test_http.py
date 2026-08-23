"""Tests for HTTP client wrapper."""

import pytest
import respx
from httpx import Response

from featureflip._http import HttpClient
from featureflip.config import Config
from featureflip.models import ConditionOperator, FlagType


class TestHttpClient:
    @pytest.fixture
    def config(self) -> Config:
        return Config(base_url="https://api.example.com")

    def test_parse_flags_response_parses_flags_and_segments(self, config: Config) -> None:
        client = HttpClient(sdk_key="test-key", config=config)
        data = {
            "flags": [
                {
                    "key": "flag-a",
                    "version": 1,
                    "type": "boolean",
                    "enabled": True,
                    "variations": [
                        {"key": "on", "value": True},
                        {"key": "off", "value": False},
                    ],
                    "rules": [],
                    "fallthrough": {"type": "fixed", "variation": "on"},
                    "offVariation": "off",
                }
            ],
            "segments": [],
        }

        flags, segments = client.parse_flags_response(data)

        assert [f.key for f in flags] == ["flag-a"]
        assert segments == []

    def test_parse_flags_response_tolerates_missing_keys(self, config: Config) -> None:
        client = HttpClient(sdk_key="test-key", config=config)
        flags, segments = client.parse_flags_response({})
        assert flags == []
        assert segments == []

    @respx.mock
    def test_get_flags(self, config: Config) -> None:
        route = respx.get("https://api.example.com/v1/sdk/flags").mock(
            return_value=Response(
                200,
                json={
                    "flags": [
                        {
                            "key": "test-flag",
                            "version": 1,
                            "type": "boolean",
                            "enabled": True,
                            "variations": [
                                {"key": "on", "value": True},
                                {"key": "off", "value": False},
                            ],
                            "rules": [],
                            "fallthrough": {"type": "fixed", "variation": "on"},
                            "offVariation": "off",
                        }
                    ]
                },
            )
        )

        client = HttpClient(sdk_key="test-key", config=config)
        flags, _segments = client.get_flags()
        client.close()

        assert len(flags) == 1
        assert flags[0].key == "test-flag"
        assert route.called

    @respx.mock
    def test_authorization_header(self, config: Config) -> None:
        route = respx.get("https://api.example.com/v1/sdk/flags").mock(
            return_value=Response(200, json={"flags": []})
        )

        client = HttpClient(sdk_key="my-sdk-key", config=config)
        client.get_flags()
        client.close()

        assert route.calls[0].request.headers["Authorization"] == "my-sdk-key"

    @respx.mock
    def test_post_events(self, config: Config) -> None:
        route = respx.post("https://api.example.com/v1/sdk/events").mock(
            return_value=Response(202)
        )

        client = HttpClient(sdk_key="test-key", config=config)
        client.post_events([
            {"type": "Evaluation", "flag_key": "test", "value": True}
        ])
        client.close()

        assert route.called
        assert route.calls[0].request.headers["Content-Type"] == "application/json"

    @respx.mock
    def test_network_error_raises(self, config: Config) -> None:
        from httpx import ConnectError

        respx.get("https://api.example.com/v1/sdk/flags").mock(
            side_effect=ConnectError("Connection failed")
        )

        client = HttpClient(sdk_key="test-key", config=config)
        with pytest.raises(ConnectError):
            client.get_flags()
        client.close()

    @respx.mock
    def test_get_flags_with_rules(self, config: Config) -> None:
        """Test parsing flags with targeting rules."""
        route = respx.get("https://api.example.com/v1/sdk/flags").mock(
            return_value=Response(
                200,
                json={
                    "flags": [
                        {
                            "key": "targeted-flag",
                            "version": 2,
                            "type": "string",
                            "enabled": True,
                            "variations": [
                                {"key": "premium", "value": "premium-experience"},
                                {"key": "standard", "value": "standard-experience"},
                            ],
                            "rules": [
                                {
                                    "id": "rule-1",
                                    "priority": 1,
                                    "conditionGroups": [
                                        {
                                            "operator": "And",
                                            "conditions": [
                                                {
                                                    "attribute": "country",
                                                    "operator": "in",
                                                    "values": ["US", "CA"],
                                                    "negate": False,
                                                }
                                            ],
                                        }
                                    ],
                                    "serve": {"type": "fixed", "variation": "premium"},
                                }
                            ],
                            "fallthrough": {"type": "fixed", "variation": "standard"},
                            "offVariation": "standard",
                        }
                    ]
                },
            )
        )

        client = HttpClient(sdk_key="test-key", config=config)
        flags, _segments = client.get_flags()
        client.close()

        assert len(flags) == 1
        flag = flags[0]
        assert flag.key == "targeted-flag"
        assert len(flag.rules) == 1
        assert flag.rules[0].id == "rule-1"
        assert len(flag.rules[0].condition_groups) == 1
        assert len(flag.rules[0].condition_groups[0].conditions) == 1
        assert flag.rules[0].condition_groups[0].conditions[0].attribute == "country"
        assert route.called

    @respx.mock
    def test_get_flags_normalizes_pascalcase_operators(self, config: Config) -> None:
        """The server sends operators in PascalCase (e.g. "SemverGreaterThanOrEqual").

        Multi-word operators must round-trip to their snake_case enum members — a bare
        ``.lower()`` collapses "GreaterThan" to "greaterthan" and fails to resolve.
        """
        route = respx.get("https://api.example.com/v1/sdk/flags").mock(
            return_value=Response(
                200,
                json={
                    "flags": [
                        {
                            "key": "versioned-flag",
                            "version": 1,
                            "type": "boolean",
                            "enabled": True,
                            "variations": [
                                {"key": "true", "value": True},
                                {"key": "false", "value": False},
                            ],
                            "rules": [
                                {
                                    "id": "rule-1",
                                    "priority": 1,
                                    "conditionGroups": [
                                        {
                                            "operator": "And",
                                            "conditions": [
                                                {
                                                    "attribute": "age",
                                                    "operator": "GreaterThan",
                                                    "values": ["18"],
                                                    "negate": False,
                                                },
                                                {
                                                    "attribute": "version",
                                                    "operator": "SemverGreaterThanOrEqual",
                                                    "values": ["2.0"],
                                                    "negate": False,
                                                },
                                            ],
                                        }
                                    ],
                                    "serve": {"type": "fixed", "variation": "false"},
                                }
                            ],
                            "fallthrough": {"type": "fixed", "variation": "true"},
                            "offVariation": "false",
                        }
                    ]
                },
            )
        )

        client = HttpClient(sdk_key="test-key", config=config)
        flags, _segments = client.get_flags()
        client.close()

        conditions = flags[0].rules[0].condition_groups[0].conditions
        assert conditions[0].operator is ConditionOperator.GREATER_THAN
        assert conditions[1].operator is ConditionOperator.SEMVER_GREATER_THAN_OR_EQUAL
        assert route.called

    @respx.mock
    def test_get_flags_with_rollout(self, config: Config) -> None:
        """Test parsing flags with percentage rollout."""
        route = respx.get("https://api.example.com/v1/sdk/flags").mock(
            return_value=Response(
                200,
                json={
                    "flags": [
                        {
                            "key": "rollout-flag",
                            "version": 1,
                            "type": "boolean",
                            "enabled": True,
                            "variations": [
                                {"key": "on", "value": True},
                                {"key": "off", "value": False},
                            ],
                            "rules": [],
                            "fallthrough": {
                                "type": "rollout",
                                "bucketBy": "user_id",
                                "salt": "abc123",
                                "variations": [
                                    {"key": "on", "weight": 50},
                                    {"key": "off", "weight": 50},
                                ],
                            },
                            "offVariation": "off",
                        }
                    ]
                },
            )
        )

        client = HttpClient(sdk_key="test-key", config=config)
        flags, _segments = client.get_flags()
        client.close()

        assert len(flags) == 1
        flag = flags[0]
        assert flag.fallthrough.type.value == "rollout"
        assert flag.fallthrough.bucket_by == "user_id"
        assert flag.fallthrough.salt == "abc123"
        assert len(flag.fallthrough.variations) == 2
        assert route.called

    @respx.mock
    def test_user_agent_header(self, config: Config) -> None:
        """Test that the User-Agent header is set correctly."""
        route = respx.get("https://api.example.com/v1/sdk/flags").mock(
            return_value=Response(200, json={"flags": []})
        )

        client = HttpClient(sdk_key="test-key", config=config)
        client.get_flags()
        client.close()

        assert "featureflip-python" in route.calls[0].request.headers["User-Agent"]

    @respx.mock
    def test_context_manager(self, config: Config) -> None:
        """Test that HttpClient can be used as a context manager."""
        route = respx.get("https://api.example.com/v1/sdk/flags").mock(
            return_value=Response(200, json={"flags": []})
        )

        with HttpClient(sdk_key="test-key", config=config) as client:
            flags, segments = client.get_flags()

        assert flags == []
        assert segments == []
        assert route.called

    @respx.mock
    def test_http_error_response(self, config: Config) -> None:
        """Test that HTTP error responses raise exceptions."""
        from httpx import HTTPStatusError

        respx.get("https://api.example.com/v1/sdk/flags").mock(
            return_value=Response(401, json={"error": "Unauthorized"})
        )

        client = HttpClient(sdk_key="invalid-key", config=config)
        with pytest.raises(HTTPStatusError):
            client.get_flags()
        client.close()

    @respx.mock
    def test_get_flags_with_segment_rule(self, config: Config) -> None:
        """Test parsing flags with segment-based targeting rules."""
        route = respx.get("https://api.example.com/v1/sdk/flags").mock(
            return_value=Response(
                200,
                json={
                    "flags": [
                        {
                            "key": "segment-flag",
                            "version": 1,
                            "type": "boolean",
                            "enabled": True,
                            "variations": [
                                {"key": "on", "value": True},
                                {"key": "off", "value": False},
                            ],
                            "rules": [
                                {
                                    "id": "rule-1",
                                    "priority": 1,
                                    "conditionGroups": [],
                                    "serve": {"type": "fixed", "variation": "on"},
                                    "segmentKey": "beta-users",
                                }
                            ],
                            "fallthrough": {"type": "fixed", "variation": "off"},
                            "offVariation": "off",
                        }
                    ]
                },
            )
        )

        client = HttpClient(sdk_key="test-key", config=config)
        flags, _segments = client.get_flags()
        client.close()

        assert len(flags) == 1
        flag = flags[0]
        assert len(flag.rules) == 1
        assert flag.rules[0].segment_key == "beta-users"
        assert route.called

class TestUnknownOperatorTolerance:
    """An operator this SDK version does not know must degrade ONE condition (#2372).

    ``ConditionOperator(raw)`` raises ``ValueError`` on an unrecognised name, and that
    used to propagate out of the whole flags-payload parse — so one unknown operator in
    one condition of one flag failed the ENTIRE fetch and every flag fell back to the
    caller's defaults. The trigger is ordinary: a new operator shipped server-side
    reaching an SDK a customer has pinned.

    The companion rule is in ``test_evaluation.py``: an unrecognised operator fails
    CLOSED, so tolerating it here cannot become a match-everyone.
    """

    @staticmethod
    def _flag(key: str, operator: str) -> dict:
        return {
            "key": key,
            "version": 1,
            "type": "Boolean",
            "enabled": True,
            "variations": [
                {"key": "on", "value": True},
                {"key": "off", "value": False},
            ],
            "rules": [
                {
                    "id": "r1",
                    "priority": 0,
                    "conditionGroups": [
                        {
                            "operator": "And",
                            "conditions": [
                                {
                                    "attribute": "country",
                                    "operator": operator,
                                    "values": ["US"],
                                    "negate": True,
                                }
                            ],
                        }
                    ],
                    "serve": {"type": "Fixed", "variation": "on"},
                }
            ],
            "fallthrough": {"type": "Fixed", "variation": "off"},
            "offVariation": "off",
        }

    def _parse(self, payload: dict) -> list:
        client = HttpClient(sdk_key="test-key", config=Config(base_url="https://api.example.com"))
        flags, _segments = client.parse_flags_response(payload)
        return flags

    def test_unknown_operator_is_retained_as_raw_string(self) -> None:
        flags = self._parse({"flags": [self._flag("f", "SomeFutureOperator")], "segments": []})

        condition = flags[0].rules[0].condition_groups[0].conditions[0]
        assert condition.operator == "SomeFutureOperator"

    def test_known_operators_still_parse_to_members(self) -> None:
        flags = self._parse({"flags": [self._flag("f", "SemverGreaterThanOrEqual")], "segments": []})

        condition = flags[0].rules[0].condition_groups[0].conditions[0]
        assert condition.operator is ConditionOperator.SEMVER_GREATER_THAN_OR_EQUAL

    def test_one_unknown_operator_does_not_fail_the_whole_response(self) -> None:
        """The point of the issue: one bad flag must not take its neighbours down."""
        flags = self._parse(
            {
                "flags": [
                    self._flag("broken", "SomeFutureOperator"),
                    self._flag("healthy", "Equals"),
                ],
                "segments": [],
            }
        )

        assert len(flags) == 2
        healthy = next(f for f in flags if f.key == "healthy")
        assert (
            healthy.rules[0].condition_groups[0].conditions[0].operator
            is ConditionOperator.EQUALS
        )


class TestUnknownFlagTypeTolerance:
    """A flag type this SDK version does not know must not fail the fetch (#2395).

    ``FlagType(raw)`` raised ``ValueError`` on an unrecognised name, and because the
    throw surfaced mid-payload it failed the ENTIRE flags parse — so the day a new flag
    type ships server-side, every pinned SDK stops serving EVERY flag, not just the one
    carrying the new type.

    Tolerating it is unconditionally safe here in a way it is not for ``ServeType`` or
    ``ConditionLogic``: nothing evaluates ``FlagConfiguration.type``. It is parsed,
    stored, re-exported — and never read. ``go``, ``ruby`` and ``php`` do not model the
    field at all.
    """

    @staticmethod
    def _flag(key: str, flag_type: object) -> dict:
        return {
            "key": key,
            "version": 1,
            "type": flag_type,
            "enabled": True,
            "variations": [
                {"key": "on", "value": True},
                {"key": "off", "value": False},
            ],
            "rules": [],
            "fallthrough": {"type": "Fixed", "variation": "off"},
            "offVariation": "off",
        }

    def _parse(self, payload: dict) -> list:
        client = HttpClient(sdk_key="test-key", config=Config(base_url="https://api.example.com"))
        flags, _segments = client.parse_flags_response(payload)
        return flags

    def test_unknown_flag_type_is_retained_as_raw_string(self) -> None:
        flags = self._parse({"flags": [self._flag("f", "Timestamp")], "segments": []})

        assert flags[0].type == "Timestamp"

    def test_known_flag_types_still_parse_to_members(self) -> None:
        flags = self._parse({"flags": [self._flag("f", "Boolean")], "segments": []})

        assert flags[0].type is FlagType.BOOLEAN

    def test_lowercase_wire_spelling_still_parses(self) -> None:
        """The wire sends PascalCase, but the parse has always lowercased first."""
        flags = self._parse({"flags": [self._flag("f", "json")], "segments": []})

        assert flags[0].type is FlagType.JSON

    def test_one_unknown_flag_type_does_not_fail_the_whole_response(self) -> None:
        """The point of the issue: one new-typed flag must not take its neighbours down."""
        flags = self._parse(
            {
                "flags": [
                    self._flag("future", "Timestamp"),
                    self._flag("healthy", "Boolean"),
                ],
                "segments": [],
            }
        )

        assert len(flags) == 2
        healthy = next(f for f in flags if f.key == "healthy")
        assert healthy.type is FlagType.BOOLEAN

    def test_non_string_flag_type_is_still_rejected(self) -> None:
        """The OTHER axis. An integer is the #2279 shape — server and SDK disagreeing
        about the wire format — and stays a wholesale rejection (#2315). Coercing it
        would make ``0`` indistinguishable from a genuine unknown type name."""
        with pytest.raises(TypeError):
            self._parse({"flags": [self._flag("f", 0)], "segments": []})
