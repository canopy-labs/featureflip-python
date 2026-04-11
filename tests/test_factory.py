"""Unit tests for _get_or_create_core dedupe semantics."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

from featureflip._core import (
    _LIVE_CORES,
    _get_or_create_core_with_stub,
    _reset_for_testing,
    _SharedFeatureflipCore,
)

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _reset_cache_each_test() -> Generator[None, None, None]:
    """Ensure a clean _LIVE_CORES map before and after every test."""
    _reset_for_testing()
    yield
    _reset_for_testing()


class TestFactoryDedupe:
    def test_first_call_returns_core(self) -> None:
        """Use a test-stub factory to avoid network entirely."""
        core = _get_or_create_core_with_stub("sdk-key-first")
        try:
            assert core is not None
            assert core._ref_count == 1
            assert len(_LIVE_CORES) == 1
        finally:
            core._release()

    def test_same_key_twice_shares_one_core(self) -> None:
        c1 = _get_or_create_core_with_stub("sdk-key-same")
        c2 = _get_or_create_core_with_stub("sdk-key-same")
        try:
            assert c1 is c2
            assert c1._ref_count == 2
            assert len(_LIVE_CORES) == 1
        finally:
            c1._release()
            c2._release()

    def test_different_keys_create_independent_cores(self) -> None:
        c1 = _get_or_create_core_with_stub("sdk-key-a")
        c2 = _get_or_create_core_with_stub("sdk-key-b")
        try:
            assert c1 is not c2
            assert len(_LIVE_CORES) == 2
        finally:
            c1._release()
            c2._release()

    def test_close_only_handle_removes_from_cache(self) -> None:
        c1 = _get_or_create_core_with_stub("sdk-key-recycle")
        c1._release()  # refcount 1 -> 0, shutdown, remove from cache
        assert len(_LIVE_CORES) == 0

        # Next call creates a fresh core
        c2 = _get_or_create_core_with_stub("sdk-key-recycle")
        try:
            assert len(_LIVE_CORES) == 1
        finally:
            c2._release()

    def test_release_one_of_two_handles_keeps_core_alive(self) -> None:
        c1 = _get_or_create_core_with_stub("sdk-key-twohandles")
        c2 = _get_or_create_core_with_stub("sdk-key-twohandles")
        try:
            c1._release()
            assert c2._ref_count == 1
            assert len(_LIVE_CORES) == 1
        finally:
            c2._release()


class TestFactoryRaces:
    def test_concurrent_same_key_all_share_one_core(self) -> None:
        """32 concurrent _get_or_create_core_with_stub calls must share one core."""
        thread_count = 32
        barrier = threading.Barrier(thread_count)
        results: list[_SharedFeatureflipCore | None] = [None] * thread_count

        def worker(idx: int) -> None:
            barrier.wait()  # line all threads up at the gate
            results[idx] = _get_or_create_core_with_stub("sdk-key-concurrent")

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(thread_count)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        try:
            # All 32 threads got the same core reference
            first = results[0]
            assert first is not None
            for r in results:
                assert r is first

            # Exactly one entry in the cache
            assert len(_LIVE_CORES) == 1

            # Refcount equals the number of threads that acquired
            assert first._ref_count == thread_count
        finally:
            # Release 32 times to fully shut down
            if results[0] is not None:
                for _ in range(thread_count):
                    results[0]._release()
