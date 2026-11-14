"""The intended change and the contract's checks of a candidate text before any load (ADR-0008 steps 1, 2 and 2b,
as amended by ADR-0067; S0116). Pure over text: Beancount's parser only (through `beancount_port`), nothing read or
written.

**The intended change** (`IntendedChange`, ADR-0009: an edit producer returns it with its edits) is the targeted set
of `design/specs/write-path/README.md`: the entries inserted (each with the file it lands in), replaced and removed,
each by its canonical key (`entry_keys.entry_key`); a replaced or removed entry also by the `Target` (file, line, date,
keyword) of its text, and the key the producer read there. The producer works it out from its request, apart from
the edits; the checks below hold the edits' result to it.

1. **`parse`** (step 1): the old and the new text of each edited file through Beancount's parser, each as that file
   (its path is the report name). A parser error the new text has and the old one has not (by error key, so a line
   shift does not count) refuses `parse_error`: the text Finzytrack wrote did not parse (ADR-0067's one exception: it
   blocks).
2. **`diff`** (step 2), per edited file:
   - the options (plugins and includes among them) of the new text equal the old's (`options_changed`), the display
     context aside (Beancount derives it from the numbers in the file);
   - each replaced or removed target is, in the old text, an entry at that line whose key is the intent's `old`
     (`target_not_intended`): a plugin's entry, an entry of another file or a second entry with the same text is not
     the target;
   - the edits are the intent's: the entries the plan's replace and token edits own are exactly the intent's
     replacements, and its deletes exactly the removals (`edits_not_intent`); where its inserts land is held by the
     next point, file by file;
   - the new text's entries are the old text's untargeted entries, in their order, with exactly the intended new
     entries (replacements and insertions) among them (`not_intended_change`): every other entry is identical, by key.
     Example: deleting the dinner of 6 January leaves the lunch of 5 January with the same text; if its amount
     changed from `10.50` to `10.51`, or the dinner's text came back as a new entry, the plan is refused.
   A file the intent names and the plan does not change, or the reverse, is refused the same way.
3. **`outside_spans`** (step 2b): each new text is the old text with exactly the planned spans replaced: the spans
   are worked out from the plan's edits (never taken from `apply_edits`' record), the candidate files must be exactly
   the files the edits name, the record's splices must be at exactly those spans, and the old text with each span
   replaced by its splice's new text must be the candidate, byte for byte (`outside_spans_changed`). This catches a
   changed comment, whitespace or line ending, which step 2 cannot see.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger.core import entry_keys
from ledger_engine.ledger.core import source_edit as se
from ledger_engine.mirror.core import rows


class Reason(StrEnum):
    PARSE_ERROR = "parse_error"  # step 1: the new text has a parser error the old one has not
    OPTIONS_CHANGED = "options_changed"  # step 2: an option, plugin or include line changed
    NOT_TARGET = "target_not_intended"  # step 2: no entry with the intent's key at the target's line
    EDITS_NOT_INTENT = "edits_not_intent"  # step 2: the plan's edits own other entries or files than the intent's
    NOT_INTENDED = "not_intended_change"  # step 2: the new entries are not the untargeted ones plus the intended
    OUTSIDE_SPANS = "outside_spans_changed"  # step 2b: a byte outside the planned spans changed


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: Reason
    path: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class Insertion:
    """A new entry in file `path`, by its key."""

    path: str
    key: str


@dataclass(frozen=True, slots=True)
class Replacement:
    """The entry at `target`, whose key is `old`, becomes the entry whose key is `new`."""

    target: se.Target
    old: str
    new: str


@dataclass(frozen=True, slots=True)
class Removal:
    """The entry at `target`, whose key is `old`, is removed."""

    target: se.Target
    old: str


@dataclass(frozen=True, slots=True)
class IntendedChange:
    inserts: tuple[Insertion, ...] = ()
    replaces: tuple[Replacement, ...] = ()
    removes: tuple[Removal, ...] = ()

    def paths(self) -> set[str]:
        return (
            {i.path for i in self.inserts}
            | {r.target.path for r in self.replaces}
            | {r.target.path for r in self.removes}
        )


@dataclass(frozen=True, slots=True)
class Produced:
    """What an edit producer returns (ADR-0009): the plan and the change it is meant to make."""

    plan: se.EditPlan
    intent: IntendedChange


@dataclass(frozen=True, slots=True)
class FileParse:
    entries: tuple[port.Directive, ...]
    errors: tuple[port.BeancountError, ...]
    options: port.OptionsMap


@dataclass(frozen=True, slots=True)
class Parses:
    """Step 1's parses of each edited file: the old text's and the new text's."""

    old: Mapping[str, FileParse]
    new: Mapping[str, FileParse]


