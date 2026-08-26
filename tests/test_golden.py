"""Golden-vector parity harness for the Python SDK (#1477).

Loads the canonical cross-SDK fixture at tests/golden/vectors.json and
asserts all four vector classes against the Python SDK's own functions.
The fixture is generated from the .NET engine and must NOT be modified here;
if a vector fails, first verify the harness, then fix the Python SDK source.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from featureflip.context import EvaluationContext
from featureflip.detail import EvaluationDetail, EvaluationReason
from featureflip.evaluation import FlagEvaluator
from featureflip.models import (
    Condition,
    ConditionGroup,
    ConditionLogic,
    ConditionOperator,
    FlagConfiguration,
    FlagType,
    Prerequisite,
    Segment,
    ServeConfig,
    ServeType,
    TargetingRule,
    Variation,
    WeightedVariation,
)

VECTORS = json.loads((Path(__file__).parent / "golden" / "vectors.json").read_text())
EV = FlagEvaluator()

# Map Python UPPER_SNAKE reasons to the fixture's canonical PascalCase kinds.
_KIND: dict[EvaluationReason, str] = {
    EvaluationReason.FLAG_DISABLED: "FlagDisabled",
    EvaluationReason.RULE_MATCH: "RuleMatch",
    EvaluationReason.FALLTHROUGH: "Fallthrough",
    EvaluationReason.PREREQUISITE_FAILED: "PrerequisiteFailed",
    EvaluationReason.FLAG_NOT_FOUND: "FlagNotFound",
    EvaluationReason.ERROR: "Error",
}


def _normalize_reason(detail: EvaluationDetail) -> dict[str, Any]:
    """Convert an EvaluationDetail into the fixture's canonical reason dict."""
    out: dict[str, Any] = {"kind": _KIND[detail.reason]}
    if detail.rule_id is not None:
        out["ruleId"] = detail.rule_id
    if detail.prerequisite_key is not None:
        out["prerequisiteKey"] = detail.prerequisite_key
    return out


def _typed_attr(attr: dict[str, Any]) -> Any:
    """Return the attribute value with its native Python type.

    json.loads already produces int/float/bool/str from the fixture, so
    we return the value as-is — this ensures the #1458 numeric-coercion
    path receives a real Python number rather than a string.
    """
    return attr["value"]


def _context_from_fixture(ctx: dict[str, Any]) -> EvaluationContext:
    """Build an EvaluationContext from a fixture context dict.

    The fixture uses ``{"userId": "...", "attributes": {...}}`` — a nested
    shape — whereas EvaluationContext.from_dict expects a flat dict where
    every key except userId/user_id lands in attributes. We flatten the
    nested attributes into the top-level dict before calling from_dict.
    """
    flat: dict[str, Any] = {}
    if "userId" in ctx:
        flat["userId"] = ctx["userId"]
    if "user_id" in ctx:
        flat["user_id"] = ctx["user_id"]
    flat.update(ctx.get("attributes", {}))
    return EvaluationContext.from_dict(flat)


# ---------------------------------------------------------------------------
# Wire-format parsers (mirrors _http.py — test-only; does NOT change SDK source)
# ---------------------------------------------------------------------------

def _parse_serve(data: dict[str, Any]) -> ServeConfig:
    variations = None
    if "variations" in data:
        variations = [
            WeightedVariation(key=v["key"], weight=v["weight"])
            for v in data["variations"]
        ]
    return ServeConfig(
        type=ServeType(data["type"].lower()),
        variation=data.get("variation"),
        bucket_by=data.get("bucketBy"),
        salt=data.get("salt"),
        variations=variations,
    )


def _parse_condition(data: dict[str, Any]) -> Condition:
    return Condition(
        attribute=data["attribute"],
        operator=ConditionOperator(data["operator"]),
        values=data["values"],
        negate=data.get("negate", False),
    )


def _parse_condition_group(data: dict[str, Any]) -> ConditionGroup:
    return ConditionGroup(
        operator=ConditionLogic(data.get("operator", "And").lower()),
        conditions=[_parse_condition(c) for c in data.get("conditions", [])],
    )


def _parse_rule(data: dict[str, Any]) -> TargetingRule:
    return TargetingRule(
        id=data["id"],
        priority=data["priority"],
        condition_groups=[
            _parse_condition_group(g) for g in data.get("conditionGroups", [])
        ],
        serve=_parse_serve(data["serve"]),
        segment_key=data.get("segmentKey"),
    )


