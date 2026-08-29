"""Tests for flag-change detection and the client's update hook.

Covers the cross-SDK contract established by #1866 (js/node) and #2080 (C#):
the initial load is silent, later snapshots report only what moved, and a
change fans out to the flags a segment edit or a prerequisite drags with it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from featureflip import Config, FeatureflipClient
from featureflip._core import _reset_for_testing
from featureflip._updates import add_prerequisite_dependents, diff_snapshot
from featureflip.models import (
    Condition,
    ConditionLogic,
    ConditionOperator,
    FlagConfiguration,
    FlagType,
    Prerequisite,
    Segment,
    ServeConfig,
    ServeType,
    TargetingRule,
    Variation,
)


def make_flag(
    key: str = "test-flag",
    *,
    value: Any = True,
    version: int = 1,
    enabled: bool = True,
    segment_key: str | None = None,
    prerequisites: list[Prerequisite] | None = None,
) -> FlagConfiguration:
    """A boolean flag, optionally targeting a segment or gated on prerequisites."""
    rules: list[TargetingRule] = []
    if segment_key is not None:
        rules.append(
            TargetingRule(
                id="rule-1",
                priority=0,
                condition_groups=[],
                serve=ServeConfig(type=ServeType.FIXED, variation="on"),
                segment_key=segment_key,
            )
        )
    return FlagConfiguration(
        key=key,
        version=version,
        type=FlagType.BOOLEAN,
        enabled=enabled,
        variations=[Variation(key="on", value=value), Variation(key="off", value=False)],
        rules=rules,
        fallthrough=ServeConfig(type=ServeType.FIXED, variation="on"),
        off_variation="off",
        prerequisites=prerequisites or [],
    )


def make_segment(key: str = "seg", *, version: int = 1, attribute: str = "plan") -> Segment:
    return Segment(
        key=key,
        version=version,
        conditions=[
            Condition(attribute=attribute, operator=ConditionOperator.EQUALS, values=["pro"])
        ],
        condition_logic=ConditionLogic.AND,
    )


def keyed(flags: list[FlagConfiguration]) -> dict[str, FlagConfiguration]:
    return {flag.key: flag for flag in flags}


def keyed_segments(segments: list[Segment]) -> dict[str, Segment]:
    return {segment.key: segment for segment in segments}


class TestDiffSnapshot:
    """Unit tests for the snapshot diff itself."""

    def test_identical_snapshot_reports_nothing(self) -> None:
        flags = [make_flag("a"), make_flag("b")]
        segments = [make_segment()]
        assert (
            diff_snapshot(keyed(flags), keyed_segments(segments), flags, segments) == []
        )

    def test_added_flag_is_reported(self) -> None:
        assert diff_snapshot(
            keyed([make_flag("a")]), {}, [make_flag("a"), make_flag("b")], []
        ) == ["b"]

    def test_removed_flag_is_reported(self) -> None:
        assert diff_snapshot(
            keyed([make_flag("a"), make_flag("b")]), {}, [make_flag("a")], []
        ) == ["b"]

    def test_modified_flag_is_reported(self) -> None:
        assert diff_snapshot(
            keyed([make_flag("a", enabled=True)]),
            {},
            [make_flag("a", enabled=False)],
            [],
        ) == ["a"]

    def test_version_bump_alone_is_reported(self) -> None:
        assert diff_snapshot(
            keyed([make_flag("a", version=1)]), {}, [make_flag("a", version=2)], []
        ) == ["a"]

    def test_changed_segment_reports_the_flags_referencing_it(self) -> None:
        """A segment edit moves evaluated values without bumping any flag version."""
        flags = [make_flag("gated", segment_key="seg"), make_flag("ungated")]
        changed = diff_snapshot(
            keyed(flags),
            keyed_segments([make_segment(version=1)]),
            flags,
            [make_segment(version=2, attribute="tier")],
        )
        assert changed == ["gated"]

    def test_removed_segment_reports_the_flags_referencing_it(self) -> None:
        flags = [make_flag("gated", segment_key="seg")]
        changed = diff_snapshot(
            keyed(flags), keyed_segments([make_segment()]), flags, []
        )
        assert changed == ["gated"]

    def test_changed_prerequisite_reports_its_dependents(self) -> None:
        """The dependent's own config is untouched; its evaluated value still flips."""
        previous = [
            make_flag("base", enabled=True),
            make_flag("dependent", prerequisites=[Prerequisite("base", "on")]),
        ]
        incoming = [
            make_flag("base", enabled=False),
            make_flag("dependent", prerequisites=[Prerequisite("base", "on")]),
        ]
        assert diff_snapshot(keyed(previous), {}, incoming, []) == ["base", "dependent"]

    def test_prerequisite_fanout_is_transitive(self) -> None:
        previous = [
            make_flag("base", enabled=True),
            make_flag("mid", prerequisites=[Prerequisite("base", "on")]),
            make_flag("leaf", prerequisites=[Prerequisite("mid", "on")]),
        ]
        incoming = [
            make_flag("base", enabled=False),
            make_flag("mid", prerequisites=[Prerequisite("base", "on")]),
            make_flag("leaf", prerequisites=[Prerequisite("mid", "on")]),
        ]
        assert diff_snapshot(keyed(previous), {}, incoming, []) == [
            "base",
            "leaf",
            "mid",
        ]

    def test_segment_change_reaches_the_dependents_of_the_flags_it_pulled_in(
        self,
    ) -> None:
        """The prerequisite walk must run after the segment scan, not before."""
        flags = [
            make_flag("gated", segment_key="seg"),
            make_flag("dependent", prerequisites=[Prerequisite("gated", "on")]),
        ]
        changed = diff_snapshot(
            keyed(flags),
            keyed_segments([make_segment(version=1)]),
            flags,
            [make_segment(version=2, attribute="tier")],
        )
        assert changed == ["dependent", "gated"]

    def test_removed_flag_still_reaches_its_dependents(self) -> None:
        """Dependents are resolved from the INCOMING flags, which still hold the row."""
        previous = [
            make_flag("base"),
            make_flag("dependent", prerequisites=[Prerequisite("base", "on")]),
        ]
        incoming = [make_flag("dependent", prerequisites=[Prerequisite("base", "on")])]
        assert diff_snapshot(keyed(previous), {}, incoming, []) == ["base", "dependent"]

    def test_prerequisite_cycle_terminates(self) -> None:
        """A cycle is server-rejected but reachable via a malformed payload."""
        previous = [
            make_flag("a", enabled=True, prerequisites=[Prerequisite("b", "on")]),
            make_flag("b", prerequisites=[Prerequisite("a", "on")]),
        ]
        incoming = [
            make_flag("a", enabled=False, prerequisites=[Prerequisite("b", "on")]),
            make_flag("b", prerequisites=[Prerequisite("a", "on")]),
        ]
        assert diff_snapshot(keyed(previous), {}, incoming, []) == ["a", "b"]

    def test_next_flags_may_be_a_single_use_iterator(self) -> None:
        """The diff iterates the incoming flags more than once."""
        previous = keyed([make_flag("a", enabled=True)])
        changed = diff_snapshot(
            previous, {}, iter([make_flag("a", enabled=False)]), iter([])
        )
        assert changed == ["a"]


