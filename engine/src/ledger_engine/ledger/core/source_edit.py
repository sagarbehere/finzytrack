"""Source splicing: where an entry's text is, where a new entry goes, and the new bytes of each file (ADR-0009; S0115).

Pure functions over text: they take each file's current bytes (a `Snapshot`) and return the new bytes of each changed
file, or a `Refusal` naming why. They read and write nothing; positions come only from Beancount's parser and lexer
run on those bytes (through `beancount_port`; INV-03), never from a load's output.

**The ownership rule** (owner, 2026-11-13; journal S0115):
1. An edit owns only the span of an entry the parser found in the file's current text (`locate`): the entry's lines
   as Fava's `find_entry_lines` reads them (the first line, then every following line up to a blank line, a line
   holding only whitespace, or a line whose first character is not whitespace). The parser's entry on the first line
   must have the target's date and keyword (`Target`; it is the entry Beancount read from the date and keyword tokens
   of that line), and the span alone must parse, with no error, as that one entry (a multi-line string with a line at
   column 0 would end Fava's span inside the entry, and an indented directive Beancount kept after an error is not
   at the start of its line: both refused, `NOT_WHOLE`). So nothing a plugin made, nothing of
   another file and nothing that is not in the text can be targeted.
2. An insert goes where Fava's `insert-entry` options route it, else at the end of the default file (`route_insert`),
   and never inside a `pushtag` or `pushmeta` region (asked of the parser: a probe entry placed there must get no
   pushed tag or metadata). The option an insert is routed by must itself be a whole entry at column 0 (as `locate`
   decides), else the insert is refused (`NOT_WHOLE`): Beancount keeps an indented directive after an error.
3. Every byte outside the owned spans stays the same; new text takes the file's line ending (its first line's, as
   Fava does; `\\n` for a file without one) and a missing final newline stays missing. New text is exactly one
   entry, in complete `\\n` lines, and holds no `include`, `option`, `plugin`, `pushtag`, `poptag`, `pushmeta` or
   `popmeta` token (each would change entries outside the span).
4. Edits land only in the plan's `files`. Overlapping edits, an encrypted (`.gpg`, `.asc`) or non-UTF-8 file, a plan
   whose snapshot is not the current bytes, and an edit that is not what `locate`, `route_insert` or `replace_token`
   gives on the current text are refused before any new byte is made.
5. All the edits of a plan are planned from one snapshot (their positions are the snapshot's), and applied as if
   bottom-up: no edit shifts another's position, so the input order does not matter. Inserts at one point are placed
   in date order, then Fava's order of kinds (`open`, `balance`, the rest, `document`, `close`), then by text.

Lines are split at `\\n` only, as Beancount's lexer counts them (a lone `\\r` is not a line end). A delete removes the
span only: Fava's `delete_entry_slice` also removes the blank lines after it, which are outside the span. The caller
gives the files of a snapshot in the load's include order: Fava breaks ties between options of one date in that order.
Callers' duties not checked here: an entry inside a push region is parsed with the pushed tags and metadata, so a
whole-entry replace must print the entry as written, not as parsed (WS-WRITE stage 4, BL-0265).

Ported from Fava 1.30.16 (`fava/core/file.py`: `find_entry_lines`, `find_insert_position`, `save_entry_slice`,
`delete_entry_slice`, `insert_entry`, `_incomplete_sortkey`; `fava/core/fava_options.py`: the `insert-entry` option;
`fava/beans/account.py`: `get_entry_accounts`), under the MIT licence:

    The MIT License (MIT)

    Copyright (c) 2015-2016 Dominik Aumayr <dominik@aumayr.name>  [privacy-scan: allow email: Fava's MIT notice]

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

Fava was created by Dominik Aumayr and is maintained by Dominik Aumayr and Jakob Schnitzer (Fava's AUTHORS file).
"""

from __future__ import annotations

import datetime
import hashlib
import itertools
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import grammar


