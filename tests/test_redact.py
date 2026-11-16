"""Redaction of the SDK key at the logger boundary (#5167)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from featureflip import _redact
from featureflip._redact import (
    RedactingLogger,
    get_logger,
    register_key,
)

KEY = "sdk_server_SentinelKeyMustNeverAppearInLogs-0123456789"


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    _redact._keys = ()
    yield
    _redact._keys = ()


def emit(**kw: object) -> list[dict[str, object]]:
    with capture_logs() as logs:
        get_logger().warning("evt", **kw)
    return logs


class TestRedaction:
    def test_exception_message_carrying_the_key_is_redacted(self) -> None:
        register_key(KEY)
        err = RuntimeError(f"connect failed, headers: Authorization: {KEY}")
        logs = emit(error=str(err))
        assert KEY not in str(logs)
        assert "[redacted]" in logs[0]["error"]

    def test_exception_object_is_rendered_not_mutated(self) -> None:
        register_key(KEY)
        err = RuntimeError(f"boom {KEY}")
        logs = emit(error=err)
        assert logs[0]["error"] == "RuntimeError: boom [redacted]"
        assert KEY in str(err)  # what the SDK raises is untouched

    def test_exc_info_traceback_is_redacted(self) -> None:
        register_key(KEY)
        with capture_logs() as logs:
            try:
                raise RuntimeError(f"boom {KEY}")
            except RuntimeError:
                get_logger().error("evt", exc_info=True)
        assert KEY not in str(logs)
        assert "boom [redacted]" in logs[0]["exception"]

    def test_event_text_and_positional_args_are_redacted(self) -> None:
        register_key(KEY)
        with capture_logs() as logs:
            get_logger().warning(f"bad {KEY}")
            get_logger().warning("bad %s", KEY)
        assert KEY not in str(logs)

    def test_structured_fields_including_nested_are_redacted(self) -> None:
        register_key(KEY)
        logs = emit(
            plain=KEY,
            nested={"headers": [f"Bearer {KEY}"], "n": 3},
            obj=_Stringy(),
        )
        assert KEY not in str(logs)
        assert logs[0]["nested"] == {"headers": ["Bearer [redacted]"], "n": 3}

    def test_short_key_is_not_redacted(self) -> None:
        register_key("k")
        register_key("short-key-19-chars!")
        logs = emit(error="skipped a malformed flag, short-key-19-chars!")
        assert logs[0]["error"] == "skipped a malformed flag, short-key-19-chars!"

    def test_every_registered_key_is_redacted(self) -> None:
        other = KEY.replace("server", "client")
        register_key(KEY)
        register_key(other)
        assert emit(a=f"{KEY} {other}")[0]["a"] == "[redacted] [redacted]"

    def test_keys_are_never_released(self) -> None:
        register_key(KEY)
        register_key(KEY)
        assert _redact._keys == (KEY,)
        assert emit(a=KEY)[0]["a"] == "[redacted]"

    def test_cycles_and_unprintable_values_do_not_break_logging(self) -> None:
        register_key(KEY)
        loop: list[object] = [KEY]
        loop.append(loop)

        class Bad(Exception):
            def __str__(self) -> str:
                raise RuntimeError("no")

        logs = emit(loop=loop, err=Bad())
        assert KEY not in str(logs)

    def test_bytes_carrying_the_key_are_redacted(self) -> None:
        register_key(KEY)
        logs = emit(raw=f"x {KEY}".encode(), other=b"abc")
        assert logs[0]["raw"] == "x [redacted]"
        assert logs[0]["other"] == b"abc"

    def test_bind_keeps_redacting(self) -> None:
        register_key(KEY)
        with capture_logs() as logs:
            get_logger("n").bind(who=KEY).info("evt", err=KEY)
        assert KEY not in str(logs)


def _stdlib_config(*processors: Any) -> None:
    structlog.configure(
        processors=[structlog.stdlib.add_logger_name, *processors,
                    structlog.stdlib.render_to_log_kwargs],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
    )


class TestHostLoggerIntegration:
    @pytest.fixture(autouse=True)
    def _restore(self) -> Any:
        yield
        structlog.reset_defaults()

    def test_stdlib_logger_name_is_the_modules(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from featureflip import _core, _events

        _stdlib_config()
        caplog.set_level(logging.DEBUG)
        _core.logger.warning("a")
        _events.logger.warning("b")
        assert [r.name for r in caplog.records] == [
            "featureflip._core",
            "featureflip._events",
        ]

    def test_exception_does_not_render_the_live_traceback(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        register_key(KEY)
        structlog.configure(
            processors=[
                structlog.processors.format_exc_info,
                structlog.processors.KeyValueRenderer(),
            ],
            logger_factory=structlog.PrintLoggerFactory(),
        )
        try:
            raise RuntimeError(f"boom {KEY}")
        except RuntimeError:
            get_logger("n").exception("evt")
        out = capsys.readouterr().out
        assert KEY not in out
        assert "boom [redacted]" in out

    def test_exception_with_stdlib_backed_config(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        register_key(KEY)
        _stdlib_config(structlog.processors.format_exc_info)
        caplog.set_level(logging.DEBUG)
        try:
            raise RuntimeError(f"boom {KEY}")
        except RuntimeError:
            get_logger("n").exception("evt")
        assert KEY not in caplog.text
        assert all(r.exc_info is None for r in caplog.records)

    def test_filtered_levels_do_no_redaction_work(self) -> None:
        register_key(KEY)
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR)
        )

        rendered: list[bool] = []

        class Tripwire:
            def __str__(self) -> str:
                rendered.append(True)
                return "x"

        get_logger("n").debug("evt", obj=Tripwire())
        assert rendered == []


    def test_default_sink_is_covered(self, capsys: pytest.CaptureFixture[str]) -> None:
        structlog.reset_defaults()
        register_key(KEY)
        RedactingLogger().warning("evt", error=f"x {KEY}")
        out = capsys.readouterr()
        assert KEY not in out.out + out.err
        assert "[redacted]" in out.out + out.err


class _Stringy:
    def __str__(self) -> str:
        return f"obj {KEY}"
