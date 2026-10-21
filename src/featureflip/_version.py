"""Single source of the SDK's version.

Read from the installed distribution's metadata rather than hardcoded, because
``publish-python-sdk.yml`` sets the release version by ``sed``-ing
``pyproject.toml`` and nothing else. A literal here would be correct only until
the next release, which is exactly how the User-Agent came to advertise 0.1.0
from a 2.4.x SDK (#2141).
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

import structlog

try:
    __version__ = _distribution_version("featureflip")
except PackageNotFoundError:  # pragma: no cover - source tree without an install
    # Running straight from src/ (e.g. PYTHONPATH=src) with no dist-info to read.
    # Report it plainly instead of guessing a number that could be wrong.
    __version__ = "0.0.0+unknown"

USER_AGENT = f"featureflip-python/{__version__}"

# RFC 9110 product token: tchars.
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def user_agent(wrapper_name: str | None, wrapper_version: str | None) -> str:
    """The User-Agent: the SDK token, then the wrapper's token when one is set (#4153).

    A wrapper that is not a valid product token is dropped with a warning rather
    than raised, because identity metadata must never stop the client starting.
    """
    if not wrapper_name:
        return USER_AGENT
    valid = _TOKEN.fullmatch(wrapper_name) is not None and (
        wrapper_version is None or _TOKEN.fullmatch(wrapper_version) is not None
    )
    if not valid:
        structlog.get_logger().warning(
            "featureflip_invalid_wrapper",
            message=f"Ignoring wrapper '{wrapper_name}/{wrapper_version}': not a valid User-Agent product token",
        )
        return USER_AGENT
    if wrapper_version is None:
        return f"{USER_AGENT} {wrapper_name}"
    return f"{USER_AGENT} {wrapper_name}/{wrapper_version}"


__all__ = ["USER_AGENT", "__version__", "user_agent"]
