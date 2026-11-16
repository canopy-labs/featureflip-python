"""The SDK key must never reach a log line or an exception message (#5166).

Drives the noisy paths with a sentinel key, captures everything at DEBUG, and
makes the stubbed transport errors quote the ``Authorization`` header, so a
passing run proves the redactor and not just clean call sites.
"""

from __future__ import annotations

import logging
import time
import traceback
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx
import structlog
from httpx import Response
from structlog.testing import capture_logs

from featureflip import FeatureflipClient, _redact
from featureflip._core import _reset_for_testing
from featureflip.config import Config

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

KEY = "sdk_server_SentinelKeyMustNeverAppearInLogs-0123456789"
BASE = "https://sentinel.example.com"
FLAGS = {
    "flags": [
        {
            "key": "a-bool",
            "version": 1,
            "type": "boolean",
            "enabled": True,
            "variations": [
                {"key": "on", "value": True},
                {"key": "off", "value": False},
            ],
            "rules": [],
            "fallthrough": {"type": "fixed", "variation": "on"},
            "offVariation": "off",
        }
    ],
    "segments": [],
}


def leaky(request: httpx.Request) -> Response:
    """A transport error that quotes the request headers, as a third party might."""
    raise httpx.ConnectError(
        f"connection refused; Authorization: {request.headers['authorization']}"
    )


def cfg(**kw: Any) -> Config:
    kw.setdefault("base_url", BASE)
    kw.setdefault("streaming", False)
    kw.setdefault("poll_interval", 0.05)
    kw.setdefault("flush_interval", 0.05)
    kw.setdefault("init_timeout", 1.0)
    return Config(**kw)


@pytest.fixture(autouse=True)
def _isolate() -> Iterator[None]:
    _redact._keys = ()
    _reset_for_testing()
    yield
    _reset_for_testing()


@pytest.fixture
def sink(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> Iterator[Callable[[], str]]:
    """Everything emitted anywhere: structlog's default sink, stdlib logging, and
    the structured event dicts."""
    caplog.set_level(logging.DEBUG)
    with capture_logs() as events:

        def collected() -> str:
            out = capsys.readouterr()
            return "\n".join(
                [out.out, out.err, caplog.text, *(repr(e) for e in events)]
            )

        yield collected


def drive(client: FeatureflipClient, raised: list[BaseException]) -> None:
    """Exercise the public surface; record anything the SDK raises."""
    steps: list[Callable[[], object]] = [
        lambda: client.variation("a-bool", {"user_id": "u"}, False),
        lambda: client.variation("a-bool", {"user_id": "u"}, "wrong-type"),
        lambda: client.variation("no-such-flag", {"user_id": "u"}, False),
        lambda: client.variation_detail("no-such-flag", {"user_id": "u"}, 1),
        lambda: client.track("clicked", {"user_id": "u"}, {"k": "v"}),
        lambda: client.identify({"user_id": "u"}),
        client.flush,
    ]
    for step in steps:
        try:
            step()
        except Exception as e:
            raised.append(e)


def assert_clean(sink: Callable[[], str], raised: list[BaseException]) -> None:
    for e in raised:
        assert KEY not in str(e)
        assert KEY not in repr(e)
        assert KEY not in "".join(traceback.format_exception(e))
        # No chain for a host or error tracker to walk into the original.
        assert e.__cause__ is None
        assert e.__context__ is None
    assert KEY not in sink()


def run_case(
    sink: Callable[[], str],
    flags_response: Any,
    config: Config | None = None,
) -> None:
    raised: list[BaseException] = []
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/v1/sdk/flags").mock(side_effect=flags_response)
        router.post(f"{BASE}/v1/sdk/events").mock(side_effect=leaky)
        router.get(f"{BASE}/v1/sdk/stream").mock(side_effect=leaky)
        client: FeatureflipClient | None = None
        try:
            client = FeatureflipClient(sdk_key=KEY, config=config or cfg())
        except Exception as e:
            raised.append(e)
        if client is not None:
            drive(client, raised)
            # The "options ignored" warning path: same key, different config.
            try:
                other = FeatureflipClient(sdk_key=KEY, config=cfg(poll_interval=9.0))
                other.close()
            except Exception as e:
                raised.append(e)
            time.sleep(0.2)  # let the poller and event flusher hit the failures
            try:
                client.close()
            except Exception as e:
                raised.append(e)
    assert_clean(sink, raised)


class TestSentinelKeyNeverLogged:
    def test_init_200_then_poll_errors_quoting_the_key(
        self, sink: Callable[[], str]
    ) -> None:
        calls: list[int] = []

        def first_ok_then_leaky(request: httpx.Request) -> Response:
            calls.append(1)
            if len(calls) == 1:
                return Response(200, json=FLAGS)
            return leaky(request)

        run_case(sink, first_ok_then_leaky)
        assert len(calls) > 1  # the poller really hit the failure

    def test_init_401_with_the_key_in_the_body(self, sink: Callable[[], str]) -> None:
        run_case(sink, lambda _r: Response(401, json={"error": f"bad key {KEY}"}))

    def test_init_500_with_the_key_in_the_body(self, sink: Callable[[], str]) -> None:
        run_case(sink, lambda _r: Response(500, text=f"upstream said {KEY}"))

    def test_snapshot_write_failure_after_a_good_fetch(
        self, sink: Callable[[], str]
    ) -> None:
        class Store:
            def get(self) -> str | None:
                return None

            def put(self, _text: str) -> None:
                raise RuntimeError(f"store write failed for {KEY}")

        run_case(sink, lambda _r: Response(200, json=FLAGS), cfg(snapshot_store=Store()))

    def test_init_transport_error_quoting_the_key(
        self, sink: Callable[[], str]
    ) -> None:
        run_case(sink, leaky)

    def test_init_transport_error_with_snapshot_store(
        self, sink: Callable[[], str]
    ) -> None:
        class Store:
            def get(self) -> str | None:
                return None

            def put(self, _text: str) -> None:
                raise RuntimeError(f"store write failed for {KEY}")

        run_case(sink, leaky, cfg(snapshot_store=Store()))

    def test_streaming_transport_error_quoting_the_key(
        self, sink: Callable[[], str]
    ) -> None:
        run_case(sink, lambda _r: Response(200, json=FLAGS), cfg(streaming=True))

    def test_the_default_structlog_sink_is_covered(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        structlog.reset_defaults()
        raised: list[BaseException] = []
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{BASE}/v1/sdk/flags").mock(side_effect=leaky)
            try:
                FeatureflipClient(sdk_key=KEY, config=cfg())
            except Exception as e:
                raised.append(e)
        out = capsys.readouterr()
        assert KEY not in out.out + out.err
        assert all(KEY not in str(e) for e in raised)
