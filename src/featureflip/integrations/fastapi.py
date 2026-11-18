"""FastAPI integration: build one client at startup, hand it to routes.

Requires the ``fastapi`` extra: ``pip install "featureflip[fastapi]"``.

    app = FastAPI(lifespan=featureflip_lifespan())

    @app.get("/")
    def home(client: FeatureflipClient = Depends(get_featureflip_client)):
        return {"on": client.variation("new-ui", {"user_id": "1"}, default=False)}
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi import __version__ as _fastapi_version
from starlette.requests import HTTPConnection  # noqa: TC002  (FastAPI resolves the annotation at runtime)

from featureflip.client import FeatureflipClient
from featureflip.config import Config

__all__ = ["WRAPPER_NAME", "featureflip_lifespan", "get_featureflip_client"]

WRAPPER_NAME = "featureflip-fastapi"

_STATE_ATTR = "featureflip"

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[Any]]


def _with_wrapper(config: Config | None) -> Config:
    """Tag requests with this integration unless the caller set a wrapper."""
    config = config or Config()
    if config.wrapper_name:
        return config
    return dataclasses.replace(
        config, wrapper_name=WRAPPER_NAME, wrapper_version=_fastapi_version
    )


def featureflip_lifespan(
    sdk_key: str | None = None,
    config: Config | None = None,
    *,
    client: FeatureflipClient | None = None,
    lifespan: Lifespan | None = None,
) -> Lifespan:
    """Return a FastAPI ``lifespan`` that owns the Featureflip client.

    The client is built at startup (the constructor blocks on the first flag
    fetch, so it runs in a worker thread) and stored on ``app.state.featureflip``
    and in the lifespan state (``request.state.featureflip``). The lifespan state
    is what reaches routes on a mounted sub-app, whose own ``app.state`` is empty.
    The client is closed at shutdown.

    Args:
        sdk_key: SDK key. Falls back to ``FEATUREFLIP_SDK_KEY``.
        config: Client configuration.
        client: Use an existing client (for example ``FeatureflipClient.for_testing``)
            instead of building one. It is not closed on shutdown.
        lifespan: Your own lifespan, entered after the client exists and exited
            before it closes.
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[Any]:
        owned = client is None
        if client is None:
            built = await asyncio.to_thread(
                FeatureflipClient, sdk_key, _with_wrapper(config)
            )
        else:
            built = client
        setattr(app.state, _STATE_ATTR, built)
        try:
            if lifespan is None:
                yield {_STATE_ATTR: built}
            else:
                async with lifespan(app) as state:
                    yield {**(state or {}), _STATE_ATTR: built}
        finally:
            if owned:
                await asyncio.to_thread(built.close)

    return _lifespan


def get_featureflip_client(connection: HTTPConnection) -> FeatureflipClient:
    """``Depends`` provider returning the client the lifespan created.

    Works on HTTP and WebSocket routes, and on routes of a sub-app mounted on
    the app that owns the lifespan.
    """
    found = getattr(connection.state, _STATE_ATTR, None)
    if found is None:
        found = getattr(connection.app.state, _STATE_ATTR, None)
    if found is None:
        raise RuntimeError(
            "No Featureflip client found. Create the app with "
            "FastAPI(lifespan=featureflip_lifespan())."
        )
    return found  # type: ignore[no-any-return]
