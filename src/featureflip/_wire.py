"""Serialize the in-memory flag state back to the Evaluation API's wire shape.

The SDK keeps parsed models, not the raw response, so a persisted snapshot is
rebuilt from them. The output is accepted by ``HttpClient.parse_flags_response``
(the same parser live payloads go through), which the round-trip tests pin.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from featureflip.models import (
        Condition,
        ConditionGroup,
        FlagConfiguration,
        Segment,
        ServeConfig,
        TargetingRule,
    )


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _operator(op: Any) -> str:
    """PascalCase wire name for a known operator; an unknown one verbatim."""
    return _pascal(op.value) if isinstance(op, Enum) else str(op)


def _condition(c: Condition) -> dict[str, Any]:
    return {
        "attribute": c.attribute,
        "operator": _operator(c.operator),
        "values": list(c.values),
        "negate": c.negate,
    }


def _group(g: ConditionGroup) -> dict[str, Any]:
    return {
        "operator": _pascal(g.operator.value),
        "conditions": [_condition(c) for c in g.conditions],
    }


def _serve(s: ServeConfig) -> dict[str, Any]:
    out: dict[str, Any] = {"type": _pascal(s.type.value)}
    if s.variation is not None:
        out["variation"] = s.variation
    if s.bucket_by is not None:
        out["bucketBy"] = s.bucket_by
    if s.salt is not None:
        out["salt"] = s.salt
    if s.variations is not None:
        out["variations"] = [{"key": v.key, "weight": v.weight} for v in s.variations]
    return out


def _rule(r: TargetingRule) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": r.id,
        "priority": r.priority,
        "conditionGroups": [_group(g) for g in r.condition_groups],
        "serve": _serve(r.serve),
    }
    if r.segment_key is not None:
        out["segmentKey"] = r.segment_key
    return out


def _flag(f: FlagConfiguration) -> dict[str, Any]:
    return {
        "key": f.key,
        "version": f.version,
        "type": _pascal(f.type.value) if isinstance(f.type, Enum) else f.type,
        "enabled": f.enabled,
        "variations": [{"key": v.key, "value": v.value} for v in f.variations],
        "rules": [_rule(r) for r in f.rules],
        "fallthrough": _serve(f.fallthrough),
        "offVariation": f.off_variation,
        "prerequisites": [
            {
                "prerequisiteFlagKey": p.prerequisite_flag_key,
                "expectedVariationKey": p.expected_variation_key,
            }
            for p in f.prerequisites
        ],
    }


def _segment(s: Segment) -> dict[str, Any]:
    return {
        "key": s.key,
        "version": s.version,
        "conditions": [_condition(c) for c in s.conditions],
        "conditionLogic": _pascal(s.condition_logic.value),
    }


def serialize_flags_payload(
    flags: Iterable[FlagConfiguration], segments: Iterable[Segment]
) -> str:
    """The full state as compact JSON text in the ``/v1/sdk/flags`` shape."""
    return json.dumps(
        {
            "flags": [_flag(f) for f in flags],
            "segments": [_segment(s) for s in segments],
        },
        separators=(",", ":"),
    )
