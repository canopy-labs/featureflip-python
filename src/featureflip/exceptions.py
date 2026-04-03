"""Exceptions for Featureflip SDK."""


class FeatureflipError(Exception):
    """Base exception for Featureflip SDK."""

    pass


class InitializationError(FeatureflipError):
    """Raised when client initialization fails."""

    pass


class ConfigurationError(FeatureflipError):
    """Raised when configuration is invalid."""

    pass
