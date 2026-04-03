"""Tests for FeatureflipClient."""

from __future__ import annotations

import os
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from featureflip.client import FeatureflipClient
from featureflip.config import Config
from featureflip.detail import EvaluationDetail, EvaluationReason
from featureflip.exceptions import InitializationError
from featureflip.models import (
    FlagConfiguration,
    FlagType,
    ServeConfig,
    ServeType,
    Variation,
)


def create_test_flag(
    key: str = "test-flag",
    value: Any = True,
    enabled: bool = True,
) -> FlagConfiguration:
    """Create a test flag configuration."""
    return FlagConfiguration(
        key=key,
        version=1,
        type=FlagType.BOOLEAN,
        enabled=enabled,
        variations=[
            Variation(key="on", value=value),
            Variation(key="off", value=not value if isinstance(value, bool) else None),
        ],
        rules=[],
        fallthrough=ServeConfig(type=ServeType.FIXED, variation="on"),
        off_variation="off",
    )


class TestClientInitialization:
    """Tests for client initialization."""

    def test_init_with_sdk_key(self) -> None:
        """Constructor accepts SDK key directly."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            assert client is not None
            client.close()

    def test_init_from_env_var(self) -> None:
        """Constructor falls back to FEATUREFLIP_SDK_KEY env var."""
        with (
            patch.dict(os.environ, {"FEATUREFLIP_SDK_KEY": "env-key"}),
            patch("featureflip.client.HttpClient") as mock_http,
        ):
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                config=Config(streaming=False, send_events=False),
            )

            assert client is not None
            mock_http.assert_called_once()
            # Check that env-key was used
            call_args = mock_http.call_args
            assert call_args[0][0] == "env-key"
            client.close()

    def test_init_raises_without_sdk_key(self) -> None:
        """Constructor raises ValueError without SDK key."""
        with patch.dict(os.environ, {}, clear=True):
            # Ensure FEATUREFLIP_SDK_KEY is not set
            os.environ.pop("FEATUREFLIP_SDK_KEY", None)

            with pytest.raises(ValueError, match="SDK key"):
                FeatureflipClient(config=Config(streaming=False, send_events=False))

    def test_init_raises_on_timeout(self) -> None:
        """Constructor raises InitializationError on timeout."""
        with patch("featureflip.client.HttpClient") as mock_http:
            # Simulate slow response
            def slow_get_flags() -> tuple[list[FlagConfiguration], list]:
                time.sleep(5)
                return ([], [])

            mock_http.return_value.get_flags.side_effect = slow_get_flags

            with pytest.raises(InitializationError, match="timeout"):
                FeatureflipClient(
                    sdk_key="test-key",
                    config=Config(
                        init_timeout=0.1,
                        streaming=False,
                        send_events=False,
                    ),
                )

    def test_init_raises_on_http_error(self) -> None:
        """Constructor raises InitializationError on HTTP error."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.side_effect = Exception("Connection failed")

            with pytest.raises(InitializationError, match="Connection failed"):
                FeatureflipClient(
                    sdk_key="test-key",
                    config=Config(streaming=False, send_events=False),
                )

    def test_is_initialized_property(self) -> None:
        """is_initialized returns True after successful init."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([create_test_flag()], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            assert client.is_initialized is True
            client.close()


class TestVariation:
    """Tests for variation() method."""

    def test_returns_evaluated_value(self) -> None:
        """variation() returns the evaluated flag value."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([
                create_test_flag(key="my-flag", value=True),
            ], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            result = client.variation("my-flag", {"user_id": "123"}, default=False)

            assert result is True
            client.close()

    def test_returns_default_on_unknown_flag(self) -> None:
        """variation() returns default when flag is not found."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            result = client.variation("unknown-flag", {"user_id": "123"}, default="fallback")

            assert result == "fallback"
            client.close()

    def test_never_raises(self) -> None:
        """variation() never raises, returns default on any error."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([create_test_flag()], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            # Mock evaluator to raise an error
            from unittest.mock import MagicMock
            client._evaluator = MagicMock()
            client._evaluator.evaluate.side_effect = RuntimeError("Evaluation crashed!")

            # Should return default without raising
            result = client.variation("test-flag", {"user_id": "123"}, default="safe")

            assert result == "safe"
            client.close()

    def test_queues_event_when_track_true(self) -> None:
        """variation() queues evaluation event when track=True."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([create_test_flag()], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            # Mock the event processor
            client._event_processor = MagicMock()

            client.variation("test-flag", {"user_id": "123"}, default=False, track=True)

            # Check event was queued
            client._event_processor.queue_event.assert_called_once()
            event = client._event_processor.queue_event.call_args[0][0]
            assert event["type"] == "Evaluation"
            assert event["flagKey"] == "test-flag"
            client.close()

    def test_skips_event_when_track_false(self) -> None:
        """variation() skips event when track=False."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([create_test_flag()], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            # Mock the event processor
            client._event_processor = MagicMock()

            client.variation("test-flag", {"user_id": "123"}, default=False, track=False)

            # Check no event was queued
            client._event_processor.queue_event.assert_not_called()
            client.close()


