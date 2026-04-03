"""Integration tests for Featureflip Python SDK.

These tests verify the client works correctly end-to-end using respx to mock HTTP responses.
They test the full client lifecycle including initialization, flag evaluation, event tracking,
and update mechanisms (polling).
"""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from featureflip import Config, FeatureflipClient
from featureflip.exceptions import InitializationError

# Mock flag response - boolean flag
MOCK_BOOLEAN_FLAG = {
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

# Mock flag response - string flag
MOCK_STRING_FLAG = {
    "key": "string-flag",
    "version": 1,
    "type": "string",
    "enabled": True,
    "variations": [
        {"key": "variant-a", "value": "value-a"},
        {"key": "variant-b", "value": "value-b"},
    ],
    "rules": [],
    "fallthrough": {"type": "fixed", "variation": "variant-a"},
    "offVariation": "variant-b",
}

# Mock flag response - disabled flag
MOCK_DISABLED_FLAG = {
    "key": "disabled-flag",
    "version": 1,
    "type": "boolean",
    "enabled": False,
    "variations": [
        {"key": "on", "value": True},
        {"key": "off", "value": False},
    ],
    "rules": [],
    "fallthrough": {"type": "fixed", "variation": "on"},
    "offVariation": "off",
}

# Mock flags response with all flags
MOCK_FLAGS_RESPONSE = {
    "flags": [MOCK_BOOLEAN_FLAG, MOCK_STRING_FLAG, MOCK_DISABLED_FLAG]
}

# Updated flag for polling tests
MOCK_UPDATED_BOOLEAN_FLAG = {
    **MOCK_BOOLEAN_FLAG,
    "version": 2,
    "fallthrough": {"type": "fixed", "variation": "off"},
}

MOCK_UPDATED_FLAGS_RESPONSE = {
    "flags": [MOCK_UPDATED_BOOLEAN_FLAG, MOCK_STRING_FLAG, MOCK_DISABLED_FLAG]
}


@pytest.fixture
def test_config() -> Config:
    """Create a test configuration with short timeouts and polling."""
    return Config(
        base_url="https://eval.featureflip.io",
        streaming=False,  # Use polling for simpler tests
        send_events=True,
        poll_interval=0.5,  # Short interval for testing
        flush_interval=60.0,  # Long interval - we'll flush manually
        flush_batch_size=100,
        init_timeout=5.0,
        connect_timeout=2.0,
        read_timeout=5.0,
    )


@pytest.fixture
def no_events_config() -> Config:
    """Create a test configuration with events disabled."""
    return Config(
        base_url="https://eval.featureflip.io",
        streaming=False,
        send_events=False,
        poll_interval=60.0,  # Long interval for tests that don't need polling
        init_timeout=5.0,
    )


class TestClientInitialization:
    """Tests for client initialization flow."""

    @respx.mock
    def test_successful_initialization(self, no_events_config: Config) -> None:
        """Client successfully initializes and loads flags."""
        # Setup mock
        flags_route = respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )

        # Create client
        client = FeatureflipClient(sdk_key="test-sdk-key", config=no_events_config)

        # Verify initialization
        assert client.is_initialized is True
        assert flags_route.called
        # Note: call_count may be > 1 if polling starts immediately after init
        assert flags_route.call_count >= 1

        # Clean up
        client.close()

    @respx.mock
    def test_initialization_with_empty_flags(self, no_events_config: Config) -> None:
        """Client initializes successfully with no flags."""
        # Setup mock with empty flags
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json={"flags": []})
        )

        # Create client
        client = FeatureflipClient(sdk_key="test-sdk-key", config=no_events_config)

        # Verify initialization
        assert client.is_initialized is True

        # Unknown flag should return default
        result = client.variation("any-flag", {"user_id": "123"}, default="fallback")
        assert result == "fallback"

        client.close()

    @respx.mock
    def test_initialization_timeout(self) -> None:
        """Client raises InitializationError on timeout."""
        # Setup mock that takes too long
        def slow_response(_: httpx.Request) -> httpx.Response:
            time.sleep(2.0)
            return httpx.Response(200, json=MOCK_FLAGS_RESPONSE)

        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            side_effect=slow_response
        )

        # Create config with very short timeout
        config = Config(
            base_url="https://eval.featureflip.io",
            streaming=False,
            send_events=False,
            init_timeout=0.1,
        )

        # Should raise InitializationError
        with pytest.raises(InitializationError, match="timeout"):
            FeatureflipClient(sdk_key="test-sdk-key", config=config)

    @respx.mock
    def test_initialization_http_500_error(self) -> None:
        """Client raises InitializationError on HTTP 500."""
        # Setup mock returning 500
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(500, json={"error": "Internal Server Error"})
        )

        config = Config(
            base_url="https://eval.featureflip.io",
            streaming=False,
            send_events=False,
        )

        with pytest.raises(InitializationError):
            FeatureflipClient(sdk_key="test-sdk-key", config=config)

    @respx.mock
    def test_initialization_http_401_unauthorized(self) -> None:
        """Client raises InitializationError on HTTP 401."""
        # Setup mock returning 401
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(401, json={"error": "Unauthorized"})
        )

        config = Config(
            base_url="https://eval.featureflip.io",
            streaming=False,
            send_events=False,
        )

        with pytest.raises(InitializationError):
            FeatureflipClient(sdk_key="invalid-key", config=config)

    @respx.mock
    def test_initialization_network_error(self) -> None:
        """Client raises InitializationError on network failure."""
        # Setup mock to raise connection error
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        config = Config(
            base_url="https://eval.featureflip.io",
            streaming=False,
            send_events=False,
        )

        with pytest.raises(InitializationError, match="Connection refused"):
            FeatureflipClient(sdk_key="test-sdk-key", config=config)


