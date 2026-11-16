"""Strips the SDK key out of everything the SDK logs.

Failure paths log the underlying exception text (``error=str(e)``) because a
silent failure is worse. That text comes from httpx or from a caller-supplied
transport, and the SDK sets the key as the ``Authorization`` header on every
request, so a transport that quotes request headers in its error message hands
the credential to whatever the host ships logs to (#5167).

Module loggers are shared by every client in the process while a key belongs
to one core, so the redactor holds a process-wide set of every key a core has
been built with. Keys are never released: the set is bounded by the distinct
keys the process uses, redacting a closed client's key is harmless, and
releasing on shutdown would open a window where a still-running background
thread of another client (or a late log from this one) logs the key unredacted.
Every SDK module logs through :func:`get_logger`, which wraps the host's
structlog logger, so the default sink is covered too and a call site cannot
forget to opt in.

Redacted: the event, positional ``%`` args, every keyword value (nested
containers included) and ``exc_info``, which is rendered to redacted text under
``exception`` and never passed on as a live object (a host renderer such as
``format_exc_info`` would print the real traceback). The caller's exception
objects are never mutated.

Internal, not part of the public API.
"""

from __future__ import annotations

import threading
import traceback
from typing import Any

import structlog

REPLACEMENT = "[redacted]"

# A real key is ``sdk_server_``/``sdk_client_`` plus 43 base64url characters:
# 54 in total, always. Redacting a far shorter value corrupts every log line it
# appears inside (key ``k`` turns "skipped" into "s[redacted]ipped"). A string
# too short to be a Featureflip key cannot authenticate, so there is no
# credential to protect.
MIN_REDACTABLE_LENGTH = 20

_lock = threading.Lock()
# Copy-on-write: readers take the tuple without locking, writers swap it.
_keys: tuple[str, ...] = ()

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "warn": 30, "error": 40,
           "critical": 50, "fatal": 50, "exception": 40}


def register_key(key: str | None) -> None:
    """Start redacting ``key`` for the rest of the process."""
    global _keys
    if not key or len(key) < MIN_REDACTABLE_LENGTH:
        return
    with _lock:
        if key not in _keys:
            _keys = (*_keys, key)


def redact_text(text: str, keys: tuple[str, ...] | None = None) -> str:
    for key in _keys if keys is None else keys:
        if key in text:
            text = text.replace(key, REPLACEMENT)
    return text


def _safe_str(value: Any) -> str | None:
    try:
        return str(value)
    except Exception:
        return None


def _redact_value(value: Any, keys: tuple[str, ...], seen: set[int]) -> Any:
    if isinstance(value, str):
        return redact_text(value, keys)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray)):
        if any(k.encode() in value for k in keys):
            return redact_text(bytes(value).decode(errors="replace"), keys)
        return value
    if isinstance(value, BaseException):
        # Rendered, never mutated: the caller's exception object is untouched.
        text = _safe_str(value)
        if text is None:
            return f"{type(value).__name__}: <unprintable>"
        return redact_text(f"{type(value).__name__}: {text}", keys)
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        if id(value) in seen:
            return "<cycle>"
        seen = seen | {id(value)}
        if isinstance(value, dict):
            return {
                _redact_value(k, keys, seen): _redact_value(v, keys, seen)
                for k, v in value.items()
            }
        items = [_redact_value(v, keys, seen) for v in value]
        if isinstance(value, list):
            return items
        return tuple(items) if isinstance(value, tuple) else type(value)(items)
    # Any other object is rendered by the sink via str()/repr(): if that text
    # would carry the key, hand the sink the redacted text instead.
    rendered = _safe_str(value)
    try:
        rendered_repr = repr(value)
    except Exception:
        rendered_repr = ""
    if any(k in (rendered or "") or k in rendered_repr for k in keys):
        return redact_text(rendered or rendered_repr, keys)
    return value