class TestVariationDetail:
    """Tests for variation_detail() method."""

    def test_returns_detail_object(self) -> None:
        """variation_detail() returns EvaluationDetail object."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([create_test_flag()], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            result = client.variation_detail("test-flag", {"user_id": "123"}, default=False)

            assert isinstance(result, EvaluationDetail)
            assert result.value is True
            assert result.reason == EvaluationReason.FALLTHROUGH
            client.close()

    def test_returns_flag_not_found_for_unknown(self) -> None:
        """variation_detail() returns FLAG_NOT_FOUND for unknown flags."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            result = client.variation_detail("unknown", {"user_id": "123"}, default="fallback")

            assert result.value == "fallback"
            assert result.reason == EvaluationReason.FLAG_NOT_FOUND
            client.close()


class TestTrack:
    """Tests for track() method."""

    def test_queues_custom_event(self) -> None:
        """track() queues a custom event."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            # Mock the event processor
            client._event_processor = MagicMock()

            client.track("purchase", {"user_id": "123"}, metadata={"amount": 99.99})

            # Check event was queued
            client._event_processor.queue_event.assert_called_once()
            event = client._event_processor.queue_event.call_args[0][0]
            assert event["type"] == "Custom"
            assert event["flagKey"] == "purchase"
            assert event["metadata"]["amount"] == 99.99
            client.close()


class TestIdentify:
    """Tests for identify() method."""

    def test_queues_identify_event(self) -> None:
        """identify() queues an identify event."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            # Mock the event processor
            client._event_processor = MagicMock()

            client.identify({"user_id": "123", "email": "test@example.com"})

            # Check event was queued
            client._event_processor.queue_event.assert_called_once()
            event = client._event_processor.queue_event.call_args[0][0]
            assert event["type"] == "Identify"
            assert event["context"]["user_id"] == "123"
            assert event["context"]["email"] == "test@example.com"
            client.close()

    def test_identify_event_includes_flagkey(self) -> None:
        """identify() includes flagKey '$identify' per SDK event contract."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            client._event_processor = MagicMock()

            client.identify({"user_id": "123"})

            event = client._event_processor.queue_event.call_args[0][0]
            assert event["flagKey"] == "$identify"
            client.close()


class TestFlush:
    """Tests for flush() method."""

    def test_flushes_events(self) -> None:
        """flush() flushes pending events."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            # Mock the event processor
            client._event_processor = MagicMock()

            client.flush()

            client._event_processor.flush.assert_called_once()
            client.close()


class TestClose:
    """Tests for close() method."""

    def test_shuts_down_cleanly(self) -> None:
        """close() shuts down all components."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            client.close()

            # Verify HTTP client was closed
            mock_http.return_value.close.assert_called()


class TestContextManager:
    """Tests for context manager support."""

    def test_context_manager_works(self) -> None:
        """Context manager enters and exits cleanly."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([create_test_flag()], [])

            with FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            ) as client:
                result = client.variation("test-flag", {"user_id": "123"}, default=False)
                assert result is True

            # Verify close was called
            mock_http.return_value.close.assert_called()


