"""Change detection for the flag-update hook.

The store is handed a *full* snapshot on every poll tick and on every SSE
``sync`` reconnect, so notifying on each one would report "everything changed"
once per poll interval. These helpers reduce a snapshot to the set of flag keys
whose evaluated value could actually have moved.

Mirrors ``packages/js-sdk/src/core/store.ts`` (``diffSnapshot`` /
``addPrerequisiteDependents``); the semantics are cross-SDK contract, not an
implementation detail.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from featureflip.models import FlagConfiguration, Segment

#: A flag-update listener: called with the keys whose configuration changed.
#:
#: Public despite living in a private module — re-exported from ``featureflip``
#: so callers can annotate their own listeners. See
#: :meth:`featureflip.FeatureflipClient.on_update`.
FlagUpdateListener = Callable[[list[str]], None]

__all__ = ["FlagUpdateListener", "add_prerequisite_dependents", "diff_snapshot"]


def diff_snapshot(
    previous_flags: Mapping[str, FlagConfiguration],
    previous_segments: Mapping[str, Segment],
    next_flags: Iterable[FlagConfiguration],
    next_segments: Iterable[Segment],
) -> list[str]:
    """Keys whose evaluated value may differ between two snapshots.

    Reports added, removed and modified flags, then fans out to the flags a
    changed *segment* or a changed *prerequisite* drags along with it.

    Comparison is by value: the model dataclasses are frozen and compare on
    their configuration fields (``FlagConfiguration._variations_by_key`` is a
    derived index and is declared ``compare=False``), so ``!=`` is an exact
    config comparison and needs no serialization.

    Args:
        previous_flags: Flags currently in the store, keyed by flag key.
        previous_segments: Segments currently in the store, keyed by segment key.
        next_flags: Flags arriving in the new snapshot.
        next_segments: Segments arriving in the new snapshot.

    Returns:
        The changed flag keys. Empty when the snapshots are equivalent.
    """
    # Materialized: both are iterated more than once below, so a generator
    # would be silently empty on the second pass.
    incoming_flags = list(next_flags)
    incoming_segments = list(next_segments)

    changed: set[str] = set()

    next_flag_keys: set[str] = set()
    for flag in incoming_flags:
        next_flag_keys.add(flag.key)
        previous = previous_flags.get(flag.key)
        if previous is None or previous != flag:
            changed.add(flag.key)
    # Removed server-side: still a change for anyone holding the old value.
    changed.update(key for key in previous_flags if key not in next_flag_keys)

    changed_segments: set[str] = set()
    next_segment_keys: set[str] = set()
    for segment in incoming_segments:
        next_segment_keys.add(segment.key)
        previous_segment = previous_segments.get(segment.key)
        if previous_segment is None or previous_segment != segment:
            changed_segments.add(segment.key)
    changed_segments.update(
        key for key in previous_segments if key not in next_segment_keys
    )

    if changed_segments:
        # A segment edit alters evaluation outcomes without bumping any flag's
        # version, so the flags referencing it have to be pulled in by hand.
        #
        # Only the incoming flags need scanning: a flag that dropped its
        # reference to a changed segment must have had its own rules edited,
        # which changes its own config and is already reported above.
        for flag in incoming_flags:
            if any(
                rule.segment_key is not None and rule.segment_key in changed_segments
                for rule in flag.rules
            ):
                changed.add(flag.key)

    # Must run last: it walks out from the fully-resolved changed set, so a flag
    # that only the segment scan above pulled in still reaches its dependents.
    add_prerequisite_dependents(incoming_flags, changed)

    return sorted(changed)


def add_prerequisite_dependents(
    flags: Iterable[FlagConfiguration], changed: set[str]
) -> None:
    """Expand ``changed`` in place with every transitive prerequisite dependent.

    A flag's ``version`` covers its own prerequisite rows but not the flags
    those rows point at, so toggling a prerequisite bumps only the
    prerequisite's version. The evaluator resolves prerequisites recursively,
    so the dependent's value still flips (to its off variation, with reason
    ``PREREQUISITE_FAILED``) — leaving the dependent silently absent from the
    change notification.

    Walks reverse edges out from the changed set rather than scanning every
    flag's chain, so cost is proportional to the affected subgraph rather than
    to the whole snapshot. Over-reporting is harmless (a listener re-reads a
    flag that evaluates the same); under-reporting is the bug this prevents.

    Args:
        flags: The flags to build the reverse dependency index from — normally
            the *incoming* snapshot, so a removed flag still resolves its
            dependents (they are still present and still carry the now-dangling
            prerequisite row).
        changed: Mutated in place to include the dependents.
    """
    if not changed:
        return

    dependents: dict[str, list[str]] = {}
    for flag in flags:
        for prerequisite in flag.prerequisites:
            key = prerequisite.prerequisite_flag_key
            if not key:
                continue
            dependents.setdefault(key, []).append(flag.key)
    if not dependents:
        return

    # ``changed`` doubles as the visited set, so a prerequisite cycle (rejected
    # by the server, but reachable via a malformed payload) terminates instead
    # of looping forever. No depth cap is needed for the same reason — unlike
    # the evaluator, this walk visits each flag at most once.
    queue = list(changed)
    while queue:
        key = queue.pop()
        for dependent in dependents.get(key, ()):
            if dependent not in changed:
                changed.add(dependent)
                queue.append(dependent)
