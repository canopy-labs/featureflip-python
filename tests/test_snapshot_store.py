"""Tests for the snapshot contract: envelope, wire round trip and FileSnapshotStore."""

from __future__ import annotations

import hashlib
import json
import stat
import sys
import threading
from typing import TYPE_CHECKING, Any

import pytest

from featureflip._http import parse_wire_payload
from featureflip._wire import serialize_flags_payload
from featureflip.models import ConditionOperator
from featureflip.snapshot import (
    FileSnapshotStore,
    SnapshotInvalidError,
    StartupInfo,
    compute_key_hash,
    decode_envelope,
    encode_envelope,
)

if TYPE_CHECKING:
    from pathlib import Path

BASE = "https://eval.example.com"
KEY = "sdk-key-1"
NOW = 1_800_000_000_000


def payload() -> dict[str, Any]:
    return {
        "flags": [
            {
                "key": "checkout",
                "version": 3,
                "type": "Boolean",
                "enabled": True,
                "variations": [
                    {"key": "on", "value": True},
                    {"key": "off", "value": False},
                ],
                "rules": [
                    {
                        "id": "r1",
                        "priority": 0,
                        "conditionGroups": [
                            {
                                "operator": "And",
                                "conditions": [
                                    {
                                        "attribute": "plan",
                                        "operator": "In",
                                        "values": ["pro", "team"],
                                        "negate": False,
                                    }
                                ],
                            }
                        ],
                        "serve": {"type": "Fixed", "variation": "on"},
                        "segmentKey": "beta",
                    },
                    {
                        "id": "r2",
                        "priority": 1,
                        "conditionGroups": [],
                        "serve": {
                            "type": "Rollout",
                            "bucketBy": "user_id",
                            "salt": "s",
                            "variations": [
                                {"key": "on", "weight": 30},
                                {"key": "off", "weight": 70},
                            ],
                        },
                    },
                ],
                "fallthrough": {"type": "Fixed", "variation": "off"},
                "offVariation": "off",
                "prerequisites": [
                    {"prerequisiteFlagKey": "base", "expectedVariationKey": "on"}
                ],
            },
            {
                "key": "base",
                "version": 1,
                "type": "Json",
                "enabled": False,
                "variations": [{"key": "on", "value": {"a": [1, 2.5, None]}}],
                "rules": [],
                "fallthrough": {"type": "Fixed", "variation": "on"},
                "offVariation": "on",
                "prerequisites": [],
            },
        ],
        "segments": [
            {
                "key": "beta",
                "version": 2,
                "conditions": [
                    {
                        "attribute": "v",
                        "operator": "SemverGreaterThanOrEqual",
                        "values": ["1.0.0"],
                        "negate": True,
                    }
                ],
                "conditionLogic": "Or",
            }
        ],
    }


def make_envelope(**overrides: Any) -> str:
    env: dict[str, Any] = {
        "v": 1,
        "keyHash": compute_key_hash(BASE, KEY),
        "fetchedAt": NOW - 5_000,
        "data": payload(),
    }
    env.update(overrides)
    return json.dumps(env)


def invalid(text: Any) -> str:
    with pytest.raises(SnapshotInvalidError) as exc:
        decode_envelope(text, compute_key_hash(BASE, KEY), NOW)
    return exc.value.why


class TestKeyHash:
    def test_hex_sha256_of_stripped_base_url_newline_key(self) -> None:
        expected = hashlib.sha256(f"{BASE}\n{KEY}".encode()).hexdigest()
        assert compute_key_hash(BASE, KEY) == expected
        assert compute_key_hash(BASE + "///", KEY) == expected

    def test_differs_per_key_and_base_url(self) -> None:
        assert compute_key_hash(BASE, "other") != compute_key_hash(BASE, KEY)
        assert compute_key_hash("https://other.example.com", KEY) != compute_key_hash(
            BASE, KEY
        )


class TestWireRoundTrip:
    def test_serialized_state_parses_back_to_equal_models(self) -> None:
        flags, segments = parse_wire_payload(payload())
        text = serialize_flags_payload(flags, segments)
        flags2, segments2 = parse_wire_payload(json.loads(text))
        assert flags2 == flags
        assert segments2 == segments

    def test_serialized_state_is_the_wire_shape(self) -> None:
        flags, segments = parse_wire_payload(payload())
        data = json.loads(serialize_flags_payload(flags, segments))
        assert data == payload()

    def test_every_operator_round_trips(self) -> None:
        wire_ops = [
            "".join(part.capitalize() for part in op.value.split("_"))
            for op in ConditionOperator
        ]
        p = payload()
        p["flags"][0]["rules"][0]["conditionGroups"][0]["conditions"] = [
            {"attribute": "a", "operator": w, "values": ["x"], "negate": False}
            for w in wire_ops
        ]
        flags, segments = parse_wire_payload(p)
        flags2, _ = parse_wire_payload(
            json.loads(serialize_flags_payload(flags, segments))
        )
        assert flags2 == flags

    def test_unknown_operator_and_flag_type_survive(self) -> None:
        p = payload()
        p["flags"][0]["type"] = "futuretype"
        p["flags"][0]["rules"][0]["conditionGroups"][0]["conditions"][0][
            "operator"
        ] = "FutureOp"
        flags, segments = parse_wire_payload(p)
        flags2, _ = parse_wire_payload(
            json.loads(serialize_flags_payload(flags, segments))
        )
        assert flags2 == flags