class Reason(StrEnum):
    MISSING_FILE = "file_missing"  # an edit, a target or the default file names a file the snapshot does not hold
    ENCRYPTED = "encrypted_file"  # a `.gpg` or `.asc` name, any case (ADR-0059; the save's step 0 rule)
    NOT_UTF8 = "not_utf8"  # a file, or new text that cannot be UTF-8 (a lone surrogate)
    STALE = "stale_snapshot"  # the plan's (or the span's) snapshot is not the current bytes
    NOT_ALLOWED = "file_not_allowed"  # an edit in a file outside the plan's `files`
    NO_ENTRY = "no_entry_at_position"  # the parser found no entry at that line of the current text
    FIRST_LINE = "first_line_mismatch"  # the entry the parser found on that line lacks the target's date or keyword
    NOT_WHOLE = "span_not_whole_entry"  # Fava's span does not parse alone as that one entry
    NOT_LOCATED = "edit_not_located"  # an edit is not what locate or replace_token gives on the current text
    NOT_ENTRY_LINE = "not_an_entry_line"  # a token edit on a line that is not the entry's or one of its postings'
    NOT_ONE_ACCOUNT = "token_not_one_account"  # the old account is not exactly one account token of that line
    BAD_TEXT = "bad_entry_text"  # new text is not exactly one entry in complete `\n` lines
    FORBIDDEN_LINE = "forbidden_line"  # new text holds an include, option, plugin or push/pop token
    PUSH_REGION = "insert_in_push_region"
    NOT_ROUTED = "insert_not_routed"  # an insert is not where route_insert puts it
    OVERLAP = "overlapping_edits"


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: Reason
    path: str | None
    detail: str


type Keyword = Literal[
    "txn", "open", "close", "balance", "pad", "note", "document", "price", "event", "query", "custom", "commodity"
]
"""An entry's keyword as a target names it; `txn` stands for every transaction flag (`*`, `!`, `txn`, `P`, ...)."""

_CLASSES: Final[dict[Keyword, type]] = {
    "txn": port.Transaction,
    "open": port.Open,
    "close": port.Close,
    "balance": port.Balance,
    "pad": port.Pad,
    "note": port.Note,
    "document": port.Document,
    "price": port.Price,
    "event": port.Event,
    "query": port.Query,
    "custom": port.Custom,
    "commodity": port.Commodity,
}
_FORBIDDEN: Final = frozenset({"INCLUDE", "OPTION", "PLUGIN", "PUSHTAG", "POPTAG", "PUSHMETA", "POPMETA"})
_ENCRYPTED: Final = (".gpg", ".asc")  # as `overlay.ENCRYPTED_SUFFIXES`; pinned equal by the tests
_PROBE: Final = '1970-01-01 txn "probe"\n'
_RANK: Final[dict[type, int]] = {port.Open: -2, port.Balance: -1, port.Document: 1, port.Close: 2}  # Fava's sort


@dataclass(frozen=True, slots=True)
class Target:
    """The entry an intent row names: its file, the line its first line is on (1-based), its date and keyword."""

    path: str
    lineno: int
    date: datetime.date
    keyword: Keyword


@dataclass(frozen=True, slots=True)
class Span:
    """An entry's text in one snapshot of its file: lines `first` to `stop` (0-based, `stop` excluded), characters
    `start` to `end` of the decoded text, and the SHA-256 of the file's bytes it was located in."""

    target: Target
    sha256: str
    first: int
    stop: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Insert:
    """A new entry: `text` goes before line `line` (0-based) of `path`, or at its end when `line` is None, as
    `route_insert` placed it with `default_file`; `sha256` is the snapshot's hash of `path`."""

    path: str
    line: int | None
    text: str
    default_file: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Replace:
    span: Span
    text: str


@dataclass(frozen=True, slots=True)
class Delete:
    span: Span


@dataclass(frozen=True, slots=True)
class ReplaceToken:
    """One account token on the entry's own line or a posting's line (`lineno`, 1-based): characters `start` to
    `end` of the decoded text hold `old` and get `new`."""

    span: Span
    lineno: int
    old: grammar.AccountName
    new: grammar.AccountName
    start: int
    end: int


