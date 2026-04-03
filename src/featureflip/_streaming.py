"""SSE streaming handler for real-time flag updates."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

import httpx
import structlog
from httpx_sse import connect_sse

if TYPE_CHECKING:
    from collections.abc import Callable

    from featureflip.config import Config

logger = structlog.get_logger()


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
        on_flag_updated: Callable[[str], None],
        on_flag_deleted: Callable[[str], None],
        on_segment_updated: Callable[[], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        """Initialize the streaming handler.

        Args:
            sdk_key: The SDK key for authentication.
            config: Client configuration options.
            on_flag_updated: Callback invoked with flag key when a flag is created or updated.
            on_flag_deleted: Callback invoked with flag key when a flag is deleted.
            on_segment_updated: Callback invoked when a segment is updated (triggers full refetch).
            on_error: Callback invoked when an error occurs.
        """
        self._sdk_key = sdk_key
        self._config = config
        self._on_flag_updated = on_flag_updated
        self._on_flag_deleted = on_flag_deleted
        self._on_segment_updated = on_segment_updated
        self._on_error = on_error
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the streaming connection in a background thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("streaming_started")

    def stop(self) -> None:
        """Stop the streaming connection."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("streaming_stopped")

    def _run(self) -> None:
        """Main streaming loop with automatic reconnection."""
        while not self._stop_event.is_set():
            try:
                self._connect()
            except Exception as e:
                logger.warning("streaming_error", error=str(e))
                self._on_error(e)
                # Wait before reconnecting
                self._stop_event.wait(5.0)

    def _connect(self) -> None:
        """Connect to SSE stream and process events."""
        url = self._get_stream_url()
        headers = self._get_headers()

        with (
            httpx.Client(
                timeout=httpx.Timeout(
                    connect=self._config.connect_timeout,
                    read=None,  # No timeout for reading SSE stream
                    write=self._config.read_timeout,
                    pool=self._config.connect_timeout,
                ),
            ) as client,
            connect_sse(client, "GET", url, headers=headers) as event_source,
        ):
            logger.debug("sse_connected", url=url)
            for event in event_source.iter_sse():
                if self._stop_event.is_set():
                    break
                self._handle_event(event.event, event.data)

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
            "User-Agent": "featureflip-python/0.1.0",
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
            # Unknown events are ignored silently
        except Exception as e:
            logger.error("event_parse_error", error=str(e), event_type=event_type)