class TestTestClient:
    """Tests for test client factory."""

    def test_for_testing_creates_test_client(self) -> None:
        """for_testing() creates a client with fixed values."""
        client = FeatureflipClient.for_testing({
            "feature-a": True,
            "feature-b": "variant-1",
            "feature-c": 42,
        })

        assert client.variation("feature-a", {}, default=False) is True
        assert client.variation("feature-b", {}, default="default") == "variant-1"
        assert client.variation("feature-c", {}, default=0) == 42
        client.close()

    def test_test_client_returns_default_for_unknown(self) -> None:
        """Test client returns default for flags not in the test data."""
        client = FeatureflipClient.for_testing({"known": True})

        result = client.variation("unknown", {}, default="fallback")

        assert result == "fallback"
        client.close()

    def test_test_client_ignores_context(self) -> None:
        """Test client ignores context - always returns fixed value."""
        client = FeatureflipClient.for_testing({"flag": "value"})

        # Different contexts should return the same value
        result1 = client.variation("flag", {"user_id": "1"}, default="x")
        result2 = client.variation("flag", {"user_id": "2"}, default="x")
        result3 = client.variation("flag", {}, default="x")

        assert result1 == result2 == result3 == "value"
        client.close()

    def test_set_test_value_updates_value(self) -> None:
        """set_test_value() updates the test value."""
        client = FeatureflipClient.for_testing({"flag": "initial"})

        assert client.variation("flag", {}, default="x") == "initial"

        client.set_test_value("flag", "updated")

        assert client.variation("flag", {}, default="x") == "updated"
        client.close()

    def test_set_test_value_raises_on_non_test_client(self) -> None:
        """set_test_value() raises on non-test client."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            with pytest.raises(RuntimeError, match="test client"):
                client.set_test_value("flag", "value")

            client.close()

    def test_test_client_is_initialized(self) -> None:
        """Test client is always initialized."""
        client = FeatureflipClient.for_testing({})

        assert client.is_initialized is True
        client.close()

    def test_test_client_no_network_calls(self) -> None:
        """Test client makes no network calls."""
        with patch("featureflip.client.HttpClient") as mock_http:
            client = FeatureflipClient.for_testing({"flag": True})

            # Verify HttpClient was never instantiated
            mock_http.assert_not_called()

            # Operations should work without network
            client.variation("flag", {}, default=False)
            client.track("event", {})
            client.identify({})
            client.flush()
            client.close()


class TestStreaming:
    """Tests for streaming updates."""

    def test_starts_streaming_by_default(self) -> None:
        """Client starts streaming handler when streaming=True."""
        with (
            patch("featureflip.client.HttpClient") as mock_http,
            patch("featureflip.client.StreamingHandler") as mock_stream,
        ):
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=True, send_events=False),
            )

            mock_stream.return_value.start.assert_called_once()
            client.close()
            mock_stream.return_value.stop.assert_called_once()

    def test_starts_polling_when_streaming_disabled(self) -> None:
        """Client starts polling handler when streaming=False."""
        with (
            patch("featureflip.client.HttpClient") as mock_http,
            patch("featureflip.client.PollingHandler") as mock_poll,
        ):
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            mock_poll.return_value.start.assert_called_once()
            client.close()
            mock_poll.return_value.stop.assert_called_once()


class TestEventProcessing:
    """Tests for event processing."""

    def test_starts_event_processor_when_enabled(self) -> None:
        """Client starts event processor when send_events=True."""
        with (
            patch("featureflip.client.HttpClient") as mock_http,
            patch("featureflip.client.EventProcessor") as mock_events,
        ):
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=True),
            )

            mock_events.return_value.start.assert_called_once()
            client.close()
            mock_events.return_value.stop.assert_called_once()

    def test_no_event_processor_when_disabled(self) -> None:
        """Client does not start event processor when send_events=False."""
        with (
            patch("featureflip.client.HttpClient") as mock_http,
            patch("featureflip.client.EventProcessor") as mock_events,
        ):
            mock_http.return_value.get_flags.return_value = ([], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            mock_events.assert_not_called()
            client.close()


class TestPollingUpdate:
    """Tests for polling update behavior."""

    def test_polling_update_replaces_flags_instead_of_merging(self) -> None:
        """Polling updates should replace the entire flag store, not merge.

        When a flag is deleted server-side, polling returns a full snapshot
        without that flag. The client must clear the old store so deleted
        flags don't persist.
        """
        with patch("featureflip.client.HttpClient") as mock_http:
            # Initial flags: flag-a and flag-b
            mock_http.return_value.get_flags.return_value = ([
                create_test_flag(key="flag-a", value=True),
                create_test_flag(key="flag-b", value=True),
            ], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            # Both flags exist
            assert client.variation("flag-a", {"user_id": "1"}, default=False) is True
            assert client.variation("flag-b", {"user_id": "1"}, default=False) is True

            # Simulate polling update: only flag-a remains (flag-b was deleted)
            client._on_polling_update([
                create_test_flag(key="flag-a", value=True),
            ])

            # flag-a should still exist
            assert client.variation("flag-a", {"user_id": "1"}, default=False) is True
            # flag-b should be gone — polling is a full snapshot replacement
            assert client.variation("flag-b", {"user_id": "1"}, default=False) is False

            client.close()

    def test_polling_update_replaces_segments_instead_of_merging(self) -> None:
        """Polling updates should replace the entire segment store, not merge."""
        from featureflip.models import ConditionLogic, Segment

        with patch("featureflip.client.HttpClient") as mock_http:
            seg_a = Segment(key="seg-a", version=1, conditions=[], condition_logic=ConditionLogic.AND)
            seg_b = Segment(key="seg-b", version=1, conditions=[], condition_logic=ConditionLogic.AND)

            mock_http.return_value.get_flags.return_value = ([], [seg_a, seg_b])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            # Both segments exist
            assert client._get_segment("seg-a") is not None
            assert client._get_segment("seg-b") is not None

            # Simulate polling update: only seg-a remains
            client._on_polling_update([], segments=[seg_a])

            assert client._get_segment("seg-a") is not None
            # seg-b should be gone
            assert client._get_segment("seg-b") is None

            client.close()


class TestThreadSafety:
    """Tests for thread-safe flag storage."""

    def test_concurrent_evaluations_are_safe(self) -> None:
        """Multiple threads can safely evaluate flags concurrently."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([
                create_test_flag(key="flag-1", value=True),
                create_test_flag(key="flag-2", value="hello"),
            ], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )

            results: list[Any] = []
            errors: list[Exception] = []

            def evaluate_flags() -> None:
                try:
                    for _ in range(100):
                        r1 = client.variation("flag-1", {"user_id": "123"}, default=False)
                        r2 = client.variation("flag-2", {"user_id": "123"}, default="")
                        results.append((r1, r2))
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=evaluate_flags) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors
            assert len(results) == 1000
            assert all(r == (True, "hello") for r in results)
            client.close()


