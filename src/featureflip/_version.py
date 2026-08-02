"""Single source of the SDK's version.

Read from the installed distribution's metadata rather than hardcoded, because
``publish-python-sdk.yml`` sets the release version by ``sed``-ing
``pyproject.toml`` and nothing else. A literal here would be correct only until
the next release, which is exactly how the User-Agent came to advertise 0.1.0
from a 2.4.x SDK (#2141).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

try:
    __version__ = _distribution_version("featureflip")
except PackageNotFoundError:  # pragma: no cover - source tree without an install
    # Running straight from src/ (e.g. PYTHONPATH=src) with no dist-info to read.
    # Report it plainly instead of guessing a number that could be wrong.
    __version__ = "0.0.0+unknown"

USER_AGENT = f"featureflip-python/{__version__}"

__all__ = ["USER_AGENT", "__version__"]
