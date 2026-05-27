"""Internal shared core for FeatureflipClient.

The shared core owns the expensive resources (HTTP client, background threads,
SSE/polling handlers, flag store, event processor) of a FeatureflipClient.
Refcounted: multiple FeatureflipClient handles can share one core, and the
real shutdown runs only when the last handle is released.

This module is private (underscore-prefixed). The only legitimate users are
``FeatureflipClient`` (which wraps it in a handle) and unit tests.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar

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


class _SharedFeatureflipCore:
    """Internal shared core owning all expensive resources of a FeatureflipClient.

    Refcounted: multiple FeatureflipClient handles can share one core, and the
    real shutdown runs when the last handle is released.
    """

    def __init__(
        self,
        sdk_key: str | None,
        config: Config,
        *,
        test_mode_values: dict[str, Any] | None = None,
        test_mode_flags: dict[str, FlagConfiguration] | None = None,
    ) -> None:
        """Initialize a shared core.

        For production use, pass ``sdk_key`` and ``config`` and leave the
        test-mode parameters as ``None``. The constructor blocks on the initial
        flag fetch (up to ``config.init_timeout`` seconds) and starts the
        streaming/polling handler and event processor before returning.

        For test stubs, pass ``test_mode_values`` (fixed-value stub) or
        ``test_mode_flags`` (pre-built flag store). No HTTP client or background
        threads are started in either test mode.
        """
        self._refcount_lock = threading.Lock()
        self._ref_count = 1
        self._is_shut_down = False

        self._sdk_key = sdk_key
        self._config = config

        self._test_mode_values = test_mode_values
        self._test_values: dict[str, Any] = dict(test_mode_values) if test_mode_values else {}

        self._flags: dict[str, FlagConfiguration] = dict(test_mode_flags) if test_mode_flags else {}
        self._segments: dict[str, Segment] = {}
        self._flags_lock = threading.RLock()
        self._initialized = threading.Event()

        self._evaluator = FlagEvaluator()

        self._http_client: HttpClient | None = None
        self._streaming_handler: StreamingHandler | None = None
        self._polling_handler: PollingHandler | None = None
        self._event_processor: EventProcessor | None = None

        # Test modes bypass all network and threading
        if test_mode_values is not None or test_mode_flags is not None:
            self._initialized.set()
            return

        # Production path: requires a real SDK key
        if not sdk_key:
            raise ValueError(
                "SDK key is required for production _SharedFeatureflipCore construction"
            )

        # Construct HTTP client
        self._http_client = HttpClient(sdk_key, config)

        # Fetch initial flags with timeout
        self._fetch_initial_flags()

        # Start streaming or polling
        if config.streaming:
            self._start_streaming()
        else:
            self._start_polling()

        # Start event processor if enabled
        if config.send_events:
            self._start_event_processor()

    # --- Test factories ---

    @classmethod
    def _create_for_testing_skeleton(cls) -> _SharedFeatureflipCore:
        """Create a minimal core for refcount tests — no flag store, no resources."""
        return cls(sdk_key=None, config=Config(), test_mode_flags={})

    @classmethod
    def _create_for_testing_with_flags(
        cls, flags: dict[str, FlagConfiguration]
    ) -> _SharedFeatureflipCore:
        """Create a core backed by a pre-built flag dict for evaluation tests."""
        return cls(sdk_key=None, config=Config(), test_mode_flags=flags)

    @classmethod
    def _create_for_testing_stub(cls, flags: dict[str, Any]) -> _SharedFeatureflipCore:
        """Create a test-stub core that returns fixed values (no flag evaluation).

        This is the backing implementation for ``FeatureflipClient.for_testing``.
        """
        return cls(sdk_key=None, config=Config(), test_mode_values=flags)

    # --- Refcount ---

    def _acquire(self) -> bool:
        """Atomically increment the refcount if the core is still alive.

        Returns:
            True if the refcount was incremented, False if the core has
            already shut down (caller must construct a new one).
        """
        with self._refcount_lock:
            if self._ref_count <= 0:
                return False
            self._ref_count += 1
            return True

    def _release(self) -> None:
        """Decrement the refcount. Run shutdown exactly once when it hits zero.

        Over-release (calling _release more times than _acquire was called) is
        a no-op — the advisory guard prevents the counter from drifting below
        zero for the common case, and _acquire's ``<= 0`` check is the backstop
        for any racing over-release.
        """
        run_shutdown = False
        with self._refcount_lock:
            if self._ref_count <= 0:
                return
            self._ref_count -= 1
            if self._ref_count == 0 and not self._is_shut_down:
                self._is_shut_down = True
                run_shutdown = True
        if run_shutdown:
            self._shutdown()

    # --- Public-to-handle API ---

    @property
    def is_initialized(self) -> bool:
        """Return True if flags have been loaded (or this is a test stub)."""
        return self._initialized.is_set() or self._test_mode_values is not None

    def evaluate(
        self,
        key: str,
        context: dict[str, Any],
        default: T,
    ) -> EvaluationDetail:
        """Evaluate a flag and return the full detail.

        In test-stub mode, returns the fixed value for the given key (or a
        FLAG_NOT_FOUND detail if the key isn't in the stub map).
        """
        try:
            # Test-stub mode: short-circuit to fixed values
            if self._test_mode_values is not None:
                if key in self._test_values:
                    return EvaluationDetail(
                        value=self._test_values[key],
                        reason=EvaluationReason.FALLTHROUGH,
                    )
                return EvaluationDetail(value=default, reason=EvaluationReason.FLAG_NOT_FOUND)

            # Normal path: look up flag in the store and evaluate
            flag = self._get_flag(key)
            if flag is None:
                return EvaluationDetail(value=default, reason=EvaluationReason.FLAG_NOT_FOUND)

            eval_context = EvaluationContext.from_dict(context or {})
            with self._flags_lock:
                all_flags = dict(self._flags)
            detail = self._evaluator.evaluate(
                flag, eval_context, self._get_segment, all_flags=all_flags
            )

            if detail.value is None:
                return EvaluationDetail(
                    value=default,
                    reason=detail.reason,
                    rule_id=detail.rule_id,
                    variation_key=detail.variation_key,
                    prerequisite_key=detail.prerequisite_key,
                )
            return detail
        except Exception as e:
            logger.warning("evaluation_error", key=key, error=str(e))
            return EvaluationDetail(value=default, reason=EvaluationReason.ERROR, error=e)

    def set_test_value(self, key: str, value: Any) -> None:
        """Update a test-stub value (only works on test-stub cores).

        Raises:
            RuntimeError: If called on a non-stub core.
        """
        if self._test_mode_values is None:
            raise RuntimeError("set_test_value() can only be called on a test-stub core")
        self._test_values[key] = value

    # --- Internal flag store ---

    def _get_flag(self, key: str) -> FlagConfiguration | None:
        with self._flags_lock:
            return self._flags.get(key)

    def _get_segment(self, key: str) -> Segment | None:
        with self._flags_lock:
            return self._segments.get(key)

    # --- Flag/segment update methods ---

    def _fetch_initial_flags(self) -> None:
        """Fetch initial flag configurations with timeout.

        Raises:
            InitializationError: If fetching times out or fails.
        """
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
        with self._flags_lock:
            for flag in flags:
                self._flags[flag.key] = flag

    def _update_segments(self, segments: list[Segment]) -> None:
        with self._flags_lock:
            for segment in segments:
                self._segments[segment.key] = segment

    def _update_single_flag(self, flag: FlagConfiguration) -> None:
        with self._flags_lock:
            self._flags[flag.key] = flag

    # --- Streaming / polling callbacks ---

    def _on_streaming_flag_updated(self, key: str) -> None:
        try:
            if self._http_client is None:
                return
            flag = self._http_client.get_flag(key)
            self._update_single_flag(flag)
        except Exception as e:
            logger.warning("streaming_flag_fetch_error", key=key, error=str(e))

    def _on_streaming_flag_deleted(self, key: str) -> None:
        with self._flags_lock:
            self._flags.pop(key, None)

    def _on_streaming_segment_updated(self) -> None:
        try:
            if self._http_client is None:
                return
            flags, segments = self._http_client.get_flags()
            self._update_flags(flags)
            self._update_segments(segments)
        except Exception as e:
            logger.warning("streaming_segment_refetch_error", error=str(e))

    def _on_streaming_error(self, error: Exception) -> None:
        logger.warning("streaming_error", error=str(error))

    def _on_polling_update(
        self,
        flags: list[FlagConfiguration],
        segments: list[Segment] | None = None,
    ) -> None:
        """Handle polling update. Polling receives full snapshots — replace store."""
        with self._flags_lock:
            self._flags = {flag.key: flag for flag in flags}
            if segments is not None:
                self._segments = {segment.key: segment for segment in segments}

    def _on_polling_error(self, error: Exception) -> None:
        logger.warning("polling_error", error=str(error))

    def _start_streaming(self) -> None:
        # _start_streaming is only called from the production __init__ path,
        # which has already validated that sdk_key is not None.
        assert self._sdk_key is not None
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
        assert self._http_client is not None
        self._polling_handler = PollingHandler(
            http_client=self._http_client,
            config=self._config,
            on_update=self._on_polling_update,
            on_error=self._on_polling_error,
        )
        self._polling_handler.start()

    def _start_event_processor(self) -> None:
        assert self._http_client is not None
        self._event_processor = EventProcessor(
            http_client=self._http_client,
            flush_interval=self._config.flush_interval,
            flush_batch_size=self._config.flush_batch_size,
        )
        self._event_processor.start()

    # --- Track / identify / flush / queue_evaluation_event ---

    def track(
        self,
        event_name: str,
        context: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Queue a custom event."""
        if self._test_mode_values is not None or self._event_processor is None:
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
        """Send user attributes for segment building."""
        if self._test_mode_values is not None or self._event_processor is None:
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

    def queue_evaluation_event(
        self,
        key: str,
        value: Any,
        context: dict[str, Any],
        reason: EvaluationReason,
        rule_id: str | None = None,
    ) -> None:
        """Queue an evaluation event (called by the handle's variation() method)."""
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

    # --- Shutdown ---

    def _shutdown(self) -> None:
        """Stop background threads, flush events, close HTTP client.

        Called exactly once when the refcount reaches zero.
        """
        # Remove from owning map first (if registered via _get_or_create_core).
        # Safe to call for test stubs that were never added — the helper no-ops
        # if _sdk_key is None.
        _remove_from_live_cores(self)

        # Stop streaming or polling
        if self._streaming_handler is not None:
            try:
                self._streaming_handler.stop()
            except Exception as e:
                logger.warning("streaming_stop_error", error=str(e))
            self._streaming_handler = None

        if self._polling_handler is not None:
            try:
                self._polling_handler.stop()
            except Exception as e:
                logger.warning("polling_stop_error", error=str(e))
            self._polling_handler = None

        # Stop event processor (flushes remaining events)
        if self._event_processor is not None:
            try:
                self._event_processor.stop()
            except Exception as e:
                logger.warning("event_processor_stop_error", error=str(e))
            self._event_processor = None

        # Close HTTP client
        if self._http_client is not None:
            try:
                self._http_client.close()
            except Exception as e:
                logger.warning("http_client_close_error", error=str(e))
            self._http_client = None

        logger.info("core_shut_down")


# =============================================================================
# Process-wide cache and factory
# =============================================================================

_LIVE_CORES: dict[str, _SharedFeatureflipCore] = {}
_LIVE_CORES_LOCK = threading.Lock()


def _get_or_create_core(sdk_key: str, config: Config) -> _SharedFeatureflipCore:
    """Get the cached core for ``sdk_key`` or construct a new one.

    If a cached core exists and is still alive, acquire a refcount on it and
    return it. If the cached core has shut down (shouldn't happen under normal
    use because ``_shutdown`` removes the entry, but defensive), remove the
    stale entry and construct a fresh one.

    Holds ``_LIVE_CORES_LOCK`` across the constructor call. This is intentional
    — see the design spec for rationale. The GIL makes lock contention cheap
    during network I/O, and we avoid the C#/Java-style speculative-construct-
    then-discard pattern.

    If the cached core was constructed with different config, log a warning
    and return the cached instance (config is ignored on subsequent calls).
    """
    with _LIVE_CORES_LOCK:
        existing = _LIVE_CORES.get(sdk_key)
        if existing is not None and existing._acquire():
            if not _configs_equal(existing._config, config):
                logger.warning(
                    "featureflip_client_config_mismatch",
                    message=(
                        "FeatureflipClient called with different config for SDK "
                        "key already in use; the cached instance's config is "
                        "preserved"
                    ),
                )
            return existing
        # Stale entry or not present — remove and construct fresh.
        if existing is not None:
            _LIVE_CORES.pop(sdk_key, None)
        # The constructor blocks on the initial flag fetch
        # (up to config.init_timeout seconds). We hold the lock the whole time.
        new_core = _SharedFeatureflipCore(sdk_key, config)
        _LIVE_CORES[sdk_key] = new_core
        return new_core


def _get_or_create_core_with_stub(sdk_key: str) -> _SharedFeatureflipCore:
    """Test helper: like _get_or_create_core but constructs stub cores.

    Used by tests/test_factory.py to exercise the cache logic without hitting
    the network. Stub cores bypass HTTP and background threads via the
    test_mode_flags={} path.
    """
    with _LIVE_CORES_LOCK:
        existing = _LIVE_CORES.get(sdk_key)
        if existing is not None and existing._acquire():
            return existing
        if existing is not None:
            _LIVE_CORES.pop(sdk_key, None)
        new_core = _SharedFeatureflipCore(
            sdk_key=sdk_key, config=Config(), test_mode_flags={}
        )
        _LIVE_CORES[sdk_key] = new_core
        return new_core


def _remove_from_live_cores(core: _SharedFeatureflipCore) -> None:
    """Remove a core from the _LIVE_CORES cache if it's the entry for its key.

    Called from ``_SharedFeatureflipCore._shutdown``. Guards against the case
    where the entry has already been replaced by a newer core for the same
    key (don't remove the replacement).
    """
    if core._sdk_key is None:
        return  # test stubs with no sdk_key were never added to the cache
    with _LIVE_CORES_LOCK:
        if _LIVE_CORES.get(core._sdk_key) is core:
            del _LIVE_CORES[core._sdk_key]


def _reset_for_testing() -> None:
    """Clear the _LIVE_CORES cache and force-shutdown every entry.

    For test isolation only. Called by test fixtures (pytest autouse fixtures
    or setup/teardown methods) to prevent cross-test contamination via the
    cache.

    NOTE ON REFCOUNT SEMANTICS: The _LIVE_CORES cache does NOT hold its own
    refcount increment — the "first handle" refcount baked into the
    constructor's _ref_count=1 is owned by the first returned handle, not by
    the map. So calling _release() here borrows against whichever handle
    still holds that slot. The advisory over-release guard in _release() makes
    any resulting double-decrement a safe no-op. This matches the C# (#781)
    and Java (#797) patterns.
    """
    with _LIVE_CORES_LOCK:
        to_release = list(_LIVE_CORES.values())
        _LIVE_CORES.clear()
    for core in to_release:
        core._release()


def _configs_equal(a: Config, b: Config) -> bool:
    """Compare two Config objects by value across all 9 fields."""
    return (
        a.base_url == b.base_url
        and a.connect_timeout == b.connect_timeout
        and a.read_timeout == b.read_timeout
        and a.streaming == b.streaming
        and a.poll_interval == b.poll_interval
        and a.send_events == b.send_events
        and a.flush_interval == b.flush_interval
        and a.flush_batch_size == b.flush_batch_size
        and a.init_timeout == b.init_timeout
    )
