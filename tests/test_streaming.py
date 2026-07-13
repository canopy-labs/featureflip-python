"""Tests for SSE streaming handler."""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from featureflip._streaming import _SSE_READ_TIMEOUT_SECONDS, StreamingHandler
from featureflip.config import Config


def test_sse_read_timeout_is_a_finite_liveness_watchdog() -> None:
    """A finite SSE read timeout acts as a liveness watchdog: the server pings
    every ~30s, so no bytes for this long means a half-open socket — close and
    reconnect instead of blocking forever on read=None."""
    assert _SSE_READ_TIMEOUT_SECONDS is not None
    assert _SSE_READ_TIMEOUT_SECONDS > 30.0


class TestStreamingHandler:
    """Test suite for StreamingHandler."""

    @pytest.fixture
    def config(self) -> Config:
        """Create a test configuration."""
        return Config(base_url="https://api.example.com")

    @pytest.fixture
    def on_flag_updated(self) -> MagicMock:
        """Create a mock flag updated callback."""
        return MagicMock()

    @pytest.fixture
    def on_flag_deleted(self) -> MagicMock:
        """Create a mock flag deleted callback."""
        return MagicMock()

    @pytest.fixture
    def on_segment_updated(self) -> MagicMock:
        """Create a mock segment updated callback."""
        return MagicMock()

    @pytest.fixture
    def on_error(self) -> MagicMock:
        """Create a mock error callback."""
        return MagicMock()

    def _make_handler(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> StreamingHandler:
        """Create a StreamingHandler with standard test callbacks."""
        return StreamingHandler(
            sdk_key="test-key",
            config=config,
            http_client=MagicMock(),
            on_flag_updated=on_flag_updated,
            on_flag_deleted=on_flag_deleted,
            on_segment_updated=on_segment_updated,
            on_error=on_error,
            on_update=MagicMock(),
        )

    def test_handler_creation(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Test that handler can be created with required parameters."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )
        assert handler is not None
        handler.stop()

    def test_stop_before_start(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Stopping before starting should not raise."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )
        handler.stop()  # Should not raise

    def test_flag_updated_event(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Test that flag.updated event calls on_flag_updated with key."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )

        event_data = json.dumps({"key": "test-flag", "version": 2})
        handler._handle_event("flag.updated", event_data)

        on_flag_updated.assert_called_once_with("test-flag")
        on_flag_deleted.assert_not_called()
        on_segment_updated.assert_not_called()

        handler.stop()

    def test_flag_created_event(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Test that flag.created event calls on_flag_updated with key."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )

        event_data = json.dumps({"key": "new-flag", "version": 1})
        handler._handle_event("flag.created", event_data)

        on_flag_updated.assert_called_once_with("new-flag")
        on_flag_deleted.assert_not_called()
        on_segment_updated.assert_not_called()

        handler.stop()

    def test_flag_deleted_event(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Test that flag.deleted event calls on_flag_deleted with key."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )

        event_data = json.dumps({"key": "deleted-flag", "version": 3})
        handler._handle_event("flag.deleted", event_data)

        on_flag_deleted.assert_called_once_with("deleted-flag")
        on_flag_updated.assert_not_called()
        on_segment_updated.assert_not_called()

        handler.stop()

    def test_segment_updated_event(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Test that segment.updated event calls on_segment_updated."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )

        event_data = json.dumps({"key": "segment-1", "version": 2})
        handler._handle_event("segment.updated", event_data)

        on_segment_updated.assert_called_once_with()
        on_flag_updated.assert_not_called()
        on_flag_deleted.assert_not_called()

        handler.stop()

    def test_old_hyphenated_event_names_ignored(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Test that old hyphenated event names (flag-updated) are ignored."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )

        event_data = json.dumps({"key": "test-flag", "version": 1})
        handler._handle_event("flag-updated", event_data)
        handler._handle_event("flag-deleted", event_data)

        on_flag_updated.assert_not_called()
        on_flag_deleted.assert_not_called()
        on_segment_updated.assert_not_called()

        handler.stop()

    def test_invalid_event_data_does_not_crash(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Test that invalid event data is handled gracefully."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )

        # Invalid JSON
        handler._handle_event("flag.updated", "not valid json")
        on_flag_updated.assert_not_called()

        # Missing key field
        handler._handle_event("flag.updated", json.dumps({"version": 1}))
        on_flag_updated.assert_not_called()

        handler.stop()

    def test_unknown_event_type_ignored(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Test that unknown event types are ignored."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )

        handler._handle_event("unknown-event", json.dumps({"data": "test"}))
        on_flag_updated.assert_not_called()
        on_flag_deleted.assert_not_called()
        on_segment_updated.assert_not_called()

        handler.stop()

    def test_stream_url_construction(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Test that the stream URL is constructed correctly."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )

        expected_url = "https://api.example.com/v1/sdk/stream"
        assert handler._get_stream_url() == expected_url

        handler.stop()

    def test_headers_include_auth(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Test that headers include the SDK key for auth."""
        handler = StreamingHandler(
            sdk_key="my-sdk-key",
            config=config,
            http_client=MagicMock(),
            on_flag_updated=on_flag_updated,
            on_flag_deleted=on_flag_deleted,
            on_segment_updated=on_segment_updated,
            on_error=on_error,
            on_update=MagicMock(),
        )

        headers = handler._get_headers()
        assert headers["Authorization"] == "my-sdk-key"
        assert "User-Agent" in headers

        handler.stop()

    def test_sync_event_applies_full_replace_via_on_update(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """A `sync` snapshot is parsed and routed to on_update (full replace)."""
        http_client = MagicMock()
        flags = [MagicMock()]
        segments = [MagicMock()]
        http_client.parse_flags_response.return_value = (flags, segments)
        on_update = MagicMock()
        handler = StreamingHandler(
            sdk_key="test-key",
            config=config,
            http_client=http_client,
            on_flag_updated=on_flag_updated,
            on_flag_deleted=on_flag_deleted,
            on_segment_updated=on_segment_updated,
            on_error=on_error,
            on_update=on_update,
        )

        snapshot = {"flags": [{"key": "x"}], "segments": []}
        handler._handle_event("sync", json.dumps(snapshot))

        http_client.parse_flags_response.assert_called_once_with(snapshot)
        on_update.assert_called_once_with(flags, segments)
        # A sync must NOT be treated as a per-key delta.
        on_flag_updated.assert_not_called()
        on_segment_updated.assert_not_called()

        handler.stop()

    def test_backoff_delay_uses_base_for_first_failures(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """The first (re)connect uses the base delay with no jitter."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )
        handler._reconnect_base_delay = 3.0
        handler._max_reconnect_delay = 30.0

        assert handler._backoff_delay(0) == 3.0
        assert handler._backoff_delay(1) == 3.0

    def test_backoff_delay_escalates_and_caps_with_jitter(
        self,
        config: Config,
        on_flag_updated: MagicMock,
        on_flag_deleted: MagicMock,
        on_segment_updated: MagicMock,
        on_error: MagicMock,
    ) -> None:
        """Backoff doubles up to the cap, with [d/2, d) jitter once escalating."""
        handler = self._make_handler(
            config, on_flag_updated, on_flag_deleted, on_segment_updated, on_error
        )
        handler._reconnect_base_delay = 1.0
        handler._max_reconnect_delay = 30.0

        # failures=2 -> raw 2.0 -> jitter band [1.0, 2.0)
        d2 = handler._backoff_delay(2)
        assert 1.0 <= d2 < 2.0
        # large failure count -> capped at 30 -> jitter band [15.0, 30.0)
        d_big = handler._backoff_delay(20)
        assert 15.0 <= d_big < 30.0

    def _make_fallback_handler(self) -> StreamingHandler:
        """Handler whose connect always fails, with a harmless mock poller."""
        http_client = MagicMock()
        http_client.get_flags.return_value = ([], [])
        handler = StreamingHandler(
            sdk_key="test-key",
            config=Config(base_url="https://api.example.com", poll_interval=0.05),
            http_client=http_client,
            on_flag_updated=MagicMock(),
            on_flag_deleted=MagicMock(),
            on_segment_updated=MagicMock(),
            on_error=MagicMock(),
            on_update=MagicMock(),
        )
        handler._reconnect_base_delay = 0.001
        handler._max_reconnect_delay = 0.002
        handler._fallback_threshold = 2
        return handler

    @staticmethod
    def _wait_until(predicate: object, timeout: float = 3.0) -> None:
        deadline = time.time() + timeout
        while not predicate() and time.time() < deadline:  # type: ignore[operator]
            time.sleep(0.01)

    def test_fallback_polling_starts_after_threshold_and_stops_on_stop(self) -> None:
        handler = self._make_fallback_handler()
        handler._connect = MagicMock(return_value=False)  # every connect fails

        handler.start()
        self._wait_until(lambda: handler._fallback_poller is not None)
        assert handler._fallback_poller is not None, (
            "fallback poller should start after threshold failures"
        )

        handler.stop()
        assert handler._fallback_poller is None, "stop() must reap the fallback poller"

    def test_fallback_polling_stops_on_recovery(self) -> None:
        handler = self._make_fallback_handler()
        handler._connect = MagicMock(return_value=False)

        handler.start()
        self._wait_until(lambda: handler._fallback_poller is not None)
        assert handler._fallback_poller is not None

        handler._connect.return_value = True  # stream recovers
        self._wait_until(lambda: handler._fallback_poller is None)
        assert handler._fallback_poller is None, (
            "fallback poller should be torn down on recovery"
        )

        handler.stop()

    def test_fallback_polling_stops_while_stream_stays_connected(self) -> None:
        """A healthy reconnect reaps the poller *while* the stream stays alive.

        ``_connect`` blocks inside ``iter_sse()`` for the whole lifetime of a
        healthy stream, so reaping the fallback poller only after ``_connect``
        returns leaves the stream and the poller running concurrently for the
        entire healthy period (stale full-replace polls can revert SSE deltas).
        The poller must be torn down as soon as the stream is confirmed healthy
        (first frame received) — not when the connection later dies.
        """
        handler = self._make_fallback_handler()
        handler._http.parse_flags_response.return_value = ([], [])

        # A poller is already running (e.g. after N prior stream failures).
        poller = MagicMock()
        handler._fallback_poller = poller

        # Model the SSE stream: yield the connect `sync` frame, then stay alive
        # (block) exactly as a real healthy `iter_sse()` does — it only returns
        # on EOF/error.
        sync_event = MagicMock()
        sync_event.event = "sync"
        sync_event.data = json.dumps({"flags": [], "segments": []})
        stream_alive = threading.Event()

        def iter_sse() -> object:
            yield sync_event
            stream_alive.wait(timeout=5.0)  # healthy stream: blocks here

        event_source = MagicMock()
        event_source.iter_sse.return_value = iter_sse()
        sse_cm = MagicMock()
        sse_cm.__enter__.return_value = event_source

        try:
            with patch(
                "featureflip._streaming.connect_sse", return_value=sse_cm
            ):
                connect_thread = threading.Thread(
                    target=handler._connect, daemon=True
                )
                connect_thread.start()

                # The poller must be reaped WHILE _connect is still blocked in
                # iter_sse() (i.e. before it returns).
                self._wait_until(lambda: handler._fallback_poller is None)
                assert handler._fallback_poller is None, (
                    "fallback poller must be reaped once the stream is healthy, "
                    "not only after the (blocking) connection dies"
                )
                assert connect_thread.is_alive(), (
                    "stream should still be connected when the poller is reaped"
                )
                poller.stop.assert_called_once()
        finally:
            stream_alive.set()
            connect_thread.join(timeout=5.0)

    def test_start_fallback_polling_is_noop_after_stop(self) -> None:
        handler = self._make_fallback_handler()
        handler._stop_event.set()  # simulate stop() already began

        handler._start_fallback_polling()

        assert handler._fallback_poller is None, "must not start a poller after stop"
