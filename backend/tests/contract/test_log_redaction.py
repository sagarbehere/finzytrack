"""The redaction corpus (ARCHITECTURE.md §6A.3, §6A.6; ADR-0039; EP-26; INV-23).

The log a user may attach must hold no financial content and no free text outside debug mode. Redaction is
default-deny: a value is written as itself only when its event declares the field with a safe kind and the value fits
that kind; everything else becomes a marker of its type. Every registered event is emitted with every hostile value
in every field, and every other way into the file (undeclared fields, context variables, an unregistered event, other
libraries' records, warnings, uncaught exceptions) is checked the same way. The expected markers come from §6A.3, not
from the code.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import NoReturn, cast, override

import pytest
import structlog

from app.domain.text import Secret, UntrustedText
from app.platform.clock import FrozenClock
from app.platform.observability import (
    EVENTS,
    Event,
    EventLog,
    Field,
    Kind,
    LogContext,
    LogSettings,
    Roots,
    bound_context,
)
from app.platform.observability.events import ENGINE_CLOSED, ENGINE_START_FAILED, ENGINE_STDERR

AT = datetime(2026, 4, 1, 4, 30, 1, 234000, tzinfo=UTC)

# Each sensitive text below must never reach the file outside debug mode. None of them fits any safe kind: each has a
# space or a colon, or is not a str at all.
PAYEE = "ACME Groceries Ltd"
ACCOUNT = "Assets:Bank:Checking"
KEY_TEXT = "hunter2 is the passphrase"
OUTSIDE = "/opt/outside/bank-export.csv"
MONEY = "1234.56"
LEAKS = (PAYEE, "ACME", ACCOUNT, "Assets:Bank", KEY_TEXT, "hunter2", OUTSIDE, "bank-export", MONEY)


def fail(text: str) -> NoReturn:
    raise ValueError(text)


def raised(text: str) -> ValueError:
    try:
        fail(text)
    except ValueError as e:
        return e


@dataclass(frozen=True)
class Hostile:
    name: str
    value: object
    marker: object  # what §6A.3 says the value becomes; a callable checks a structured marker


def is_exception(rendered: object) -> bool:
    """§6A.3: an exception keeps its type and code locations (relative, never absolute), never its message."""
    if not isinstance(rendered, dict):
        return False
    data = cast("dict[str, object]", rendered)
    at = data.get("at")
    if not isinstance(at, list):
        return False
    frames = cast("list[object]", at)
    return (
        data.get("type") == "ValueError"
        and bool(frames)
        and all(isinstance(a, str) and not a.startswith("/") for a in frames)
        and set(data) <= {"type", "at"}
    )


HOSTILE = (
    Hostile("Decimal", Decimal(MONEY), "<money>"),
    Hostile("account name as str", ACCOUNT, f"<text len={len(ACCOUNT)}>"),
    Hostile("payee as str", PAYEE, f"<text len={len(PAYEE)}>"),
    Hostile("UntrustedText", UntrustedText(PAYEE), f"<untrusted len={len(PAYEE)}>"),
    Hostile("Secret", Secret(KEY_TEXT), "<secret>"),
    Hostile("path outside the app dirs", PurePosixPath(OUTSIDE), "<path>"),
    Hostile("exception with a payee", raised(f"duplicate {PAYEE} {MONEY}"), is_exception),
    Hostile("list", [ACCOUNT, MONEY], "<value type=list>"),
    Hostile("dict", {"payee": PAYEE}, "<value type=dict>"),
    Hostile("bytes", PAYEE.encode(), f"<bytes len={len(PAYEE)}>"),
    Hostile("bool", True, "<value type=bool>"),
)


class Logs:
    """An `EventLog` in a temp app dir, and the lines it wrote."""

    def __init__(self, root: Path, debug: bool = False, registry: tuple[Event, ...] = EVENTS) -> None:
        self.app = root / "local"
        self.cache = root / "cache"
        (self.app / "logs").mkdir(parents=True)
        self.cache.mkdir()
        self.path = self.app / "logs" / "finzytrack.jsonl"
        self.log = EventLog(
            self.path,
            context=LogContext(session_id="test-session-0001", app_version="0.4.0.dev0"),
            clock=FrozenClock(AT),
            roots=Roots(self.app, self.cache),
            debug=lambda: debug,
            registry=registry,
        )

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    def lines(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.text().splitlines()]

    def last(self) -> dict[str, object]:
        lines = self.lines()
        assert lines, "nothing was written"
        return lines[-1]


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[Logs]:
    out = Logs(tmp_path)
    yield out
    out.log.close()


def assert_no_leak(text: str) -> None:
    for leak in LEAKS:
        assert leak not in text, f"{leak!r} reached the log: {text}"


def check(rendered: object, marker: object) -> None:
    if callable(marker):
        assert marker(rendered), rendered
    else:
        assert rendered == marker


# -- Every registered event, every field, every hostile value ---------------------------------------------------


def field_cases() -> list[tuple[Event, Field, Hostile]]:
    return [(e, f, h) for e in EVENTS for f in e.fields for h in HOSTILE]


@pytest.mark.inv("INV-23")
@pytest.mark.parametrize(("event", "field", "hostile"), field_cases(), ids=lambda x: getattr(x, "name", str(x)))
def test_every_declared_field_redacts_every_hostile_value(
    logs: Logs, event: Event, field: Field, hostile: Hostile
) -> None:
    logs.log(event, **{field.name: hostile.value})
    line = logs.last()
    assert line["event"] == event.name
    check(line[field.name], hostile.marker)
    assert_no_leak(logs.text())


@pytest.mark.inv("INV-23")
@pytest.mark.parametrize("event", EVENTS, ids=lambda e: e.name)
def test_every_registered_event_with_all_fields_hostile_at_once_leaks_nothing(logs: Logs, event: Event) -> None:
    for hostile in HOSTILE:
        logs.log(event, **{f.name: hostile.value for f in event.fields})
    assert len(logs.lines()) == len(HOSTILE)
    assert_no_leak(logs.text())


def test_the_registry_is_not_empty_and_covers_the_engine_events() -> None:
    names = {e.name for e in EVENTS}
    assert {"engine.ready", "engine.start_failed", "engine.stderr", "engine.closed"} <= names
    assert len(field_cases()) > 0


def test_engine_stderr_and_start_failed_text_is_redacted_outside_debug_mode(logs: Logs) -> None:
    """BL-0088: the engine's stderr line is untrusted text; a failure's detail is free text."""
    logs.log(ENGINE_STDERR, line=UntrustedText(f"plugin: {PAYEE} {MONEY}"))
    logs.log(ENGINE_START_FAILED, kind="start_failed", detail=f"handshake: {OUTSIDE}")
    stderr, failed = logs.lines()
    assert stderr["line"] == f"<untrusted len={len(PAYEE) + len(MONEY) + 9}>"
    assert stderr["source"] == "engine"
    assert failed["kind"] == "start_failed"
    assert failed["detail"] == f"<text len={len(OUTSIDE) + 11}>"
    assert failed["level"] == "error"
    assert_no_leak(logs.text())


# -- Safe kinds: shown only when the value fits ------------------------------------------------------------------

EVERY_KIND = Event(
    "test.every_kind",
    "info",
    "platform",
    (
        Field("an_id", Kind.ID),
        Field("a_count", Kind.COUNT),
        Field("a_duration", Kind.DURATION_MS),
        Field("a_size", Kind.BYTES),
        Field("a_choice", Kind.ENUM, frozenset({"open", "closed"})),
        Field("a_text", Kind.SAFE_TEXT, frozenset({"the engine is starting"})),
        Field("a_detail", Kind.DEBUG_DETAIL),
    ),
)


@pytest.fixture
def kinds(tmp_path: Path) -> Iterator[Logs]:
    out = Logs(tmp_path, registry=(*EVENTS, EVERY_KIND))
    yield out
    out.log.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("an_id", "0.4.0.dev0"),
        ("an_id", "3f2a9c1e-7b4d-4e1a-9c2b-0d5e6f7a8b9c"),
        ("an_id", 4242),
        ("an_id", 0),
        ("a_count", 0),
        ("a_count", 17),
        ("a_duration", 0),
        ("a_duration", 12.5),
        ("a_size", 1048576),
        ("a_choice", "closed"),
        ("a_text", "the engine is starting"),
        ("a_detail", None),
    ],
)
def test_a_value_that_fits_its_safe_kind_is_shown(kinds: Logs, field: str, value: object) -> None:
    kinds.log(EVERY_KIND, **{field: value})
    assert kinds.last()[field] == value


@pytest.mark.parametrize(
    ("field", "value", "marker"),
    [
        ("an_id", "has space", "<text len=9>"),
        ("an_id", "Assets:Bank", "<text len=11>"),
        ("an_id", "", "<text len=0>"),
        ("an_id", "x" * 129, "<text len=129>"),
        ("an_id", "-leading", "<text len=8>"),
        ("an_id", "line\nbreak", "<text len=10>"),
        ("an_id", -1, "<number>"),
        ("an_id", True, "<value type=bool>"),
        ("an_id", UntrustedText("Amazon"), "<untrusted len=6>"),  # untrusted text is never an id, whatever its shape
        ("an_id", Decimal(7), "<money>"),
        ("a_count", -1, "<number>"),
        ("a_count", True, "<value type=bool>"),
        ("a_count", 1.5, "<number>"),
        ("a_count", "3", "<text len=1>"),
        ("a_count", Decimal(3), "<money>"),
        ("a_duration", float("nan"), "<number>"),
        ("a_duration", float("inf"), "<number>"),
        ("a_duration", -0.5, "<number>"),
        ("a_duration", False, "<value type=bool>"),
        ("a_duration", Decimal("1.5"), "<money>"),
        ("a_size", -1, "<number>"),
        ("a_size", 2.0, "<number>"),
        ("a_choice", "other", "<text len=5>"),
        ("a_choice", UntrustedText("ajar"), "<untrusted len=4>"),
        ("a_text", "the engine is starting!", "<text len=23>"),
        ("a_detail", "anything", "<text len=8>"),
        ("a_detail", 12, "<number>"),
    ],
)
def test_a_value_that_does_not_fit_its_kind_is_redacted_by_its_type(
    kinds: Logs, field: str, value: object, marker: str
) -> None:
    kinds.log(EVERY_KIND, **{field: value})
    assert kinds.last()[field] == marker


def test_an_id_shaped_plain_word_in_an_id_field_is_shown(kinds: Logs) -> None:
    """Stated limit (§6A.3): the kind is the developer's declaration. A one-word str that fits the id shape is shown
    in an `id` field; only the shape is checked. `UntrustedText` never passes (above)."""
    kinds.log(EVERY_KIND, an_id="Amazon")
    assert kinds.last()["an_id"] == "Amazon"


# -- Paths --------------------------------------------------------------------------------------------------------


def test_paths_under_the_app_dirs_are_made_relative_and_others_hidden(kinds: Logs) -> None:
    cases = [
        (kinds.app / "logs" / "finzytrack.jsonl", "app:/logs/finzytrack.jsonl"),
        (kinds.app, "app:/"),
        (kinds.cache / "mirror" / "gen-3.sqlite", "app-cache:/mirror/gen-3.sqlite"),
        (kinds.app / ".." / "elsewhere" / "ledger.beancount", "<path>"),
        (kinds.app.parent / "local-twin" / "x", "<path>"),  # a sibling whose name starts like the app dir
        (PurePosixPath("logs/finzytrack.jsonl"), "<path>"),  # relative: no root to judge it against
        (PurePosixPath(OUTSIDE), "<path>"),
    ]
    for n, (value, marker) in enumerate(cases, start=1):
        kinds.log(EVERY_KIND, a_detail=value)
        assert len(kinds.lines()) == n, f"no line for {value}"
        assert kinds.last()["a_detail"] == marker, value


# -- Every other way into the file --------------------------------------------------------------------------------


def test_undeclared_fields_are_counted_never_written(logs: Logs) -> None:
    logs.log(ENGINE_CLOSED, payee=PAYEE, **{ACCOUNT: MONEY})
    line = logs.last()
    assert line["undeclared_fields"] == 2
    assert "payee" not in line
    assert_no_leak(logs.text())


def test_common_fields_cannot_be_overwritten_by_an_events_fields(logs: Logs) -> None:
    logs.log(ENGINE_CLOSED, event=PAYEE, level="debug", ts=ACCOUNT, source="ui", session_id=PAYEE, debug=True)
    line = logs.last()
    assert (line["event"], line["level"], line["ts"]) == ("engine.closed", "info", "2026-04-01T04:30:01.234Z")
    assert (line["source"], line["session_id"]) == ("backend", "test-session-0001")
    assert "debug" not in line
    assert line["undeclared_fields"] == 6
    assert_no_leak(logs.text())


def test_context_ids_are_shown_when_they_fit_and_other_context_is_counted(logs: Logs) -> None:
    with bound_context(correlation_id="req-01H8XYZ", ws="ws-7"):
        logs.log(ENGINE_CLOSED)
    line = logs.last()
    assert (line["correlation_id"], line["ws"]) == ("req-01H8XYZ", "ws-7")
    with bound_context(correlation_id=ACCOUNT, ws=PAYEE):
        logs.log(ENGINE_CLOSED)
    line = logs.last()
    assert (line["correlation_id"], line["ws"]) == (f"<text len={len(ACCOUNT)}>", f"<text len={len(PAYEE)}>")
    with structlog.contextvars.bound_contextvars(payee=PAYEE, amount=Decimal(MONEY)):
        logs.log(ENGINE_CLOSED)
    line = logs.last()
    assert line["undeclared_fields"] == 2
    assert "payee" not in line
    assert_no_leak(logs.text())


def test_an_unregistered_event_writes_no_name_and_no_field(logs: Logs) -> None:
    """Declaring an event inline with safe kinds does not get around the registry."""
    rogue = Event("import.duplicate_found", "info", "importing", (Field("payee", Kind.ID),))
    logs.log(rogue, payee="ACMEGroceries")
    line = logs.last()
    assert line["event"] == "log.unregistered"
    assert line["undeclared_fields"] == 1
    assert "payee" not in line
    assert "ACMEGroceries" not in logs.text()
    assert "import.duplicate_found" not in logs.text()


@pytest.fixture
def captured(logs: Logs) -> Iterator[Logs]:
    release = logs.log.capture_process()
    try:
        yield logs
    finally:
        release()


def test_another_librarys_record_is_redacted(captured: Logs) -> None:
    lib = logging.getLogger("thirdparty.client")
    lib.warning("paid %s to %s", Decimal(MONEY), PAYEE, extra={"account": ACCOUNT})
    line = captured.last()
    assert line["event"] == "lib.record"
    assert line["level"] == "warning"
    assert line["logger"] == "thirdparty.client"
    assert line["message"] == f"<text len={len(f'paid {MONEY} to {PAYEE}')}>"
    assert "account" not in line
    assert_no_leak(captured.text())


def test_another_librarys_exception_keeps_only_its_type_and_code_locations(captured: Logs) -> None:
    lib = logging.getLogger("thirdparty.client")
    try:
        fail(f"bad row {PAYEE} {MONEY}")
    except ValueError:
        lib.exception("failed for %s", ACCOUNT)
    line = captured.last()
    assert line["level"] == "error"
    assert is_exception(line["exception"]), line
    assert_no_leak(captured.text())


def test_a_critical_record_is_written_as_error_and_records_below_warning_are_not_written(captured: Logs) -> None:
    """§6A.5 volume: another library's info and debug records stay out, even when it turns its own level down."""
    lib = logging.getLogger("thirdparty.chatty")
    lib.setLevel(logging.DEBUG)
    try:
        lib.info("chatty %s", PAYEE)
        lib.debug("chattier %s", PAYEE)
        assert captured.lines() == []
        lib.critical("down")
    finally:
        lib.setLevel(logging.NOTSET)
    assert captured.last()["level"] == "error"


