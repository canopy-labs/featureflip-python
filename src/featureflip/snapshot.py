"""Snapshot store: start from a last-known config when the Evaluation API is down.

``SnapshotStore`` is an opaque text store. The SDK owns the stored text end to
end (envelope, environment check, age check and every startup reason), so a
store only has to hand back exactly what it was last given. One store instance
may safely be given to several SDK keys: the SDK rejects text written for a
different SDK key or base URL itself.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

import structlog

from featureflip._http import parse_wire_payload

if TYPE_CHECKING:
    from featureflip.models import FlagConfiguration, Segment

logger = structlog.get_logger()

__all__ = ["FileSnapshotStore", "SnapshotStore", "StartupInfo"]

#: Why a flag was served from persisted data rather than a live fetch.
REASON_SNAPSHOT = "snapshot"
REASON_EXPIRED = "snapshot-expired"
REASON_INVALID = "snapshot-invalid"

#: How far in the future a stored ``fetchedAt`` may be (clock skew between the
#: writer and the reader) and still be accepted, with its age clamped to 0.
FUTURE_FETCHED_AT_TOLERANCE_MS = 60_000

#: While live, an unchanged state is rewritten (refreshing ``fetchedAt``) at most
#: this often. Changed content is never throttled.
SNAPSHOT_REFRESH_INTERVAL_SECONDS = 300.0


class SnapshotStore(Protocol):
    """Pluggable persistence for the last good config snapshot.

    Both methods are synchronous (the SDK is threaded) and may be called from a
    background thread. A store that raises is handled: a failing ``get`` is a
    miss and a failing ``put`` is a dropped write, each with a logged warning.
    """

    def get(self) -> str | None:
        """Return the text last stored, or ``None`` if nothing is stored."""
        ...

    def put(self, text: str) -> None:
        """Store the text, replacing any previous value."""
        ...


@dataclass(frozen=True)
class StartupInfo:
    """Where the client's flag data came from at startup.

    Attributes:
        source: ``"live"`` after a successful fetch from the Evaluation API,
            ``"snapshot"`` when started from the snapshot store, ``"none"`` when
            there is no data yet (caller defaults are served).
        reason: ``"snapshot"`` for a snapshot start, ``"snapshot-expired"`` or
            ``"snapshot-invalid"`` when a stored snapshot was ignored. ``None``
            once the client is live, or when nothing was stored.
        snapshot_age_seconds: Age of the snapshot that was used or rejected as
            expired, in whole seconds.
    """

    source: Literal["live", "snapshot", "none"]
    reason: str | None = None
    snapshot_age_seconds: float | None = None


class SnapshotInvalidError(Exception):
    """Stored text failed a check. ``why`` names it, never the contents or key."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


@dataclass(frozen=True)
class DecodedSnapshot:
    """A validated snapshot, already parsed into models."""

    flags: list[FlagConfiguration]
    segments: list[Segment]
    fetched_at_ms: int


def compute_key_hash(base_url: str, sdk_key: str) -> str:
    """Identify the (base URL, SDK key) pair without storing the key."""
    return hashlib.sha256(f"{base_url.rstrip('/')}\n{sdk_key}".encode()).hexdigest()


def encode_envelope(key_hash: str, fetched_at_ms: int, data_text: str) -> str:
    """The stored text, from an already serialized ``data``."""
    return (
        f'{{"v":1,"keyHash":{json.dumps(key_hash)},'
        f'"fetchedAt":{int(fetched_at_ms)},"data":{data_text}}}'
    )


def _positive_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    return None


def decode_envelope(text: Any, key_hash: str, now_ms: int) -> DecodedSnapshot:
    """Parse and check stored text. Age is not checked here.

    Raises:
        SnapshotInvalidError: for any failed check, in the pinned order.
    """
    if not isinstance(text, str):
        raise SnapshotInvalidError("store returned a non-string value")
    try:
        env = json.loads(text)
    except Exception:
        raise SnapshotInvalidError(
            "empty text" if text == "" else "not valid JSON"
        ) from None
    if not isinstance(env, dict):
        raise SnapshotInvalidError("not a snapshot envelope")
    version = env.get("v")
    if isinstance(version, bool) or not isinstance(version, (int, float)) or version != 1:
        raise SnapshotInvalidError("unsupported envelope version")
    if env.get("keyHash") != key_hash:
        raise SnapshotInvalidError("written for a different SDK key or base URL")
    fetched_at = _positive_integer(env.get("fetchedAt"))
    if fetched_at is None:
        raise SnapshotInvalidError("fetchedAt is not a positive integer")
    if fetched_at > now_ms + FUTURE_FETCHED_AT_TOLERANCE_MS:
        raise SnapshotInvalidError("fetchedAt is in the future")
    data = env.get("data")
    if not isinstance(data, dict):
        raise SnapshotInvalidError("missing data")
    try:
        if not isinstance(data.get("flags"), list) or not isinstance(
            data.get("segments"), list
        ):
            raise ValueError("flags and segments must be arrays")
        flags, segments = parse_wire_payload(data)
    except Exception:
        raise SnapshotInvalidError("data failed payload validation") from None
    return DecodedSnapshot(flags=flags, segments=segments, fetched_at_ms=fetched_at)


class FileSnapshotStore:
    """Persist the snapshot text to one file (mode 0600), written atomically.

    The store is opaque: it neither parses nor checks what it holds, so one
    instance can be shared by several SDK keys. Never raises: a failure is logged
    and treated as a miss (``get``) or a dropped write (``put``). The parent
    directory must already exist.

    There is deliberately no fsync before the rename, so a power loss can leave a
    zero-length or partial file. The SDK reads that as ``snapshot-invalid``,
    logs a warning and serves defaults, and the next successful write replaces it.

    A write runs on a daemon thread, so a process killed mid-write can leave a
    ``<name>.<pid>.<hex>.tmp`` file next to the snapshot. It is never read.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def get(self) -> str | None:
        try:
            return self._path.read_bytes().decode("utf-8", errors="replace")
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.warning(
                "snapshot_file_read_failed", path=str(self._path), error=str(e)
            )
            return None

    def put(self, text: str) -> None:
        tmp = self._path.with_name(
            f"{self._path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        )
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(text.encode("utf-8"))
                f.flush()
            tmp.replace(self._path)
        except Exception as e:
            logger.warning(
                "snapshot_file_write_failed", path=str(self._path), error=str(e)
            )
            with contextlib.suppress(Exception):
                tmp.unlink(missing_ok=True)
