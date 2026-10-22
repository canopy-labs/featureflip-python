"""Warn-once reporting for a percentage rollout that skipped a context it
could not bucket (#4236). Logs only the flag key, the rule id (or
``fallthrough``) and the bucket-by attribute NAME, never a context value.
"""

from __future__ import annotations

import threading

import structlog

logger = structlog.get_logger()

_MAX_KEYS = 1000
_seen: set[tuple[str, str, str]] = set()
_lock = threading.Lock()


def warn_unbucketable(flag_key: str, rule_id: str | None, bucket_by: str) -> None:
    """Log once per (flag_key, rule id or "fallthrough", bucket_by) per process.

    The set is capped; once full, unseen keys are dropped silently.
    """
    where = "fallthrough" if rule_id is None else f"rule {rule_id}"
    key = (flag_key, rule_id if rule_id is not None else "fallthrough", bucket_by)
    with _lock:
        if key in _seen or len(_seen) >= _MAX_KEYS:
            return
        _seen.add(key)
    logger.warning(
        "rollout_unbucketable",
        flag_key=flag_key,
        where=where,
        bucket_by=bucket_by,
    )