type Edit = Insert | Replace | Delete | ReplaceToken


@dataclass(frozen=True, slots=True)
class EditPlan:
    """Edits planned from one snapshot (`snapshot`: every file's SHA-256), to land only in `files`."""

    snapshot: Mapping[str, str]
    files: frozenset[str]
    edits: tuple[Edit, ...]

    @classmethod
    def of(cls, snapshot: Snapshot, files: Iterable[str], edits: Iterable[Edit]) -> EditPlan:
        return cls(dict(snapshot.hashes), frozenset(files), tuple(edits))


@dataclass(frozen=True, slots=True)
class Splice:
    """One change made to a file: characters `start` to `end` of the old text held `old`; the new text holds `new`
    from character `at`."""

    start: int
    end: int
    old: str
    new: str
    at: int


@dataclass(frozen=True, slots=True)
class Applied:
    """The new bytes of each changed file, the SHA-256 of its old and new bytes, and the splices made (for `revert`)."""

    files: Mapping[str, bytes]
    before: Mapping[str, str]
    after: Mapping[str, str]
    splices: Mapping[str, tuple[Splice, ...]]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class _Text:
    path: str
    text: str
    lines: tuple[str, ...]  # split at "\n", each keeping its "\n" (the last may lack it)
    offsets: tuple[int, ...]  # the start of each line, then len(text)
    newline: str
    final_newline: bool


@dataclass(frozen=True, slots=True)
class _Parsed:
    entries: Mapping[int, port.Directive]  # by line (1-based), as the parser found them in the text
    order: tuple[port.Directive, ...]  # in the parser's order
    errors: tuple[port.BeancountError, ...]  # the parser's errors (the contract's step 1 compares them, S0116)
    options: port.OptionsMap


@dataclass(frozen=True, slots=True)
class _Option:
    date: datetime.date
    pattern: re.Pattern[str]
    path: str
    lineno: int


def _decode(path: str, data: bytes) -> _Text | Refusal:
    if path.lower().endswith(_ENCRYPTED):
        return Refusal(Reason.ENCRYPTED, path, "an encrypted file is never edited")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return Refusal(Reason.NOT_UTF8, path, f"not UTF-8 at byte {e.start}")
    parts = text.split("\n")
    lines = tuple(p + "\n" for p in parts[:-1]) + ((parts[-1],) if parts[-1] else ())
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    return _Text(path, text, lines, tuple(offsets), newline, text == "" or text.endswith("\n"))


class Snapshot:
    """The bytes of every file of a ledger set at one moment, in the load's include order. Decoding and parsing are
    done once per file, when first needed (the producer, `apply_edits` and the contract's step 1 share them when given
    one snapshot, S0116: Beancount's parse of the same bytes)."""

    def __init__(self, files: Mapping[str, bytes]) -> None:
        self.files: Final[Mapping[str, bytes]] = dict(files)
        self.hashes: Final[Mapping[str, str]] = {p: _sha256(d) for p, d in self.files.items()}
        self._texts: dict[str, _Text | Refusal] = {}
        self._parsed: dict[str, _Parsed] = {}
        self._options: list[_Option] | Refusal | None = None
        self._regions: dict[tuple[str, int | None], Refusal | None] = {}

    def text(self, path: str) -> _Text | Refusal:
        if path not in self.files:
            return Refusal(Reason.MISSING_FILE, path, "not in the snapshot")
        if path not in self._texts:
            self._texts[path] = _decode(path, self.files[path])
        return self._texts[path]

    def parsed(self, path: str) -> _Parsed | Refusal:
        text = self.text(path)
        if isinstance(text, Refusal):
            return text
        if path not in self._parsed:
            entries, errors, options = port.parse_string(self.files[path], path)
            self._parsed[path] = _Parsed(
                {int(e.meta["lineno"]): e for e in entries}, tuple(entries), tuple(errors), options
            )
        return self._parsed[path]


# ---- locate --------------------------------------------------------------------------------------------------------


