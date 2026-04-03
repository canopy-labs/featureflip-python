"""Tests for event processor."""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from featureflip._events import EventProcessor


class TestEventProcessor:
    """Test suite for EventProcessor."""

    @pytest.fixture
    def mock_http(self) -> MagicMock:
        """Create a mock HTTP client."""
        return MagicMock()

    def test_queue_event_adds_to_batch(self, mock_http: MagicMock) -> None:
        """Test that events are queued."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        event = {"type": "Evaluation", "flag_key": "test-flag", "user_id": "user-1"}
        processor.queue_event(event)
        # Flush to verify event was queued
        processor.flush()
        mock_http.post_events.assert_called_once()
        sent_events = mock_http.post_events.call_args[0][0]
        assert len(sent_events) == 1
        assert sent_events[0]["flag_key"] == "test-flag"

    def test_flush_sends_events_to_api(self, mock_http: MagicMock) -> None:
        """Test that flush calls post_events with queued events."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        events = [
            {"type": "Evaluation", "flag_key": "flag-1", "user_id": "user-1"},
            {"type": "Evaluation", "flag_key": "flag-2", "user_id": "user-2"},
        ]
        for event in events:
            processor.queue_event(event)
        processor.flush()
        mock_http.post_events.assert_called_once()
        sent_events = mock_http.post_events.call_args[0][0]
        assert len(sent_events) == 2

    def test_flush_clears_queue(self, mock_http: MagicMock) -> None:
        """Test that queue is empty after flush."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.queue_event({"type": "Evaluation", "flag_key": "test-flag"})
        processor.flush()
        # Second flush should not send anything
        processor.flush()
        # post_events should only have been called once
        mock_http.post_events.assert_called_once()

    def test_flush_on_batch_size_threshold(self, mock_http: MagicMock) -> None:
        """Test that auto-flush occurs when reaching flush_batch_size."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=3
        )
        processor.queue_event({"type": "Evaluation", "flag_key": "flag-1"})
        processor.queue_event({"type": "Evaluation", "flag_key": "flag-2"})
        # Should not have flushed yet
        mock_http.post_events.assert_not_called()
        # Third event should trigger flush
        processor.queue_event({"type": "Evaluation", "flag_key": "flag-3"})
        mock_http.post_events.assert_called_once()
        sent_events = mock_http.post_events.call_args[0][0]
        assert len(sent_events) == 3

    def test_flush_interval_triggers_flush(self, mock_http: MagicMock) -> None:
        """Test that background thread flushes periodically."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=0.1, flush_batch_size=100
        )
        processor.start()
        try:
            processor.queue_event({"type": "Evaluation", "flag_key": "test-flag"})
            # Wait for the flush interval to trigger
            time.sleep(0.25)
            # Should have been flushed by the background thread
            mock_http.post_events.assert_called()
            sent_events = mock_http.post_events.call_args[0][0]
            assert len(sent_events) >= 1
        finally:
            processor.stop()

    def test_stop_flushes_remaining_events(self, mock_http: MagicMock) -> None:
        """Test that stopping flushes any remaining events."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.start()
        processor.queue_event({"type": "Evaluation", "flag_key": "test-flag"})
        # Stop should flush remaining events
        processor.stop()
        mock_http.post_events.assert_called_once()
        sent_events = mock_http.post_events.call_args[0][0]
        assert len(sent_events) == 1

    def test_empty_flush_does_nothing(self, mock_http: MagicMock) -> None:
        """Test that flushing empty queue doesn't call API."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.flush()
        mock_http.post_events.assert_not_called()

    def test_queue_event_is_thread_safe(self, mock_http: MagicMock) -> None:
        """Test that multiple threads can queue safely."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=1000
        )
        num_threads = 10
        events_per_thread = 100
        threads: list[threading.Thread] = []

        def queue_events(thread_id: int) -> None:
            for i in range(events_per_thread):
                processor.queue_event(
                    {"type": "Evaluation", "thread": thread_id, "index": i}
                )

        for i in range(num_threads):
            t = threading.Thread(target=queue_events, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        processor.flush()
        mock_http.post_events.assert_called_once()
        sent_events = mock_http.post_events.call_args[0][0]
        assert len(sent_events) == num_threads * events_per_thread

    def test_evaluation_event_structure(self, mock_http: MagicMock) -> None:
        """Test that evaluation events have required fields."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        event = {
            "type": "Evaluation",
            "flag_key": "test-flag",
            "user_id": "user-123",
            "variation_key": "on",
            "timestamp": "2024-01-01T00:00:00+00:00",
        }
        processor.queue_event(event)
        processor.flush()
        sent_events = mock_http.post_events.call_args[0][0]
        assert sent_events[0]["type"] == "Evaluation"
        assert sent_events[0]["flag_key"] == "test-flag"
        assert sent_events[0]["user_id"] == "user-123"
        assert sent_events[0]["variation_key"] == "on"
        assert "timestamp" in sent_events[0]

    def test_identify_event_structure(self, mock_http: MagicMock) -> None:
        """Test that identify events have required fields."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        event = {
            "type": "Identify",
            "user_id": "user-123",
            "attributes": {"plan": "premium", "country": "US"},
            "timestamp": "2024-01-01T00:00:00+00:00",
        }
        processor.queue_event(event)
        processor.flush()
        sent_events = mock_http.post_events.call_args[0][0]
        assert sent_events[0]["type"] == "Identify"
        assert sent_events[0]["user_id"] == "user-123"
        assert sent_events[0]["attributes"]["plan"] == "premium"

    def test_track_event_structure(self, mock_http: MagicMock) -> None:
        """Test that track events have required fields."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        event = {
            "type": "Custom",
            "event_name": "purchase",
            "user_id": "user-123",
            "metadata": {"amount": 99.99, "currency": "USD"},
            "timestamp": "2024-01-01T00:00:00+00:00",
        }
        processor.queue_event(event)
        processor.flush()
        sent_events = mock_http.post_events.call_args[0][0]
        assert sent_events[0]["type"] == "Custom"
        assert sent_events[0]["event_name"] == "purchase"
        assert sent_events[0]["user_id"] == "user-123"
        assert sent_events[0]["metadata"]["amount"] == 99.99

    def test_flush_handles_http_error_gracefully(self, mock_http: MagicMock) -> None:
        """Test that HTTP errors during flush are handled gracefully."""
        mock_http.post_events.side_effect = Exception("Network error")
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.queue_event({"type": "Evaluation", "flag_key": "test-flag"})
        # Should not raise - errors are logged but not propagated
        processor.flush()
        mock_http.post_events.assert_called_once()

    def test_stop_before_start(self, mock_http: MagicMock) -> None:
        """Test that stopping before starting doesn't raise."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        # Should not raise
        processor.stop()

    def test_multiple_start_stop_cycles(self, mock_http: MagicMock) -> None:
        """Test that processor can be started and stopped multiple times."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=0.1, flush_batch_size=100
        )
        processor.start()
        processor.queue_event({"type": "Evaluation", "flag_key": "flag-1"})
        time.sleep(0.15)
        processor.stop()

        initial_call_count = mock_http.post_events.call_count

        processor.start()
        processor.queue_event({"type": "Evaluation", "flag_key": "flag-2"})
        time.sleep(0.15)
        processor.stop()

        assert mock_http.post_events.call_count > initial_call_count

    def test_background_thread_is_daemon(self, mock_http: MagicMock) -> None:
        """Test that the background thread is a daemon thread."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.start()
        try:
            assert processor._thread is not None
            assert processor._thread.daemon is True
        finally:
            processor.stop()

    def test_flush_continues_after_error(self, mock_http: MagicMock) -> None:
        """Test that flush continues working after an error."""
        call_count = 0

        def post_events_with_error(_events: list[dict[str, Any]]) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("First error")
            # Subsequent calls succeed

        mock_http.post_events.side_effect = post_events_with_error
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )

        # First flush fails
        processor.queue_event({"type": "Evaluation", "flag_key": "flag-1"})
        processor.flush()  # Should not raise

        # Second flush should work
        processor.queue_event({"type": "Evaluation", "flag_key": "flag-2"})
        processor.flush()

        assert mock_http.post_events.call_count == 2