def test_a_warning_is_redacted(captured: Logs) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn(f"odd value {PAYEE} {MONEY}", UserWarning, stacklevel=1)
    line = captured.last()
    assert (line["event"], line["logger"]) == ("lib.record", "py.warnings")
    assert_no_leak(captured.text())


def test_an_uncaught_exception_in_a_thread_keeps_only_its_type_and_locations(captured: Logs) -> None:
    def boom() -> None:
        fail(f"{PAYEE} {MONEY}")

    t = threading.Thread(target=boom, name="engine-boot")
    t.start()
    t.join(10)
    line = captured.last()
    assert (line["event"], line["level"], line["thread"]) == ("app.uncaught", "error", "engine-boot")
    assert is_exception(line["exception"]), line
    assert_no_leak(captured.text())


def test_an_uncaught_exception_on_the_main_thread_is_logged_the_same_way(captured: Logs) -> None:
    err = raised(f"{PAYEE} {MONEY}")
    sys.excepthook(type(err), err, err.__traceback__)
    line = captured.last()
    assert line["event"] == "app.uncaught"
    assert is_exception(line["exception"]), line
    assert_no_leak(captured.text())


class Dying:
    """An object whose finalizer raises: the exception has no caller, so Python hands it to `sys.unraisablehook`."""

    def __del__(self) -> None:
        fail(f"{PAYEE} {MONEY}")