def _parse_flag(data: dict[str, Any]) -> FlagConfiguration:
    return FlagConfiguration(
        key=data["key"],
        version=data["version"],
        type=FlagType(data["type"].lower()),
        enabled=data["enabled"],
        variations=[
            Variation(key=v["key"], value=v["value"])
            for v in data["variations"]
        ],
        rules=[_parse_rule(r) for r in data.get("rules", [])],
        fallthrough=_parse_serve(data["fallthrough"]),
        off_variation=data["offVariation"],
        prerequisites=[
            Prerequisite(
                prerequisite_flag_key=p["prerequisiteFlagKey"],
                expected_variation_key=p["expectedVariationKey"],
            )
            for p in data.get("prerequisites", []) or []
        ],
    )


def _parse_segment(data: dict[str, Any]) -> Segment:
    return Segment(
        key=data["key"],
        version=data["version"],
        conditions=[_parse_condition(c) for c in data.get("conditions", [])],
        condition_logic=ConditionLogic(data.get("conditionLogic", "and").lower()),
    )


# ---------------------------------------------------------------------------
# Test functions
# ---------------------------------------------------------------------------

def test_bucket_vectors() -> None:
    """Bucket hash output must match the engine's MD5/little-endian result."""
    for v in VECTORS["bucketVectors"]:
        got = EV.compute_bucket(v["salt"], v["value"])
        assert got == v["expectedBucket"], (
            f"[{v['id']}] compute_bucket({v['salt']!r}, {v['value']!r}) "
            f"= {got}, expected {v['expectedBucket']}"
        )


def test_rollout_vectors() -> None:
    """Rollout bucketing selects the correct weighted variation."""
    for v in VECTORS["rolloutVectors"]:
        flag = _parse_flag({
            "key": "rollout",
            "version": 1,
            "type": "String",
            "enabled": True,
            "variations": [{"key": w["key"], "value": w["key"]} for w in v["variations"]],
            "rules": [],
            "fallthrough": {
                "type": "Rollout",
                "salt": v["salt"],
                "bucketBy": "userId",
                "variations": v["variations"],
            },
            "offVariation": v["variations"][0]["key"],
            "prerequisites": [],
        })
        ctx = EvaluationContext.from_dict({"userId": v["value"]})
        result = EV.evaluate(flag, ctx)
        assert result.variation_key == v["expectedVariation"], (
            f"[{v['id']}] variation_key={result.variation_key!r}, "
            f"expected {v['expectedVariation']!r}"
        )


def _run_condition_vectors(vectors: list) -> None:
    """Build the single-condition flag each vector describes and assert the match.

    Shared by the engine-generated condition vectors and the hand-authored
    date-grammar vectors, which have an identical input shape.
    """
    assert vectors, "no vectors loaded"
    for v in vectors:
        flag = _parse_flag({
            "key": "cond",
            "version": 1,
            "type": "String",
            "enabled": True,
            "variations": [
                {"key": "match", "value": "match"},
                {"key": "nomatch", "value": "nomatch"},
            ],
            "rules": [
                {
                    "id": "r",
                    "priority": 0,
                    "serve": {"type": "Fixed", "variation": "match"},
                    "conditionGroups": [
                        {
                            "operator": "And",
                            "conditions": [
                                {
                                    "attribute": "attr",
                                    "operator": v["operator"],
                                    "values": v["values"],
                                    "negate": v.get("negate", False),
                                }
                            ],
                        }
                    ],
                    "segmentKey": None,
                }
            ],
            "fallthrough": {"type": "Fixed", "variation": "nomatch"},
            "offVariation": "nomatch",
            "prerequisites": [],
        })
        ctx = EvaluationContext.from_dict({"attr": _typed_attr(v["attribute"])})
        got = EV.evaluate(flag, ctx).variation_key == "match"
        assert got == v["expectedMatch"], (
            f"[{v['id']}] operator={v['operator']!r} attr={v['attribute']!r} "
            f"values={v['values']!r} negate={v.get('negate', False)} "
            f"-> match={got}, expected {v['expectedMatch']}"
        )


def test_condition_vectors() -> None:
    """Individual condition operators must evaluate to the expected match result."""
    _run_condition_vectors(VECTORS["conditionVectors"])


