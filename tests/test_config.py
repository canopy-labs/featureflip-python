"""Tests for client configuration."""


import pytest

from featureflip.config import Config


class TestConfig:
    def test_default_values(self) -> None:
        config = Config()
        assert config.base_url == "https://eval.featureflip.io"
        assert config.connect_timeout == 5.0
        assert config.read_timeout == 10.0
        assert config.streaming is True
        assert config.poll_interval == 30.0
        assert config.send_events is True
        assert config.flush_interval == 30.0
        assert config.flush_batch_size == 100
        assert config.init_timeout == 10.0

    def test_custom_values(self) -> None:
        config = Config(
            base_url="https://custom.example.com",
            connect_timeout=10.0,
            streaming=False,
            poll_interval=60.0,
        )
        assert config.base_url == "https://custom.example.com"
        assert config.connect_timeout == 10.0
        assert config.streaming is False
        assert config.poll_interval == 60.0

    def test_base_url_strips_trailing_slash(self) -> None:
        config = Config(base_url="https://example.com/")
        assert config.base_url == "https://example.com"


class TestConfigValidation:
    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="connect_timeout must be positive"):
            Config(connect_timeout=-1.0)

    def test_zero_poll_interval_raises(self) -> None:
        with pytest.raises(ValueError, match="poll_interval must be positive"):
            Config(poll_interval=0)

    def test_negative_flush_batch_size_raises(self) -> None:
        with pytest.raises(ValueError, match="flush_batch_size must be positive"):
            Config(flush_batch_size=0)
