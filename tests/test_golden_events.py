"""Golden runner for the ``eventPayloadVectors`` class.

Locks what ``identify()`` and ``track()`` actually put on the wire:
``{type, flagKey, userId?, variation?, timestamp, metadata?}``.

That shape had no executable spec at all, which is the direct cause of #2359 —
a three-way payload divergence across six server SDKs (js/node/python forwarded
the caller's attributes as ``metadata``; php/go/ruby discarded them) sat
unnoticed indefinitely. Nothing compared an emitted event against an expected
shape, and the receiving end reduces every event to a counter tuple, so no
downstream assertion caught it either.

Hand-authored rather than engine-generated, because the engine emits no events:
it returns an ``EvaluationResult``, and the payload is built a layer above that.
See ``tools/golden-vectors/README.md`` for the full runner contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from featureflip import Config, FeatureflipClient
from featureflip._core import _reset_for_testing
from featureflip._http import HttpClient

VECTORS: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "golden" / "vectors.json").read_text()
)["eventPayloadVectors"]

# An ISO-8601 instant that designates UTC. Deliberately not an equality check:
# the precision and the zero-offset spelling differ legitimately per SDK (this
# one emits microseconds and a "+00:00" offset), so a literal expectation would
# lock in a divergence rather than a contract.
UTC_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)$")

# The context capabilities this SDK has. A vector requiring anything outside this
# set is skipped explicitly, so a structural gap cannot masquerade as a pass.
# Python takes a plain dict, so it has both: the identity spelling is observable,
# and a context can carry attributes with no identity at all.
CAPABILITIES = {"mapContext", "anonymousContext"}

RUNNABLE = [v for v in VECTORS if set(v.get("requires", [])) <= CAPABILITIES]

# Every evaluation vector runs through BOTH read paths. variation_detail() once
# recorded no event at all, which made every flag the OpenFeature provider reads
# invisible to analytics (#4300); pinning it here keeps the two paths identical.
CASES = [(v, "variation") for v in RUNNABLE] + [
    (v, "variation_detail") for v in RUNNABLE if v["kind"] == "evaluation"
]


def test_has_vectors_to_run() -> None:
    assert len(VECTORS) > 0


def test_executed_the_expected_number_of_vectors() -> None:
    """A runner that silently skips everything is worse than no runner at all."""
    assert len(RUNNABLE) >= 19
    assert len(CASES) >= 25


@pytest.mark.parametrize(
    ("vector", "accessor"),
    CASES,
    ids=[f"{v['id']}-{accessor}" for v, accessor in CASES],
)
def test_event_payload_vector(vector: dict[str, Any], accessor: str) -> None:
    _reset_for_testing()
    flags, segments = [], []
    if vector["kind"] == "evaluation":
        # The real wire parser, fed the same snapshot envelope the server sends.
        flags, segments = HttpClient.__new__(HttpClient).parse_flags_response(
            {
                "environment": "golden",
                "version": 1,
                "flags": vector["flags"],
                "segments": vector["segments"],
            }
        )
    with patch("featureflip._core.HttpClient") as mock_http:
        mock_http.return_value.get_flags.return_value = (flags, segments)
        client = FeatureflipClient(
            sdk_key=f"events-{vector['id']}",
            config=Config(streaming=False, send_events=False),
        )
        client._core._event_processor = MagicMock()

        try:
            if vector["kind"] == "identify":
                client.identify(vector["context"])
            elif vector["kind"] == "evaluation":
                # Boolean read with a False default: every vector's served value
                # is checked through the event, not the return value.
                getattr(client, accessor)(vector["flagKey"], vector["context"], False)
            elif "metadata" in vector:
                client.track(vector["eventKey"], vector["context"], vector["metadata"])
            else:
                # No `metadata` key at all -> the argument is omitted, which must
                # put the same bytes on the wire as an explicitly empty bag.
                client.track(vector["eventKey"], vector["context"])

            assert client._core._event_processor.queue_event.call_count == 1
            queued = client._core._event_processor.queue_event.call_args[0][0]
        finally:
            client.close()

    # Compare the SERIALIZED event, not the dict behind it. Omission is a
    # serialization-time property across the fleet (go's `omitempty`, java's
    # NON_NULL, php's empty array encoding as `[]`), and post_events hands this
    # dict straight to json= — so the round-trip is the real wire form.
    event = json.loads(json.dumps(queued))

    # The EXACT field set, not a subset: #2359 was a field being present in three
    # SDKs and absent in three, which a subset assertion cannot see.
    assert sorted(event.keys()) == sorted([*vector["expect"].keys(), "timestamp"])

    for field, expected in vector["expect"].items():
        assert event[field] == expected, field

    assert UTC_INSTANT.match(event["timestamp"]), event["timestamp"]