class TestFlagEvaluation:
    """Tests for flag evaluation after initialization."""

    @respx.mock
    def test_boolean_flag_evaluation(self, no_events_config: Config) -> None:
        """Client evaluates boolean flag correctly."""
        # Setup mock
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )

        with FeatureflipClient(
            sdk_key="test-sdk-key", config=no_events_config
        ) as client:
            result = client.variation("test-flag", {"user_id": "123"}, default=False)
            assert result is True

    @respx.mock
    def test_string_flag_evaluation(self, no_events_config: Config) -> None:
        """Client evaluates string flag correctly."""
        # Setup mock
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )

        with FeatureflipClient(
            sdk_key="test-sdk-key", config=no_events_config
        ) as client:
            result = client.variation(
                "string-flag", {"user_id": "123"}, default="default"
            )
            assert result == "value-a"

    @respx.mock
    def test_disabled_flag_returns_off_variation(
        self, no_events_config: Config
    ) -> None:
        """Client returns off variation for disabled flag."""
        # Setup mock
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )

        with FeatureflipClient(
            sdk_key="test-sdk-key", config=no_events_config
        ) as client:
            result = client.variation(
                "disabled-flag", {"user_id": "123"}, default=True
            )
            assert result is False

    @respx.mock
    def test_unknown_flag_returns_default(self, no_events_config: Config) -> None:
        """Client returns default value for unknown flag."""
        # Setup mock
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )

        with FeatureflipClient(
            sdk_key="test-sdk-key", config=no_events_config
        ) as client:
            result = client.variation(
                "unknown-flag", {"user_id": "123"}, default="my-default"
            )
            assert result == "my-default"

    @respx.mock
    def test_variation_detail_returns_reason(self, no_events_config: Config) -> None:
        """variation_detail returns evaluation reason."""
        # Setup mock
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )

        with FeatureflipClient(
            sdk_key="test-sdk-key", config=no_events_config
        ) as client:
            detail = client.variation_detail("test-flag", {"user_id": "123"}, default=False)
            assert detail.value is True
            assert detail.reason is not None


