"""Default-deny redaction: what one log line may contain (ARCHITECTURE.md §6A.3; ADR-0039; EP-26; INV-23).

Pure: `redact_line` turns an event, its fields and the bound context into the line's JSON object. A value is written
as itself only when its event declares the field with a safe kind and the value fits that kind:

- `id`: an int >= 0, or a `str` of 1 to 128 letters, digits and `._+-` that starts with a letter or digit (no
  spaces, no colons, so no account name and no sentence); `UntrustedText` never fits;
- `count`, `bytes`: an int >= 0; `duration_ms`: a finite int or float >= 0 (never a bool or a `Decimal`);
- `enum`, `safe_text`: a `str` equal to one of the field's declared values.

Every other value is replaced by a marker of its type (`by_type`): `Decimal` → `<money>`, `str` → `<text len=N>`,
`UntrustedText` → `<untrusted len=N>`, `Secret` → `<secret>`, a path under the app dirs → `app:/…` or
`app-cache:/…`, any other path → `<path>`, an exception → its type and code locations (its message is dropped),
bytes → `<bytes len=N>`, other numbers → `<number>`, anything else → `<value type=T>`; `None` stays `null`.
In debug mode a `debug_detail` field shows a text or number as itself, cut at `DEBUG_CAP` characters; a secret, an
exception or any other type is still redacted. A field the event does not declare, and any bound context other than
`correlation_id` and `ws`, is never written, not even its name: the line counts them in `undeclared_fields`.
Text is read with `str`'s own methods, so a `str` subclass cannot change what is checked or written.
"""

from __future__ import annotations

import math
import os
import re
import sys
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePath

from app.domain.text import Secret, UntrustedText
from app.platform.observability.events import Event, Field, Kind, Level

ID_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
DEBUG_CAP = 2000
MAX_FRAMES = 20
CONTEXT_IDS = ("correlation_id", "ws")

type Json = bool | int | float | str | list[str] | dict[str, Json] | None


@dataclass(frozen=True, slots=True)
class Roots:
    """The app's own folders: a path under one of them is written relative to it (`app:/logs/…`)."""

    app_local_data: PurePath
    app_cache: PurePath


@dataclass(frozen=True, slots=True)
class Common:
    ts: str
    session_id: str
    app_version: str


def redact_line(
    event: Event,
    level: Level,
    fields: Mapping[str, object],
    context: Mapping[str, object],
    common: Common,
    debug: bool,
    roots: Roots,
) -> dict[str, Json]:
    out: dict[str, Json] = {"ts": common.ts, "level": level, "event": event.name, "session_id": common.session_id}
    for key in CONTEXT_IDS:
        if key in context:
            out[key] = _safe(Kind.ID, frozenset(), context[key], roots)
    out["source"] = event.source
    out["feature"] = event.feature
    out["app_version"] = common.app_version
    if debug:
        out["debug"] = True
    declared = {f.name: f for f in event.fields}
    undeclared = sum(k not in CONTEXT_IDS for k in context) + sum(k not in declared for k in fields)
    if undeclared:
        out["undeclared_fields"] = undeclared
    for f in event.fields:
        if f.name in fields:
            out[f.name] = field_value(f, fields[f.name], debug, roots)
    return out


def field_value(f: Field, value: object, debug: bool, roots: Roots) -> Json:
    if f.kind is Kind.DEBUG_DETAIL:
        return _debug_value(value, roots) if debug else by_type(value, roots)
    return _safe(f.kind, f.values, value, roots)


def _safe(kind: Kind, values: frozenset[str], value: object, roots: Roots) -> Json:
    if value is None:
        return None
    if isinstance(value, bool):
        return by_type(value, roots)
    match kind:
        case Kind.ID:
            if isinstance(value, int) and value >= 0:
                return int(value)
            if isinstance(value, str) and ID_TEXT.fullmatch(value):
                return str.__str__(value)
        case Kind.COUNT | Kind.BYTES:
            if isinstance(value, int) and value >= 0:
                return int(value)
        case Kind.DURATION_MS:
            if isinstance(value, int | float) and math.isfinite(value) and value >= 0:
                return int(value) if isinstance(value, int) else float(value)
        case Kind.ENUM | Kind.SAFE_TEXT:
            if isinstance(value, str) and str.__str__(value) in values:
                return str.__str__(value)
        case Kind.DEBUG_DETAIL:
            pass
    return by_type(value, roots)


def _debug_value(value: object, roots: Roots) -> Json:
    if isinstance(value, UntrustedText):
        return _cap(str.__str__(value.text))
    if isinstance(value, str):
        return _cap(str.__str__(value))
    if isinstance(value, Decimal):
        return str(Decimal.__str__(value))
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and math.isfinite(value):
        return float(value)
    return by_type(value, roots)


def _cap(text: str) -> str:
    n = str.__len__(text)
    return text if n <= DEBUG_CAP else f"{text[:DEBUG_CAP]}…<cut len={n}>"


def by_type(value: object, roots: Roots) -> Json:
    """The marker §6A.3 gives a value that may not be written as itself."""
    if value is None:
        return None
    if isinstance(value, Secret):
        return "<secret>"
    if isinstance(value, UntrustedText):
        return f"<untrusted len={str.__len__(value.text)}>"
    if isinstance(value, str):
        return f"<text len={str.__len__(value)}>"
    if isinstance(value, Decimal):
        return "<money>"
    if isinstance(value, bool):
        return "<value type=bool>"
    if isinstance(value, int | float):
        return "<number>"
    if isinstance(value, bytes):
        return f"<bytes len={bytes.__len__(value)}>"
    if isinstance(value, bytearray):
        return f"<bytes len={bytearray.__len__(value)}>"
    if isinstance(value, PurePath):
        return _path(value, roots)
    if isinstance(value, BaseException):
        return _exception(value)
    return f"<value type={type(value).__name__}>"


def _path(value: PurePath, roots: Roots) -> str:
    if not value.is_absolute():
        return "<path>"
    path = PurePath(os.path.normpath(value))
    for prefix, root in (("app", roots.app_local_data), ("app-cache", roots.app_cache)):
        base = PurePath(os.path.normpath(root))
        if path == base or path.is_relative_to(base):
            rel = path.relative_to(base).as_posix()
            return f"{prefix}:/" if rel == "." else f"{prefix}:/{rel}"
    return "<path>"


def _exception(e: BaseException) -> dict[str, Json]:
    """The exception's type and where it was raised; never its message, args, notes or causes (§6A.3)."""
    t = type(e)
    name = t.__qualname__ if t.__module__ == "builtins" else f"{t.__module__}.{t.__qualname__}"
    frames = traceback.StackSummary.extract(traceback.walk_tb(e.__traceback__), lookup_lines=False)
    at = [f"{_code_path(fr.filename)}:{fr.lineno} in {fr.name}" for fr in frames][-MAX_FRAMES:]
    return {"type": name, "at": at}


def _code_path(filename: str) -> str:
    """A code location relative to the import path entry that holds it; any other file is `<path>`."""
    best = ""
    for entry in sys.path:
        if entry and os.path.isabs(entry):
            base = os.path.join(os.path.normpath(entry), "")
            if filename.startswith(base) and len(base) > len(best):
                best = base
    return PurePath(filename.removeprefix(best)).as_posix() if best else "<path>"