class TestEvaluationEventKeys:
    """Tests that evaluation events use camelCase keys matching backend contract."""

    def test_evaluation_event_uses_camelcase_flag_key(self) -> None:
        """_queue_evaluation_event must use 'flagKey' not 'flag_key'."""
        with patch("featureflip.client.HttpClient") as mock_http:
            mock_http.return_value.get_flags.return_value = ([create_test_flag()], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )
            client._event_processor = MagicMock()

            client.variation("test-flag", {"user_id": "123"}, default=False, track=True)

            event = client._event_processor.queue_event.call_args[0][0]
            assert "flagKey" in event, f"Event should use 'flagKey' but has keys: {list(event.keys())}"
            assert "flag_key" not in event, "Event should not use snake_case 'flag_key'"
            assert event["flagKey"] == "test-flag"
            client.close()

    def test_evaluation_event_uses_camelcase_rule_id(self) -> None:
        """_queue_evaluation_event must use 'ruleId' not 'rule_id'."""
        with patch("featureflip.client.HttpClient") as mock_http:
            flag = create_test_flag()
            mock_http.return_value.get_flags.return_value = ([flag], [])

            client = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )
            client._event_processor = MagicMock()

            # Call _queue_evaluation_event directly with a rule_id
            client._queue_evaluation_event(
                key="test-flag",
                value=True,
                context={"user_id": "123"},
                reason=EvaluationReason.RULE_MATCH,
                rule_id="rule-abc",
            )

            event = client._event_processor.queue_event.call_args[0][0]
            assert "ruleId" in event, f"Event should use 'ruleId' but has keys: {list(event.keys())}"
            assert "rule_id" not in event, "Event should not use snake_case 'rule_id'"
            assert event["ruleId"] == "rule-abc"
            client.close()