class TestEnvelope:
    def test_encode_matches_documented_shape(self) -> None:
        text = encode_envelope("abc", 1_700_000_000_123, '{"flags":[],"segments":[]}')
        assert json.loads(text) == {
            "v": 1,
            "keyHash": "abc",
            "fetchedAt": 1_700_000_000_123,
            "data": {"flags": [], "segments": []},
        }

    def test_decode_valid(self) -> None:
        decoded = decode_envelope(make_envelope(), compute_key_hash(BASE, KEY), NOW)
        assert decoded.fetched_at_ms == NOW - 5_000
        assert [f.key for f in decoded.flags] == ["checkout", "base"]
        assert [s.key for s in decoded.segments] == ["beta"]

    def test_non_string_is_invalid(self) -> None:
        assert "non-string" in invalid(b"{}")
        assert "non-string" in invalid({"v": 1})

    def test_empty_text_is_invalid(self) -> None:
        assert invalid("") == "empty text"

    def test_garbage_and_truncated(self) -> None:
        assert invalid("not json") == "not valid JSON"
        assert invalid(make_envelope()[:40]) == "not valid JSON"

    def test_not_an_object(self) -> None:
        assert invalid("[1]") == "not a snapshot envelope"
        assert invalid("null") == "not a snapshot envelope"
        assert invalid("3") == "not a snapshot envelope"

    def test_wrong_version(self) -> None:
        assert invalid(make_envelope(v=2)) == "unsupported envelope version"
        assert invalid(make_envelope(v=True)) == "unsupported envelope version"

    def test_key_hash_mismatch(self) -> None:
        other = compute_key_hash(BASE, "another-key")
        assert "different SDK key" in invalid(make_envelope(keyHash=other))
        other_url = compute_key_hash("https://x.example.com", KEY)
        assert "different SDK key" in invalid(make_envelope(keyHash=other_url))

    @pytest.mark.parametrize("bad", [0, -5, 1.5, "1700", None, True])
    def test_fetched_at_must_be_positive_integer(self, bad: Any) -> None:
        assert invalid(make_envelope(fetchedAt=bad)) == (
            "fetchedAt is not a positive integer"
        )

    def test_future_tolerance(self) -> None:
        ok = decode_envelope(
            make_envelope(fetchedAt=NOW + 60_000), compute_key_hash(BASE, KEY), NOW
        )
        assert ok.fetched_at_ms == NOW + 60_000
        assert invalid(make_envelope(fetchedAt=NOW + 60_001)) == (
            "fetchedAt is in the future"
        )

    def test_data_missing_or_wrong_type(self) -> None:
        assert invalid(make_envelope(data=None)) == "missing data"
        assert invalid(make_envelope(data="x")) == "missing data"

    @pytest.mark.parametrize(
        "data",
        [
            {},
            {"flags": []},
            {"flags": {}, "segments": []},
            {"flags": [{"key": "a"}], "segments": []},
            {"flags": [1], "segments": []},
        ],
    )
    def test_payload_validation_failure(self, data: Any) -> None:
        assert invalid(make_envelope(data=data)) == "data failed payload validation"

    def test_wrong_enum_type_discards_wholesale(self) -> None:
        p = payload()
        p["segments"][0]["conditionLogic"] = 0
        assert invalid(make_envelope(data=p)) == "data failed payload validation"


class TestStartupInfo:
    def test_defaults_and_frozen(self) -> None:
        info = StartupInfo("none")
        assert info.reason is None
        assert info.snapshot_age_seconds is None
        with pytest.raises(AttributeError):
            info.source = "live"  # type: ignore[misc]


class TestFileSnapshotStore:
    def test_round_trip(self, tmp_path: Path) -> None:
        store = FileSnapshotStore(tmp_path / "snap.json")
        store.put("héllo ✓")
        assert store.get() == "héllo ✓"

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        store = FileSnapshotStore(str(tmp_path / "snap.json"))
        store.put("x")
        assert store.get() == "x"

    def test_missing_file_is_silent_miss(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert FileSnapshotStore(tmp_path / "nope.json").get() is None
        out = capsys.readouterr()
        assert "snapshot" not in out.out + out.err

    def test_directory_warns_and_misses(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert FileSnapshotStore(tmp_path).get() is None
        out = capsys.readouterr()
        assert "snapshot_file_read_failed" in out.out + out.err

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
    def test_mode_is_0600_even_over_a_world_readable_file(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "snap.json"
        path.write_text("old")
        path.chmod(0o644)
        store = FileSnapshotStore(path)
        store.put("new")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text() == "new"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
    def test_fresh_file_is_0600(self, tmp_path: Path) -> None:
        path = tmp_path / "snap.json"
        FileSnapshotStore(path).put("x")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_no_temp_file_left_after_concurrent_puts(self, tmp_path: Path) -> None:
        path = tmp_path / "snap.json"
        store = FileSnapshotStore(path)
        threads = [
            threading.Thread(target=store.put, args=(f"text-{i}" * 1000,))
            for i in range(16)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(p.name for p in tmp_path.iterdir()) == ["snap.json"]
        assert store.get() in {f"text-{i}" * 1000 for i in range(16)}

    def test_put_into_missing_directory_warns_and_leaves_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = FileSnapshotStore(tmp_path / "missing" / "snap.json")
        store.put("x")  # must not raise
        assert not (tmp_path / "missing").exists()
        out = capsys.readouterr()
        assert "snapshot_file_write_failed" in out.out + out.err

    def test_put_failure_removes_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pathlib import Path as P

        def boom(*_args: Any) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(P, "replace", boom)
        FileSnapshotStore(tmp_path / "snap.json").put("x")
        assert list(tmp_path.iterdir()) == []

    def test_does_not_mkdir_the_parent(self, tmp_path: Path) -> None:
        FileSnapshotStore(tmp_path / "a" / "b.json").put("x")
        assert not (tmp_path / "a").exists()
