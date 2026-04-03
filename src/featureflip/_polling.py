"""Polling handler for flag updates."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

    from featureflip.config import Config
    from featureflip.models import FlagConfiguration, Segment

logger = structlog.get_logger()


class HttpClientProtocol(Protocol):
    """Protocol for HTTP client to enable loose coupling."""

    def get_flags(self) -> tuple[list[FlagConfiguration], list[Segment]]:
        """Fetch all flag and segment configurations from the API."""
        ...


class PollingHandler:
    """Handles periodic polling for flag updates.

    This handler periodically fetches flag configurations from the API
    and calls the provided callbacks when updates are received or errors occur.
    It runs in a background thread and can be started and stopped.
    """

    def __init__(
        self,
        http_client: HttpClientProtocol,
        config: Config,
        on_update: Callable[[list[FlagConfiguration], list[Segment]], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        """Initialize the polling handler.

        Args:
            http_client: HTTP client for fetching flag configurations.
            config: Client configuration options.
            on_update: Callback invoked when flags are fetched successfully.
            on_error: Callback invoked when an error occurs.
        """
        self._http = http_client
        self._config = config
        self._on_update = on_update
        self._on_error = on_error
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start polling in a background thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("polling_started", interval=self._config.poll_interval)

    def stop(self) -> None:
        """Stop polling."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("polling_stopped")

    def _run(self) -> None:
        """Main polling loop."""
        while not self._stop_event.is_set():
            try:
                flags, segments = self._http.get_flags()
                self._on_update(flags, segments)
                logger.debug("polling_success", flag_count=len(flags))
            except Exception as e:
                logger.warning("polling_error", error=str(e))
                self._on_error(e)
            self._stop_event.wait(self._config.poll_interval)