def test_an_unraisable_exception_keeps_only_its_type_and_locations(captured: Logs) -> None:
    Dying()  # dropped at once: CPython runs the finalizer here
    line = captured.last()
    assert line["event"] == "app.uncaught"
    assert is_exception(line["exception"]), line
    assert_no_leak(captured.text())


def test_release_restores_the_process_hooks(logs: Logs) -> None:
    before = (sys.excepthook, threading.excepthook, logging.getLogger().handlers[:])
    release = logs.log.capture_process()
    assert sys.excepthook is not before[0]
    release()
    assert (sys.excepthook, threading.excepthook, logging.getLogger().handlers) == before
    logging.getLogger("thirdparty.client").warning("after release")
    assert logs.lines() == []


# -- Values that lie, and lines that cannot be written ------------------------------------------------------------


class Liar(str):
    """A `str` whose own methods lie about its content or raise."""

    __slots__ = ()

    @override
    def __len__(self) -> int:
        fail(f"{PAYEE} {MONEY}")

    @override
    def __str__(self) -> str:
        return "0.4.0"

    @override
    def __eq__(self, other: object) -> bool:
        return True

    @override
    def __hash__(self) -> int:
        return hash("start_failed")


def test_a_str_subclass_cannot_change_what_is_checked_or_written(kinds: Logs) -> None:
    kinds.log(EVERY_KIND, an_id=Liar(PAYEE), a_choice=Liar(PAYEE), a_text=Liar(PAYEE), a_detail=Liar(PAYEE))
    line = kinds.last()
    marker = f"<text len={len(PAYEE)}>"
    assert (line["an_id"], line["a_choice"], line["a_text"], line["a_detail"]) == (marker,) * 4
    assert_no_leak(kinds.text())


