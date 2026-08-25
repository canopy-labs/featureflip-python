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


class UnevaluableEntityError(FeatureflipError):
    """Raised when a flag or segment carries an enum value this build cannot EVALUATE.

    ``ServeType`` and ``ConditionLogic`` are the two enums that are BOTH carried on the
    wire as strings AND consulted by the evaluator, and each dispatches on a two-way
    branch with no third arm::

        serve.type is FIXED  ... else ROLLOUT
        logic      is AND    ... else ANY (OR)

    So an unrecognised value cannot simply be tolerated the way an unknown ``FlagType``
    is (#2401) — it would take the ELSE arm and silently evaluate as a rollout, or match
    ANY condition where the server asked for ALL, so a rule fails OPEN and over-targets.
    Equally it cannot fail the whole payload the way a TYPE violation does, because one
    additive server change would then take down every flag on a pinned client (the
    #2372/#2395 outage shape).

    So the containing entity is dropped instead and the rest of the payload applies
    (#2402). Deliberately NOT a subclass of ``ValueError``/``TypeError``: those signal
    the wire-contract violations that are still rejected wholesale, and the shared
    ``malformedConfigVectors`` runner distinguishes the two by exactly that.
    """

    pass