def _entry_stop(lines: Sequence[str], first: int) -> int:
    """Fava's `find_entry_lines`: the entry's lines run from `first` to the first blank line, line of whitespace only,
    or line whose first character is not whitespace (or the end of the file)."""
    stop = first + 1
    while stop < len(lines) and lines[stop].strip() and lines[stop][0].isspace():
        stop += 1
    return stop


def _postings(entry: port.Directive) -> int:
    return len(entry.postings) if isinstance(entry, port.Transaction) else 0


def locate(snapshot: Snapshot, target: Target) -> Span | Refusal:
    """The span of the entry `target` names, in the snapshot's text of its file (rule 1)."""
    text = snapshot.text(target.path)
    parsed = snapshot.parsed(target.path)
    if isinstance(text, Refusal):
        return text
    if isinstance(parsed, Refusal):
        return parsed
    entry = parsed.entries.get(target.lineno)
    if entry is None or not 1 <= target.lineno <= len(text.lines):
        return Refusal(Reason.NO_ENTRY, target.path, f"the parser found no entry at line {target.lineno}")
    first = target.lineno - 1
    if not isinstance(entry, _CLASSES[target.keyword]) or entry.date != target.date:
        return Refusal(
            Reason.FIRST_LINE, target.path, f"line {target.lineno} is not a {target.keyword} of {target.date}"
        )
    stop = _entry_stop(text.lines, first)
    alone, errors, _ = port.parse_string("".join(text.lines[first:stop]).encode())
    if (
        errors
        or len(alone) != 1
        or type(alone[0]) is not type(entry)
        or alone[0].date != entry.date
        or _postings(alone[0]) != _postings(entry)
    ):
        return Refusal(Reason.NOT_WHOLE, target.path, f"the lines from {target.lineno} are not the whole entry")
    return Span(target, snapshot.hashes[target.path], first, stop, text.offsets[first], text.offsets[stop])


# ---- new text ------------------------------------------------------------------------------------------------------


def _entry_of(text: str, path: str | None) -> port.Directive | Refusal:
    """The one entry `text` holds (rule 3)."""
    if not text.endswith("\n") or "\r" in text or text[0].isspace():
        return Refusal(Reason.BAD_TEXT, path, "new text must be complete \\n lines starting at column 0")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate (S0115 verifier F4)
        return Refusal(Reason.NOT_UTF8, path, "new text cannot be written as UTF-8")
    tokens = port.lex(text.encode())
    forbidden = sorted({t.kind.lower() for t in tokens if t.kind in _FORBIDDEN})
    if forbidden:
        return Refusal(Reason.FORBIDDEN_LINE, path, f"new text holds {', '.join(forbidden)}")
    entries, errors, _ = port.parse_string(text.encode())
    if errors or len(entries) != 1:
        return Refusal(Reason.BAD_TEXT, path, f"new text is {len(entries)} entries with {len(errors)} parser errors")
    return entries[0]


# ---- route_insert --------------------------------------------------------------------------------------------------


def _entry_accounts(entry: port.Directive) -> list[str]:
    """Fava's `get_entry_accounts`: a transaction's posting accounts reversed, a custom entry's account values, a
    pad's account then its source, else the entry's account."""
    if isinstance(entry, port.Transaction):
        return [p.account for p in reversed(entry.postings)]
    if isinstance(entry, port.Custom):
        return port.custom_accounts(entry)
    if isinstance(entry, port.Pad):
        return [entry.account, entry.source_account]
    account = getattr(entry, "account", None)
    return [account] if isinstance(account, str) else []