class TestEventTracking:
    """Tests for event tracking and flushing."""

    @respx.mock
    def test_events_are_sent_on_flush(self, test_config: Config) -> None:
        """Events are batched and sent to API on flush."""
        # Setup mocks
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )
        events_route = respx.post("https://eval.featureflip.io/v1/sdk/events").mock(
            return_value=httpx.Response(202)
        )

        with FeatureflipClient(sdk_key="test-sdk-key", config=test_config) as client:
            # Evaluate a flag (which queues an event)
            client.variation("test-flag", {"user_id": "123"}, default=False)

            # Flush events
            client.flush()

            # Verify events were sent
            assert events_route.called
            assert events_route.call_count >= 1

            # Check the event payload
            request = events_route.calls[0].request
            payload = request.read()
            assert b"Evaluation" in payload
            assert b"test-flag" in payload

    @respx.mock
    def test_track_sends_custom_event(self, test_config: Config) -> None:
        """Custom events are queued and sent."""
        # Setup mocks
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )
        events_route = respx.post("https://eval.featureflip.io/v1/sdk/events").mock(
            return_value=httpx.Response(202)
        )

        with FeatureflipClient(sdk_key="test-sdk-key", config=test_config) as client:
            # Track a custom event
            client.track(
                "purchase",
                {"user_id": "123"},
                metadata={"amount": 99.99, "currency": "USD"},
            )

            # Flush events
            client.flush()

            # Verify events were sent
            assert events_route.called

            # Check the event payload
            request = events_route.calls[0].request
            payload = request.read()
            assert b"Custom" in payload
            assert b"purchase" in payload

    @respx.mock
    def test_identify_sends_user_attributes(self, test_config: Config) -> None:
        """Identify events are sent with user attributes."""
        # Setup mocks
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )
        events_route = respx.post("https://eval.featureflip.io/v1/sdk/events").mock(
            return_value=httpx.Response(202)
        )

        with FeatureflipClient(sdk_key="test-sdk-key", config=test_config) as client:
            # Identify a user
            client.identify(
                {
                    "user_id": "user-123",
                    "email": "test@example.com",
                    "plan": "premium",
                }
            )

            # Flush events
            client.flush()

            # Verify events were sent
            assert events_route.called

            # Check the event payload
            request = events_route.calls[0].request
            payload = request.read()
            assert b"Identify" in payload
            assert b"user-123" in payload

    @respx.mock
    def test_events_not_sent_when_disabled(self, no_events_config: Config) -> None:
        """Events are not sent when send_events=False."""
        # Setup mocks
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )
        events_route = respx.post("https://eval.featureflip.io/v1/sdk/events").mock(
            return_value=httpx.Response(202)
        )

        with FeatureflipClient(
            sdk_key="test-sdk-key", config=no_events_config
        ) as client:
            # Evaluate flags and track events
            client.variation("test-flag", {"user_id": "123"}, default=False)
            client.track("event", {"user_id": "123"})
            client.identify({"user_id": "123"})
            client.flush()

            # Verify no events were sent
            assert not events_route.called

    @respx.mock
    def test_event_flush_handles_api_error(self, test_config: Config) -> None:
        """Event flush handles API errors gracefully."""
        # Setup mocks
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )
        # Events endpoint returns 500
        respx.post("https://eval.featureflip.io/v1/sdk/events").mock(
            return_value=httpx.Response(500, json={"error": "Internal Server Error"})
        )

        # Should not raise - errors are logged but not propagated
        with FeatureflipClient(sdk_key="test-sdk-key", config=test_config) as client:
            client.track("event", {"user_id": "123"})
            client.flush()  # Should not raise


class TestPollingUpdates:
    """Tests for polling-based flag updates."""

    @respx.mock
    def test_polling_updates_flags(self) -> None:
        """Polling fetches new flag values."""
        # The polling handler starts immediately after init and makes its first poll
        # right away. So by the time we evaluate, the polling may have already
        # updated the flags. We need to track when the update happens and verify
        # that the client correctly reflects the updated value.

        # Track calls to return original flags first, then updated flags
        call_count = 0

        def get_flags_response(_: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # First 2 calls return original flags (init + first poll)
            if call_count <= 2:
                return httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
            # Subsequent calls return updated flags
            return httpx.Response(200, json=MOCK_UPDATED_FLAGS_RESPONSE)

        # Setup mock that returns different values on subsequent calls
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            side_effect=get_flags_response
        )

        # Create config with very short poll interval
        config = Config(
            base_url="https://eval.featureflip.io",
            streaming=False,
            send_events=False,
            poll_interval=0.2,  # 200ms poll interval
            init_timeout=5.0,
        )

        with FeatureflipClient(sdk_key="test-sdk-key", config=config) as client:
            # Initial value should be True (on) - from original flags
            initial_value = client.variation(
                "test-flag", {"user_id": "123"}, default=None
            )
            assert initial_value is True

            # Wait for polling to update (poll_interval * 2 + buffer)
            time.sleep(0.6)

            # After poll, value should be False (off) - updated flag
            updated_value = client.variation(
                "test-flag", {"user_id": "123"}, default=None
            )
            assert updated_value is False

    @respx.mock
    def test_polling_handles_api_error(self) -> None:
        """Polling handles API errors gracefully and retries."""
        call_count = 0

        def get_flags_response(_: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
            elif call_count == 2:
                # Second call fails
                return httpx.Response(500)
            else:
                # Third call succeeds with updated flags
                return httpx.Response(200, json=MOCK_UPDATED_FLAGS_RESPONSE)

        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            side_effect=get_flags_response
        )

        config = Config(
            base_url="https://eval.featureflip.io",
            streaming=False,
            send_events=False,
            poll_interval=0.1,
            init_timeout=5.0,
        )

        with FeatureflipClient(sdk_key="test-sdk-key", config=config) as client:
            # Initial evaluation should work
            result = client.variation("test-flag", {"user_id": "123"}, default=None)
            assert result is True

            # Wait for error poll and recovery poll
            time.sleep(0.4)

            # Should have recovered and gotten updated value
            result = client.variation("test-flag", {"user_id": "123"}, default=None)
            # Value should be from the recovered poll (False from updated flags)
            assert result is False


