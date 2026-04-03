"""Event processor for batching and flushing analytics events."""

from __future__ import annotations

import threading
from typing import Any, Protocol

import structlog

logger = structlog.get_logger()


class HttpClientProtocol(Protocol):
    """Protocol for HTTP client to allow loose coupling."""

    def post_events(self, events: list[dict[str, Any]]) -> None:
        """Send events to the API."""
        ...


class EventProcessor:
    """Batches and flushes analytics events.

    This processor collects events in an internal queue and sends them to the API
    either when the batch reaches a threshold size or after a time interval.
    Events are also flushed when the processor is stopped.

    Thread-safe: multiple threads can safely queue events concurrently.
    """

    def __init__(
        self,
        http_client: HttpClientProtocol,
        flush_interval: float = 30.0,
        flush_batch_size: int = 100,
    ) -> None:
        """Initialize the event processor.

        Args:
            http_client: HTTP client for sending events to the API.
            flush_interval: Seconds between automatic flushes (default 30).
            flush_batch_size: Number of events that triggers an immediate flush (default 100).
        """
        self._http = http_client
        self._flush_interval = flush_interval
        self._flush_batch_size = flush_batch_size
        self._queue: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def queue_event(self, event: dict[str, Any]) -> None:
        """Add an event to the queue.

        Thread-safe. If the queue reaches the flush_batch_size threshold,
        an immediate flush is triggered.

        Args:
            event: Event dictionary to queue. Should contain at minimum a 'type' field.
        """
        should_flush = False
        with self._lock:
            self._queue.append(event)
            if len(self._queue) >= self._flush_batch_size:
                should_flush = True

        if should_flush:
            self.flush()

    def flush(self) -> None:
        """Flush all queued events immediately.

        Blocks until the flush is complete. If the queue is empty, no API call is made.
        HTTP errors are logged but not raised.
        """
        events_to_send: list[dict[str, Any]] = []
        with self._lock:
            if not self._queue:
                return
            events_to_send = self._queue.copy()
            self._queue.clear()

        if not events_to_send:
            return

        try:
            logger.debug("flushing_events", count=len(events_to_send))
            self._http.post_events(events_to_send)
            logger.debug("events_flushed_successfully", count=len(events_to_send))
        except Exception as e:
            logger.warning("event_flush_error", error=str(e), count=len(events_to_send))
            # Events are lost on error - this is intentional to prevent memory growth
            # In a production system, you might want to implement retry logic

    def start(self) -> None:
        """Start the background flush thread.

        The background thread will periodically flush events at the configured interval.
        """
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("event_processor_started", flush_interval=self._flush_interval)

    def stop(self) -> None:
        """Stop the background thread and flush remaining events.

        Blocks until the thread has stopped and all remaining events are flushed.
        """
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        # Flush any remaining events
        self.flush()
        logger.info("event_processor_stopped")

    def _run(self) -> None:
        """Main loop for the background flush thread."""
        while not self._stop_event.is_set():
            # Wait for either the interval or stop signal
            self._stop_event.wait(self._flush_interval)
            if not self._stop_event.is_set():
                self.flush()
