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
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar

import structlog

from featureflip._events import EventProcessor
from featureflip._http import HttpClient
from featureflip._polling import PollingHandler
from featureflip._streaming import StreamingHandler
from featureflip._updates import (
    FlagUpdateListener,
    add_prerequisite_dependents,
    diff_snapshot,
)
from featureflip.config import Config
from featureflip.context import EvaluationContext
from featureflip.detail import EvaluationDetail, EvaluationReason
from featureflip.evaluation import FlagEvaluator
from featureflip.exceptions import InitializationError
from featureflip.inspector import EvaluationEvent

if TYPE_CHECKING:
    from collections.abc import Callable

    from featureflip.inspector import EvaluationInspector
    from featureflip.models import FlagConfiguration, Segment

logger = structlog.get_logger()

T = TypeVar("T")


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    """Drop the optional wire fields when they carry nothing.

    ``userId`` and ``metadata`` are both optional on the backend DTO. Every
    server SDK omits them rather than sending ``null`` or an empty object, so
    the same call produces the same bytes whichever SDK made it (#2359).
    """
    if event.get("userId") is None:
        event.pop("userId", None)
    if not event.get("metadata"):
        event.pop("metadata", None)
    return event


def _resolve_user_id(context: dict[str, Any]) -> str | None:
    """Resolve the built-in user id from a raw context dict.

    Honors the ``user_id`` / ``userId`` alias (universal across the engine and
    every SDK) so a caller passing either spelling gets the same top-level
    ``userId`` on analytics events. Returns ``None`` when neither is present.
    """
    raw = context.get("user_id")
    if raw is None:
        raw = context.get("userId")
    return str(raw) if raw is not None else None


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

        # Filter to callables defensively — Python callers bypass the type
        # hints, and a non-callable entry must be dropped at construction
        # rather than blowing up on the evaluation hot path.
        self._inspectors: list[EvaluationInspector] = [
            inspector for inspector in (config.inspectors or []) if callable(inspector)
        ]

        self._test_mode_values = test_mode_values
        self._test_values: dict[str, Any] = dict(test_mode_values) if test_mode_values else {}

        self._flags: dict[str, FlagConfiguration] = dict(test_mode_flags) if test_mode_flags else {}
        self._segments: dict[str, Segment] = {}
        self._flags_lock = threading.RLock()
        self._initialized = threading.Event()

        # False until the first snapshot lands. The first one establishes a
        # baseline SILENTLY: a cold load is not a configuration change, and
        # OpenFeature-style consumers signal it with a readiness event instead.
        self._has_snapshot = False

        # Update listeners live on the core, so they fire regardless of which
        # handle triggered the update; handles drop their own on close.
        # A dedicated lock — taking _flags_lock to notify would hold the store
        # for the duration of arbitrary user callbacks.
        self._update_listeners: list[FlagUpdateListener] = []
        self._update_listeners_lock = threading.Lock()

        self._evaluator = FlagEvaluator()

        self._http_client: HttpClient | None = None
        self._streaming_handler: StreamingHandler | None = None
        self._polling_handler: PollingHandler | None = None
        self._event_processor: EventProcessor | None = None

        # Test modes bypass all network and threading
        if test_mode_values is not None or test_mode_flags is not None:
            self._initialized.set()
            # The store is already populated, so a later snapshot pushed in by a
            # test is a real change and must be diffed, not swallowed as a
            # baseline.
            self._has_snapshot = True
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
    def _create_for_testing_stub(
        cls,
        flags: dict[str, Any],
        inspectors: list[EvaluationInspector] | None = None,
    ) -> _SharedFeatureflipCore:
        """Create a test-stub core that returns fixed values (no flag evaluation).

        This is the backing implementation for ``FeatureflipClient.for_testing``.
        ``inspectors`` is optional so existing call sites are unaffected.
        """
        return cls(
            sdk_key=None,
            config=Config(inspectors=list(inspectors) if inspectors else []),
            test_mode_values=flags,
        )

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

        This is the single evaluation choke point — every ``variation`` /
        ``variation_detail`` call funnels through it — so the registered
        evaluation inspectors are notified here, exactly once per call, with
        the value and reason the caller actually receives. ``_evaluate``
        resolves a complete detail on *every* exit path (test-stub hit,
        test-stub miss, flag-not-found, evaluated result, ``None``-value
        default substitution, and the error branch), so notifying once around
        it instruments them all with no risk of a double fire.
        """
        detail = self._evaluate(key, context, default)
        self._notify_inspectors(key, context, detail)
        return detail

    def _evaluate(
        self,
        key: str,
        context: dict[str, Any],
        default: T,
    ) -> EvaluationDetail:
        """Resolve the evaluation detail. Never raises — see ``evaluate``."""
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

            # Malformed config: the evaluator selected a variation key the flag
            # does not define (e.g. a fallthrough/rule naming a since-deleted
            # variation). Degrade to the caller's default and report ERROR,
            # mirroring the engine's ServeVariation + the C#/Java SDKs (#1989).
            # A variation that genuinely exists with a None value is NOT this
            # case — hence the key lookup rather than a ``value is None`` check.
            if detail.variation_key and flag.get_variation(detail.variation_key) is None:
                return EvaluationDetail(
                    value=default,
                    reason=EvaluationReason.ERROR,
                    rule_id=detail.rule_id,
                    variation_key=detail.variation_key,
                    prerequisite_key=detail.prerequisite_key,
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

    # --- Evaluation inspectors ---

    def _notify_inspectors(
        self,
        flag_key: str,
        context: dict[str, Any],
        detail: EvaluationDetail,
    ) -> None:
        """Fire the registered evaluation inspectors for one evaluation.

        Never raises. Both halves are isolated:

        * Building the event must never disturb evaluation — a caller passing a
          non-mapping ``context`` (or mutating its own dict concurrently) would
          otherwise make ``dict(context)`` throw *out of* ``evaluate`` and
          silently downgrade the caller to their default, purely because an
          inspector happened to be registered. On failure the notification is
          skipped and logged.
        * A throwing inspector is isolated: it neither changes the value the
          caller receives nor stops the remaining inspectors from firing; the
          failure is logged and swallowed.

        The event-build guard lives *here*, not around ``_evaluate`` in
        ``evaluate`` — wrapping the call site would route the failure into the
        evaluation error handling and could double-fire the inspectors.
        """
        # Hot-path guard: with no inspectors registered, allocate nothing.
        if not self._inspectors:
            return

        try:
            event = EvaluationEvent(
                flag_key=flag_key,
                # Copy so a buggy inspector can't mutate the caller's context.
                context=dict(context or {}),
                value=detail.value,
                reason=detail.reason,
                variation_key=detail.variation_key,
                rule_id=detail.rule_id,
                prerequisite_key=detail.prerequisite_key,
            )
        except Exception as e:
            logger.warning("inspector_event_build_error", key=flag_key, error=str(e))
            return

        for inspector in self._inspectors:
            try:
                inspector(event)
            except Exception as e:
                logger.warning("inspector_error", key=flag_key, error=str(e))

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
                # Establishes the baseline; fires nothing (_has_snapshot False).
                self._apply_snapshot(flags, segments)
                self._initialized.set()
                logger.info("client_initialized", flag_count=len(flags))
            except FuturesTimeoutError as e:
                raise InitializationError(
                    f"Initialization timeout after {self._config.init_timeout}s"
                ) from e
            except Exception as e:
                raise InitializationError(f"Failed to initialize: {e}") from e

    def _apply_snapshot(
        self,
        flags: list[FlagConfiguration],
        segments: list[Segment] | None,
    ) -> None:
        """Replace the store with a full snapshot and notify what changed.

        Every source of a full snapshot funnels through here — the initial
        fetch, each poll tick, and the SSE ``sync``/segment-refetch path — so
        the baseline rule and the diff are applied uniformly.

        ``segments=None`` means "this source did not carry segments"; the
        existing segments are kept and excluded from the diff, rather than
        being treated as deleted.
        """
        with self._flags_lock:
            if self._has_snapshot:
                changed = diff_snapshot(
                    self._flags,
                    self._segments,
                    flags,
                    segments if segments is not None else self._segments.values(),
                )
            else:
                # First snapshot: establish the baseline silently.
                changed = []

            self._flags = {flag.key: flag for flag in flags}
            if segments is not None:
                self._segments = {segment.key: segment for segment in segments}
            self._has_snapshot = True

        # Outside the lock: listeners are user code and may re-enter the client.
        self._notify_update_listeners(changed)

    def _update_single_flag(self, flag: FlagConfiguration) -> None:
        with self._flags_lock:
            previous = self._flags.get(flag.key)
            self._flags[flag.key] = flag
            # A re-delivered identical config is not a change. The SSE path can
            # re-fetch the same flag (reconnect, duplicate event), and reporting
            # those would make the hook noisy in exactly the way the snapshot
            # baseline exists to prevent.
            changed = (
                self._with_prerequisite_dependents_locked(flag.key)
                if previous != flag
                else []
            )
        self._notify_update_listeners(changed)

    def _with_prerequisite_dependents_locked(self, key: str) -> list[str]:
        """``key`` plus every flag transitively depending on it. Caller holds the lock.

        The single-key delta paths (SSE ``flag.created``/``flag.updated``/
        ``flag.deleted``) need the same fan-out as a full snapshot diff: the
        event names one flag, but each of its dependents changes evaluated
        value too.

        Reads the post-mutation store, so an upserted flag is included and a
        deleted flag's dependents — which still carry the now-dangling
        prerequisite row — remain reachable.
        """
        changed = {key}
        add_prerequisite_dependents(self._flags.values(), changed)
        return sorted(changed)

    # --- Update listeners ---

    def on_update(self, listener: FlagUpdateListener) -> Callable[[], None]:
        """Register a flag-update listener. Returns an unsubscribe callable."""
        with self._update_listeners_lock:
            self._update_listeners.append(listener)
        return lambda: self.off_update(listener)

    def off_update(self, listener: FlagUpdateListener) -> None:
        """Remove a listener. Removing one that isn't registered is a no-op."""
        with self._update_listeners_lock, suppress(ValueError):
            self._update_listeners.remove(listener)

    def _notify_update_listeners(self, keys: list[str]) -> None:
        """Fire listeners with ``keys``. No-op when nothing changed.

        A throwing listener is isolated: it neither breaks the streaming/polling
        callback that produced the update nor stops the remaining listeners.
        Each listener gets its own copy, so one mutating the list cannot corrupt
        what the others see.
        """
        if not keys:
            return
        with self._update_listeners_lock:
            listeners = list(self._update_listeners)
        for listener in listeners:
            try:
                listener(list(keys))
            except Exception as e:
                logger.warning("flag_update_listener_error", error=str(e))

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
            # Dependents resolved from the POST-delete store: they still carry
            # the now-dangling prerequisite row, and their evaluated value has
            # changed even though their own config has not.
            changed = (
                self._with_prerequisite_dependents_locked(key)
                if self._flags.pop(key, None) is not None
                else []
            )
        self._notify_update_listeners(changed)

    def _on_streaming_segment_updated(self) -> None:
        try:
            if self._http_client is None:
                return
            flags, segments = self._http_client.get_flags()
            # get_flags() returns the full current snapshot, so REPLACE the store
            # (drop flags/segments deleted server-side) rather than per-key
            # merging — otherwise a delete only lands via a later flag.deleted
            # delta or a poll/sync full-replace. Mirrors _on_polling_update.
            self._apply_snapshot(flags, segments)
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
        self._apply_snapshot(flags, segments)

    def _on_polling_error(self, error: Exception) -> None:
        logger.warning("polling_error", error=str(error))

    def _start_streaming(self) -> None:
        # _start_streaming is only called from the production __init__ path,
        # which has already validated that sdk_key and http_client are not None.
        assert self._sdk_key is not None
        assert self._http_client is not None
        self._streaming_handler = StreamingHandler(
            sdk_key=self._sdk_key,
            config=self._config,
            http_client=self._http_client,
            on_flag_updated=self._on_streaming_flag_updated,
            on_flag_deleted=self._on_streaming_flag_deleted,
            on_segment_updated=self._on_streaming_segment_updated,
            on_error=self._on_streaming_error,
            on_update=self._on_polling_update,
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
        # Send the backend SdkEventDto shape: top-level userId, no `context`.
        # See apps/evaluation-api Controllers/SdkController.RecordEvents — the
        # backend has no context/value/reason field, so anything else is dropped.
        event = _compact_event(
            {
                "type": "Custom",
                "flagKey": event_name,
                "userId": _resolve_user_id(context or {}),
                "metadata": metadata,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._event_processor.queue_event(event)

    def identify(self, context: dict[str, Any]) -> None:
        """Record an identify event for analytics.

        Does not affect flag evaluation: targeting rules and segments are matched
        against the context passed to each variation call.
        """
        if self._test_mode_values is not None or self._event_processor is None:
            return
        # Promote the user id to the top-level userId field and put the remaining
        # attributes in metadata (stripping both alias spellings so the identity
        # isn't duplicated inside the metadata bag).
        attributes = {
            k: v
            for k, v in (context or {}).items()
            if k not in ("user_id", "userId")
        }
        event = _compact_event(
            {
                "type": "Identify",
                "flagKey": "$identify",
                "userId": _resolve_user_id(context or {}),
                "metadata": attributes,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._event_processor.queue_event(event)

    def flush(self) -> None:
        """Flush pending events immediately."""
        if self._event_processor is not None:
            self._event_processor.flush()

    def queue_evaluation_event(
        self,
        key: str,
        context: dict[str, Any],
        variation_key: str | None,
    ) -> None:
        """Queue an evaluation event (called by the handle's variation() method).

        Emits the backend SdkEventDto shape — top-level ``userId`` and
        ``variation`` — mirroring the JS SDK. The backend has no context/value/
        reason/ruleId fields, so they are not sent (System.Text.Json would drop
        them and bind UserId/Variation to null; see #1920).
        """
        if self._event_processor is None:
            return
        event: dict[str, Any] = _compact_event(
            {
                "type": "Evaluation",
                "flagKey": key,
                "userId": _resolve_user_id(context or {}),
                "variation": variation_key,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
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

        # Last, so a listener cannot be fired by a handler still winding down.
        # Nothing will update the store again, and holding these would keep
        # references to consumer objects alive for the life of the process.
        with self._update_listeners_lock:
            self._update_listeners.clear()

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
    """Compare two Config objects by value across the 9 comparable fields.

    ``inspectors`` is deliberately excluded: callbacks aren't structurally
    comparable, and a differing callback must not trigger the "different
    options" warning for a shared core. So are ``wrapper_name`` and
    ``wrapper_version``: they identify the caller rather than change behavior.
    """
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
