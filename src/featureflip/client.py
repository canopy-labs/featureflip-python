"""Main client for Featureflip SDK."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar, cast

import structlog

from featureflip._events import EventProcessor
from featureflip._http import HttpClient
from featureflip._polling import PollingHandler
from featureflip._streaming import StreamingHandler
from featureflip.config import Config
from featureflip.context import EvaluationContext
from featureflip.detail import EvaluationDetail, EvaluationReason
from featureflip.evaluation import FlagEvaluator
from featureflip.exceptions import InitializationError

if TYPE_CHECKING:
    from featureflip.models import FlagConfiguration, Segment

logger = structlog.get_logger()

T = TypeVar("T")


class FeatureflipClient:
    """Main client for interacting with the Featureflip service.

    This client handles:
    - Initial flag loading with timeout
    - Real-time flag updates via streaming or polling
    - Local flag evaluation
    - Event tracking (evaluations, custom events, identify)

    Example:
        >>> client = FeatureflipClient(sdk_key="sdk-xxx")
        >>> if client.variation("new-feature", {"user_id": "123"}, default=False):
        ...     show_new_feature()
        >>> client.close()

    As a context manager:
        >>> with FeatureflipClient(sdk_key="sdk-xxx") as client:
        ...     value = client.variation("flag", {"user_id": "123"}, default=False)
    """

    def __init__(
        self,
        sdk_key: str | None = None,
        config: Config | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            sdk_key: SDK key for authentication. Falls back to FEATUREFLIP_SDK_KEY env var.
            config: Client configuration. Uses defaults if not provided.

        Raises:
            InitializationError: If initialization times out or fails.
            ValueError: If no SDK key provided.
        """
        # Resolve SDK key
        resolved_key = sdk_key or os.environ.get("FEATUREFLIP_SDK_KEY")
        if not resolved_key:
            raise ValueError(
                "SDK key is required. Pass sdk_key parameter or set FEATUREFLIP_SDK_KEY env var."
            )
        self._sdk_key: str = resolved_key

        # Use default config if not provided
        self._config = config or Config()

        # Flag and segment storage (thread-safe)
        self._flags: dict[str, FlagConfiguration] = {}
        self._segments: dict[str, Segment] = {}
        self._flags_lock = threading.RLock()
        self._initialized = threading.Event()

        # Test mode flag
        self._test_mode = False
        self._test_values: dict[str, Any] = {}

        # Components
        self._http_client: HttpClient | None = None
        self._evaluator = FlagEvaluator()
        self._streaming_handler: StreamingHandler | None = None
        self._polling_handler: PollingHandler | None = None
        self._event_processor: EventProcessor | None = None

        # Initialize
        self._initialize()

    def _initialize(self) -> None:
        """Initialize the client by fetching flags and starting background processes."""
        # Create HTTP client
        self._http_client = HttpClient(self._sdk_key, self._config)

        # Fetch initial flags with timeout
        self._fetch_initial_flags()

        # Start streaming or polling
        if self._config.streaming:
            self._start_streaming()
        else:
            self._start_polling()

        # Start event processor if enabled
        if self._config.send_events:
            self._start_event_processor()

    def _fetch_initial_flags(self) -> None:
        """Fetch initial flag configurations with timeout.

        Raises:
            InitializationError: If fetching times out or fails.
        """
        # HTTP client must be set before calling this method
        assert self._http_client is not None

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._http_client.get_flags)
            try:
                flags, segments = future.result(timeout=self._config.init_timeout)
                self._update_flags(flags)
                self._update_segments(segments)
                self._initialized.set()
                logger.info("client_initialized", flag_count=len(flags))
            except FuturesTimeoutError as e:
                raise InitializationError(
                    f"Initialization timeout after {self._config.init_timeout}s"
                ) from e
            except Exception as e:
                raise InitializationError(f"Failed to initialize: {e}") from e

    def _update_flags(self, flags: list[FlagConfiguration]) -> None:
        """Update stored flags (thread-safe).

        Args:
            flags: List of flag configurations to store.
        """
        with self._flags_lock:
            for flag in flags:
                self._flags[flag.key] = flag

    def _update_segments(self, segments: list[Segment]) -> None:
        """Update stored segments (thread-safe).

        Args:
            segments: List of segment configurations to store.
        """
        with self._flags_lock:
            for segment in segments:
                self._segments[segment.key] = segment

    def _get_segment(self, key: str) -> Segment | None:
        """Get a segment by key (thread-safe).

        Args:
            key: Segment key to look up.

        Returns:
            Segment if found, None otherwise.
        """
        with self._flags_lock:
            return self._segments.get(key)

    def _update_single_flag(self, flag: FlagConfiguration) -> None:
        """Update a single flag (thread-safe).

        Args:
            flag: Flag configuration to update.
        """
        with self._flags_lock:
            self._flags[flag.key] = flag

    def _on_streaming_flag_updated(self, key: str) -> None:
        """Handle flag update from streaming — fetch and update single flag."""
        try:
            if self._http_client is None:
                return
            flag = self._http_client.get_flag(key)
            self._update_single_flag(flag)
        except Exception as e:
            logger.warning("streaming_flag_fetch_error", key=key, error=str(e))

    def _on_streaming_flag_deleted(self, key: str) -> None:
        """Handle flag deletion from streaming."""
        with self._flags_lock:
            self._flags.pop(key, None)

    def _on_streaming_segment_updated(self) -> None:
        """Handle segment update from streaming — refetch all."""
        try:
            if self._http_client is None:
                return
            flags, segments = self._http_client.get_flags()
            self._update_flags(flags)
            self._update_segments(segments)
        except Exception as e:
            logger.warning("streaming_segment_refetch_error", error=str(e))

    def _on_streaming_error(self, error: Exception) -> None:
        """Handle streaming error.

        Args:
            error: The error that occurred.
        """
        logger.warning("streaming_error", error=str(error))

    def _on_polling_update(self, flags: list[FlagConfiguration], segments: list[Segment] | None = None) -> None:
        """Handle flags update from polling.

        Polling receives full snapshots, so we replace the entire store
        rather than merging. This ensures deleted flags/segments are removed.

        Args:
            flags: Updated flag configurations (full snapshot).
            segments: Updated segment configurations (full snapshot).
        """
        with self._flags_lock:
            self._flags = {flag.key: flag for flag in flags}
            if segments is not None:
                self._segments = {segment.key: segment for segment in segments}

    def _on_polling_error(self, error: Exception) -> None:
        """Handle polling error.

        Args:
            error: The error that occurred.
        """
        logger.warning("polling_error", error=str(error))

    def _start_streaming(self) -> None:
        """Start the streaming handler."""
        self._streaming_handler = StreamingHandler(
            sdk_key=self._sdk_key,
            config=self._config,
            on_flag_updated=self._on_streaming_flag_updated,
            on_flag_deleted=self._on_streaming_flag_deleted,
            on_segment_updated=self._on_streaming_segment_updated,
            on_error=self._on_streaming_error,
        )
        self._streaming_handler.start()

    def _start_polling(self) -> None:
        """Start the polling handler."""
        assert self._http_client is not None
        self._polling_handler = PollingHandler(
            http_client=self._http_client,
            config=self._config,
            on_update=self._on_polling_update,
            on_error=self._on_polling_error,
        )
        self._polling_handler.start()

    def _start_event_processor(self) -> None:
        """Start the event processor."""
        assert self._http_client is not None
        self._event_processor = EventProcessor(
            http_client=self._http_client,
            flush_interval=self._config.flush_interval,
            flush_batch_size=self._config.flush_batch_size,
        )
        self._event_processor.start()

    def _get_flag(self, key: str) -> FlagConfiguration | None:
        """Get a flag by key (thread-safe).

        Args:
            key: Flag key to look up.

        Returns:
            Flag configuration if found, None otherwise.
        """
        with self._flags_lock:
            return self._flags.get(key)

    def variation(
        self,
        key: str,
        context: dict[str, Any],
        default: T,
        track: bool = True,
    ) -> T:
        """Evaluate a flag and return its value.

        Never raises. Returns default on any error.

        Args:
            key: Flag key to evaluate.
            context: Context dictionary with user attributes.
            default: Default value to return if flag is not found or on error.
            track: Whether to track this evaluation event.

        Returns:
            The evaluated flag value or default.
        """
        try:
            # Handle test mode
            if self._test_mode:
                value = self._test_values.get(key, default)
                return cast("T", value)

            # Get flag
            flag = self._get_flag(key)
            if flag is None:
                logger.debug("flag_not_found", key=key)
                return default

            # Create evaluation context
            eval_context = EvaluationContext.from_dict(context or {})

            # Evaluate
            detail = self._evaluator.evaluate(flag, eval_context, self._get_segment)
            value = detail.value if detail.value is not None else default

            # Track evaluation
            if track and self._event_processor is not None:
                self._queue_evaluation_event(
                    key=key,
                    value=value,
                    context=context or {},
                    reason=detail.reason,
                    rule_id=detail.rule_id,
                )

            return cast("T", value)

        except Exception as e:
            logger.warning("evaluation_error", key=key, error=str(e))
            return default

    def variation_detail(
        self,
        key: str,
        context: dict[str, Any],
        default: T,
    ) -> EvaluationDetail:
        """Evaluate a flag with detailed result.

        Args:
            key: Flag key to evaluate.
            context: Context dictionary with user attributes.
            default: Default value to return if flag is not found or on error.

        Returns:
            EvaluationDetail with value, reason, and optional rule_id.
        """
        try:
            # Handle test mode
            if self._test_mode:
                value = self._test_values.get(key, default)
                if key in self._test_values:
                    return EvaluationDetail(value=value, reason=EvaluationReason.FALLTHROUGH)
                return EvaluationDetail(value=default, reason=EvaluationReason.FLAG_NOT_FOUND)

            # Get flag
            flag = self._get_flag(key)
            if flag is None:
                return EvaluationDetail(value=default, reason=EvaluationReason.FLAG_NOT_FOUND)

            # Create evaluation context
            eval_context = EvaluationContext.from_dict(context or {})

            # Evaluate
            detail = self._evaluator.evaluate(flag, eval_context, self._get_segment)

            # Ensure default is used if value is None
            if detail.value is None:
                return EvaluationDetail(
                    value=default,
                    reason=detail.reason,
                    rule_id=detail.rule_id,
                )

            return detail

        except Exception as e:
            logger.warning("evaluation_error", key=key, error=str(e))
            return EvaluationDetail(value=default, reason=EvaluationReason.ERROR, error=e)

    def track(
        self,
        event_name: str,
        context: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track a custom event.

        Args:
            event_name: Name of the event to track.
            context: Context dictionary with user attributes.
            metadata: Optional additional metadata for the event.
        """
        if self._test_mode or self._event_processor is None:
            return

        event = {
            "type": "Custom",
            "flagKey": event_name,
            "context": context or {},
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._event_processor.queue_event(event)

    def identify(self, context: dict[str, Any]) -> None:
        """Send user attributes for segment building.

        Args:
            context: Context dictionary with user attributes.
        """
        if self._test_mode or self._event_processor is None:
            return

        event = {
            "type": "Identify",
            "flagKey": "$identify",
            "context": context or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._event_processor.queue_event(event)

    def flush(self) -> None:
        """Flush pending events immediately."""
        if self._event_processor is not None:
            self._event_processor.flush()

    def close(self) -> None:
        """Shut down the client, flushing events and stopping background threads."""
        # Stop streaming or polling
        if self._streaming_handler is not None:
            self._streaming_handler.stop()
            self._streaming_handler = None

        if self._polling_handler is not None:
            self._polling_handler.stop()
            self._polling_handler = None

        # Stop event processor (flushes remaining events)
        if self._event_processor is not None:
            self._event_processor.stop()
            self._event_processor = None

        # Close HTTP client
        if self._http_client is not None:
            self._http_client.close()
            self._http_client = None

        logger.info("client_closed")

    @property
    def is_initialized(self) -> bool:
        """Return True if the client has loaded flags.

        Returns:
            True if initialized, False otherwise.
        """
        return self._initialized.is_set() or self._test_mode

    def __enter__(self) -> FeatureflipClient:
        """Enter context manager.

        Returns:
            This client instance.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit context manager and close the client.

        Args:
            exc_type: Exception type if an exception was raised.
            exc_val: Exception value if an exception was raised.
            exc_tb: Exception traceback if an exception was raised.
        """
        self.close()

    @classmethod
    def for_testing(cls, flags: dict[str, Any]) -> FeatureflipClient:
        """Create a test client with fixed flag values.

        The test client:
        - Makes no network calls
        - Does not start background threads
        - Does not track events
        - Ignores context (always returns fixed values)

        Args:
            flags: Dictionary mapping flag keys to their values.

        Returns:
            A test client instance.

        Example:
            >>> client = FeatureflipClient.for_testing({
            ...     "feature-a": True,
            ...     "feature-b": "variant-1",
            ... })
            >>> client.variation("feature-a", {}, default=False)
            True
        """
        # Create instance without calling __init__
        instance = object.__new__(cls)

        # Set up test mode
        instance._test_mode = True
        instance._test_values = dict(flags)

        # Set up minimal state
        instance._sdk_key = "test-key"
        instance._config = Config()
        instance._flags = {}
        instance._segments = {}
        instance._flags_lock = threading.RLock()
        instance._initialized = threading.Event()
        instance._initialized.set()

        # No components
        instance._http_client = None
        instance._evaluator = FlagEvaluator()
        instance._streaming_handler = None
        instance._polling_handler = None
        instance._event_processor = None

        return instance

    def set_test_value(self, key: str, value: Any) -> None:
        """Update a test value (only works on test clients).

        Args:
            key: Flag key to update.
            value: New value for the flag.

        Raises:
            RuntimeError: If called on a non-test client.
        """
        if not self._test_mode:
            raise RuntimeError("set_test_value() can only be called on a test client")
        self._test_values[key] = value

    def _queue_evaluation_event(
        self,
        key: str,
        value: Any,
        context: dict[str, Any],
        reason: EvaluationReason,
        rule_id: str | None = None,
    ) -> None:
        """Queue an evaluation event.

        Args:
            key: Flag key that was evaluated.
            value: Value that was returned.
            context: Context used for evaluation.
            reason: Reason for the evaluation result.
            rule_id: ID of matched rule, if any.
        """
        if self._event_processor is None:
            return

        event: dict[str, Any] = {
            "type": "Evaluation",
            "flagKey": key,
            "value": value,
            "context": context,
            "reason": reason.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if rule_id is not None:
            event["ruleId"] = rule_id

        self._event_processor.queue_event(event)
