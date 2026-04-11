"""Main client for Featureflip SDK."""

from __future__ import annotations

import os
from typing import Any, TypeVar, cast

import structlog

from featureflip._core import _get_or_create_core, _SharedFeatureflipCore
from featureflip.config import Config
from featureflip.detail import EvaluationDetail, EvaluationReason

logger = structlog.get_logger()

T = TypeVar("T")


class FeatureflipClient:
    """Main client for interacting with the Featureflip service.

    Multiple instances constructed with the same SDK key share an underlying
    refcounted client. Two ``FeatureflipClient(sdk_key="x")`` calls return
    distinct handle objects that delegate to the same shared core; the core
    shuts down only when the last handle is closed.

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
        resolved_config = config or Config()

        # Get-or-create the shared core. Subsequent constructions with the
        # same SDK key return a handle pointing at the cached core; only the
        # first call actually spins up background threads and fetches flags.
        self._core: _SharedFeatureflipCore = _get_or_create_core(
            resolved_key, resolved_config
        )
        self._closed = False

    # --- Typed evaluation methods ---

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
        if self._closed:
            return default
        try:
            detail = self._core.evaluate(key, context or {}, default)
            value = detail.value if detail.value is not None else default

            if track:
                self._core.queue_evaluation_event(
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

        Does NOT track an evaluation event (matches the pre-refactor behavior).

        Args:
            key: Flag key to evaluate.
            context: Context dictionary with user attributes.
            default: Default value to return if flag is not found or on error.

        Returns:
            EvaluationDetail with value, reason, and optional rule_id.
        """
        if self._closed:
            return EvaluationDetail(value=default, reason=EvaluationReason.ERROR)
        return self._core.evaluate(key, context or {}, default)

    # --- Event tracking ---

    def track(
        self,
        event_name: str,
        context: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track a custom event."""
        if self._closed:
            return
        self._core.track(event_name, context, metadata)

    def identify(self, context: dict[str, Any]) -> None:
        """Send user attributes for segment building."""
        if self._closed:
            return
        self._core.identify(context)

    def flush(self) -> None:
        """Flush pending events immediately."""
        if self._closed:
            return
        self._core.flush()

    # --- Lifecycle ---

    @property
    def is_initialized(self) -> bool:
        """Return True if the client has loaded flags.

        Returns False if this handle has been closed, even if other handles
        pointing at the same shared core are still alive.
        """
        if self._closed:
            return False
        return self._core.is_initialized

    def close(self) -> None:
        """Close this handle.

        Decrements the refcount on the shared core. If this is the last live
        handle for the core's SDK key, the core shuts down (flushes events,
        stops background threads, closes HTTP client). Otherwise the core
        stays alive and other handles continue to work.

        Safe to call multiple times on the same handle; subsequent calls are
        no-ops.
        """
        if self._closed:
            return
        self._closed = True
        self._core._release()

    def __enter__(self) -> FeatureflipClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.close()

    # --- Test-stub factory ---

    @classmethod
    def for_testing(cls, flags: dict[str, Any]) -> FeatureflipClient:
        """Create a test client with fixed flag values.

        The test client:
        - Makes no network calls
        - Does not start background threads
        - Does not track events
        - Ignores context (always returns fixed values)
        - Does NOT participate in the _LIVE_CORES cache (no SDK key collision)

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
        # Create instance without calling __init__ (same technique the original
        # used). Then wire up a standalone test-stub core that never enters
        # _LIVE_CORES (it has no sdk_key).
        instance = object.__new__(cls)
        instance._core = _SharedFeatureflipCore(
            sdk_key=None,
            config=Config(),
            test_mode_values=dict(flags),
        )
        instance._closed = False
        return instance

    def set_test_value(self, key: str, value: Any) -> None:
        """Update a test value (only works on test clients).

        Args:
            key: Flag key to update.
            value: New value for the flag.

        Raises:
            RuntimeError: If called on a non-test client.
        """
        self._core.set_test_value(key, value)

    def _queue_evaluation_event(
        self,
        key: str,
        value: Any,
        context: dict[str, Any],
        reason: EvaluationReason,
        rule_id: str | None = None,
    ) -> None:
        """Queue an evaluation event. Delegates to the shared core.

        Preserved for backwards compatibility with existing tests that call
        this method directly on the client.
        """
        self._core.queue_evaluation_event(
            key=key,
            value=value,
            context=context,
            reason=reason,
            rule_id=rule_id,
        )
