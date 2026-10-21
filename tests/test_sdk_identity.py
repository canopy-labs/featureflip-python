"""The SDK token plus a wrapper's token in the User-Agent (#4153, contract in #4138).

``featureflip-python/<version>`` was already sent; what was missing is a way for a
library that wraps the SDK (the OpenFeature provider) to name itself, so traffic
through it can be counted separately. Asserted on the wire for the flag fetch and
the event post, and on the headers the stream sends, since the stream opens its own
httpx client and shares nothing with ``HttpClient``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import respx
from httpx import Response
from structlog.testing import capture_logs

from featureflip._core import _configs_equal
from featureflip._http import HttpClient
from featureflip._streaming import StreamingHandler
from featureflip._version import __version__
from featureflip.config import Config

BASE = "https://api.example.com"
SDK_TOKEN = f"featureflip-python/{__version__}"


def _agents(config: Config) -> dict[str, str]:
    """User-Agent sent on the flag fetch, the event post and the stream, by path."""
    with respx.mock:
        flags = respx.get(f"{BASE}/v1/sdk/flags").mock(return_value=Response(200, json={"flags": []}))
        events = respx.post(f"{BASE}/v1/sdk/events").mock(return_value=Response(202))
        client = HttpClient(sdk_key="k", config=config)
        client.get_flags()
        client.post_events([{"type": "Evaluation", "flagKey": "f"}])
        client.close()

    stream = StreamingHandler(
        sdk_key="k",
        config=config,
        http_client=MagicMock(),
        on_flag_updated=MagicMock(),
        on_flag_deleted=MagicMock(),
        on_segment_updated=MagicMock(),
        on_error=MagicMock(),
        on_update=MagicMock(),
    )
    return {
        "flags": flags.calls[0].request.headers["User-Agent"],
        "events": events.calls[0].request.headers["User-Agent"],
        "stream": stream._get_headers()["User-Agent"],
    }


def test_no_wrapper_sends_the_bare_sdk_token() -> None:
    assert _agents(Config(base_url=BASE)) == {"flags": SDK_TOKEN, "events": SDK_TOKEN, "stream": SDK_TOKEN}


def test_wrapper_is_appended_on_every_path() -> None:
    config = Config(base_url=BASE, wrapper_name="featureflip-openfeature-python", wrapper_version="0.1.1")

    expected = f"{SDK_TOKEN} featureflip-openfeature-python/0.1.1"
    assert _agents(config) == {"flags": expected, "events": expected, "stream": expected}


def test_wrapper_without_a_version_is_sent_as_a_bare_name() -> None:
    assert _agents(Config(base_url=BASE, wrapper_name="my-wrapper"))["flags"] == f"{SDK_TOKEN} my-wrapper"


def test_invalid_wrapper_is_dropped_with_a_warning() -> None:
    # A space is not a legal product-token character. Identity metadata must never
    # stop the client starting, so the wrapper is dropped and said so.
    with capture_logs() as logs:
        agents = _agents(Config(base_url=BASE, wrapper_name="bad name", wrapper_version="1.0"))

    assert agents["flags"] == SDK_TOKEN
    assert any(e["log_level"] == "warning" and "bad name" in str(e) for e in logs)


def test_wrapper_does_not_count_as_a_different_config() -> None:
    # It identifies the caller rather than changing behavior, so the provider sharing
    # an SDK key with the host application must not trip the mismatch warning.
    assert _configs_equal(Config(), Config(wrapper_name="w", wrapper_version="1"))
