"""Evaluation inspector types for the Featureflip SDK.

An *inspector* is a plain callable invoked synchronously, in-process, on every
flag evaluation. It receives an :class:`EvaluationEvent` describing what the
caller actually got back and returns nothing — inspectors are void observers,
meant for piping exposures into analytics / experimentation tooling with no
backend dependency.

Register inspectors at client construction::

    from featureflip import Config, FeatureflipClient

    client = FeatureflipClient(
        sdk_key="sdk-xxx",
        config=Config(inspectors=[lambda e: posthog.capture("$feature_flag_called", e)]),
    )

Inspectors are honored on the *first* construction for a given SDK key, like
every other config option (the shared core is cached per key).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from featureflip.detail import EvaluationReason


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EvaluationEvent:
    """A single flag evaluation, handed to every registered inspector.

    This payload is the frozen cross-SDK inspector contract; the field names
    mirror the other Featureflip SDKs (adapted to Python's snake_case).

    Attributes:
        flag_key: The key of the flag that was evaluated.
        context: A copy of the full evaluation context. Mutating it does not
            affect the caller's dictionary. Treat as read-only.
        value: The value the caller actually receives (default already applied).
        reason: Why this value was served. Uses the Python SDK's native
            ``EvaluationReason`` enum — each SDK emits its own reason casing.
        variation_key: The winning variation key. ``None`` when the flag wasn't
            found or evaluation errored.
        rule_id: The matched rule's id — set only when the reason is
            ``RULE_MATCH``.
        prerequisite_key: The failing prerequisite's flag key — set only when
            the reason is ``PREREQUISITE_FAILED``.
        timestamp: ISO-8601 timestamp of the evaluation.
    """

    flag_key: str
    context: dict[str, Any]
    value: Any
    reason: EvaluationReason
    variation_key: str | None = None
    rule_id: str | None = None
    prerequisite_key: str | None = None
    timestamp: str = field(default_factory=_utc_now_iso)


EvaluationInspector = Callable[["EvaluationEvent"], None]
"""A void observer invoked once per evaluation with an :class:`EvaluationEvent`."""
