"""Evaluation detail types for Featureflip SDK.

This module contains types for detailed evaluation results, including
the reason why a particular value was returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class EvaluationReason(Enum):
    """Reason for why a particular flag value was returned.

    Attributes:
        FALLTHROUGH: Flag was evaluated and fell through to the default variation.
        RULE_MATCH: Flag was evaluated and matched a targeting rule.
        FLAG_DISABLED: Flag is disabled, returning the off variation.
        FLAG_NOT_FOUND: Flag was not found in the configuration.
        PREREQUISITE_FAILED: A prerequisite flag did not serve the expected variation.
        ERROR: An error occurred during evaluation.
    """

    FALLTHROUGH = "FALLTHROUGH"
    RULE_MATCH = "RULE_MATCH"
    FLAG_DISABLED = "FLAG_DISABLED"
    FLAG_NOT_FOUND = "FLAG_NOT_FOUND"
    PREREQUISITE_FAILED = "PREREQUISITE_FAILED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class EvaluationDetail:
    """Detailed result of a flag evaluation.

    Provides additional information about why a particular value was returned,
    which is useful for debugging and analytics.

    Attributes:
        value: The evaluated flag value.
        reason: The reason for returning this value.
        rule_id: The ID of the matched rule, if applicable.
        variation_key: The key of the variation that was served.
        prerequisite_key: The key of the prerequisite flag that caused the
            evaluation to short-circuit, set when reason is PREREQUISITE_FAILED.
        error: The exception that occurred, if reason is ERROR.
    """

    value: Any
    reason: EvaluationReason
    rule_id: str | None = None
    variation_key: str | None = None
    prerequisite_key: str | None = None
    error: Exception | None = None
