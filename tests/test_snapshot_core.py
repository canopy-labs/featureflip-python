"""Core-level tests for the snapshot store: startup, persistence and refresh."""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import httpx
import pytest
import structlog

from featureflip import Config, FeatureflipClient, FileSnapshotStore, StartupInfo
from featureflip import _core as core_module
from featureflip._core import _reset_for_testing
from featureflip._http import parse_wire_payload
from featureflip.exceptions import InitializationError
from featureflip.snapshot import compute_key_hash
from tests.test_snapshot_store import make_envelope, payload

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

BASE = "https://eval.example.com"
KEY = "sdk-key-1"


@pytest.fixture(autouse=True)
def _reset() -> Generator[None, None, None]:
    _reset_for_testing()
    yield
    _reset_for_testing()


def wait_until(pred: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


class MemoryStore:
    def __init__(self, text: Any = None) -> None:
        self.text = text
        self.puts: list[str] = []
        self.get_calls = 0
        self.get_impl: Callable[[], Any] | None = None
        self.put_error: Exception | None = None
        self.put_gate: threading.Event | None = None

    def get(self) -> Any:
        self.get_calls += 1
        if self.get_impl is not None:
            return self.get_impl()
        return self.text

    def put(self, text: str) -> None:
        if self.put_error is not None:
            raise self.put_error
        self.puts.append(text)
        self.text = text
        if self.put_gate is not None:
            self.put_gate.wait(5)


class Clock:
    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall_ms = 1_800_000_000_000

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall_ms += int(seconds * 1000)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(core_module, "_monotonic", lambda: c.mono)
    monkeypatch.setattr(core_module, "_wall_ms", lambda: c.wall_ms)
    return c


def flags_and_segments() -> tuple[list[Any], list[Any]]:
    return parse_wire_payload(payload())


def cfg(store: Any = None, **kw: Any) -> Config:
    kw.setdefault("base_url", BASE)
    kw.setdefault("streaming", False)
    kw.setdefault("send_events", False)
    kw.setdefault("poll_interval", 999.0)
    kw.setdefault("init_timeout", 1.0)
    return Config(snapshot_store=store, **kw)


def envelope_for(
    *, key: str = KEY, base: str = BASE, fetched_at: int | None = None, **extra: Any
) -> str:
    if fetched_at is None:
        fetched_at = int(time.time() * 1000) - 5_000
    return make_envelope(
        keyHash=compute_key_hash(base, key), fetchedAt=fetched_at, **extra
    )


def client_with_fetch(
    config: Config, fetch: Callable[[], Any], key: str = KEY
) -> FeatureflipClient:
    with patch("featureflip._core.HttpClient") as http:
        http.return_value.get_flags.side_effect = fetch
        return FeatureflipClient(sdk_key=key, config=config)


def failing(exc: Exception) -> Callable[[], Any]:
    def fetch() -> Any:
        raise exc

    return fetch


class TestHealthyStart:
    def test_writes_envelope_and_never_reads(self) -> None:
        store = MemoryStore()
        client = client_with_fetch(cfg(store), flags_and_segments)
        try:
            assert wait_until(lambda: len(store.puts) >= 1)
            assert store.get_calls == 0
            env = json.loads(store.puts[0])
            assert env["v"] == 1
            assert env["keyHash"] == compute_key_hash(BASE, KEY)
            assert isinstance(env["fetchedAt"], int)
            assert env["data"] == payload()
            assert client.startup_info == StartupInfo("live")
        finally:
            client.close()

    def test_no_store_is_silent_and_live(self) -> None:
        with structlog.testing.capture_logs() as logs:
            client = client_with_fetch(cfg(None), flags_and_segments)
        try:
            assert client.startup_info == StartupInfo("live")
            assert not [log for log in logs if "snapshot" in str(log)]
        finally:
            client.close()

    def test_no_store_failure_still_raises(self) -> None:
        with pytest.raises(InitializationError):
            client_with_fetch(cfg(None), failing(RuntimeError("down")))


class TestStartupFallback:
    def test_fetch_error_falls_back_to_snapshot(self) -> None:
        store = MemoryStore(envelope_for())
        client = client_with_fetch(cfg(store), failing(RuntimeError("down")))
        try:
            info = client.startup_info
            assert info.source == "snapshot"
            assert info.reason == "snapshot"
            assert info.snapshot_age_seconds is not None
            assert 4 <= info.snapshot_age_seconds <= 30
            assert client.is_initialized is True
            # loading a snapshot never writes
            time.sleep(0.05)
            assert store.puts == []
        finally:
            client.close()

    def test_snapshot_serves_real_values(self) -> None:
        store = MemoryStore(envelope_for())
        client = client_with_fetch(cfg(store), failing(RuntimeError("down")))
        try:
            # "base" is disabled and serves its off variation, a JSON value
            assert client.variation("base", {}, "dflt") == {"a": [1, 2.5, None]}
            assert client.variation("missing", {}, "dflt") == "dflt"
        finally:
            client.close()

    def test_init_timeout_falls_back(self) -> None:
        store = MemoryStore(envelope_for())

        def slow() -> Any:
            time.sleep(1.5)
            return flags_and_segments()

        started = time.monotonic()
        client = client_with_fetch(cfg(store, init_timeout=0.2), slow)
        try:
            assert time.monotonic() - started < 1.2
            assert client.startup_info.source == "snapshot"
        finally:
            client.close()

    def test_warning_names_http_status_not_the_key(self) -> None:
        store = MemoryStore(envelope_for())
        response = httpx.Response(401, request=httpx.Request("GET", BASE))
        err = httpx.HTTPStatusError("nope", request=response.request, response=response)
        with structlog.testing.capture_logs() as logs:
            client = client_with_fetch(cfg(store), failing(err))
        try:
            text = json.dumps(logs, default=str)
            assert "HTTP 401" in text
            assert KEY not in text
        finally:
            client.close()

    def test_warning_names_timeout(self) -> None:
        store = MemoryStore(envelope_for())

        def slow() -> Any:
            time.sleep(0.6)
            return flags_and_segments()

        with structlog.testing.capture_logs() as logs:
            client = client_with_fetch(cfg(store, init_timeout=0.1), slow)
        try:
            assert "timed out after 0.1s" in json.dumps(logs, default=str)
        finally:
            client.close()

    def test_miss_is_plain_none_and_logs_the_cause(self) -> None:
        store = MemoryStore(None)
        with structlog.testing.capture_logs() as logs:
            client = client_with_fetch(cfg(store), failing(RuntimeError("down")))
        try:
            assert client.startup_info == StartupInfo("none")
            assert client.is_initialized is True
            warnings = [log for log in logs if log["event"] == "no_snapshot_stored"]
            assert len(warnings) == 1
            assert warnings[0]["message"] == (
                "initial flag fetch failed (down); no snapshot stored; "
                "serving defaults"
            )
            assert KEY not in json.dumps(logs, default=str)
        finally:
            client.close()

    def test_miss_with_http_status_names_status_not_the_key(self) -> None:
        store = MemoryStore(None)
        response = httpx.Response(503, request=httpx.Request("GET", BASE))
        err = httpx.HTTPStatusError("x", request=response.request, response=response)
        with structlog.testing.capture_logs() as logs:
            client = client_with_fetch(cfg(store), failing(err))
        try:
            text = json.dumps(logs, default=str)
            assert "HTTP 503); no snapshot stored" in text
            assert KEY not in text
        finally:
            client.close()

    def test_miss_with_timeout_names_the_timeout(self) -> None:
        store = MemoryStore(None)

        def slow() -> Any:
            time.sleep(0.6)
            return flags_and_segments()

        with structlog.testing.capture_logs() as logs:
            client = client_with_fetch(cfg(store, init_timeout=0.1), slow)
        try:
            assert "timed out after 0.1s); no snapshot stored" in json.dumps(
                logs, default=str
            )
        finally:
            client.close()

    def test_healthy_start_with_store_stays_quiet(self) -> None:
        store = MemoryStore(None)
        with structlog.testing.capture_logs() as logs:
            client = client_with_fetch(cfg(store), flags_and_segments)
        try:
            assert [log for log in logs if log["log_level"] == "warning"] == []
        finally:
            client.close()

    def test_expired_snapshot_reports_reason_and_age(self) -> None:
        old = int(time.time() * 1000) - 7_200_000
        store = MemoryStore(envelope_for(fetched_at=old))
        client = client_with_fetch(
            cfg(store, max_snapshot_age_seconds=3600), failing(RuntimeError("down"))
        )
        try:
            info = client.startup_info
            assert info.source == "none"
            assert info.reason == "snapshot-expired"
            assert info.snapshot_age_seconds is not None
            assert 7190 <= info.snapshot_age_seconds <= 7230
            assert client.variation("checkout", {}, "dflt") == "dflt"
        finally:
            client.close()

    def test_within_max_age_is_used(self) -> None:
        old = int(time.time() * 1000) - 7_200_000
        store = MemoryStore(envelope_for(fetched_at=old))
        client = client_with_fetch(
            cfg(store, max_snapshot_age_seconds=10_000), failing(RuntimeError("x"))
        )
        try:
            assert client.startup_info.source == "snapshot"
        finally:
            client.close()

    def test_future_tolerance(self) -> None:
        now = int(time.time() * 1000)
        ok = MemoryStore(envelope_for(fetched_at=now + 30_000))
        client = client_with_fetch(cfg(ok), failing(RuntimeError("x")))
        try:
            assert client.startup_info.source == "snapshot"
            assert client.startup_info.snapshot_age_seconds == 0
        finally:
            client.close()
        _reset_for_testing()
        bad = MemoryStore(envelope_for(fetched_at=now + 120_000))
        client = client_with_fetch(cfg(bad), failing(RuntimeError("x")))
        try:
            assert client.startup_info == StartupInfo("none", "snapshot-invalid")
        finally:
            client.close()

    def test_non_string_store_value_is_invalid(self) -> None:
        store = MemoryStore()
        store.get_impl = lambda: b"{}"
        client = client_with_fetch(cfg(store), failing(RuntimeError("x")))
        try:
            assert client.startup_info == StartupInfo("none", "snapshot-invalid")
        finally:
            client.close()

    def test_empty_text_is_invalid(self) -> None:
        client = client_with_fetch(
            cfg(MemoryStore("")), failing(RuntimeError("x"))
        )
        try:
            assert client.startup_info == StartupInfo("none", "snapshot-invalid")
        finally:
            client.close()


class TestRealFileStore:
    @pytest.mark.parametrize(
        "text",
        [
            "garbage",
            "truncated",
            "other-key",
            "other-base",
            "v2",
            "future",
        ],
    )
    def test_invalid_files(self, tmp_path: Path, text: str) -> None:
        good = envelope_for()
        now = int(time.time() * 1000)
        contents = {
            "garbage": "\x00not json at all",
            "truncated": good[: len(good) // 2],
            "other-key": envelope_for(key="a-different-key"),
            "other-base": envelope_for(base="https://elsewhere.example.com"),
            "v2": envelope_for(v=2),
            "future": envelope_for(fetched_at=now + 600_000),
        }[text]
        path = tmp_path / "snap.json"
        path.write_text(contents)
        client = client_with_fetch(
            cfg(FileSnapshotStore(path)), failing(RuntimeError("down"))
        )
        try:
            assert client.startup_info == StartupInfo("none", "snapshot-invalid")
        finally:
            client.close()

    def test_valid_file_starts_from_snapshot(self, tmp_path: Path) -> None:
        path = tmp_path / "snap.json"
        path.write_text(envelope_for())
        client = client_with_fetch(
            cfg(FileSnapshotStore(path)), failing(RuntimeError("down"))
        )
        try:
            assert client.startup_info.source == "snapshot"
        finally:
            client.close()

    def test_missing_file_is_a_plain_miss(self, tmp_path: Path) -> None:
        client = client_with_fetch(
            cfg(FileSnapshotStore(tmp_path / "none.json")),
            failing(RuntimeError("down")),
        )
        try:
            assert client.startup_info == StartupInfo("none")
        finally:
            client.close()

    def test_directory_path_is_a_miss(self, tmp_path: Path) -> None:
        client = client_with_fetch(
            cfg(FileSnapshotStore(tmp_path)), failing(RuntimeError("down"))
        )
        try:
            assert client.startup_info == StartupInfo("none")
        finally:
            client.close()

    def test_one_store_shared_by_two_sdk_keys_never_leaks(
        self, tmp_path: Path
    ) -> None:
        store = FileSnapshotStore(tmp_path / "shared.json")
        a = client_with_fetch(cfg(store), flags_and_segments, key="key-a")
        try:
            assert wait_until(lambda: (tmp_path / "shared.json").exists())
            assert wait_until(
                lambda: compute_key_hash(BASE, "key-a")
                in (tmp_path / "shared.json").read_text()
            )
        finally:
            a.close()
        b = client_with_fetch(cfg(store), failing(RuntimeError("down")), key="key-b")
        try:
            assert b.startup_info == StartupInfo("none", "snapshot-invalid")
            assert b.variation("checkout", {}, "dflt") == "dflt"
        finally:
            b.close()


class TestStoreFailures:
    def test_hung_get_is_bounded_by_init_timeout(self) -> None:
        store = MemoryStore()
        release = threading.Event()

        def hung() -> Any:
            release.wait(5)
            return envelope_for()

        store.get_impl = hung
        started = time.monotonic()
        client = client_with_fetch(
            cfg(store, init_timeout=0.2), failing(RuntimeError("down"))
        )
        try:
            assert time.monotonic() - started < 1.5
            assert client.is_initialized is True
            assert client.startup_info == StartupInfo("none")
            release.set()
            time.sleep(0.15)
            # the late result is ignored
            assert client.startup_info == StartupInfo("none")
            assert client.variation("checkout", {}, "dflt") == "dflt"
        finally:
            release.set()
            client.close()

    def test_throwing_get_is_a_miss_with_warning(self) -> None:
        store = MemoryStore()

        def boom() -> Any:
            raise RuntimeError("redis down")

        store.get_impl = boom
        with structlog.testing.capture_logs() as logs:
            client = client_with_fetch(cfg(store), failing(RuntimeError("down")))
        try:
            assert client.startup_info == StartupInfo("none")
            assert "redis down" in json.dumps(logs, default=str)
        finally:
            client.close()

    def test_throwing_put_is_dropped_with_warning(self) -> None:
        store = MemoryStore()
        store.put_error = RuntimeError("disk full")
        with structlog.testing.capture_logs() as logs:
            client = client_with_fetch(cfg(store), flags_and_segments)
            try:
                assert wait_until(lambda: "disk full" in json.dumps(logs, default=str))
                assert client.variation("checkout", {}, "dflt") is not None
                assert client.startup_info == StartupInfo("live")
            finally:
                client.close()


def live_recovery_state() -> tuple[list[Any], list[Any]]:
    """Live copy of the fixture with "base" enabled and bumped."""
    live_payload = payload()
    live_payload["flags"][1]["enabled"] = True
    live_payload["flags"][1]["version"] = 2
    return parse_wire_payload(live_payload)


class TestRecovery:
    def test_snapshot_start_then_live_recovery_at_core_level(self) -> None:
        """Deterministic: the data source is stubbed, the live apply is driven."""
        store = MemoryStore(envelope_for())
        live_flags, live_segments = live_recovery_state()
        with patch.object(
            core_module._SharedFeatureflipCore, "_start_polling", lambda _self: None
        ):
            core = snapshot_started_core(store)
        try:
            assert core.startup_info.source == "snapshot"
            changed: list[list[str]] = []
            core.on_update(changed.append)
            assert store.puts == []
            core._on_polling_update(live_flags, live_segments)
            assert core.startup_info == StartupInfo("live")
            assert len(changed) == 1
            assert "base" in changed[0]
            assert wait_until(lambda: len(store.puts) >= 1)
            written = json.loads(store.puts[0])
            assert written["data"]["flags"][1]["enabled"] is True
        finally:
            core._release()

    def test_snapshot_start_then_live_recovery_end_to_end(self) -> None:
        """The second fetch blocks, so the snapshot state is observable first."""
        store = MemoryStore(envelope_for())
        live_flags, live_segments = live_recovery_state()
        release = threading.Event()
        calls = {"n": 0}

        def fetch() -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("down")
            release.wait(5)
            return live_flags, live_segments

        with patch("featureflip._core.HttpClient") as http:
            http.return_value.get_flags.side_effect = fetch
            client = FeatureflipClient(sdk_key=KEY, config=cfg(store, poll_interval=0.05))
        try:
            assert client.startup_info.source == "snapshot"
            assert store.puts == []
            release.set()
            assert wait_until(lambda: client.startup_info == StartupInfo("live"))
            assert wait_until(lambda: len(store.puts) >= 1)
            assert json.loads(store.puts[0])["data"]["flags"][1]["enabled"] is True
        finally:
            release.set()
            client.close()

    def test_expired_start_clears_reason_and_age_on_going_live(self) -> None:
        old = int(time.time() * 1000) - 7_200_000
        store = MemoryStore(envelope_for(fetched_at=old))
        with patch.object(
            core_module._SharedFeatureflipCore, "_start_polling", lambda _self: None
        ):
            core = snapshot_started_core(store, max_snapshot_age_seconds=3600)
        try:
            assert core.startup_info.reason == "snapshot-expired"
            assert core.startup_info.snapshot_age_seconds is not None
            f, s = flags_and_segments()
            core._on_polling_update(f, s)
            assert core.startup_info == StartupInfo("live")
        finally:
            core._release()

    def test_snapshot_load_fires_no_listeners(self) -> None:
        store = MemoryStore(envelope_for())
        seen: list[list[str]] = []
        core = snapshot_started_core(store)
        try:
            core.on_update(seen.append)
            assert seen == []
            assert core.startup_info.source == "snapshot"
        finally:
            core._release()


def snapshot_started_core(store: MemoryStore, **kw: Any) -> Any:
    with patch("featureflip._core.HttpClient") as http:
        http.return_value.get_flags.side_effect = failing(RuntimeError("down"))
        from featureflip._core import _SharedFeatureflipCore

        return _SharedFeatureflipCore(KEY, cfg(store, **kw))


def live_core(store: MemoryStore, **kw: Any) -> Any:
    with patch("featureflip._core.HttpClient") as http:
        http.return_value.get_flags.side_effect = flags_and_segments
        from featureflip._core import _SharedFeatureflipCore

        return _SharedFeatureflipCore(KEY, cfg(store, **kw))


class TestSingleFlagPersistence:
    def test_delta_before_first_live_apply_does_not_persist_after_does(
        self, clock: Clock
    ) -> None:
        store = MemoryStore(envelope_for(fetched_at=clock.wall_ms - 5_000))
        core = snapshot_started_core(store)
        try:
            flags, segments = flags_and_segments()
            core._update_single_flag(flags[0])
            core._on_streaming_flag_deleted("base")
            time.sleep(0.1)
            assert store.puts == []
            core._apply_snapshot(flags, segments)
            assert wait_until(lambda: len(store.puts) == 1)
            clock.advance(1)
            core._update_single_flag(flags[1])
            assert wait_until(lambda: len(store.puts) == 2)
            core._on_streaming_flag_deleted("base")
            assert wait_until(lambda: len(store.puts) == 3)
            data = json.loads(store.puts[2])["data"]
            assert [f["key"] for f in data["flags"]] == ["checkout"]
        finally:
            core._release()

    def test_none_start_does_not_persist_deltas(self) -> None:
        store = MemoryStore(None)
        core = snapshot_started_core(store)
        try:
            flags, _ = flags_and_segments()
            core._update_single_flag(flags[0])
            time.sleep(0.1)
            assert store.puts == []
        finally:
            core._release()


class TestCoalescing:
    @pytest.mark.usefixtures("clock")
    def test_one_in_flight_plus_one_trailing(self) -> None:
        store = MemoryStore()
        store.put_gate = threading.Event()
        core = live_core(store)
        try:
            assert wait_until(lambda: len(store.puts) == 1)
            for i in range(5):
                changed = payload()
                changed["flags"][0]["version"] = 10 + i
                f, s = parse_wire_payload(changed)
                core._apply_snapshot(f, s)
            time.sleep(0.1)
            assert len(store.puts) == 1  # first still held
            store.put_gate.set()
            assert wait_until(lambda: len(store.puts) == 2)
            time.sleep(0.15)
            assert len(store.puts) == 2
            # the trailing write read the state when it ran: the latest version
            assert json.loads(store.puts[1])["data"]["flags"][0]["version"] == 14
        finally:
            store.put_gate.set()
            core._release()

    @pytest.mark.usefixtures("clock")
    def test_no_trailing_write_after_close(self) -> None:
        store = MemoryStore()
        store.put_gate = threading.Event()
        core = live_core(store)
        assert wait_until(lambda: len(store.puts) == 1)
        changed = payload()
        changed["flags"][0]["version"] = 99
        f, s = parse_wire_payload(changed)
        core._apply_snapshot(f, s)  # queued behind the held first write
        closer = threading.Thread(target=core._release)
        closer.start()
        closer.join(2)
        assert not closer.is_alive()  # close does not wait for the held write
        store.put_gate.set()
        time.sleep(0.2)
        assert len(store.puts) == 1

    def test_writes_never_run_on_the_calling_thread(self) -> None:
        store = MemoryStore()
        threads: list[str] = []
        original = store.put

        def spy(text: str) -> None:
            threads.append(threading.current_thread().name)
            original(text)

        store.put = spy  # type: ignore[method-assign]
        core = live_core(store)
        try:
            assert wait_until(lambda: len(threads) == 1)
            assert threads[0] != threading.current_thread().name
        finally:
            core._release()


class TestRefresh:
    def test_ping_refreshes_at_most_every_five_minutes(self, clock: Clock) -> None:
        store = MemoryStore()
        core = live_core(store)
        try:
            assert wait_until(lambda: len(store.puts) == 1)
            clock.advance(299)
            core._on_streaming_ping()
            time.sleep(0.1)
            assert len(store.puts) == 1
            clock.advance(2)
            core._on_streaming_ping()
            assert wait_until(lambda: len(store.puts) == 2)
            first = json.loads(store.puts[0])
            second = json.loads(store.puts[1])
            assert second["fetchedAt"] > first["fetchedAt"]
            assert second["data"] == first["data"]
            core._on_streaming_ping()
            time.sleep(0.1)
            assert len(store.puts) == 2
        finally:
            core._release()

    def test_snapshot_started_client_never_refreshes_on_ping(
        self, clock: Clock
    ) -> None:
        store = MemoryStore(envelope_for(fetched_at=clock.wall_ms - 5_000))
        core = snapshot_started_core(store)
        try:
            clock.advance(1000)
            core._on_streaming_ping()
            time.sleep(0.1)
            assert store.puts == []
            f, s = flags_and_segments()
            core._apply_snapshot(f, s)  # first live full apply always writes
            assert wait_until(lambda: len(store.puts) == 1)
        finally:
            core._release()

    def test_polls_skip_unchanged_write_changed_and_refresh_after_window(
        self, clock: Clock
    ) -> None:
        store = MemoryStore()
        core = live_core(store)
        try:
            assert wait_until(lambda: len(store.puts) == 1)
            f, s = flags_and_segments()
            clock.advance(10)
            core._on_polling_update(f, s)  # unchanged inside the window
            time.sleep(0.1)
            assert len(store.puts) == 1
            changed = payload()
            changed["flags"][0]["version"] = 7
            f2, s2 = parse_wire_payload(changed)
            core._on_polling_update(f2, s2)  # changed content is immediate
            assert wait_until(lambda: len(store.puts) == 2)
            clock.advance(301)
            core._on_polling_update(f2, s2)  # unchanged after the window
            assert wait_until(lambda: len(store.puts) == 3)
        finally:
            core._release()


class TestStreamingPing:
    def test_handler_forwards_ping(self) -> None:
        from unittest.mock import MagicMock

        from featureflip._streaming import StreamingHandler

        pings: list[int] = []
        handler = StreamingHandler(
            sdk_key="k",
            config=Config(),
            http_client=MagicMock(),
            on_flag_updated=MagicMock(),
            on_flag_deleted=MagicMock(),
            on_segment_updated=MagicMock(),
            on_error=MagicMock(),
            on_update=MagicMock(),
            on_ping=lambda: pings.append(1),
        )
        handler._handle_event("ping", "")
        assert pings == [1]

    def test_handler_without_on_ping_ignores_ping(self) -> None:
        from unittest.mock import MagicMock

        from featureflip._streaming import StreamingHandler

        handler = StreamingHandler(
            sdk_key="k",
            config=Config(),
            http_client=MagicMock(),
            on_flag_updated=MagicMock(),
            on_flag_deleted=MagicMock(),
            on_segment_updated=MagicMock(),
            on_error=MagicMock(),
            on_update=MagicMock(),
        )
        handler._handle_event("ping", "")


class TestFactoryOptionsIgnored:
    def test_different_store_warns_once_naming_the_option(self) -> None:
        first = MemoryStore()
        second = MemoryStore()
        client = client_with_fetch(cfg(first), flags_and_segments)
        try:
            with structlog.testing.capture_logs() as logs:
                other = FeatureflipClient(sdk_key=KEY, config=cfg(second))
            events = [log for log in logs if log["log_level"] == "warning"]
            assert len(events) == 1
            assert "snapshot_store" in events[0]["message"]
            assert "already exists" in events[0]["message"]
            other.close()
        finally:
            client.close()

    def test_different_max_age_warns_alone(self) -> None:
        store = MemoryStore()
        client = client_with_fetch(cfg(store), flags_and_segments)
        try:
            with structlog.testing.capture_logs() as logs:
                other = FeatureflipClient(
                    sdk_key=KEY, config=cfg(store, max_snapshot_age_seconds=5)
                )
            events = [log for log in logs if log["log_level"] == "warning"]
            assert len(events) == 1
            assert "max_snapshot_age_seconds" in events[0]["message"]
            other.close()
        finally:
            client.close()

    def test_omitted_max_age_on_later_get_is_silent(self) -> None:
        store = MemoryStore()
        client = client_with_fetch(
            cfg(store, max_snapshot_age_seconds=60), flags_and_segments
        )
        try:
            with structlog.testing.capture_logs() as logs:
                other = FeatureflipClient(sdk_key=KEY, config=cfg(store))
            assert [log for log in logs if log["log_level"] == "warning"] == []
            other.close()
        finally:
            client.close()

    def test_explicit_default_max_age_differing_from_first_still_warns(self) -> None:
        store = MemoryStore()
        client = client_with_fetch(
            cfg(store, max_snapshot_age_seconds=60), flags_and_segments
        )
        try:
            with structlog.testing.capture_logs() as logs:
                other = FeatureflipClient(
                    sdk_key=KEY, config=cfg(store, max_snapshot_age_seconds=86400)
                )
            events = [log for log in logs if log["log_level"] == "warning"]
            assert len(events) == 1
            assert "max_snapshot_age_seconds" in events[0]["message"]
            other.close()
        finally:
            client.close()

    def test_same_store_is_silent(self) -> None:
        store = MemoryStore()
        client = client_with_fetch(cfg(store), flags_and_segments)
        try:
            with structlog.testing.capture_logs() as logs:
                other = FeatureflipClient(sdk_key=KEY, config=cfg(store))
            assert [log for log in logs if log["log_level"] == "warning"] == []
            other.close()
        finally:
            client.close()

    def test_omitting_store_on_later_get_is_silent(self) -> None:
        store = MemoryStore()
        client = client_with_fetch(cfg(store), flags_and_segments)
        try:
            with structlog.testing.capture_logs() as logs:
                other = FeatureflipClient(sdk_key=KEY, config=cfg(None))
            assert [log for log in logs if log["log_level"] == "warning"] == []
            other.close()
        finally:
            client.close()


class TestStubPaths:
    def test_for_testing_has_startup_info(self) -> None:
        client = FeatureflipClient.for_testing({"a": True})
        assert client.startup_info.source == "live"

    def test_bypassed_handle_has_startup_info(self) -> None:
        from featureflip._core import _SharedFeatureflipCore

        core = _SharedFeatureflipCore._create_for_testing_skeleton()
        handle = object.__new__(FeatureflipClient)
        handle._core = core
        handle._closed = False
        assert isinstance(handle.startup_info, StartupInfo)
        core._release()


class TestFixWaveCoverage:
    @pytest.mark.usefixtures("clock")
    def test_ping_after_a_none_start_never_refreshes(self, clock: Clock) -> None:
        store = MemoryStore(None)
        core = snapshot_started_core(store)
        try:
            assert core.startup_info == StartupInfo("none")
            clock.advance(1000)
            core._on_streaming_ping()
            time.sleep(0.1)
            assert store.puts == []
        finally:
            core._release()

    def test_close_blocks_a_new_write(self) -> None:
        store = MemoryStore()
        core = live_core(store)
        assert wait_until(lambda: len(store.puts) == 1)
        core._release()
        changed = payload()
        changed["flags"][0]["version"] = 55
        f, s = parse_wire_payload(changed)
        core._apply_snapshot(f, s)
        core._persist()
        time.sleep(0.15)
        assert len(store.puts) == 1

    def test_sse_sync_persists(self) -> None:
        from unittest.mock import MagicMock

        from featureflip._streaming import StreamingHandler

        store = MemoryStore()
        core = live_core(store)
        try:
            assert wait_until(lambda: len(store.puts) == 1)
            handler = StreamingHandler(
                sdk_key="k",
                config=Config(),
                http_client=MagicMock(parse_flags_response=parse_wire_payload),
                on_flag_updated=core._on_streaming_flag_updated,
                on_flag_deleted=core._on_streaming_flag_deleted,
                on_segment_updated=core._on_streaming_segment_updated,
                on_error=core._on_streaming_error,
                on_update=core._on_polling_update,
                on_ping=core._on_streaming_ping,
            )
            changed = payload()
            changed["flags"][0]["version"] = 42
            handler._handle_event("sync", json.dumps(changed))
            assert wait_until(lambda: len(store.puts) == 2)
            assert json.loads(store.puts[1])["data"]["flags"][0]["version"] == 42
        finally:
            core._release()

    def test_segment_updated_refetch_persists(self) -> None:
        from unittest.mock import MagicMock

        store = MemoryStore()
        core = live_core(store)
        try:
            assert wait_until(lambda: len(store.puts) == 1)
            changed = payload()
            changed["segments"][0]["version"] = 9
            core._http_client = MagicMock()
            core._http_client.get_flags.return_value = parse_wire_payload(changed)
            core._on_streaming_segment_updated()
            assert wait_until(lambda: len(store.puts) == 2)
            assert json.loads(store.puts[1])["data"]["segments"][0]["version"] == 9
        finally:
            core._http_client = None
            core._release()

    def test_thread_start_failure_does_not_fail_init_or_wedge_writes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = threading.Thread.start

        def start(self: threading.Thread) -> None:
            if self.name == "featureflip-snapshot-write":
                raise RuntimeError("can't start new thread")
            original(self)

        store = MemoryStore()
        with monkeypatch.context() as m:
            m.setattr(threading.Thread, "start", start)
            with structlog.testing.capture_logs() as logs:
                core = live_core(store)
        try:
            assert core.startup_info == StartupInfo("live")
            assert core.is_initialized
            assert "can't start new thread" in json.dumps(logs, default=str)
            assert store.puts == []
            changed = payload()
            changed["flags"][0]["version"] = 77
            f, s = parse_wire_payload(changed)
            core._apply_snapshot(f, s)
            assert wait_until(lambda: len(store.puts) >= 1)
        finally:
            core._release()
