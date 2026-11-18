"""FastAPI integration tests. Offline: stub clients, no network."""

from __future__ import annotations

import subprocess
import sys
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import pytest
from fastapi import Depends, FastAPI, Request, WebSocket
from fastapi.testclient import TestClient

from featureflip import Config, FeatureflipClient
from featureflip.integrations import fastapi as ff_fastapi
from featureflip.integrations.fastapi import (
    featureflip_lifespan,
    get_featureflip_client,
)


def _app(**kwargs: Any) -> FastAPI:
    app = FastAPI(lifespan=featureflip_lifespan(**kwargs))

    @app.get("/flag")
    def flag(
        client: FeatureflipClient = Depends(get_featureflip_client),  # noqa: B008
    ) -> dict[str, Any]:
        return {"on": client.variation("new-ui", {"user_id": "1"}, default=False)}

    return app


class _FakeClient:
    instances: ClassVar[list[_FakeClient]] = []

    def __init__(self, sdk_key: str | None = None, config: Config | None = None):
        self.sdk_key = sdk_key
        self.config = config
        self.closed = 0
        _FakeClient.instances.append(self)

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClient]:
    _FakeClient.instances = []
    monkeypatch.setattr(ff_fastapi, "FeatureflipClient", _FakeClient)
    return _FakeClient


def test_plain_import_does_not_import_fastapi() -> None:
    code = "import sys, featureflip; sys.exit('fastapi' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_dependency_resolves_the_supplied_client_and_leaves_it_open() -> None:
    client = FeatureflipClient.for_testing({"new-ui": True})
    with TestClient(_app(client=client)) as http:
        assert http.get("/flag").json() == {"on": True}
        assert http.app.state.featureflip is client  # type: ignore[attr-defined]
    assert client.variation("new-ui", {}, default=False) is True  # still open


def test_lifespan_builds_one_client_and_closes_it_on_shutdown(
    fake: type[_FakeClient],
) -> None:
    app = FastAPI(lifespan=featureflip_lifespan(sdk_key="sdk-abc"))
    with TestClient(app) as http:
        assert len(fake.instances) == 1
        assert app.state.featureflip is fake.instances[0]
        assert fake.instances[0].closed == 0
        del http
    assert fake.instances[0].closed == 1


def test_config_and_wrapper_reach_the_client(fake: type[_FakeClient]) -> None:
    config = Config(init_timeout=3.0)
    with TestClient(FastAPI(lifespan=featureflip_lifespan("sdk-abc", config))):
        pass
    got = fake.instances[0]
    assert got.sdk_key == "sdk-abc"
    assert got.config is not None
    assert got.config.init_timeout == 3.0
    assert got.config.wrapper_name == "featureflip-fastapi"
    assert got.config.wrapper_version


def test_callers_wrapper_is_kept(fake: type[_FakeClient]) -> None:
    config = Config(wrapper_name="mine", wrapper_version="1.0.0")
    with TestClient(FastAPI(lifespan=featureflip_lifespan("k", config))):
        pass
    assert fake.instances[0].config is not None
    assert fake.instances[0].config.wrapper_name == "mine"


def test_missing_sdk_key_fails_clearly_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FEATUREFLIP_SDK_KEY", raising=False)
    app = FastAPI(lifespan=featureflip_lifespan())
    with pytest.raises(ValueError, match="SDK key is required"), TestClient(app):
        pass


def test_wraps_an_existing_lifespan_in_order(fake: type[_FakeClient]) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def mine(app: FastAPI):  # type: ignore[no-untyped-def]
        events.append(f"start:{app.state.featureflip is fake.instances[0]}")
        yield
        events.append(f"stop:closed={fake.instances[0].closed}")

    app = FastAPI(lifespan=featureflip_lifespan(sdk_key="k", lifespan=mine))
    with TestClient(app):
        pass
    assert events == ["start:True", "stop:closed=0"]
    assert fake.instances[0].closed == 1


def test_dependency_without_lifespan_raises_a_clear_error() -> None:
    app = FastAPI()

    @app.get("/x")
    def x(
        _: FeatureflipClient = Depends(get_featureflip_client),  # noqa: B008
    ) -> dict[str, bool]:
        return {}

    with (
        TestClient(app, raise_server_exceptions=True) as http,
        pytest.raises(RuntimeError, match="featureflip_lifespan"),
    ):
        http.get("/x")


def test_dependency_works_in_a_mounted_sub_app() -> None:
    # A mounted sub-app's own lifespan never runs, so app.state is empty there.
    client = FeatureflipClient.for_testing({"new-ui": True})
    sub = FastAPI()

    @sub.get("/flag")
    def flag(
        c: FeatureflipClient = Depends(get_featureflip_client),  # noqa: B008
    ) -> dict[str, Any]:
        return {"on": c.variation("new-ui", {"user_id": "1"}, default=False)}

    root = FastAPI(lifespan=featureflip_lifespan(client=client))
    root.mount("/sub", sub)
    with TestClient(root) as http:
        assert http.get("/sub/flag").json() == {"on": True}


def test_dependency_works_on_a_websocket_route() -> None:
    client = FeatureflipClient.for_testing({"new-ui": True})
    app = FastAPI(lifespan=featureflip_lifespan(client=client))

    @app.websocket("/ws")
    async def ws(
        websocket: WebSocket,
        c: FeatureflipClient = Depends(get_featureflip_client),  # noqa: B008
    ) -> None:
        await websocket.accept()
        await websocket.send_json(
            {"on": c.variation("new-ui", {"user_id": "1"}, default=False)}
        )
        await websocket.close()

    with TestClient(app) as http, http.websocket_connect("/ws") as conn:
        assert conn.receive_json() == {"on": True}


def test_user_lifespan_state_is_kept_alongside_the_client() -> None:
    client = FeatureflipClient.for_testing({})

    @asynccontextmanager
    async def mine(_: FastAPI):  # type: ignore[no-untyped-def]
        yield {"db": "conn"}

    app = FastAPI(lifespan=featureflip_lifespan(client=client, lifespan=mine))

    @app.get("/s")
    def s(request: Request) -> dict[str, Any]:
        return {"db": request.state.db, "ff": request.state.featureflip is client}

    with TestClient(app) as http:
        assert http.get("/s").json() == {"db": "conn", "ff": True}