def _insert_options(snapshot: Snapshot) -> list[_Option] | Refusal:
    """Every `insert-entry` option of the snapshot's text, as Fava's `parse_options` keeps them (a `fava-option`
    custom entry whose first value, `-` read as `_`, is `insert_entry`, whose second value is a text (none: the empty
    pattern) that compiles), in the load's order: by date, then line, then the files' order."""
    if snapshot._options is not None:  # pyright: ignore[reportPrivateUsage]
        return snapshot._options  # pyright: ignore[reportPrivateUsage]
    found: list[_Option] = []
    for path in snapshot.files:
        parsed = snapshot.parsed(path)
        if isinstance(parsed, Refusal):
            snapshot._options = parsed  # pyright: ignore[reportPrivateUsage]
            return parsed
        for e in parsed.order:
            if not isinstance(e, port.Custom) or e.type != "fava-option" or not e.values:
                continue
            if str(e.values[0].value).replace("-", "_") != "insert_entry":
                continue
            value = e.values[1].value if len(e.values) > 1 else ""
            if not isinstance(value, str):
                continue
            try:
                pattern = re.compile(value)
            except re.error:
                continue
            found.append(_Option(e.date, pattern, path, int(e.meta["lineno"])))
    found.sort(key=lambda o: (o.date, o.lineno))
    snapshot._options = sorted(found, key=lambda o: o.date, reverse=True)  # pyright: ignore[reportPrivateUsage]
    return snapshot._options  # pyright: ignore[reportPrivateUsage]


def _push_region(snapshot: Snapshot, text: _Text, line: int | None) -> Refusal | None:
    """Refused when a probe entry placed before `line` (None: at the end) gets a pushed tag or pushed metadata from
    the parser (rule 2)."""
    key = (text.path, line)
    if key not in snapshot._regions:  # pyright: ignore[reportPrivateUsage]
        prefix = text.text[: text.offsets[line] if line is not None else len(text.text)]
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        lineno = prefix.count("\n") + 1
        entries, _, _ = port.parse_string((prefix + _PROBE).encode(), text.path)
        probe = [e for e in entries if isinstance(e, port.Transaction) and e.meta["lineno"] == lineno]
        where = "the end" if line is None else f"line {line + 1}"
        refusal = None
        if len(probe) != 1:
            refusal = Refusal(Reason.PUSH_REGION, text.path, f"the parser cannot place an entry at {where}")
        elif probe[0].tags or set(probe[0].meta) - {"filename", "lineno"}:
            refusal = Refusal(Reason.PUSH_REGION, text.path, f"{where} is inside a pushtag or pushmeta region")
        snapshot._regions[key] = refusal  # pyright: ignore[reportPrivateUsage]
    return snapshot._regions[key]  # pyright: ignore[reportPrivateUsage]


def _route(entry: port.Directive, options: Sequence[_Option]) -> _Option | None:
    """Fava's `find_insert_position`: the option the entry goes before, or None for the default file's end."""
    for account in _entry_accounts(entry):
        for option in options:
            if option.date >= entry.date:
                continue
            if option.pattern.match(account):
                return option
    return None


def route_insert(snapshot: Snapshot, text: str, default_file: str) -> Insert | Refusal:
    """Where Fava would insert the one entry `text` holds (rule 2), as an `Insert` edit."""
    entry = _entry_of(text, None)
    if isinstance(entry, Refusal):
        return entry
    default = snapshot.text(default_file)
    if isinstance(default, Refusal):
        return default
    options = _insert_options(snapshot)
    if isinstance(options, Refusal):
        return options
    option = _route(entry, options)
    path, line = (default_file, None) if option is None else (option.path, option.lineno - 1)
    if option is not None and isinstance(locate(snapshot, Target(path, option.lineno, option.date, "custom")), Refusal):
        # Beancount may keep a directive whose line is indented after an error: its line is then inside the text of
        # the entry above, and an insert there would split that text (S0115 verifier F1). Refused, as `locate` does.
        return Refusal(Reason.NOT_WHOLE, path, f"the insert-entry option on line {option.lineno} is not a whole entry")
    target = snapshot.text(path)
    if isinstance(target, Refusal):
        return target
    refusal = _push_region(snapshot, target, line)
    if refusal is not None:
        return refusal
    return Insert(path, line, text, default_file, snapshot.hashes[path])


# ---- replace_token -------------------------------------------------------------------------------------------------