def _exc_text(exc_info: Any) -> str | None:
    if isinstance(exc_info, BaseException):
        exc: BaseException | None = exc_info
    elif isinstance(exc_info, tuple) and len(exc_info) == 3:
        exc = exc_info[1]
    else:
        import sys

        exc = sys.exc_info()[1]
    if exc is None:
        return None
    try:
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        return f"{type(exc).__name__}: <unprintable>"


def _enabled(bound: Any, level: int) -> bool:
    for attr in ("is_enabled_for", "isEnabledFor"):
        fn = getattr(bound, attr, None)
        if fn is not None:
            try:
                return bool(fn(level))
            except Exception:
                return True
    return True


class RedactingLogger:
    """Wraps the host's structlog logger, keeping the module's logger name."""

    def __init__(self, name: str | None = None, _bound: Any = None) -> None:
        self._name = name
        self._proxy: Any = None
        self._bound = _bound

    def _target(self) -> Any:
        # Resolved lazily (never at import: the host configures structlog
        # later) and then kept, so structlog's own per-call vs
        # cache_logger_on_first_use behaviour is unchanged.
        if self._bound is not None:
            return self._bound
        if self._proxy is None:
            self._proxy = (
                structlog.get_logger(self._name)
                if self._name
                else structlog.get_logger()
            )
        return self._proxy

    def _emit(
        self, method: str, event: Any, args: tuple[Any, ...], kw: dict[str, Any]
    ) -> None:
        target = self._target()
        if method == "log":
            level, event, *rest = (event, *args)  # log(level, event, *args)
            args = tuple(rest)
            method_args: tuple[Any, ...] = (level,)
            level_no = level if isinstance(level, int) else 20
        else:
            method_args = ()
            level_no = _LEVELS.get(method, 20)
        keys = _keys
        if keys:
            bound = target.bind() if hasattr(target, "bind") else target
            if not _enabled(bound, level_no):
                return
            event = _redact_value(event, keys, set())
            args = tuple(_redact_value(a, keys, set()) for a in args)
            exc_info = kw.pop("exc_info", None)
            kw = {k: _redact_value(v, keys, set()) for k, v in kw.items()}
            if exc_info:
                text = _exc_text(exc_info)
                if text is not None:
                    kw["exception"] = redact_text(text, keys)
            target = bound
        if method == "exception" and keys:
            # Never the host's .exception(): it re-adds the live traceback.
            method = "error"
        getattr(target, method)(*method_args, event, *args, **kw)

    def debug(self, event: Any = None, *args: Any, **kw: Any) -> None:
        self._emit("debug", event, args, kw)

    def info(self, event: Any = None, *args: Any, **kw: Any) -> None:
        self._emit("info", event, args, kw)

    msg = info

    def warning(self, event: Any = None, *args: Any, **kw: Any) -> None:
        self._emit("warning", event, args, kw)

    warn = warning

    def error(self, event: Any = None, *args: Any, **kw: Any) -> None:
        self._emit("error", event, args, kw)

    def critical(self, event: Any = None, *args: Any, **kw: Any) -> None:
        self._emit("critical", event, args, kw)

    fatal = critical

    def exception(self, event: Any = None, *args: Any, **kw: Any) -> None:
        kw.setdefault("exc_info", True)
        self._emit("exception", event, args, kw)

    def log(self, level: int, event: Any = None, *args: Any, **kw: Any) -> None:
        self._emit("log", level, (event, *args), kw)

    def bind(self, **new_values: Any) -> RedactingLogger:
        target = self._target()
        keys = _keys
        if keys:
            new_values = {
                k: _redact_value(v, keys, set()) for k, v in new_values.items()
            }
        return RedactingLogger(self._name, target.bind(**new_values))

    def new(self, **new_values: Any) -> RedactingLogger:
        return RedactingLogger(self._name, self._target().new(**new_values))


def get_logger(name: str | None = None) -> RedactingLogger:
    return RedactingLogger(name)
