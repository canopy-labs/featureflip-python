"""Unit tests for _SharedFeatureflipCore refcount semantics."""

from __future__ import annotations

from featureflip._core import _SharedFeatureflipCore
from featureflip.detail import EvaluationReason
from featureflip.models import (
    FlagConfiguration,
    FlagType,
    ServeConfig,
    ServeType,
    Variation,
)


class TestRefcountSkeleton:
    def test_new_core_starts_at_refcount_one(self) -> None:
        core = _SharedFeatureflipCore._create_for_testing_skeleton()
        try:
            assert core._ref_count == 1
        finally:
            core._release()

    def test_acquire_increments_refcount(self) -> None:
        core = _SharedFeatureflipCore._create_for_testing_skeleton()
        try:
            assert core._acquire() is True
            assert core._ref_count == 2
        finally:
            core._release()
            core._release()

    def test_release_decrements_refcount(self) -> None:
        core = _SharedFeatureflipCore._create_for_testing_skeleton()
        core._acquire()  # refcount = 2
        core._release()  # refcount = 1
        assert core._ref_count == 1
        core._release()  # refcount = 0, core shuts down

    def test_release_at_zero_marks_core_shut_down(self) -> None:
        core = _SharedFeatureflipCore._create_for_testing_skeleton()
        core._release()  # 1 -> 0
        assert core._is_shut_down is True

    def test_acquire_after_shutdown_returns_false(self) -> None:
        core = _SharedFeatureflipCore._create_for_testing_skeleton()
        core._release()  # shut down
        assert core._acquire() is False
        assert core._ref_count == 0

    def test_acquire_after_over_release_returns_false(self) -> None:
        """Regression test: over-release must not produce a phantom successful acquire."""
        core = _SharedFeatureflipCore._create_for_testing_skeleton()
        core._release()  # 1 -> 0, shut down
        core._release()  # spurious extra release — should be a no-op
        assert core._acquire() is False
        assert core._is_shut_down is True


class TestCoreEvaluate:
    def test_evaluate_flag_not_found_returns_default(self) -> None:
        core = _SharedFeatureflipCore._create_for_testing_with_flags({})
        try:
            detail = core.evaluate("nonexistent", {"user_id": "u1"}, default=True)
            assert detail.value is True
            assert detail.reason == EvaluationReason.FLAG_NOT_FOUND
        finally:
            core._release()

    def test_evaluate_flag_exists_returns_evaluated_value(self) -> None:
        flag = _make_bool_flag("my-flag", enabled=True, variation="on")
        core = _SharedFeatureflipCore._create_for_testing_with_flags({flag.key: flag})
        try:
            detail = core.evaluate("my-flag", {"user_id": "u1"}, default=False)
            assert detail.value is True
        finally:
            core._release()

    def test_evaluate_in_test_stub_mode_returns_fixed_value(self) -> None:
        core = _SharedFeatureflipCore._create_for_testing_stub({"feature-a": True, "feature-b": "v1"})
        try:
            assert core.evaluate("feature-a", {}, default=False).value is True
            assert core.evaluate("feature-b", {}, default="default").value == "v1"
            assert core.evaluate("missing", {}, default="fallback").value == "fallback"
        finally:
            core._release()


def _make_bool_flag(key: str, enabled: bool, variation: str) -> FlagConfiguration:
    return FlagConfiguration(
        key=key,
        version=1,
        type=FlagType.BOOLEAN,
        enabled=enabled,
        variations=[
            Variation(key="on", value=True),
            Variation(key="off", value=False),
        ],
        rules=[],
        fallthrough=ServeConfig(type=ServeType.FIXED, variation=variation),
        off_variation="off",
    )
