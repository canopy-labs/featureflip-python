"""Evaluation context for Featureflip SDK.

This module contains the EvaluationContext class used to pass user and
environment attributes for flag evaluation.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from featureflip._canonical_number import canonical_decimal, canonical_number

# A list or mapping (a JSON array or object, empty or not) has no spelling every
# SDK shares, so a rollout can't bucket on it (#4572).
NON_SCALAR_TYPES = (list, tuple, Mapping, AbstractSet)


@dataclass
class EvaluationContext:
    """Context for evaluating feature flags.

    Contains user attributes and other contextual information used to
    evaluate targeting rules and determine which variation to serve.

    Attributes:
        user_id: Optional unique identifier for the user. Used for
            percentage-based rollouts and user-specific targeting.
        attributes: Dictionary of additional context attributes that can
            be used in targeting rules (e.g., email, plan, country).
    """

    user_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def get_attribute(self, key: str) -> Any:
        """Get an attribute value by key.

        Special handling for "user_id" and "userId" which both return the
        user_id field. All other keys are looked up in the attributes dictionary.

        Args:
            key: The attribute key to look up.

        Returns:
            The attribute value if found, None otherwise.
        """
        if key == "user_id" or key == "userId":
            return self.user_id
        return self.attributes.get(key)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationContext:
        """Create an EvaluationContext from a dictionary.

        The "user_id" key is extracted and converted to string if present.
        All other keys are stored in the attributes dictionary.

        Args:
            data: Dictionary of context attributes.

        Returns:
            A new EvaluationContext instance.
        """
        # user_id is canonical; userId is accepted as an alias. Only None means
        # "absent": "", 0 and False are real values and do not fall through.
        user_id = data.get("user_id")
        if user_id is None:
            user_id = data.get("userId")
        if isinstance(user_id, bool):
            # JSON's spelling, so a boolean id buckets like the engine's (str(False) is "False").
            user_id = "true" if user_id else "false"
        elif isinstance(user_id, float):
            # Same spelling resolve_rollout hashes, so 1.0 buckets as "1" (#4250).
            user_id = canonical_number(user_id)
        elif isinstance(user_id, Decimal):
            user_id = canonical_decimal(user_id)
        elif isinstance(user_id, NON_SCALAR_TYPES):
            # The engine's WireContext spells a non-scalar id "", so it can't bucket (#4572).
            user_id = ""
        user_id = str(user_id) if user_id is not None else None

        # Copy all attributes except user_id/userId into the attributes dict
        attributes = {k: v for k, v in data.items() if k not in ("user_id", "userId")}

        return cls(user_id=user_id, attributes=attributes)
