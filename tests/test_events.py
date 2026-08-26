"""Tests for event processor."""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from structlog.testing import capture_logs

from featureflip._events import DEFAULT_MAX_QUEUE_SIZE, EventProcessor


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


class TestEventProcessorFlushResilience:
    """Flush-failure handling: retry the transient, drop the permanent (#2456).

    ``flush()`` drains the queue *before* the POST, so the batch only survives a
    failed send if something puts it back. The production edge answers the events
    endpoint with a 503 at a low but constant rate, so "events are lost on error"
    meant analytics were bleeding away steadily rather than exceptionally.
    """

    @pytest.fixture
    def mock_http(self) -> MagicMock:
        """Create a mock HTTP client."""
        return MagicMock()

    @staticmethod
    def _status_error(status: int) -> httpx.HTTPStatusError:
        """Build the error ``post_events``' ``raise_for_status()`` would raise."""
        request = httpx.Request("POST", "https://eval.example.test/v1/sdk/events")
        response = httpx.Response(status, request=request)
        return httpx.HTTPStatusError(
            f"Server error '{status}'", request=request, response=response
        )

    @staticmethod
    def _keys(mock_http: MagicMock) -> list[str]:
        """Flag keys of the most recently sent batch, in order."""
        return [e["flagKey"] for e in mock_http.post_events.call_args[0][0]]

    def test_503_keeps_the_batch_for_the_next_flush(self, mock_http: MagicMock) -> None:
        """A 503 must not consume the events — the next flush re-sends them."""
        mock_http.post_events.side_effect = self._status_error(503)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.queue_event({"type": "Evaluation", "flagKey": "flag-1"})
        processor.queue_event({"type": "Evaluation", "flagKey": "flag-2"})

        processor.flush()
        assert mock_http.post_events.call_count == 1

        # The endpoint recovers; the same two events must arrive.
        mock_http.post_events.side_effect = None
        processor.flush()
        assert mock_http.post_events.call_count == 2
        assert self._keys(mock_http) == ["flag-1", "flag-2"]

    def test_retryable_failure_does_not_retry_inline(
        self, mock_http: MagicMock
    ) -> None:
        """One flush call makes exactly one send attempt, however it fails.

        Retrying inside ``flush()`` would spin against a down endpoint while
        holding up whichever thread called it.
        """
        mock_http.post_events.side_effect = self._status_error(503)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.queue_event({"type": "Evaluation", "flagKey": "flag-1"})

        processor.flush()

        assert mock_http.post_events.call_count == 1

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
    def test_server_errors_and_throttling_are_retryable(
        self, mock_http: MagicMock, status: int
    ) -> None:
        """5xx is transient; 429 is the server explicitly asking us to come back."""
        mock_http.post_events.side_effect = self._status_error(status)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.queue_event({"type": "Evaluation", "flagKey": "flag-1"})
        processor.flush()

        mock_http.post_events.side_effect = None
        processor.flush()

        assert mock_http.post_events.call_count == 2
        assert self._keys(mock_http) == ["flag-1"]

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectError("name resolution failed"),
            httpx.ReadTimeout("timed out"),
            httpx.ConnectTimeout("timed out"),
            httpx.RemoteProtocolError("connection reset"),
        ],
        ids=["connect", "read-timeout", "connect-timeout", "reset"],
    )
    def test_transport_failures_are_retryable(
        self, mock_http: MagicMock, error: httpx.HTTPError
    ) -> None:
        """A request that never got an answer says nothing about the batch."""
        mock_http.post_events.side_effect = error
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.queue_event({"type": "Evaluation", "flagKey": "flag-1"})
        processor.flush()

        mock_http.post_events.side_effect = None
        processor.flush()

        assert mock_http.post_events.call_count == 2
        assert self._keys(mock_http) == ["flag-1"]

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_client_errors_drop_the_batch(
        self, mock_http: MagicMock, status: int
    ) -> None:
        """401/403 = key rejected, 400 = malformed body: identical next time."""
        mock_http.post_events.side_effect = self._status_error(status)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.queue_event({"type": "Evaluation", "flagKey": "flag-1"})
        processor.flush()
        assert mock_http.post_events.call_count == 1

        # Nothing was kept, so a healthy endpoint has nothing to receive.
        mock_http.post_events.side_effect = None
        processor.flush()
        assert mock_http.post_events.call_count == 1

    def test_requeued_batch_is_retried_ahead_of_newer_events(
        self, mock_http: MagicMock
    ) -> None:
        """The batch goes back at the FRONT, keeping rough chronological order."""
        mock_http.post_events.side_effect = self._status_error(503)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.queue_event({"type": "Evaluation", "flagKey": "older"})
        processor.flush()

        mock_http.post_events.side_effect = None
        processor.queue_event({"type": "Evaluation", "flagKey": "newer"})
        processor.flush()

        assert self._keys(mock_http) == ["older", "newer"]

    def test_overflow_sheds_the_oldest_events(self, mock_http: MagicMock) -> None:
        """Past the bound the queue keeps the freshest analytics, not the stalest."""
        processor = EventProcessor(
            http_client=mock_http,
            flush_interval=60.0,
            flush_batch_size=100,
            max_queue_size=3,
        )
        for i in range(5):
            processor.queue_event({"type": "Evaluation", "flagKey": f"flag-{i}"})

        processor.flush()

        assert self._keys(mock_http) == ["flag-2", "flag-3", "flag-4"]

    def test_overflow_logs_how_many_were_dropped(self, mock_http: MagicMock) -> None:
        """Shedding is a data-loss event and must not be silent."""
        processor = EventProcessor(
            http_client=mock_http,
            flush_interval=60.0,
            flush_batch_size=100,
            max_queue_size=2,
        )
        with capture_logs() as logs:
            for i in range(5):
                processor.queue_event({"type": "Evaluation", "flagKey": f"flag-{i}"})

        overflow = [e for e in logs if e["event"] == "event_queue_overflow"]
        assert overflow, "expected an overflow warning"
        assert all(e["log_level"] == "warning" for e in overflow)
        assert sum(e["dropped"] for e in overflow) == 3

    def test_requeue_sheds_stale_events_rather_than_starving_new_ones(
        self, mock_http: MagicMock
    ) -> None:
        """A long outage must not let re-queued batches pin the queue forever."""
        mock_http.post_events.side_effect = self._status_error(503)
        processor = EventProcessor(
            http_client=mock_http,
            flush_interval=60.0,
            flush_batch_size=100,
            max_queue_size=3,
        )
        for i in range(3):
            processor.queue_event({"type": "Evaluation", "flagKey": f"flag-{i}"})
        processor.flush()  # drains 3, fails, puts all 3 back at the front

        # New events keep arriving while the endpoint is still down.
        for i in range(3, 6):
            processor.queue_event({"type": "Evaluation", "flagKey": f"flag-{i}"})

        mock_http.post_events.side_effect = None
        processor.flush()

        assert self._keys(mock_http) == ["flag-3", "flag-4", "flag-5"]

    def test_retryable_failure_is_still_logged(self, mock_http: MagicMock) -> None:
        """The fix must not hide the underlying edge problem."""
        mock_http.post_events.side_effect = self._status_error(503)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.queue_event({"type": "Evaluation", "flagKey": "flag-1"})

        with capture_logs() as logs:
            processor.flush()

        failures = [e for e in logs if e["event"] == "event_flush_error"]
        assert failures, "expected the failed send to be logged"
        assert failures[0]["log_level"] == "warning"

    def test_non_retryable_failure_logs_that_the_batch_was_dropped(
        self, mock_http: MagicMock
    ) -> None:
        """Dropping is worse than deferring, so it gets its own diagnostic."""
        mock_http.post_events.side_effect = self._status_error(401)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.queue_event({"type": "Evaluation", "flagKey": "flag-1"})

        with capture_logs() as logs:
            processor.flush()

        dropped = [e for e in logs if e["event"] == "event_flush_dropped"]
        assert dropped, "expected a drop warning naming the batch as non-retryable"
        assert dropped[0]["log_level"] == "warning"
        assert dropped[0]["count"] == 1

    def test_stop_terminates_while_the_endpoint_is_failing(
        self, mock_http: MagicMock
    ) -> None:
        """Shutdown makes ONE last attempt and then lets go of the remainder."""
        mock_http.post_events.side_effect = self._status_error(503)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100
        )
        processor.start()
        processor.queue_event({"type": "Evaluation", "flagKey": "flag-1"})

        started_at = time.monotonic()
        processor.stop()
        assert time.monotonic() - started_at < 5.0, "stop() looped or hung"
        assert mock_http.post_events.call_count == 1

        # Nothing is buffered any more, so a recovered endpoint gets nothing.
        mock_http.post_events.side_effect = None
        processor.flush()
        assert mock_http.post_events.call_count == 1

    def test_default_bound_is_ten_thousand(self, mock_http: MagicMock) -> None:
        """The default bound is generous enough to ride out a real outage."""
        assert DEFAULT_MAX_QUEUE_SIZE == 10_000
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=100_000
        )
        for i in range(DEFAULT_MAX_QUEUE_SIZE + 2):
            processor.queue_event({"type": "Evaluation", "flagKey": f"flag-{i}"})

        processor.flush()

        sent = mock_http.post_events.call_args[0][0]
        assert len(sent) == DEFAULT_MAX_QUEUE_SIZE
        assert sent[0]["flagKey"] == "flag-2"

    def test_batch_size_trigger_does_not_storm_a_failing_endpoint(
        self, mock_http: MagicMock
    ) -> None:
        """A re-queued batch must not turn every tracked event into a request.

        The re-queue leaves the queue at or above ``flush_batch_size``, so the size
        trigger is armed permanently while the endpoint is down. One request per
        evaluation is worse for the server than the dropping this replaced.
        """
        mock_http.post_events.side_effect = self._status_error(503)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=1
        )

        for i in range(10):
            processor.queue_event({"type": "Evaluation", "flagKey": f"flag-{i}"})

        assert mock_http.post_events.call_count == 1

    def test_interval_flush_still_retries_while_the_size_trigger_backs_off(
        self, mock_http: MagicMock
    ) -> None:
        """The backoff gate suppresses the size trigger, never the retry vehicle."""
        mock_http.post_events.side_effect = self._status_error(503)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=0.05, flush_batch_size=1
        )
        processor.start()
        try:
            # One tracked event, so every later attempt comes from the interval
            # thread rather than from another size trigger.
            processor.queue_event({"type": "Evaluation", "flagKey": "flag-1"})
            time.sleep(0.35)
            assert mock_http.post_events.call_count >= 3
        finally:
            mock_http.post_events.side_effect = None
            processor.stop()

    def test_successful_send_clears_the_backoff_gate(
        self, mock_http: MagicMock
    ) -> None:
        """Backoff lasts until the endpoint answers, not forever."""
        mock_http.post_events.side_effect = self._status_error(503)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=1
        )
        processor.queue_event({"type": "Evaluation", "flagKey": "flag-1"})
        assert mock_http.post_events.call_count == 1

        # An interval flush lands while the endpoint is healthy again.
        mock_http.post_events.side_effect = None
        processor.flush()
        assert mock_http.post_events.call_count == 2

        # The size trigger works again immediately.
        processor.queue_event({"type": "Evaluation", "flagKey": "flag-2"})
        assert mock_http.post_events.call_count == 3

    def test_size_trigger_never_runs_two_flushes_concurrently(
        self, mock_http: MagicMock
    ) -> None:
        """The backoff gate is only armed once a flush has already FAILED.

        The trigger fires again long before the first HTTP round-trip returns, so
        without an in-flight latch a tight loop of events starts many concurrent
        flushes.
        """
        release = threading.Event()
        in_flight = 0
        peak_in_flight = 0
        counter_lock = threading.Lock()

        def slow_post(_events: list[dict[str, Any]]) -> None:
            nonlocal in_flight, peak_in_flight
            with counter_lock:
                in_flight += 1
                peak_in_flight = max(peak_in_flight, in_flight)
            release.wait(5.0)
            with counter_lock:
                in_flight -= 1

        mock_http.post_events.side_effect = slow_post
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=1
        )

        threads = [
            threading.Thread(
                target=processor.queue_event,
                args=({"type": "Evaluation", "flagKey": f"flag-{i}"},),
            )
            for i in range(8)
        ]
        for t in threads:
            t.start()
        time.sleep(0.2)
        release.set()
        for t in threads:
            t.join(timeout=10.0)

        assert peak_in_flight == 1

    @staticmethod
    def _seed(processor: EventProcessor, count: int) -> None:
        """Put events straight into the queue, bypassing the batch-size trigger.

        The trigger would flush partway through the seeding, and what these tests
        need to isolate is the batching inside ``flush()`` itself rather than the
        trigger that happens to call it.
        """
        processor._queue.extend(
            {"type": "Evaluation", "flagKey": f"flag-{i}"} for i in range(count)
        )

    @staticmethod
    def _batches(mock_http: MagicMock) -> list[list[str]]:
        """Flag keys of every batch sent, in the order the requests were made."""
        return [
            [e["flagKey"] for e in call[0][0]]
            for call in mock_http.post_events.call_args_list
        ]

    def test_flush_sends_one_request_per_batch(self, mock_http: MagicMock) -> None:
        """A backlog is split across requests instead of posted in one body.

        Re-queuing is what lets the queue reach its bound during an outage, and a
        single 10,000-event body invites a 413 — which is not retryable, so the
        whole backlog would be dropped by the path meant to preserve it.
        """
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=2
        )
        self._seed(processor, 5)

        processor.flush()

        assert mock_http.post_events.call_count == 3
        assert self._batches(mock_http) == [
            ["flag-0", "flag-1"],
            ["flag-2", "flag-3"],
            ["flag-4"],
        ]

    def test_tracked_events_reach_the_wire_one_batch_per_request(
        self, mock_http: MagicMock
    ) -> None:
        """Same split through the public path: track five, batch size two."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=2
        )
        for i in range(5):
            processor.queue_event({"type": "Evaluation", "flagKey": f"flag-{i}"})
        processor.flush()

        assert mock_http.post_events.call_count == 3
        assert self._batches(mock_http) == [
            ["flag-0", "flag-1"],
            ["flag-2", "flag-3"],
            ["flag-4"],
        ]

    def test_retryable_failure_stops_the_loop_instead_of_spinning(
        self, mock_http: MagicMock
    ) -> None:
        """The re-queued batch is back at the head of the queue being drained.

        Continuing the loop would re-send it immediately and spin for the whole
        outage, so the flush stops after the first failure and leaves the rest for
        the next one.
        """
        mock_http.post_events.side_effect = self._status_error(503)
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=2
        )
        self._seed(processor, 5)

        processor.flush()
        assert mock_http.post_events.call_count == 1

        # Nothing was lost: the whole backlog is still there, still in order.
        mock_http.post_events.side_effect = None
        processor.flush()
        assert self._batches(mock_http)[1:] == [
            ["flag-0", "flag-1"],
            ["flag-2", "flag-3"],
            ["flag-4"],
        ]

    def test_non_retryable_batch_does_not_block_the_backlog(
        self, mock_http: MagicMock
    ) -> None:
        """A poison batch is dropped and the loop moves on to the rest."""
        rejected = self._status_error(400)
        mock_http.post_events.side_effect = [rejected, None, None]
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=2
        )
        self._seed(processor, 5)

        processor.flush()

        assert mock_http.post_events.call_count == 3
        # The rejected pair is gone; everything behind it was still delivered.
        assert self._batches(mock_http)[1:] == [["flag-2", "flag-3"], ["flag-4"]]

    def test_flush_terminates_with_a_non_positive_batch_size(
        self, mock_http: MagicMock
    ) -> None:
        """A batch size below 1 would take nothing per pass and loop forever."""
        processor = EventProcessor(
            http_client=mock_http, flush_interval=60.0, flush_batch_size=0
        )
        self._seed(processor, 3)

        processor.flush()

        assert mock_http.post_events.call_count == 3


class TestEventProcessorFlushCoalescing:
    """A flush must never run two drain loops at once (#2477)."""

    @pytest.fixture
    def mock_http(self) -> MagicMock:
        return MagicMock()

    def test_concurrent_flush_runs_only_one_drain(self, mock_http: MagicMock) -> None:
        """A second flush waits for the in-flight drain instead of opening its own.

        Two concurrent drains mean two request streams against the endpoint the
        backoff gate exists to protect -- and a success in one clears the gate a
        failure in the other has just armed.
        """
        in_flight = 0
        peak = 0
        gate = threading.Event()
        first_arrived = threading.Event()
        depth_lock = threading.Lock()

        def post_events(_events: list[dict[str, Any]]) -> None:
            nonlocal in_flight, peak
            with depth_lock:
                in_flight += 1
                peak = max(peak, in_flight)
                held = not first_arrived.is_set()
            if held:
                first_arrived.set()
                gate.wait(timeout=5.0)
            with depth_lock:
                in_flight -= 1

        mock_http.post_events.side_effect = post_events

        # Batch size 1 so the queue below needs six round-trips to drain: plenty
        # of room for a second loop to interleave if one is allowed to start.
        processor = EventProcessor(
            http_client=mock_http, flush_interval=3600.0, flush_batch_size=1
        )
        # Seeded directly rather than through queue_event: at this batch size
        # every call would fire the size trigger and start a drain of its own, so
        # the test would be parked in setup before it had started either flush.
        processor._queue.extend(
            {"type": "Evaluation", "flag_key": f"flag-{i}"} for i in range(6)
        )

        first = threading.Thread(target=processor.flush)
        first.start()
        assert first_arrived.wait(timeout=5.0), "the first flush never reached the sender"

        released = threading.Event()
        second_saw_release: list[bool] = []

        def second_flush() -> None:
            processor.flush()
            second_saw_release.append(released.is_set())

        second = threading.Thread(target=second_flush)
        second.start()

        # Room for the second caller to misbehave: uncoalesced it takes a batch
        # off the front and posts it, which the peak counter catches.
        time.sleep(0.1)

        released.set()
        gate.set()
        first.join(timeout=5.0)
        second.join(timeout=5.0)

        assert peak == 1, f"peak concurrent event requests = {peak}, want 1 -- a second drain loop ran"
        assert second_saw_release == [True], (
            "the second flush returned before the in-flight drain finished; "
            "a caller that asked for a flush must wait for it"
        )
        assert processor._queue == []

    def test_stop_drains_even_while_a_flush_is_in_flight(
        self, mock_http: MagicMock
    ) -> None:
        """stop() is the last drain there will ever be, so it must bypass coalescing."""
        gate = threading.Event()
        first_arrived = threading.Event()
        sent: list[int] = []
        sent_lock = threading.Lock()

        def post_events(events: list[dict[str, Any]]) -> None:
            held = not first_arrived.is_set()
            if held:
                first_arrived.set()
                gate.wait(timeout=5.0)
            with sent_lock:
                sent.append(len(events))

        mock_http.post_events.side_effect = post_events

        processor = EventProcessor(
            http_client=mock_http, flush_interval=3600.0, flush_batch_size=1
        )
        processor._queue.extend(
            {"type": "Evaluation", "flag_key": f"flag-{i}"} for i in range(2)
        )

        first = threading.Thread(target=processor.flush)
        first.start()
        assert first_arrived.wait(timeout=5.0)

        # Released as stop() runs, so stop() genuinely overlaps the in-flight
        # drain rather than waiting it out first.
        threading.Timer(0.05, gate.set).start()
        processor.stop()
        first.join(timeout=5.0)

        assert len(sent) == 2, f"sender saw {len(sent)} request(s), want 2 -- stop() lost events to coalescing"