def test_date_grammar_vectors() -> None:
    """A date operand outside the ISO grammar must match nothing (#2480).

    Hand-authored because the ENGINE DISSENTS: ``DateTimeOffset.TryParse`` under the
    invariant culture resolves ``05/15/2023``, ``Jan 1 2024`` and ``2024.01.01``, so
    generating these would assert the opposite of what every SDK must do. The engine
    keeps that leniency deliberately -- narrowing it would stop an already-saved
    operand from evaluating -- and Management rejects them on the write path instead.

    The six SDKs that always rejected these did so as a SIDE EFFECT of their grammar
    and not one of them asserted it, which is how js came to resolve a non-ISO operand
    in the host's timezone unnoticed.
    """
    _run_condition_vectors(VECTORS["dateGrammarVectors"])


def test_flag_vectors() -> None:
    """Full flag evaluation: variation_key, value (JSON-equal), and reason."""
    for v in VECTORS["flagVectors"]:
        all_flags = {f["key"]: _parse_flag(f) for f in v["flags"]}
        segments = {s["key"]: _parse_segment(s) for s in v.get("segments", [])}
        ctx = _context_from_fixture(v["context"])

        result = EV.evaluate(
            all_flags[v["flagKey"]],
            ctx,
            get_segment=segments.get,
            all_flags=all_flags,
        )

        exp = v["expected"]
        assert result.variation_key == exp["variation"], (
            f"[{v['id']}] variation_key={result.variation_key!r}, "
            f"expected {exp['variation']!r}"
        )
        assert json.dumps(result.value, sort_keys=True) == json.dumps(
            exp["value"], sort_keys=True
        ), (
            f"[{v['id']}] value={result.value!r}, expected {exp['value']!r}"
        )
        assert _normalize_reason(result) == exp["reason"], (
            f"[{v['id']}] reason={_normalize_reason(result)!r}, "
            f"expected {exp['reason']!r}"
        )


# ---------------------------------------------------------------------------
# coreContractVectors — the shared CORE's contract, one layer above the evaluator
# ---------------------------------------------------------------------------
#
# The four classes above assert what the evaluator computes, with the .NET engine
# as their oracle. This class asserts the shared core's client-facing contract,
# where the engine has no opinion and in fact disagrees: it returns null where an
# SDK must return the CALLER'S default. That is why #1989 and #2281 could not be
# locked with the existing classes, and why both shipped as 6-of-7-SDK
# divergences that no CI could see. These vectors are hand-authored, not
# engine-generated.
#
# The Python SDK exposes only a generic ``variation()`` — it has no typed
# accessors — so it cannot express the ``typeMismatch`` kind at all and skips it.
# The skip is explicit and asserted below, so a capability gap cannot quietly
# masquerade as a pass.

_CANONICAL_REASONS = {
    "Error": EvaluationReason.ERROR,
    "Fallthrough": EvaluationReason.FALLTHROUGH,
    "FlagNotFound": EvaluationReason.FLAG_NOT_FOUND,
}

_PYTHON_SUPPORTED_KINDS = {"match", "malformed", "notFound"}


def _contract_core(flags: list[dict[str, Any]]) -> Any:
    from featureflip._core import _SharedFeatureflipCore
    from featureflip.config import Config

    parsed = [_parse_flag(f) for f in flags]
    return _SharedFeatureflipCore(
        sdk_key=None,
        config=Config(),
        test_mode_flags={f.key: f for f in parsed},
    )


def test_core_contract_vectors() -> None:
    vectors = VECTORS["coreContractVectors"]
    assert vectors, "no coreContractVectors in fixture"

    executed = 0
    skipped_type_mismatch = 0

    for v in vectors:
        if v["kind"] not in _PYTHON_SUPPORTED_KINDS:
            skipped_type_mismatch += 1
            continue

        core = _contract_core(v["flags"])
        detail = core.evaluate(
            v["flagKey"], {"user_id": v["context"]["userId"]}, v["read"]["default"]
        )

        assert detail.value == v["expect"]["value"], (
            f"[{v['id']}] {v['description']}: value={detail.value!r}, "
            f"expected {v['expect']['value']!r}"
        )
        assert detail.reason == _CANONICAL_REASONS[v["expect"]["reason"]], (
            f"[{v['id']}] {v['description']}: reason={detail.reason!r}, "
            f"expected {v['expect']['reason']!r}"
        )
        executed += 1

    # A runner that silently skips everything is worse than no runner at all.
    assert executed >= 6, f"only {executed} contract vectors executed"
    assert skipped_type_mismatch == sum(
        1 for v in vectors if v["kind"] == "typeMismatch"
    ), "skip accounting drifted — every skipped vector must be a typeMismatch"
