"""Tests for SSE streaming handler."""

import json
from unittest.mock import MagicMock

import pytest

from featureflip._streaming import StreamingHandler
from featureflip.config import Config


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
            on_flag_updated=on_flag_updated,
            on_flag_deleted=on_flag_deleted,
            on_segment_updated=on_segment_updated,
            on_error=on_error,
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
            on_flag_updated=on_flag_updated,
            on_flag_deleted=on_flag_deleted,
            on_segment_updated=on_segment_updated,
            on_error=on_error,
        )

        headers = handler._get_headers()
        assert headers["Authorization"] == "my-sdk-key"
        assert "User-Agent" in headers

        handler.stop()
