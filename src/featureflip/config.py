"""Client configuration for Featureflip SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from featureflip.inspector import EvaluationInspector


@dataclass
class Config:
    """Configuration options for the Featureflip client.

    Attributes:
        base_url: Base URL for the evaluation API.
        connect_timeout: Timeout in seconds for establishing connections.
        read_timeout: Timeout in seconds for reading responses.
        streaming: Whether to use SSE streaming for real-time updates.
        poll_interval: Interval in seconds for polling when streaming is disabled.
        send_events: Whether to send analytics events.
        flush_interval: Interval in seconds for flushing event batches.
        flush_batch_size: Maximum number of events in a batch before flushing.
        init_timeout: Timeout in seconds for client initialization.
        inspectors: Callables invoked synchronously with an ``EvaluationEvent``
            on every flag evaluation. Void observers — the return value is
            ignored, and a raising inspector cannot affect the evaluated value.
            Deliberately excluded from the config-equality comparison (see
            ``_core._configs_equal``): callbacks aren't structurally comparable
            and a differing callback must not trigger the "different options"
            warning.
    """

    base_url: str = "https://eval.featureflip.io"
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    streaming: bool = True
    poll_interval: float = 30.0
    send_events: bool = True
    flush_interval: float = 30.0
    flush_batch_size: int = 100
    init_timeout: float = 10.0
    inspectors: list[EvaluationInspector] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate and normalize configuration values."""
        # Strip trailing slash from base_url
        self.base_url = self.base_url.rstrip("/")

        # Validate timeouts are positive
        if self.connect_timeout <= 0:
            raise ValueError("connect_timeout must be positive")
        if self.read_timeout <= 0:
            raise ValueError("read_timeout must be positive")
        if self.init_timeout <= 0:
            raise ValueError("init_timeout must be positive")

        # Validate intervals are positive
        if self.poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if self.flush_interval <= 0:
            raise ValueError("flush_interval must be positive")

        # Validate batch size is positive
        if self.flush_batch_size <= 0:
            raise ValueError("flush_batch_size must be positive")