class TestContextManager:
    """Tests for context manager behavior."""

    @respx.mock
    def test_context_manager_closes_cleanly(self, no_events_config: Config) -> None:
        """Context manager calls close() on exit."""
        # Setup mock
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )

        # Use as context manager
        with FeatureflipClient(
            sdk_key="test-sdk-key", config=no_events_config
        ) as client:
            assert client.is_initialized is True
            result = client.variation("test-flag", {"user_id": "123"}, default=False)
            assert result is True

        # After exiting context manager, client should be closed
        # (internal state cleaned up)
        assert client._http_client is None
        assert client._polling_handler is None

    @respx.mock
    def test_context_manager_closes_on_exception(
        self, no_events_config: Config
    ) -> None:
        """Context manager closes client even when exception occurs."""
        # Setup mock
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )

        with (
            pytest.raises(RuntimeError, match="Test exception"),
            FeatureflipClient(
                sdk_key="test-sdk-key", config=no_events_config
            ) as client,
        ):
            raise RuntimeError("Test exception")

        # Client should still be closed (context manager __exit__ was called)
        assert client._http_client is None


class TestErrorRecovery:
    """Tests for error recovery scenarios."""

    @respx.mock
    def test_evaluation_never_raises(self, no_events_config: Config) -> None:
        """Flag evaluation never raises exceptions."""
        # Setup mock
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )

        with FeatureflipClient(
            sdk_key="test-sdk-key", config=no_events_config
        ) as client:
            # Various edge cases that should not raise
            result1 = client.variation("unknown", {}, default="safe")
            assert result1 == "safe"

            result2 = client.variation("test-flag", None, default="safe")  # type: ignore[arg-type]
            assert result2 is True  # Should still evaluate correctly

            result3 = client.variation("test-flag", {"user_id": None}, default="safe")
            assert result3 is True

    @respx.mock
    def test_flush_on_close(self) -> None:
        """Events are flushed when client is closed."""
        # Setup mocks
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )
        events_route = respx.post("https://eval.featureflip.io/v1/sdk/events").mock(
            return_value=httpx.Response(202)
        )

        config = Config(
            base_url="https://eval.featureflip.io",
            streaming=False,
            send_events=True,
            flush_interval=60.0,  # Long interval - should flush on close
            poll_interval=60.0,
        )

        client = FeatureflipClient(sdk_key="test-sdk-key", config=config)
        client.track("event", {"user_id": "123"})

        # Close should flush events
        client.close()

        # Give a moment for the async flush
        time.sleep(0.1)

        assert events_route.called


class TestSdkKeyHandling:
    """Tests for SDK key handling."""

    @respx.mock
    def test_sdk_key_sent_in_authorization_header(
        self, no_events_config: Config
    ) -> None:
        """SDK key is sent in Authorization header."""
        # Setup mock that captures the request
        flags_route = respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )

        client = FeatureflipClient(
            sdk_key="my-secret-sdk-key", config=no_events_config
        )

        # Check the Authorization header
        assert flags_route.called
        request = flags_route.calls[0].request
        assert request.headers.get("Authorization") == "my-secret-sdk-key"

        client.close()


class TestMultipleFlagEvaluations:
    """Tests for evaluating multiple flags."""

    @respx.mock
    def test_evaluate_multiple_flags(self, no_events_config: Config) -> None:
        """Client can evaluate multiple different flags."""
        # Setup mock
        respx.get("https://eval.featureflip.io/v1/sdk/flags").mock(
            return_value=httpx.Response(200, json=MOCK_FLAGS_RESPONSE)
        )

        with FeatureflipClient(
            sdk_key="test-sdk-key", config=no_events_config
        ) as client:
            bool_result = client.variation(
                "test-flag", {"user_id": "123"}, default=False
            )
            string_result = client.variation(
                "string-flag", {"user_id": "123"}, default="default"
            )
            disabled_result = client.variation(
                "disabled-flag", {"user_id": "123"}, default=True
            )
            unknown_result = client.variation(
                "unknown-flag", {"user_id": "123"}, default="fallback"
            )

            assert bool_result is True
            assert string_result == "value-a"
            assert disabled_result is False
            assert unknown_result == "fallback"