DERIVED_OPTIONS: Final = frozenset({"dcontext"})  # Beancount's parser derives it from the numbers in the file


def _parse(data: bytes, path: str) -> FileParse:
    entries, errors, options = port.parse_string(data, path)
    return FileParse(tuple(entries), tuple(errors), options)


def _old(old: se.Snapshot, path: str) -> FileParse:
    parsed = old.parsed(path)
    if isinstance(parsed, se.Refusal):  # the file cannot be read as text: parsed from its bytes, as Beancount would
        return _parse(old.files[path], path)
    return FileParse(parsed.order, parsed.errors, parsed.options)


def parse(old: se.Snapshot, new: Mapping[str, bytes]) -> Parses | Refusal:
    """Step 1, for each file of `new` (its old bytes in the snapshot `old`, whose parse is reused when it has one)."""
    olds: dict[str, FileParse] = {}
    news: dict[str, FileParse] = {}
    for path in new:
        olds[path] = _old(old, path)
        news[path] = _parse(new[path], path)
        added = entry_keys.new_errors(
            rows.error_rows(news[path].errors), entry_keys.error_keys(rows.error_rows(olds[path].errors))
        )
        if added:
            line = added[0][1][2]
            return Refusal(Reason.PARSE_ERROR, path, f"the new text has a parser error at line {line}")
    return Parses(olds, news)


def _options(options: port.OptionsMap) -> dict[str, object]:
    return {k: v for k, v in options.items() if k not in DERIVED_OPTIONS}


def _lineno(entry: port.Directive) -> int:
    return int(entry.meta["lineno"])


def _is_subsequence(part: Sequence[str], whole: Sequence[str]) -> bool:
    it: Iterator[str] = iter(whole)
    return all(any(k == w for w in it) for k in part)


def diff(parses: Parses, plan: se.EditPlan, intent: IntendedChange) -> Refusal | None:
    """Step 2: the new texts' entries are exactly the old texts' with the intended change made."""
    edited = set(parses.new)
    extra = sorted(intent.paths() - edited)
    if extra:
        return Refusal(Reason.NOT_INTENDED, extra[0], "the intent changes a file the plan does not change")
    refusal = _edits_are_intent(plan, intent)
    if refusal is not None:
        return refusal
    for path in sorted(edited):
        old, new = parses.old[path], parses.new[path]
        if _options(old.options) != _options(new.options):
            return Refusal(Reason.OPTIONS_CHANGED, path, "an option, plugin or include line changed")
        refusal = _file_diff(path, old, new, intent)
        if refusal is not None:
            return refusal
    return None


def _edits_are_intent(plan: se.EditPlan, intent: IntendedChange) -> Refusal | None:
    replaced = {
        (e.span.target.path, e.span.target.lineno) for e in plan.edits if isinstance(e, se.Replace | se.ReplaceToken)
    }
    deleted = {(e.span.target.path, e.span.target.lineno) for e in plan.edits if isinstance(e, se.Delete)}
    want_replaced = {(r.target.path, r.target.lineno) for r in intent.replaces}
    want_deleted = {(r.target.path, r.target.lineno) for r in intent.removes}
    if len(want_replaced) != len(intent.replaces) or len(want_deleted) != len(intent.removes):
        return Refusal(Reason.EDITS_NOT_INTENT, None, "the intent names one entry twice")
    if replaced != want_replaced or deleted != want_deleted or replaced & deleted:
        wrong = sorted((replaced ^ want_replaced) | (deleted ^ want_deleted) | (replaced & deleted))
        path, line = wrong[0]
        return Refusal(Reason.EDITS_NOT_INTENT, path, f"the plan's edits and the intent differ at line {line}")
    return None


