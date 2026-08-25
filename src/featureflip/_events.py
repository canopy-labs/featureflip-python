"""Event processor for batching and flushing analytics events."""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol

import httpx
import structlog

logger = structlog.get_logger()

#: Upper bound on buffered events.
#:
#: Only reachable once a failed send starts returning batches to the queue faster than
#: they drain -- that is, a sustained outage of the events endpoint. Past the bound the
#: OLDEST events are shed, which caps memory and keeps the freshest analytics. It also
#: means a long outage sheds the re-queued (stale) batches rather than starving the new
#: events behind them, so the SDK degrades to losing the oldest data instead of hoarding
#: data it cannot send.
DEFAULT_MAX_QUEUE_SIZE = 10_000


def _is_retryable_send_failure(error: BaseException) -> bool:
    """Whether the same batch could plausibly succeed if it were sent again.

    For an HTTP answer the status decides: 5xx (the production edge answers this
    endpoint with a 503 at a low constant rate) and 429, where the server is
    explicitly asking the caller to come back later. Anything else -- 401/403 means
    the SDK key was rejected, 400 means the body is malformed -- will fail
    identically next time, and retrying it forever would pin the queue at its bound
    and starve every later event.

    Everything httpx raises for a request that never got an answer (connect, read
    and write errors, timeouts, protocol errors) derives from ``httpx.HTTPError``,
    and none of it says the server rejected the batch, so all of it is retryable.
    A failure that is not an ``HTTPError`` at all -- a serialization ``TypeError``,
    a bug in a custom client -- is deterministic and is dropped rather than retried.
    """
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status >= 500 or status == 429
    return isinstance(error, httpx.HTTPError)


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

    A send that fails for a transient reason keeps its batch for the next flush
    rather than discarding it, and each request carries at most one batch; see
    :meth:`flush`. The queue is bounded so that a long outage cannot grow it
    without limit.

    Thread-safe: multiple threads can safely queue events concurrently.
    """

    def __init__(
        self,
        http_client: HttpClientProtocol,
        flush_interval: float = 30.0,
        flush_batch_size: int = 100,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
    ) -> None:
        """Initialize the event processor.

        Args:
            http_client: HTTP client for sending events to the API.
            flush_interval: Seconds between automatic flushes (default 30).
            flush_batch_size: Number of events that triggers an immediate flush (default 100).
            max_queue_size: Most events to buffer before shedding the oldest
                (default :data:`DEFAULT_MAX_QUEUE_SIZE`).
        """
        self._http = http_client
        self._flush_interval = flush_interval
        # Defence-in-depth against a non-positive batch size: flush() takes
        # flush_batch_size events per pass, so a value below 1 would take nothing
        # and loop forever on a non-empty queue. Config.__post_init__ already
        # rejects such values, but this class is constructed directly too.
        self._flush_batch_size = flush_batch_size if flush_batch_size >= 1 else 1
        # A non-positive bound would shed every event on the way in, silently turning
        # analytics off. Fall back rather than honour it.
        self._max_queue_size = (
            max_queue_size if max_queue_size > 0 else DEFAULT_MAX_QUEUE_SIZE
        )
        self._queue: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Monotonic deadline before which the batch-size trigger must not start
        # another flush; 0.0 means "not backing off". See _requeue_locked.
        self._next_auto_flush_at = 0.0
        # True while a batch-size-triggered flush is in flight. See queue_event.
        self._auto_flush_in_flight = False
        # True from stop() onward: the queue is closed to re-queues because nothing
        # will flush it again.
        self._stopping = False

    def queue_event(self, event: dict[str, Any]) -> None:
        """Add an event to the queue.

        Thread-safe. If the queue reaches the flush_batch_size threshold,
        an immediate flush is triggered -- unless a retryable failure has put that
        trigger in backoff, or an earlier size-triggered flush is still running.

        Args:
            event: Event dictionary to queue. Should contain at minimum a 'type' field.
        """
        should_flush = False
        with self._lock:
            self._queue.append(event)
            dropped = self._trim_to_bound_locked()
            if (
                len(self._queue) >= self._flush_batch_size
                and not self._auto_flush_in_flight
                and time.monotonic() >= self._next_auto_flush_at
            ):
                # Claim the latch under the same lock that read it, so two threads
                # tracking concurrently cannot both start a flush.
                self._auto_flush_in_flight = True
                should_flush = True

        if dropped:
            logger.warning(
                "event_queue_overflow",
                dropped=dropped,
                max_queue_size=self._max_queue_size,
            )

        if should_flush:
            try:
                self.flush()
            finally:
                with self._lock:
                    self._auto_flush_in_flight = False

    def flush(self) -> None:
        """Flush all queued events immediately.

        Blocks until the flush is complete. If the queue is empty, no API call is
        made. Send failures are logged and never raised: a retryable one keeps its
        batch for the next flush, a permanent one drops it.
        """
        # One request per batch rather than one for the whole queue. Re-queuing
        # failed batches is what lets the queue grow to its bound during an outage,
        # and posting 10,000 events in a single body risks the server rejecting it
        # outright -- a 413 is not retryable, so the entire backlog would be dropped
        # by the very path that exists to preserve it (#2456).
        while True:
            with self._lock:
                if not self._queue:
                    return
                # Drained BEFORE the send, so the batch has to be held here to be
                # put back if the send fails. Without that a single transient
                # failure discarded it outright -- and the production edge answers
                # this endpoint with a 503 at a low constant rate, so evaluation
                # analytics were being lost steadily rather than exceptionally.
                events_to_send = self._drain_batch_locked(self._flush_batch_size)

            try:
                logger.debug("flushing_events", count=len(events_to_send))
                self._http.post_events(events_to_send)
                logger.debug("events_flushed_successfully", count=len(events_to_send))
                with self._lock:
                    # The endpoint is answering again; let the size trigger work.
                    self._next_auto_flush_at = 0.0
            except Exception as e:
                if _is_retryable_send_failure(e):
                    with self._lock:
                        dropped = self._requeue_locked(events_to_send)
                    logger.warning(
                        "event_flush_error",
                        error=str(e),
                        count=len(events_to_send),
                        dropped=dropped,
                    )
                    # Stop here. The batch is back at the head of the queue this
                    # loop is draining, so continuing would re-send it immediately
                    # and spin for as long as the endpoint stayed down.
                    return
                logger.warning(
                    "event_flush_dropped",
                    error=str(e),
                    count=len(events_to_send),
                )
                # Dropped, so the queue shrank and the loop still terminates. Carry
                # on to the next batch rather than letting one poison batch block
                # the backlog queued behind it.

    def _drain_batch_locked(self, max_count: int) -> list[dict[str, Any]]:
        """Take up to ``max_count`` of the OLDEST events. Caller holds ``_lock``.

        Oldest-first so the wire order matches the order the events happened in.
        """
        take = min(max_count, len(self._queue))
        batch = self._queue[:take]
        del self._queue[:take]
        return batch

    def _requeue_locked(self, events: list[dict[str, Any]]) -> int:
        """Return a failed batch to the FRONT of the queue. Caller holds ``_lock``.

        The front, so the next flush retries it ahead of newer events and rough
        chronological order survives an outage.

        Deliberately does NOT retry inline. The batch is back in the queue the
        caller just drained, so re-sending it here would spin against a down
        endpoint for as long as it stayed down, on whichever thread called
        :meth:`flush` -- an evaluation thread, in the batch-size case. The periodic
        flush thread is the retry vehicle.

        Returns:
            How many events were shed to stay within the bound.
        """
        # Shutting down: nothing will flush again, so buffering here would only
        # leak. Report the whole batch as dropped instead.
        if self._stopping:
            return len(events)

        self._queue[:0] = events

        # A re-queued batch leaves the queue at or above flush_batch_size, so without
        # this gate the next tracked event would trigger another flush -- turning a
        # failing endpoint into one request per evaluation, which is worse for the
        # server than the dropping this replaced. It only suppresses the size
        # trigger between periodic flushes; the flush loop keeps retrying.
        self._next_auto_flush_at = time.monotonic() + self._flush_interval

        return self._trim_to_bound_locked()

    def _trim_to_bound_locked(self) -> int:
        """Shed oldest-first until the queue fits the bound. Caller holds ``_lock``.

        Returns:
            How many events were dropped.
        """
        overflow = len(self._queue) - self._max_queue_size
        if overflow <= 0:
            return 0
        del self._queue[:overflow]
        return overflow

    def start(self) -> None:
        """Start the background flush thread.

        The background thread will periodically flush events at the configured interval.
        """
        self._stop_event.clear()
        with self._lock:
            # A restart re-opens the queue, and any backoff left over from the outage
            # that preceded the stop is stale.
            self._stopping = False
            self._next_auto_flush_at = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("event_processor_started", flush_interval=self._flush_interval)

    def stop(self) -> None:
        """Stop the background thread, make one final flush attempt, then let go.

        Blocks until the thread has stopped. Exactly one attempt: retrying until the
        queue drained would hang shutdown for as long as the endpoint stayed down.
        Nothing will flush after this, so whatever the attempt could not deliver is
        discarded rather than kept.
        """
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        # Closes the queue to re-queues, so the final flush below cannot put a failed
        # batch back into a queue no one will drain.
        with self._lock:
            self._stopping = True

        self.flush()

        # Anything a concurrent queue_event slipped in during the flush.
        with self._lock:
            discarded = len(self._queue)
            self._queue = []
        if discarded:
            logger.warning("events_discarded_on_stop", count=discarded)

        logger.info("event_processor_stopped")

    def _run(self) -> None:
        """Main loop for the background flush thread."""
        while not self._stop_event.is_set():
            # Wait for either the interval or stop signal
            self._stop_event.wait(self._flush_interval)
            if not self._stop_event.is_set():
                self.flush()
