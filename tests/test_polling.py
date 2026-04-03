"""Tests for polling handler."""

import time
from unittest.mock import MagicMock

import pytest

from featureflip._polling import PollingHandler
from featureflip.config import Config
from featureflip.models import (
    FlagConfiguration,
    FlagType,
    ServeConfig,
    ServeType,
    Variation,
)


class TestPollingHandler:
    """Test suite for PollingHandler."""

    @pytest.fixture
    def config(self) -> Config:
        """Create a test configuration with short poll interval."""
        return Config(base_url="https://api.example.com", poll_interval=0.1)

    @pytest.fixture
    def mock_http(self) -> MagicMock:
        """Create a mock HTTP client."""
        mock = MagicMock()
        mock.get_flags.return_value = ([
            FlagConfiguration(
                key="test-flag",
                version=1,
                type=FlagType.BOOLEAN,
                enabled=True,
                variations=[
                    Variation(key="on", value=True),
                    Variation(key="off", value=False),
                ],
                rules=[],
                fallthrough=ServeConfig(type=ServeType.FIXED, variation="on"),
                off_variation="off",
            )
        ], [])
        return mock

    @pytest.fixture
    def on_update(self) -> MagicMock:
        """Create a mock update callback."""
        return MagicMock()

    @pytest.fixture
    def on_error(self) -> MagicMock:
        """Create a mock error callback."""
        return MagicMock()

    def test_polls_on_interval(
        self, config: Config, mock_http: MagicMock, on_update: MagicMock, on_error: MagicMock
    ) -> None:
        """Test that handler polls at the configured interval."""
        handler = PollingHandler(
            http_client=mock_http, config=config, on_update=on_update, on_error=on_error
        )
        handler.start()
        time.sleep(0.35)
        handler.stop()
        # With 0.1s interval and 0.35s sleep, we expect 3-4 polls
        assert mock_http.get_flags.call_count >= 2

    def test_calls_on_update_with_flags(
        self, config: Config, mock_http: MagicMock, on_update: MagicMock, on_error: MagicMock
    ) -> None:
        """Test that on_update callback is called with fetched flags."""
        handler = PollingHandler(
            http_client=mock_http, config=config, on_update=on_update, on_error=on_error
        )
        handler.start()
        time.sleep(0.15)
        handler.stop()
        on_update.assert_called()
        flags = on_update.call_args[0][0]
        segments = on_update.call_args[0][1]
        assert len(flags) == 1
        assert flags[0].key == "test-flag"
        assert segments == []

    def test_calls_on_error_on_failure(
        self, config: Config, on_update: MagicMock, on_error: MagicMock
    ) -> None:
        """Test that on_error callback is called when polling fails."""
        mock_http = MagicMock()
        mock_http.get_flags.side_effect = Exception("Network error")
        handler = PollingHandler(
            http_client=mock_http, config=config, on_update=on_update, on_error=on_error
        )
        handler.start()
        time.sleep(0.15)
        handler.stop()
        on_error.assert_called()

    def test_stop_before_start(
        self, config: Config, mock_http: MagicMock, on_update: MagicMock, on_error: MagicMock
    ) -> None:
        """Stopping before starting should not raise."""
        handler = PollingHandler(
            http_client=mock_http, config=config, on_update=on_update, on_error=on_error
        )
        handler.stop()  # Should not raise

    def test_handler_creation(
        self, config: Config, mock_http: MagicMock, on_update: MagicMock, on_error: MagicMock
    ) -> None:
        """Test that handler can be created with required parameters."""
        handler = PollingHandler(
            http_client=mock_http, config=config, on_update=on_update, on_error=on_error
        )
        assert handler is not None

    def test_multiple_start_stop_cycles(
        self, config: Config, mock_http: MagicMock, on_update: MagicMock, on_error: MagicMock
    ) -> None:
        """Test that handler can be started and stopped multiple times."""
        handler = PollingHandler(
            http_client=mock_http, config=config, on_update=on_update, on_error=on_error
        )
        handler.start()
        time.sleep(0.15)
        handler.stop()

        initial_count = mock_http.get_flags.call_count

        handler.start()
        time.sleep(0.15)
        handler.stop()

        assert mock_http.get_flags.call_count > initial_count

    def test_continues_after_error(
        self, config: Config, on_update: MagicMock, on_error: MagicMock
    ) -> None:
        """Test that polling continues after an error."""
        mock_http = MagicMock()
        # First call fails, subsequent calls succeed
        call_count = 0

        def get_flags_with_error() -> tuple[list[FlagConfiguration], list]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("First error")
            return ([
                FlagConfiguration(
                    key="test-flag",
                    version=1,
                    type=FlagType.BOOLEAN,
                    enabled=True,
                    variations=[
                        Variation(key="on", value=True),
                        Variation(key="off", value=False),
                    ],
                    rules=[],
                    fallthrough=ServeConfig(type=ServeType.FIXED, variation="on"),
                    off_variation="off",
                )
            ], [])

        mock_http.get_flags.side_effect = get_flags_with_error
        handler = PollingHandler(
            http_client=mock_http, config=config, on_update=on_update, on_error=on_error
        )
        handler.start()
        time.sleep(0.35)
        handler.stop()

        # Verify error was called once and update was called after recovery
        on_error.assert_called_once()
        on_update.assert_called()
