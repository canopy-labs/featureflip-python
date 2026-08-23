"""Runner for the shared ``malformedConfigVectors`` class (#2315).

The rule: a config payload violating the wire contract is discarded WHOLESALE,
never partially applied. One server bug (#2279 — enums as integers over SSE but
strings over REST) produced five different user-visible outcomes because every SDK
improvised; this class makes a sixth improvisation fail a build.

Two things about python differ from its siblings, and the runner is shaped around
them rather than papering over them:

* **No store class.** ``_SharedFeatureflipCore`` holds plain ``_flags``/``_segments``
  dicts, replaced in ``_on_polling_update`` only *after* ``parse_flags_response``
  returns. So "store unchanged" is a property of the call ORDER, and the runner
  models exactly that: parse first, assign only on success.
* **No single malformed-payload exception.** ruby raises ``MalformedPayloadError``
  and js raises ``MalformedPayloadError``; python surfaces whatever the failing
  coercion raised (``ValueError`` from ``ConditionOperator(0)``, ``AttributeError``
  from ``.lower()`` on an int). The runner therefore catches that family explicitly
  instead of bare ``Exception``, so an unrelated crash still fails the test.
"""

import json
from pathlib import Path

import pytest

from featureflip._http import HttpClient

_BLOCK = json.loads(
    (Path(__file__).parent / "golden" / "vectors.json").read_text()
)["malformedConfigVectors"]

# The coercion failures a contract violation actually produces in python. Deliberately
# NOT bare Exception — that would let an unrelated bug masquerade as a clean rejection.
_REJECTION = (ValueError, AttributeError, TypeError, KeyError)


def _client() -> HttpClient:
    # The pure parser needs no network; __init__ would build a session.
    return HttpClient.__new__(HttpClient)


def _seeded(client: HttpClient) -> dict:
    """Apply the shared seed. A runner whose seed silently failed would 'pass' every
    reject vector for entirely the wrong reason, so this asserts rather than returns."""
    flags, segments = client.parse_flags_response(_BLOCK["seed"])
    store = {"flags": {f.key: f for f in flags}, "segments": {s.key: s for s in segments}}
    assert "mc-seed" in store["flags"], "seed snapshot did not apply"
    return store


def test_fixture_has_vectors():
    assert len(_BLOCK["vectors"]) >= 8


@pytest.mark.parametrize("vector", _BLOCK["vectors"], ids=lambda v: v["id"])
def test_malformed_config_vector(vector):
    client = _client()
    store = _seeded(client)
    label = f"[{vector['id']}] {vector['description']}"

    try:
        flags, segments = client.parse_flags_response(vector["payload"])
        # Mirrors _on_polling_update: the store is replaced only once the parse
        # has fully succeeded, which is what makes the discard wholesale.
        store = {
            "flags": {f.key: f for f in flags},
            "segments": {s.key: s for s in segments},
        }
        applied = True
    except _REJECTION:
        applied = False

    if vector["expect"] == "reject":
        assert not applied, f"{label}: payload was accepted"
        assert "mc-seed" in store["flags"], f"{label}: seeded config was replaced"
        assert "mc-bad-type" not in store["flags"], f"{label}: partially applied"
    elif vector["expect"] == "accept":
        assert applied, f"{label}: forward-compatible payload was rejected"
        assert (
            "mc-accepted-flag" in store["flags"] or "mc-accepted" in store["segments"]
        ), f"{label}: accepted payload did not apply"
    else:
        raise AssertionError(f"unmapped expect {vector['expect']!r}")
