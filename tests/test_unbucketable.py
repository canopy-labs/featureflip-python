"""Tests for the warn-once dedup helper (#4236)."""

import pytest
from structlog.testing import capture_logs

from featureflip import _unbucketable


@pytest.fixture(autouse=True)
def _isolated_seen() -> None:  # type: ignore[misc]
    saved = set(_unbucketable._seen)
    _unbucketable._seen.clear()
    yield  # type: ignore[misc]
    _unbucketable._seen.clear()
    _unbucketable._seen.update(saved)


def test_dedup_caps_at_1000_and_drops_silently() -> None:
    with capture_logs() as logs:
        for i in range(1000):
            _unbucketable.warn_unbucketable(f"cap-flag-{i}", "r1", "userId")
        before = len(logs)
        _unbucketable.warn_unbucketable("cap-flag-new-1001", "r1", "userId")
        _unbucketable.warn_unbucketable("cap-flag-new-1002", None, "userId")
    assert len(logs) == before
    assert len(_unbucketable._seen) == 1000
    assert not any(e.get("flag_key") == "cap-flag-new-1001" for e in logs)
