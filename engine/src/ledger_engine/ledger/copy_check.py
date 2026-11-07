"""A copy's "loads the same" load, in a fresh process (`design/specs/engine-jobs.md` §8.2, the copy, step 5; S0081 r2,
the owner's choice of 2026-11-07, "Fresh process (Recommended)").

The source's load runs in the copy job's engine, and a plugin it ran can leave anything behind in that process (its
modules, patches, caches). So the copy's own load runs in a new interpreter (`fresh_load.check`): nothing of the
source's load can reach it, whatever a plugin does. `check_loaded` is the pure part, shared with the tests' fake: the
copy's load reduced to what `loads_same` compares, with its texts mapped back to the source's places, and every path
the load read (its files, its documents roots, its `document` entries' files), for the `load_outside` check.

`main` is the child's entry: it reads one JSON request line from its standard input, loads the copy, writes one JSON
answer line to the standard output it was started with (anything a plugin prints goes to standard error), and ends
the process itself, whatever a plugin left running (BL-0203).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NoReturn, cast

from ledger_engine.ledger import loads_same, overlay
from ledger_engine.ledger.core import copy_layout as cl
from ledger_engine.ledger.policy import DESKTOP


@dataclass(frozen=True, slots=True)
class CheckRequest:
    """The copy's root (absolute, in the target), the target folder, the layout (to map paths back) and each changed
    `include` text with the one text it replaced."""

    root: str
    target: str
    layout: cl.Layout
    quoted: tuple[tuple[str, str], ...]

    def encode(self) -> dict[str, object]:
        return {
            "root": self.root,
            "target": self.target,
            "base": self.layout.base,
            "moves": [list(m) for m in self.layout.moves],
            "quoted": [list(q) for q in self.quoted],
        }

    @staticmethod
    def decode(raw: Mapping[str, object]) -> CheckRequest:
        moves = cast("list[list[str]]", raw["moves"])
        quoted = cast("list[list[str]]", raw["quoted"])
        return CheckRequest(
            str(raw["root"]),
            str(raw["target"]),
            cl.Layout(str(raw["base"]), tuple((a, b) for a, b in moves)),
            tuple((a, b) for a, b in quoted),
        )


type Outcome = Literal["checked", "refused", "uncomparable", "failed"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """`checked`: the summary and every path the load read; `refused`: the load refused (`subject`); `uncomparable`:
    a value of an unknown kind (`subject`); `failed`: the load or the process could not run."""

    outcome: Outcome
    summary: loads_same.Summary | None = None
    read: tuple[str, ...] = ()
    subject: str | None = None

    def encode(self) -> dict[str, object]:
        s = self.summary
        return {
            "outcome": self.outcome,
            "subject": self.subject,
            "read": list(self.read),
            "summary": None
            if s is None
            else {"options": s.options, "entries": [list(e) for e in s.entries], "errors": list(s.errors)},
        }

    @staticmethod
    def decode(raw: Mapping[str, object]) -> CheckResult:
        s = cast("dict[str, object] | None", raw["summary"])
        summary = None
        if s is not None:
            entries = cast("list[list[object]]", s["entries"])
            summary = loads_same.Summary(
                str(s["options"]),
                tuple((str(a), str(b), int(cast("int", c))) for a, b, c in entries),
                tuple(str(e) for e in cast("list[object]", s["errors"])),
            )
        subject = raw["subject"]
        return CheckResult(
            cast("Outcome", raw["outcome"]),
            summary,
            tuple(str(p) for p in cast("list[object]", raw["read"])),
            None if subject is None else str(subject),
        )


def back_of(request: CheckRequest) -> loads_same.Back:
    """The copy's texts mapped back to the source's places: a quoted, rewritten `include` text to the one text it
    replaced (Beancount quotes it as written in its error for a glob that matches nothing), then every path."""
    pattern, table = cl.back(request.layout, request.target)
    quoted = {f'"{n}"': f'"{o}"' for n, o in request.quoted}
    texts = re.compile("|".join(re.escape(k) for k in sorted(quoted, key=len, reverse=True))) if quoted else None

    def back(s: str) -> str:
        if texts is not None:
            s = texts.sub(lambda m: quoted[m.group(0)], s)
        return pattern.sub(lambda m: table[m.group(0)], s)

    return back


def check_loaded(loaded: overlay.LoadResult, request: CheckRequest) -> CheckResult:
    """The pure part: a load of the copy as the check reads it."""
    if isinstance(loaded, overlay.LoadRefused):
        return CheckResult("refused", subject=loaded.subject)
    fp = loaded.fingerprint
    read = (*(f for f, _ in fp.files), *(d for d, _ in fp.documents), *(d for d, _ in fp.document_files))
    try:
        summary = loads_same.summary(loaded, back_of(request))
    except loads_same.UncomparableError as e:
        return CheckResult("uncomparable", subject=e.kind)
    return CheckResult("checked", summary, tuple(read))


def main(raw: Mapping[str, object]) -> NoReturn:
    """The child's work (the module's docstring). It ends the process itself once its answer is written (BL-0203):
    a plugin can leave anything behind that a normal exit waits for (a thread that never ends, an `atexit` handler),
    and the copy job waits for this process to end. Nothing here needs a clean exit: the child holds no lock and
    writes no file, the answer is closed and the standard streams flushed before it ends. Exit status 0 only once
    the answer is written."""
    answered = False
    try:
        answered = _answer(raw)
    except BaseException:  # noqa: BLE001 (a fault of the channel itself: said on standard error, then the exit)
        traceback.print_exc()
    finally:
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                stream.flush()
        os._exit(0 if answered else 1)


def _answer(raw: Mapping[str, object]) -> bool:
    out = os.dup(1)
    os.dup2(2, 1)  # what a plugin prints goes to standard error, never into the answer
    request = CheckRequest.decode(cast("Mapping[str, object]", raw["request"]))
    try:
        result = check_loaded(overlay.load(request.root, {}, DESKTOP), request)
    except BaseException as e:  # noqa: BLE001 (the load could not run: a plugin, its `SystemExit` included, the disk)
        result = CheckResult("failed", subject=type(e).__name__)
    with os.fdopen(out, "wb") as f:
        f.write(json.dumps(result.encode()).encode("utf-8") + b"\n")  # one line: the parent reads up to it
    return True