def replace_token(
    snapshot: Snapshot, span: Span, lineno: int, old: grammar.AccountName, new: grammar.AccountName
) -> ReplaceToken | Refusal:
    """The edit that writes `new` over the one account token `old` on line `lineno` (1-based), which must be the
    span's entry's own line or one of its postings' (ADR-0009: never a regex over the file)."""
    path = span.target.path
    if locate(snapshot, span.target) != span:
        return Refusal(Reason.NOT_LOCATED, path, "the span is not the entry's span in this snapshot")
    text = snapshot.text(path)
    parsed = snapshot.parsed(path)
    if isinstance(text, Refusal):
        return text
    if isinstance(parsed, Refusal):
        return parsed
    entry = parsed.entries[span.target.lineno]
    own = {span.target.lineno}
    if isinstance(entry, port.Transaction):
        own |= {int(p.meta["lineno"]) for p in entry.postings if p.meta and "lineno" in p.meta}
    if lineno not in own:
        return Refusal(Reason.NOT_ENTRY_LINE, path, f"line {lineno} is not the entry's or a posting's line")
    line = text.lines[lineno - 1].removesuffix("\n").removesuffix("\r")
    at = grammar.account_token(line, old, new)
    if isinstance(at, grammar.Invalid):
        return Refusal(Reason.NOT_ONE_ACCOUNT, path, f"{old} is not exactly one account token on line {lineno}")
    base = text.offsets[lineno - 1]
    return ReplaceToken(span, lineno, old, new, base + at.start, base + at.end)


# ---- apply_edits ---------------------------------------------------------------------------------------------------


def _path(edit: Edit) -> str:
    return edit.path if isinstance(edit, Insert) else edit.span.target.path


def _sha(edit: Edit) -> str:
    return edit.sha256 if isinstance(edit, Insert) else edit.span.sha256


def _check(snapshot: Snapshot, edit: Edit) -> Refusal | None:
    """The edit is exactly what the primitives give on the current text."""
    path = _path(edit)
    if isinstance(edit, Insert):
        placed = route_insert(snapshot, edit.text, edit.default_file)
        if isinstance(placed, Refusal):
            return placed
        return None if placed == edit else Refusal(Reason.NOT_ROUTED, path, "the insert is not where routing puts it")
    if isinstance(edit, ReplaceToken):
        again = replace_token(snapshot, edit.span, edit.lineno, edit.old, edit.new)
        if isinstance(again, Refusal):
            return again
        return None if again == edit else Refusal(Reason.NOT_LOCATED, path, "the token edit is not at the token")
    located = locate(snapshot, edit.span.target)
    if isinstance(located, Refusal):
        return located
    if located != edit.span:
        return Refusal(Reason.NOT_LOCATED, path, "the span is not the entry's span in this snapshot")
    if isinstance(edit, Replace):
        entry = _entry_of(edit.text, path)
        if isinstance(entry, Refusal):
            return entry
    return None


@dataclass(frozen=True, slots=True)
class _Piece:
    start: int
    end: int
    new: str


def _in_newline(text: str, newline: str) -> str:
    return text.replace("\n", newline)


def _insert_key(text: str) -> tuple[datetime.date, int, str]:
    entry = _entry_of(text, None)
    if isinstance(entry, Refusal):  # never: route_insert refused such a text before
        return datetime.date.min, 0, text
    return entry.date, _RANK.get(type(entry), 0), text


def _pieces(text: _Text, edits: Sequence[Edit]) -> list[_Piece]:
    nl = text.newline
    pieces: list[_Piece] = []
    points: dict[int | None, list[str]] = {}
    for edit in edits:
        if isinstance(edit, Insert):
            points.setdefault(edit.line, []).append(edit.text)
        elif isinstance(edit, Delete):
            pieces.append(_Piece(edit.span.start, edit.span.end, ""))
        elif isinstance(edit, Replace):
            new = _in_newline(edit.text, nl)
            if edit.span.end == len(text.text) and not text.final_newline:
                new = new.removesuffix(nl)
            pieces.append(_Piece(edit.span.start, edit.span.end, new))
        else:
            pieces.append(_Piece(edit.start, edit.end, edit.new))
    for line, texts in points.items():
        ordered = [_in_newline(t, nl) for t in sorted(texts, key=_insert_key)]
        if line is not None:
            pieces.append(_Piece(text.offsets[line], text.offsets[line], "".join(t + nl for t in ordered)))
            continue
        new = "".join(nl + t for t in ordered)
        if text.text == "":
            new = new.removeprefix(nl)
        elif not text.final_newline:
            new = nl + new.removesuffix(nl)
        pieces.append(_Piece(len(text.text), len(text.text), new))
    return sorted(pieces, key=lambda p: (p.start, p.end))


