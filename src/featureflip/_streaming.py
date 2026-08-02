"""SSE streaming handler for real-time flag updates."""

from __future__ import annotations

import json
import random
import threading
from typing import TYPE_CHECKING, Any, Protocol

import httpx
import structlog
from httpx_sse import connect_sse

from featureflip._polling import PollingHandler
from featureflip._version import USER_AGENT

if TYPE_CHECKING:
    from collections.abc import Callable

    from featureflip.config import Config
    from featureflip.models import FlagConfiguration, Segment

logger = structlog.get_logger()


class _StreamingHttpClient(Protocol):
    """HTTP client surface the streaming handler needs.

    Structural (duck-typed) so the concrete ``HttpClient`` satisfies it and it
    can also feed the polling fallback (``get_flags``).
    """

    def get_flags(self) -> tuple[list[FlagConfiguration], list[Segment]]: ...

    def parse_flags_response(
        self, data: dict[str, Any]
    ) -> tuple[list[FlagConfiguration], list[Segment]]: ...


# Reconnect backoff (mirrors go-sdk constants for cross-SDK consistency).
_RECONNECT_BASE_DELAY = 3.0  # seconds
_MAX_RECONNECT_DELAY = 30.0  # cap
_FALLBACK_THRESHOLD = 5  # consecutive stream failures before polling fallback
# Liveness watchdog: a finite SSE read timeout well above the ~30s server ping
# (≈3 missed pings). With read=None a half-open socket (silent LB/NAT idle-drop
# or partition, no FIN/RST) would block the reader forever and never reconnect;
# a finite read timeout surfaces as a ReadTimeout that _connect() catches and
# the reconnect/backoff/fallback loop handles.
_SSE_READ_TIMEOUT_SECONDS = 90.0


