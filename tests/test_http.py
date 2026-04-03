"""Tests for HTTP client wrapper."""

import pytest
import respx
from httpx import Response

from featureflip._http import HttpClient
from featureflip.config import Config


class TestHttpClient:
    @pytest.fixture
    def config(self) -> Config:
        return Config(base_url="https://api.example.com")

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