def _overlap(pieces: Sequence[_Piece]) -> bool:
    """Ranges overlap when they share a character; an insert point overlaps a range that holds the character after
    it (an insert before an option line whose entry is also edited). Inserts at one point are one piece."""
    for a, b in itertools.pairwise(pieces):
        if b.start < a.end or (a.start == b.start and (a.start == a.end) != (b.start == b.end)):
            return True
    return False


def apply_edits(current: Mapping[str, bytes] | Snapshot, plan: EditPlan) -> Applied | Refusal:
    """The new bytes of every file the plan changes, from `current` (each file's bytes now, or a snapshot of them,
    whose parses are then reused), or the first refusal."""
    hashes: Mapping[str, str] = (
        current.hashes
        if isinstance(current, Snapshot)
        else {p: _sha256(current[p]) for p in plan.snapshot if p in current}
    )
    for path, sha in plan.snapshot.items():
        if hashes.get(path) != sha:
            return Refusal(Reason.STALE, path, "the file is not the bytes the plan was made from")
    if isinstance(current, Snapshot) and set(current.files) == set(plan.snapshot):
        snapshot = current
    else:
        source = current.files if isinstance(current, Snapshot) else current
        snapshot = Snapshot({p: source[p] for p in plan.snapshot})
    by_file: dict[str, list[Edit]] = {}
    for edit in plan.edits:
        path = _path(edit)
        if path not in plan.snapshot or _sha(edit) != plan.snapshot[path]:
            return Refusal(Reason.STALE, path, "the edit was planned from another snapshot")
        if path not in plan.files:
            return Refusal(Reason.NOT_ALLOWED, path, "the plan may not edit this file")
        refusal = _check(snapshot, edit)
        if refusal is not None:
            return refusal
        by_file.setdefault(path, []).append(edit)
    files: dict[str, bytes] = {}
    after: dict[str, str] = {}
    splices: dict[str, tuple[Splice, ...]] = {}
    for path, edits in by_file.items():
        text = snapshot.text(path)
        if isinstance(text, Refusal):  # never: each edit's check decoded it
            return text
        pieces = _pieces(text, edits)
        if _overlap(pieces):
            return Refusal(Reason.OVERLAP, path, "two edits own the same text")
        out: list[str] = []
        made: list[Splice] = []
        pos = shift = 0
        for p in pieces:  # all positions are the snapshot's: the same as applying bottom-up
            out.append(text.text[pos : p.start])
            made.append(Splice(p.start, p.end, text.text[p.start : p.end], p.new, p.start + shift))
            out.append(p.new)
            shift += len(p.new) - (p.end - p.start)
            pos = p.end
        out.append(text.text[pos:])
        files[path] = "".join(out).encode("utf-8")
        after[path] = _sha256(files[path])
        splices[path] = tuple(made)
    return Applied(files, {p: plan.snapshot[p] for p in files}, after, splices)


def revert(current: Mapping[str, bytes], applied: Applied) -> dict[str, bytes] | Refusal:
    """The bytes each file held before `applied`, from its bytes now, which must be exactly what `applied` made."""
    out: dict[str, bytes] = {}
    for path, sha in applied.after.items():
        if path not in current or _sha256(current[path]) != sha:
            return Refusal(Reason.STALE, path, "the file is not the bytes the plan made")
        text = current[path].decode("utf-8")
        parts: list[str] = []
        pos = 0
        for s in applied.splices[path]:
            parts.append(text[pos : s.at])
            parts.append(s.old)
            pos = s.at + len(s.new)
        parts.append(text[pos:])
        out[path] = "".join(parts).encode("utf-8")
    return out