class StreamingHandler:
    """Handles SSE streaming for real-time flag updates.

    This handler connects to the Featureflip streaming endpoint and
    receives real-time updates when flags are modified. It runs in a
    background thread and calls the provided callbacks when events occur.
    """

    def __init__(
        self,
        sdk_key: str,
        config: Config,
        http_client: _StreamingHttpClient,
        on_flag_updated: Callable[[str], None],
        on_flag_deleted: Callable[[str], None],
        on_segment_updated: Callable[[], None],
        on_error: Callable[[Exception], None],
        on_update: Callable[[list[FlagConfiguration], list[Segment]], None],
    ) -> None:
        """Initialize the streaming handler.

        Args:
            sdk_key: The SDK key for authentication.
            config: Client configuration options.
            http_client: HTTP client — parses the connect-time snapshot and backs the polling fallback.
            on_flag_updated: Callback invoked with flag key when a flag is created or updated.
            on_flag_deleted: Callback invoked with flag key when a flag is deleted.
            on_segment_updated: Callback invoked when a segment is updated (triggers full refetch).
            on_error: Callback invoked when an error occurs.
            on_update: Callback invoked with a full (flags, segments) snapshot — a full store replace.
        """
        self._sdk_key = sdk_key
        self._config = config
        self._http = http_client
        self._on_flag_updated = on_flag_updated
        self._on_flag_deleted = on_flag_deleted
        self._on_segment_updated = on_segment_updated
        self._on_error = on_error
        self._on_update = on_update
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Reconnect tunables (defaulted from module constants; tests shrink them).
        self._reconnect_base_delay = _RECONNECT_BASE_DELAY
        self._max_reconnect_delay = _MAX_RECONNECT_DELAY
        self._fallback_threshold = _FALLBACK_THRESHOLD
        self._fallback_lock = threading.Lock()
        self._fallback_poller: PollingHandler | None = None

    def start(self) -> None:
        """Start the streaming connection in a background thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("streaming_started")

    def stop(self) -> None:
        """Stop the streaming connection."""
        # Set the stop event BEFORE reaping so a concurrent
        # _start_fallback_polling (which checks the event under the lock) can't
        # leak a poller.
        self._stop_event.set()
        self._stop_fallback_polling()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("streaming_stopped")

    def _run(self) -> None:
        """Reconnect forever with capped exponential backoff.

        Never gives up while the handler is alive: a clean EOF or any error
        both back off before reconnecting (no busy-loop). After
        ``_fallback_threshold`` consecutive failures, also starts a polling
        fallback (see _start_fallback_polling); the stream keeps retrying.
        """
        consecutive_failures = 0
        while not self._stop_event.is_set():
            reached = self._connect()
            if self._stop_event.is_set():
                break
            if reached:
                consecutive_failures = 0
                self._stop_fallback_polling()
            else:
                consecutive_failures += 1
                if consecutive_failures >= self._fallback_threshold:
                    self._start_fallback_polling()
            self._stop_event.wait(self._backoff_delay(consecutive_failures))

    def _connect(self) -> bool:
        """Connect to the SSE stream and process events.

        Returns True if a live stream was established (at least one event
        received — the server always sends a ``sync`` frame first), False if
        the connection could not be established.
        """
        url = self._get_stream_url()
        headers = self._get_headers()
        reached = False
        try:
            with (
                httpx.Client(
                    timeout=httpx.Timeout(
                        connect=self._config.connect_timeout,
                        # Finite read timeout as a liveness watchdog — see
                        # _SSE_READ_TIMEOUT_SECONDS. Healthy streams reset it on
                        # every event/ping (~30s), so it only fires on a
                        # half-open socket, triggering a reconnect.
                        read=_SSE_READ_TIMEOUT_SECONDS,
                        write=self._config.read_timeout,
                        pool=self._config.connect_timeout,
                    ),
                ) as client,
                connect_sse(client, "GET", url, headers=headers) as event_source,
            ):
                logger.debug("sse_connected", url=url)
                for event in event_source.iter_sse():
                    if not reached:
                        reached = True
                        # The stream is live (the server sends a `sync` frame
                        # first). Reap the fallback poller now so a healthy
                        # stream is the sole data source. _connect() blocks in
                        # iter_sse() for the whole lifetime of a healthy stream,
                        # so reaping only after it returns (in _run) would leave
                        # the poller running for the entire healthy period — its
                        # periodic full-store replaces can revert an SSE delta
                        # applied mid-flight (stale value flapping).
                        self._stop_fallback_polling()
                    if self._stop_event.is_set():
                        break
                    self._handle_event(event.event, event.data)
        except Exception as e:
            logger.warning("streaming_error", error=str(e))
            self._on_error(e)
        return reached

    def _backoff_delay(self, failures: int) -> float:
        """Capped exponential backoff.

        Base for the first (re)connect, then doubling up to the cap, with
        [d/2, d) jitter once escalating (thundering-herd avoidance).
        """
        if failures <= 1:
            return self._reconnect_base_delay
        delay = self._reconnect_base_delay
        for _ in range(1, failures):
            delay *= 2
            if delay >= self._max_reconnect_delay:
                return self._with_jitter(self._max_reconnect_delay)
        return self._with_jitter(delay)

    def _with_jitter(self, delay: float) -> float:
        """Return a value in [delay/2, delay)."""
        half = delay / 2
        return half + random.random() * half

    def _start_fallback_polling(self) -> None:
        """Start a parallel polling fallback (full re-fetch, retries forever).

        Idempotent. A start racing a concurrent stop() is a no-op: stop() sets
        the stop event before reaping, so a set event under the lock means a
        post-stop start would leak a poller — bail instead.
        """
        with self._fallback_lock:
            if self._fallback_poller is not None or self._stop_event.is_set():
                return
            poller = PollingHandler(
                http_client=self._http,
                config=self._config,
                on_update=self._on_update,
                on_error=self._on_error,
            )
            poller.start()
            self._fallback_poller = poller
            logger.warning("streaming_fallback_polling_started")

    def _stop_fallback_polling(self) -> None:
        with self._fallback_lock:
            if self._fallback_poller is not None:
                self._fallback_poller.stop()
                self._fallback_poller = None
                logger.info("streaming_fallback_polling_stopped")

    def _get_stream_url(self) -> str:
        """Get the streaming endpoint URL.

        Returns:
            The full URL for the SSE stream endpoint.
        """
        return f"{self._config.base_url}/v1/sdk/stream"

    def _get_headers(self) -> dict[str, str]:
        """Get the headers for the streaming request.

        Returns:
            Dictionary of HTTP headers.
        """
        return {
            "Authorization": self._sdk_key,
            "User-Agent": USER_AGENT,
            "Accept": "text/event-stream",
        }

    def _handle_event(self, event_type: str, data: str) -> None:
        """Handle an SSE event."""
        try:
            if event_type in ("flag.created", "flag.updated"):
                payload = json.loads(data)
                key = payload.get("key")
                if key:
                    self._on_flag_updated(key)
                    logger.debug("flag_updated_via_sse", key=key)
            elif event_type == "flag.deleted":
                payload = json.loads(data)
                key = payload.get("key")
                if key:
                    self._on_flag_deleted(key)
                    logger.debug("flag_deleted_via_sse", key=key)
            elif event_type == "segment.updated":
                self._on_segment_updated()
                logger.debug("segment_updated_via_sse")
            elif event_type == "sync":
                # Full config snapshot the server sends on (re)connect. Replace
                # the whole store so flags changed OR deleted during a disconnect
                # are re-synced. Full replace, never a per-key merge.
                flags, segments = self._http.parse_flags_response(json.loads(data))
                self._on_update(flags, segments)
                logger.debug("sync_snapshot_applied", flag_count=len(flags))
            # Unknown events are ignored silently
        except Exception as e:
            logger.error("event_parse_error", error=str(e), event_type=event_type)