class TestAddPrerequisiteDependents:
    def test_empty_changed_set_is_left_alone(self) -> None:
        changed: set[str] = set()
        add_prerequisite_dependents([make_flag("a")], changed)
        assert changed == set()

    def test_dependents_are_added_in_place(self) -> None:
        changed = {"base"}
        add_prerequisite_dependents(
            [make_flag("dependent", prerequisites=[Prerequisite("base", "on")])],
            changed,
        )
        assert changed == {"base", "dependent"}


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    _reset_for_testing()
    yield
    _reset_for_testing()


class TestClientUpdateHook:
    """The public ``FeatureflipClient.on_update`` surface."""

    def _client(self, flags: list[FlagConfiguration], segments: list[Segment] | None = None):
        patcher = patch("featureflip._core.HttpClient")
        mock_http = patcher.start()
        mock_http.return_value.get_flags.return_value = (flags, segments or [])
        client = FeatureflipClient(
            sdk_key="test-key",
            config=Config(streaming=False, send_events=False),
        )
        return client, patcher

    def test_first_snapshot_establishes_the_baseline_silently(self) -> None:
        """A cold load is not a change — the first snapshot only sets the baseline.

        Driven at the core, because the guard is unreachable through the public
        API in Python: ``_SharedFeatureflipCore.__init__`` blocks on the initial
        fetch, so no handle — and therefore no listener — exists until the
        baseline is already set. Asserting this via ``client.on_update`` would
        pass no matter what the guard did. It is kept because the cross-SDK
        contract requires it and because a future non-blocking init (js works
        that way) would make it reachable.
        """
        seen: list[list[str]] = []
        client, patcher = self._client([make_flag("a", enabled=True)])
        try:
            core = client._core
            core.on_update(seen.append)

            # Back to cold: the store is populated but no baseline is recorded.
            core._has_snapshot = False
            core._apply_snapshot([make_flag("a", enabled=False)], [])
            assert seen == []

            # The next one diffs against that baseline and does fire.
            core._apply_snapshot([make_flag("a", enabled=True)], [])
            assert seen == [["a"]]
        finally:
            client.close()
            patcher.stop()

    def test_registering_after_construction_sees_no_startup_event(self) -> None:
        """The public path: a listener attached to a ready client starts quiet."""
        seen: list[list[str]] = []
        client, patcher = self._client([make_flag("a")])
        try:
            client.on_update(seen.append)
            assert seen == []
        finally:
            client.close()
            patcher.stop()

    def test_later_snapshot_fires_with_changed_keys(self) -> None:
        seen: list[list[str]] = []
        client, patcher = self._client([make_flag("a", enabled=True), make_flag("b")])
        try:
            client.on_update(seen.append)
            client._core._on_polling_update(
                [make_flag("a", enabled=False), make_flag("b")]
            )
            assert seen == [["a"]]
        finally:
            client.close()
            patcher.stop()

    def test_unchanged_snapshot_does_not_fire(self) -> None:
        """Polling delivers a full snapshot every tick; identical ones are silent."""
        seen: list[list[str]] = []
        client, patcher = self._client([make_flag("a")])
        try:
            client.on_update(seen.append)
            client._core._on_polling_update([make_flag("a")])
            client._core._on_polling_update([make_flag("a")])
            assert seen == []
        finally:
            client.close()
            patcher.stop()

    def test_unsubscribe_stops_delivery(self) -> None:
        seen: list[list[str]] = []
        client, patcher = self._client([make_flag("a", enabled=True)])
        try:
            unsubscribe = client.on_update(seen.append)
            unsubscribe()
            client._core._on_polling_update([make_flag("a", enabled=False)])
            assert seen == []
        finally:
            client.close()
            patcher.stop()

    def test_close_removes_only_this_handles_listeners(self) -> None:
        """Two handles share one core; closing one must not silence the other."""
        first_seen: list[list[str]] = []
        second_seen: list[list[str]] = []
        client, patcher = self._client([make_flag("a", enabled=True)])
        try:
            second = FeatureflipClient(
                sdk_key="test-key",
                config=Config(streaming=False, send_events=False),
            )
            client.on_update(first_seen.append)
            second.on_update(second_seen.append)

            client.close()
            second._core._on_polling_update([make_flag("a", enabled=False)])

            assert first_seen == []
            assert second_seen == [["a"]]
            second.close()
        finally:
            patcher.stop()

    def test_a_throwing_listener_does_not_stop_the_others(self) -> None:
        seen: list[list[str]] = []
        client, patcher = self._client([make_flag("a", enabled=True)])
        try:

            def boom(_keys: list[str]) -> None:
                raise RuntimeError("listener exploded")

            client.on_update(boom)
            client.on_update(seen.append)
            client._core._on_polling_update([make_flag("a", enabled=False)])
            assert seen == [["a"]]
        finally:
            client.close()
            patcher.stop()

    def test_listeners_receive_independent_copies(self) -> None:
        """One listener mutating its list must not corrupt what the next sees."""
        seen: list[list[str]] = []
        client, patcher = self._client([make_flag("a", enabled=True)])
        try:
            client.on_update(lambda keys: keys.clear())
            client.on_update(seen.append)
            client._core._on_polling_update([make_flag("a", enabled=False)])
            assert seen == [["a"]]
        finally:
            client.close()
            patcher.stop()

    def test_single_flag_delta_fires_with_dependents(self) -> None:
        """The SSE flag.updated path fans out the same way a snapshot diff does."""
        seen: list[list[str]] = []
        client, patcher = self._client(
            [
                make_flag("base", enabled=True),
                make_flag("dependent", prerequisites=[Prerequisite("base", "on")]),
            ]
        )
        try:
            client.on_update(seen.append)
            client._core._update_single_flag(make_flag("base", enabled=False))
            assert seen == [["base", "dependent"]]
        finally:
            client.close()
            patcher.stop()

    def test_redelivered_identical_flag_does_not_fire(self) -> None:
        """An SSE reconnect can re-fetch an unchanged flag."""
        seen: list[list[str]] = []
        client, patcher = self._client([make_flag("base")])
        try:
            client.on_update(seen.append)
            client._core._update_single_flag(make_flag("base"))
            assert seen == []
        finally:
            client.close()
            patcher.stop()

    def test_flag_deletion_fires_with_dependents(self) -> None:
        seen: list[list[str]] = []
        client, patcher = self._client(
            [
                make_flag("base"),
                make_flag("dependent", prerequisites=[Prerequisite("base", "on")]),
            ]
        )
        try:
            client.on_update(seen.append)
            client._core._on_streaming_flag_deleted("base")
            assert seen == [["base", "dependent"]]
        finally:
            client.close()
            patcher.stop()

    def test_deleting_an_absent_flag_does_not_fire(self) -> None:
        seen: list[list[str]] = []
        client, patcher = self._client([make_flag("a")])
        try:
            client.on_update(seen.append)
            client._core._on_streaming_flag_deleted("never-existed")
            assert seen == []
        finally:
            client.close()
            patcher.stop()

    def test_on_update_after_close_is_inert(self) -> None:
        client, patcher = self._client([make_flag("a")])
        try:
            client.close()
            seen: list[list[str]] = []
            unsubscribe = client.on_update(seen.append)
            unsubscribe()
            assert seen == []
        finally:
            patcher.stop()