@pytest.mark.skipif(sys.platform == "win32", reason="a read-only folder by POSIX mode bits")
def test_a_line_that_cannot_be_written_is_dropped_without_printing_the_record(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Rotation fails (the folder became read-only): the stdlib's error dump would print the raw record, every field
    unredacted, to stderr. Nothing of it is printed; one fixed notice is, once."""
    out = Logs(tmp_path)
    out.log.close()
    log = EventLog(
        out.path,
        context=LogContext(session_id="test-session-0001", app_version="v"),
        clock=FrozenClock(AT),
        roots=Roots(out.app, out.cache),
        settings=LogSettings(max_bytes=1, backups=1),
    )
    log(ENGINE_CLOSED)  # the file now holds one line, so the next one rolls over
    (out.app / "logs").chmod(0o500)
    try:
        log(ENGINE_STDERR, line=UntrustedText(f"{PAYEE} {MONEY}"))
        log(ENGINE_START_FAILED, kind="start_failed", detail=f"{ACCOUNT} {KEY_TEXT}")
    finally:
        (out.app / "logs").chmod(0o700)
        log.close()
    assert [line["event"] for line in out.lines()] == ["engine.closed"]
    printed, err = capfd.readouterr()
    assert_no_leak(printed + err)
    assert "Logging error" not in err
    assert err.count("a log line could not be written") == 1


def test_a_closed_log_writes_nothing_and_prints_nothing(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """After close, an event must not reach logging's last-resort handler, which prints the raw record to stderr."""
    out = Logs(tmp_path)
    out.log(ENGINE_CLOSED)
    out.log.close()
    out.log(ENGINE_START_FAILED, kind="start_failed", detail=f"{ACCOUNT} {PAYEE}")
    assert [line["event"] for line in out.lines()] == ["engine.closed"]
    printed, err = capfd.readouterr()
    assert_no_leak(printed + err)


# -- Debug mode ---------------------------------------------------------------------------------------------------


@pytest.fixture
def debug_logs(tmp_path: Path) -> Iterator[Logs]:
    out = Logs(tmp_path, debug=True)
    yield out
    out.log.close()


def test_debug_mode_shows_debug_detail_fields_and_marks_every_line(debug_logs: Logs) -> None:
    debug_logs.log(ENGINE_STDERR, line=UntrustedText(f"plugin: {PAYEE}"))
    debug_logs.log(ENGINE_START_FAILED, kind="start_failed", detail=f"handshake: {ACCOUNT}")
    debug_logs.log(ENGINE_CLOSED)
    stderr, failed, closed = debug_logs.lines()
    assert stderr["line"] == f"plugin: {PAYEE}"
    assert failed["detail"] == f"handshake: {ACCOUNT}"
    assert all(line["debug"] is True for line in (stderr, failed, closed))


def test_debug_mode_never_shows_secrets_undeclared_fields_or_misfit_safe_kinds(debug_logs: Logs) -> None:
    debug_logs.log(ENGINE_STDERR, line=Secret(KEY_TEXT), payee=PAYEE)
    debug_logs.log(ENGINE_START_FAILED, kind=PAYEE, detail=raised(PAYEE))
    stderr, failed = debug_logs.lines()
    assert stderr["line"] == "<secret>"
    assert stderr["undeclared_fields"] == 1
    assert failed["kind"] == f"<text len={len(PAYEE)}>"
    assert is_exception(failed["detail"])
    assert_no_leak(debug_logs.text())


def test_debug_mode_caps_a_long_text(debug_logs: Logs) -> None:
    """§6A.3: never full file contents."""
    debug_logs.log(ENGINE_STDERR, line=UntrustedText("x" * 50_000))
    line = debug_logs.last()["line"]
    assert isinstance(line, str)
    assert line.startswith("x" * 2000)
    assert line.endswith("<cut len=50000>")
    assert len(line) < 2100


def test_debug_mode_is_read_per_line(tmp_path: Path) -> None:
    """Debug mode switches itself off (§6A.3); the next line is redacted again."""
    on = [True]
    app = tmp_path / "local"
    (app / "logs").mkdir(parents=True)
    path = app / "logs" / "finzytrack.jsonl"
    debug: Callable[[], bool] = lambda: on[0]  # noqa: E731
    log = EventLog(
        path,
        context=LogContext(session_id="test-session-0001", app_version="v"),
        clock=FrozenClock(AT),
        roots=Roots(app, tmp_path / "cache"),
        debug=debug,
    )
    log(ENGINE_STDERR, line=UntrustedText(PAYEE))
    on[0] = False
    log(ENGINE_STDERR, line=UntrustedText(PAYEE))
    log.close()
    first, second = (json.loads(x) for x in path.read_text().splitlines())
    assert first["line"] == PAYEE
    assert second["line"] == f"<untrusted len={len(PAYEE)}>"
    assert "debug" not in second
