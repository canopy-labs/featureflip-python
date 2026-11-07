"""Shared cross-SDK snapshot scenario against a real local HTTP server.

Sync against a stub API, stop the API, start a fresh client with the same store,
and assert real values are served and startup_info says ``snapshot``.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

from featureflip import Config, FeatureflipClient, FileSnapshotStore
from featureflip._core import _reset_for_testing
from tests.test_snapshot_store import payload

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

KEY = "parity-key"


class StubApi:
    def __init__(self) -> None:
        body = json.dumps(payload()).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/v1/sdk/flags" and self.headers.get(
                    "Authorization"
                ) == KEY:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def _reset() -> Generator[None, None, None]:
    _reset_for_testing()
    yield
    _reset_for_testing()


def config(url: str, store: FileSnapshotStore, **kw: Any) -> Config:
    return Config(
        base_url=url,
        streaming=False,
        send_events=False,
        poll_interval=999.0,
        connect_timeout=0.5,
        snapshot_store=store,
        **kw,
    )


def test_second_client_starts_from_the_first_clients_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    api = StubApi()
    a = FeatureflipClient(sdk_key=KEY, config=config(api.url, FileSnapshotStore(path)))
    try:
        assert a.startup_info.source == "live"
        expected = a.variation("base", {"user_id": "u"}, "dflt")
        assert expected == {"a": [1, 2.5, None]}
        deadline = time.monotonic() + 3
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert path.exists()
    finally:
        a.close()
    api.stop()

    b = FeatureflipClient(
        sdk_key=KEY,
        config=config(api.url, FileSnapshotStore(path), init_timeout=0.5),
    )
    try:
        assert b.is_initialized is True
        assert b.startup_info.source == "snapshot"
        assert b.startup_info.reason == "snapshot"
        assert b.variation("base", {"user_id": "u"}, "dflt") == expected
    finally:
        b.close()