def _file_diff(path: str, old: FileParse, new: FileParse, intent: IntendedChange) -> Refusal | None:
    at_line = {_lineno(e): e for e in old.entries}
    targeted: set[int] = set()
    expected = Counter[str]()
    for r in [*intent.replaces, *intent.removes]:
        if r.target.path != path:
            continue
        entry = at_line.get(r.target.lineno)
        if entry is None or entry_keys.entry_key(entry) != r.old:
            line = r.target.lineno
            return Refusal(Reason.NOT_TARGET, path, f"line {line} does not hold the intended entry")
        targeted.add(r.target.lineno)
        if isinstance(r, Replacement):
            expected[r.new] += 1
    expected.update(i.key for i in intent.inserts if i.path == path)
    untargeted = [entry_keys.entry_key(e) for e in old.entries if _lineno(e) not in targeted]
    keys = [entry_keys.entry_key(e) for e in new.entries]
    if Counter(keys) != Counter(untargeted) + expected or not _is_subsequence(untargeted, keys):
        return Refusal(Reason.NOT_INTENDED, path, _what_differs(untargeted, expected, new.entries, keys))
    return None


def _what_differs(
    untargeted: Sequence[str], expected: Counter[str], entries: Sequence[port.Directive], keys: Sequence[str]
) -> str:
    """Where the new text first departs from the intended result (a line of the new text, never its content)."""
    allowed = Counter(untargeted) + expected
    for entry, key in zip(entries, keys, strict=True):
        if allowed[key] <= 0:
            return f"line {_lineno(entry)} of the new text is not an intended entry"
        allowed[key] -= 1
    if +allowed:
        return "an intended or untargeted entry is missing from the new text"
    return "the untargeted entries are not in their order"


# ---- step 2b ---------------------------------------------------------------------------------------------------------


def _planned_regions(snapshot: se.Snapshot, path: str, edits: Sequence[se.Edit]) -> list[tuple[int, int]] | None:
    """The character ranges of the old text the plan's edits own (an insert: the point it goes at; inserts at one
    point are one), worked out from the edits and the old text alone. None when the old text cannot be read."""
    text = snapshot.text(path)
    if isinstance(text, se.Refusal):
        return None
    regions: set[tuple[int, int]] = set()
    for e in edits:
        if isinstance(e, se.Insert):
            point = len(text.text) if e.line is None else text.offsets[e.line]
            regions.add((point, point))
        elif isinstance(e, se.ReplaceToken):
            regions.add((e.start, e.end))
        else:
            regions.add((e.span.start, e.span.end))
    return sorted(regions)


def outside_spans(old: se.Snapshot, applied: se.Applied, plan: se.EditPlan) -> Refusal | None:
    """Step 2b: every candidate is its old text with exactly the planned spans replaced."""
    by_file: dict[str, list[se.Edit]] = {}
    for e in plan.edits:
        by_file.setdefault(e.path if isinstance(e, se.Insert) else e.span.target.path, []).append(e)
    if set(applied.files) != set(by_file):
        path = sorted(set(applied.files) ^ set(by_file))[0]
        return Refusal(Reason.OUTSIDE_SPANS, path, "the candidate files are not the files the plan edits")
    for path, edits in sorted(by_file.items()):
        regions = _planned_regions(old, path, edits)
        splices = sorted(applied.splices.get(path, ()), key=lambda s: (s.start, s.end))
        if regions is None or [(s.start, s.end) for s in splices] != regions:
            return Refusal(Reason.OUTSIDE_SPANS, path, "the splices are not the planned spans")
        text = old.files[path].decode("utf-8")
        parts: list[str] = []
        pos = 0
        for s in splices:
            parts += [text[pos : s.start], s.new]
            pos = s.end
        parts.append(text[pos:])
        if "".join(parts).encode("utf-8") != applied.files[path]:
            return Refusal(Reason.OUTSIDE_SPANS, path, "a byte outside the planned spans changed")
    return None
