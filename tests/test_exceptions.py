"""Tests for Featureflip exceptions."""

import pytest

from featureflip.exceptions import (
    ConfigurationError,
    FeatureflipError,
    InitializationError,
)


class TestExceptionHierarchy:
    """Test that exception hierarchy is correct."""

    def test_featureflip_error_is_base_exception(self) -> None:
        """FeatureflipError should inherit from Exception."""
        assert issubclass(FeatureflipError, Exception)

    def test_initialization_error_inherits_from_base(self) -> None:
        """InitializationError should inherit from FeatureflipError."""
        assert issubclass(InitializationError, FeatureflipError)

    def test_configuration_error_inherits_from_base(self) -> None:
        """ConfigurationError should inherit from FeatureflipError."""
        assert issubclass(ConfigurationError, FeatureflipError)


class TestExceptionUsage:
    """Test that exceptions can be raised and caught correctly."""

    def test_can_raise_and_catch_featureflip_error(self) -> None:
        with pytest.raises(FeatureflipError, match="test message"):
            raise FeatureflipError("test message")

    def test_can_raise_and_catch_initialization_error(self) -> None:
        with pytest.raises(InitializationError, match="init failed"):
            raise InitializationError("init failed")

    def test_can_raise_and_catch_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError, match="bad config"):
            raise ConfigurationError("bad config")

    def test_can_catch_derived_as_base(self) -> None:
        """Derived exceptions should be catchable as FeatureflipError."""
        with pytest.raises(FeatureflipError):
            raise InitializationError("derived exception")

        with pytest.raises(FeatureflipError):
            raise ConfigurationError("derived exception")
