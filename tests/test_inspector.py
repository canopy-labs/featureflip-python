"""Tests for the evaluation inspector callback (``Config.inspectors``).

Mirrors the js-sdk matrix (``packages/js-sdk/tests/inspector.test.ts``): the
inspector fires exactly once per evaluation on every exit path of the core's
single choke point, with the value and reason the caller actually receives.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from featureflip import _core as core_module
from featureflip._core import _SharedFeatureflipCore
from featureflip.client import FeatureflipClient
from featureflip.config import Config
from featureflip.detail import EvaluationReason
from featureflip.models import (
    Condition,
    ConditionGroup,
    ConditionLogic,
    ConditionOperator,
    FlagConfiguration,
    FlagType,
    Prerequisite,
    ServeConfig,
    ServeType,
    TargetingRule,
    Variation,
)

if TYPE_CHECKING:
    from featureflip.inspector import EvaluationEvent

ISO_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _bool_flag(
    key: str,
    *,
    enabled: bool = True,
    fallthrough: str = "on",
    rules: list[TargetingRule] | None = None,
    prerequisites: list[Prerequisite] | None = None,
) -> FlagConfiguration:
    return FlagConfiguration(
        key=key,
        version=1,
        type=FlagType.BOOLEAN,
        enabled=enabled,
        variations=[
            Variation(key="on", value=True),
            Variation(key="off", value=False),
        ],
        rules=rules or [],
        fallthrough=ServeConfig(type=ServeType.FIXED, variation=fallthrough),
        off_variation="off",
        prerequisites=prerequisites or (),
    )


def _flags() -> dict[str, FlagConfiguration]:
    """Build the fixture flag store: one flag per evaluation exit path."""
    rule = TargetingRule(
        id="rule-1",
        priority=1,
        condition_groups=[
            ConditionGroup(
                operator=ConditionLogic.AND,
                conditions=[
                    Condition(
                        attribute="userId",
                        operator=ConditionOperator.EQUALS,
                        values=["alice"],
                    )
                ],
            )
        ],
        serve=ServeConfig(type=ServeType.FIXED, variation="on"),
    )
    flags = [
        _bool_flag("flag-on"),
        _bool_flag("flag-off", enabled=False),
        _bool_flag("flag-rule", fallthrough="off", rules=[rule]),
        _bool_flag(
            "flag-prereq",
            prerequisites=[
                Prerequisite(prerequisite_flag_key="flag-off", expected_variation_key="on")
            ],
        ),
        # Malformed on purpose: the fallthrough serves a variation key the flag
        # does not define (e.g. a since-deleted variation). Degrades to the
        # caller's default and reports ERROR — mirroring the engine + C#/Java.
        _bool_flag("flag-missing-variation", fallthrough="ghost"),
    ]
    return {flag.key: flag for flag in flags}


def _make_core(inspectors: list[Any]) -> _SharedFeatureflipCore:
    """Build a networkless core backed by the fixture flags + given inspectors."""
    return _SharedFeatureflipCore(
        sdk_key=None,
        config=Config(inspectors=inspectors),
        test_mode_flags=_flags(),
    )


def _make_stub_core(inspectors: list[Any]) -> _SharedFeatureflipCore:
    """Build a networkless test-stub core (fixed values) + given inspectors.

    The stub path never touches ``context`` while resolving the detail, so it
    isolates "what did registering an inspector change?" from any context
    handling inside the evaluator itself.
    """
    return _SharedFeatureflipCore(
        sdk_key=None,
        config=Config(inspectors=inspectors),
        test_mode_values={"feature-a": "variant-1"},
    )


def _client_for(core: _SharedFeatureflipCore) -> FeatureflipClient:
    """Wrap an existing core in a client handle without running ``__init__``.

    Same technique ``FeatureflipClient.for_testing`` uses — the handle never
    enters the ``_LIVE_CORES`` cache because the core has no SDK key.
    """
    client = object.__new__(FeatureflipClient)
    client._core = core
    client._closed = False
    return client


# A context the caller might plausibly pass by mistake: not a mapping, and one
# that ``dict(...)`` rejects with ValueError rather than TypeError.
BAD_CONTEXT = ["user_id", "bob"]


class TestInspectorPayload:
    def test_fires_once_with_full_payload_on_fallthrough(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_core([events.append])
        try:
            context = {"user_id": "bob", "plan": "pro"}
            detail = core.evaluate("flag-on", context, default=False)
            assert detail.value is True

            assert len(events) == 1
            event = events[0]
            assert event.flag_key == "flag-on"
            assert event.value is True
            assert event.variation_key == "on"
            assert event.reason is EvaluationReason.FALLTHROUGH
            assert event.rule_id is None
            assert event.prerequisite_key is None
            assert ISO_TIMESTAMP.match(event.timestamp)
            assert event.context == context
        finally:
            core._release()

    def test_reports_rule_id_on_rule_match(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_core([events.append])
        try:
            detail = core.evaluate("flag-rule", {"user_id": "alice"}, default=False)
            assert detail.value is True

            assert len(events) == 1
            assert events[0].reason is EvaluationReason.RULE_MATCH
            assert events[0].rule_id == "rule-1"
            assert events[0].variation_key == "on"
        finally:
            core._release()

    def test_reports_flag_disabled_with_off_value(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_core([events.append])
        try:
            detail = core.evaluate("flag-off", {"user_id": "bob"}, default=True)
            assert detail.value is False

            assert len(events) == 1
            assert events[0].reason is EvaluationReason.FLAG_DISABLED
            assert events[0].value is False
            assert events[0].variation_key == "off"
        finally:
            core._release()

    def test_reports_flag_not_found_with_default_and_no_variation_key(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_core([events.append])
        try:
            detail = core.evaluate("missing", {"user_id": "bob"}, default=True)
            assert detail.value is True

            assert len(events) == 1
            assert events[0].flag_key == "missing"
            assert events[0].reason is EvaluationReason.FLAG_NOT_FOUND
            assert events[0].value is True
            assert events[0].variation_key is None
        finally:
            core._release()

    def test_reports_prerequisite_failed_with_prerequisite_key(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_core([events.append])
        try:
            detail = core.evaluate("flag-prereq", {"user_id": "bob"}, default=True)
            assert detail.value is False

            assert len(events) == 1
            assert events[0].reason is EvaluationReason.PREREQUISITE_FAILED
            assert events[0].prerequisite_key == "flag-off"
            assert events[0].value is False
        finally:
            core._release()

    def test_reports_error_when_served_variation_key_is_not_defined(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_core([events.append])
        try:
            # The returned detail (what the caller sees) degrades to the default
            # and reports ERROR — not the misleading FALLTHROUGH the evaluator
            # resolved for the since-deleted variation key.
            detail = core.evaluate("flag-missing-variation", {"user_id": "bob"}, default=False)
            assert detail.value is False
            assert detail.reason is EvaluationReason.ERROR
            assert detail.variation_key == "ghost"  # kept for diagnostics

            assert len(events) == 1
            assert events[0].reason is EvaluationReason.ERROR
            assert events[0].value is False
        finally:
            core._release()

    def test_reports_error_and_still_returns_default_when_evaluation_raises(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_core([events.append])
        try:
            evaluator = MagicMock()
            evaluator.evaluate.side_effect = RuntimeError("boom")
            core._evaluator = evaluator

            detail = core.evaluate("flag-on", {"user_id": "bob"}, default=True)
            assert detail.value is True
            assert detail.reason is EvaluationReason.ERROR

            assert len(events) == 1
            assert events[0].reason is EvaluationReason.ERROR
            assert events[0].value is True
            assert events[0].variation_key is None
        finally:
            core._release()

    def test_fires_on_the_test_stub_paths(self) -> None:
        events: list[EvaluationEvent] = []
        core = _SharedFeatureflipCore(
            sdk_key=None,
            config=Config(inspectors=[events.append]),
            test_mode_values={"feature-a": "variant-1"},
        )
        try:
            assert core.evaluate("feature-a", {"user_id": "bob"}, default="d").value == "variant-1"
            assert core.evaluate("missing", {"user_id": "bob"}, default="d").value == "d"

            assert len(events) == 2
            assert events[0].flag_key == "feature-a"
            assert events[0].value == "variant-1"
            assert events[0].reason is EvaluationReason.FALLTHROUGH
            assert events[1].flag_key == "missing"
            assert events[1].value == "d"
            assert events[1].reason is EvaluationReason.FLAG_NOT_FOUND
        finally:
            core._release()


class TestInspectorSemantics:
    def test_invokes_every_registered_inspector(self) -> None:
        first: list[EvaluationEvent] = []
        second: list[EvaluationEvent] = []
        core = _make_core([first.append, second.append])
        try:
            core.evaluate("flag-on", {"user_id": "bob"}, default=False)
            assert len(first) == 1
            assert len(second) == 1
        finally:
            core._release()

    def test_isolates_a_raising_inspector(self) -> None:
        seen: list[EvaluationEvent] = []

        def boom(_event: EvaluationEvent) -> None:
            raise RuntimeError("inspector boom")

        core = _make_core([boom, seen.append])
        try:
            with patch.object(core_module, "logger") as logger:
                detail = core.evaluate("flag-on", {"user_id": "bob"}, default=False)

            # (a) the returned value is unaffected
            assert detail.value is True
            assert detail.reason is EvaluationReason.FALLTHROUGH
            # (b) siblings registered after the thrower still fire
            assert len(seen) == 1
            # (c) the failure is logged, not propagated
            logger.warning.assert_called_once_with(
                "inspector_error", key="flag-on", error="inspector boom"
            )
        finally:
            core._release()

    def test_ignores_non_callable_entries_without_raising(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_core([None, "nope", 42, events.append])
        try:
            detail = core.evaluate("flag-on", {"user_id": "bob"}, default=False)
            assert detail.value is True
            assert len(events) == 1
        finally:
            core._release()

    def test_no_inspectors_configured_is_a_no_op(self) -> None:
        core = _make_core([])
        try:
            assert core._inspectors == []
            detail = core.evaluate("flag-on", {"user_id": "bob"}, default=False)
            assert detail.value is True
        finally:
            core._release()

    def test_event_context_is_a_copy_of_the_callers_dict(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_core([events.append])
        try:
            context = {"user_id": "bob", "plan": "pro"}
            core.evaluate("flag-on", context, default=False)

            event_context = events[0].context
            assert event_context == context
            assert event_context is not context

            # A buggy inspector mutating the event's context must not touch the
            # caller's dictionary.
            event_context["plan"] = "mutated"
            event_context["injected"] = True
            assert context == {"user_id": "bob", "plan": "pro"}
        finally:
            core._release()


class TestInspectorEventBuildFailure:
    """Registering an inspector must never change the value the caller receives.

    Regression guard for the asymmetry where building the ``EvaluationEvent``
    was unguarded: a non-mapping ``context`` made ``dict(context)`` raise out of
    ``evaluate``, so the *same* call returned the evaluated value with no
    inspectors registered but the caller's default (via ``variation``) or a
    raised exception (via ``variation_detail``) once one was.
    """

    def test_stub_value_is_identical_with_and_without_an_inspector(self) -> None:
        # The stub path resolves the detail without touching `context`, so this
        # is a genuinely correctly-evaluated value — not a default in disguise.
        baseline_core = _make_stub_core([])
        try:
            baseline = baseline_core.evaluate("feature-a", BAD_CONTEXT, default="d")
        finally:
            baseline_core._release()
        assert baseline.value == "variant-1"

        events: list[EvaluationEvent] = []
        core = _make_stub_core([events.append])
        try:
            observed = core.evaluate("feature-a", BAD_CONTEXT, default="d")
        finally:
            core._release()

        assert observed.value == baseline.value
        assert observed.reason is baseline.reason
        # The one un-buildable notification is skipped, not half-fired.
        assert events == []

    def test_flag_not_found_detail_is_identical_with_and_without_an_inspector(self) -> None:
        baseline_core = _make_core([])
        try:
            baseline = baseline_core.evaluate("missing", BAD_CONTEXT, default=True)
        finally:
            baseline_core._release()

        events: list[EvaluationEvent] = []
        core = _make_core([events.append])
        try:
            observed = core.evaluate("missing", BAD_CONTEXT, default=True)
        finally:
            core._release()

        assert observed.value is baseline.value
        assert observed.reason is baseline.reason
        assert observed.reason is EvaluationReason.FLAG_NOT_FOUND
        assert events == []

    def test_client_variation_returns_the_evaluated_value_not_the_default(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_stub_core([events.append])
        client = _client_for(core)
        try:
            # Would return the `default` before the guard: the ValueError from
            # dict(BAD_CONTEXT) escaped evaluate() into variation()'s blanket
            # except, silently downgrading the caller.
            assert client.variation("feature-a", BAD_CONTEXT, default="d") == "variant-1"
        finally:
            client.close()
        assert events == []

    def test_client_variation_detail_does_not_raise(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_stub_core([events.append])
        client = _client_for(core)
        try:
            # variation_detail has no try/except, so before the guard this
            # raised ValueError straight into user code.
            detail = client.variation_detail("feature-a", BAD_CONTEXT, default="d")
            assert detail.value == "variant-1"
            assert detail.reason is EvaluationReason.FALLTHROUGH
        finally:
            client.close()
        assert events == []

    def test_logs_the_event_build_failure(self) -> None:
        core = _make_core([lambda _event: None])
        try:
            with patch.object(core_module, "logger") as logger:
                core.evaluate("missing", BAD_CONTEXT, default=True)

            logger.warning.assert_called_once_with(
                "inspector_event_build_error",
                key="missing",
                error="dictionary update sequence element #0 has length 7; 2 is required",
            )
        finally:
            core._release()

    def test_siblings_and_later_evaluations_still_work_after_a_build_failure(self) -> None:
        first: list[EvaluationEvent] = []
        second: list[EvaluationEvent] = []
        core = _make_core([first.append, second.append])
        try:
            core.evaluate("flag-on", BAD_CONTEXT, default=False)
            assert first == []
            assert second == []

            # The core is not left in a poisoned state: a well-formed
            # evaluation afterwards fires every inspector exactly once.
            detail = core.evaluate("flag-on", {"user_id": "bob"}, default=False)
            assert detail.value is True
            assert len(first) == 1
            assert len(second) == 1
            assert first[0].flag_key == "flag-on"
            assert first[0].reason is EvaluationReason.FALLTHROUGH
        finally:
            core._release()

    def test_a_context_of_none_still_builds_an_empty_event_context(self) -> None:
        events: list[EvaluationEvent] = []
        core = _make_stub_core([events.append])
        try:
            detail = core.evaluate("feature-a", None, default="d")  # type: ignore[arg-type]
            assert detail.value == "variant-1"
            assert len(events) == 1
            assert events[0].context == {}
        finally:
            core._release()


class TestForTestingInspectors:
    """``for_testing`` must be able to carry inspectors.

    The notify itself is reachable on the stub paths (see
    ``test_fires_on_the_test_stub_paths``), but before this the factory built
    its core with a bare ``Config()`` — so a user who registered an inspector in
    production had no way to get one onto a stub client and saw zero events when
    unit-testing code that depends on inspector side effects. Optional trailing
    argument, mirroring the PHP SDK's ``forTesting(array $flags, array $inspectors = [])``.
    """

    def test_an_inspector_passed_to_for_testing_actually_fires(self) -> None:
        events: list[EvaluationEvent] = []
        client = FeatureflipClient.for_testing(
            {"feature-a": "variant-1"}, inspectors=[events.append]
        )
        try:
            assert client.variation("feature-a", {"user_id": "bob"}, default="d") == "variant-1"

            assert len(events) == 1
            assert events[0].flag_key == "feature-a"
            assert events[0].value == "variant-1"
            assert events[0].reason is EvaluationReason.FALLTHROUGH
            assert events[0].context == {"user_id": "bob"}
            assert ISO_TIMESTAMP.match(events[0].timestamp)
        finally:
            client.close()

    def test_fires_on_the_stub_miss_path_too(self) -> None:
        events: list[EvaluationEvent] = []
        client = FeatureflipClient.for_testing({"feature-a": True}, inspectors=[events.append])
        try:
            assert client.variation("missing", {"user_id": "bob"}, default=False) is False

            assert len(events) == 1
            assert events[0].flag_key == "missing"
            assert events[0].reason is EvaluationReason.FLAG_NOT_FOUND
        finally:
            client.close()

    def test_every_inspector_fires_and_a_raising_one_is_isolated(self) -> None:
        seen: list[EvaluationEvent] = []

        def boom(_event: EvaluationEvent) -> None:
            raise RuntimeError("inspector boom")

        client = FeatureflipClient.for_testing(
            {"feature-a": True}, inspectors=[boom, seen.append]
        )
        try:
            assert client.variation("feature-a", {"user_id": "bob"}, default=False) is True
            assert len(seen) == 1
        finally:
            client.close()

    def test_for_testing_without_inspectors_is_unchanged(self) -> None:
        # The pre-existing call shape: positional flags only, no second arg.
        client = FeatureflipClient.for_testing({"feature-a": True, "feature-b": "variant-1"})
        try:
            assert client._core._inspectors == []
            assert client.variation("feature-a", {}, default=False) is True
            assert client.variation("feature-b", {}, default="d") == "variant-1"
            assert client.variation("missing", {}, default="fallback") == "fallback"
        finally:
            client.close()

    def test_an_explicit_none_behaves_like_omitting_the_argument(self) -> None:
        client = FeatureflipClient.for_testing({"feature-a": True}, inspectors=None)
        try:
            assert client._core._inspectors == []
            assert client.variation("feature-a", {}, default=False) is True
        finally:
            client.close()

    def test_core_stub_factory_threads_inspectors_through(self) -> None:
        events: list[EvaluationEvent] = []
        core = _SharedFeatureflipCore._create_for_testing_stub(
            {"feature-a": True}, [events.append]
        )
        try:
            assert core.evaluate("feature-a", {"user_id": "bob"}, default=False).value is True
            assert len(events) == 1
        finally:
            core._release()

    def test_core_stub_factory_without_inspectors_is_unchanged(self) -> None:
        core = _SharedFeatureflipCore._create_for_testing_stub({"feature-a": True})
        try:
            assert core._inspectors == []
            assert core.evaluate("feature-a", {}, default=False).value is True
        finally:
            core._release()


class TestConfigInspectors:
    def test_defaults_to_an_empty_list(self) -> None:
        assert Config().inspectors == []

    def test_each_config_gets_its_own_list(self) -> None:
        a = Config()
        b = Config()
        a.inspectors.append(lambda _event: None)
        assert b.inspectors == []
