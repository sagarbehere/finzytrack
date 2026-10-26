#!/usr/bin/env python3
"""The failure-mode spec check (S0041; spec: design/specs/failure-modes.md). Stdlib only.

The spec has one table per program (shell S, webview W, main sidecar M, ledger engine E), one row per failure:
`| <letter><n> | <failure> | who | data | user | [States |] Test |`, the failure's number fixed by FAILURES; only the
engine's rows have a States cell. Lines inside a fenced code block or an HTML comment are not read (a line that opens
or closes a comment is hidden whole). This check refuses:
- a table row whose first cell looks like a row ID (letters then digits, in any case) of no known program; a row
  with the wrong number of cells for its program;
- a (program, failure) pair with no row or with two; a row whose ID number and failure disagree; an unknown failure;
- a row whose last cell (Test) is empty or holds anything but `;`-separated references, each one of:
  `` `path::test_name` `` or `` `path::Class::test_name` `` (the file exists inside the repo; a Python test is a
  `def test_...` in it, inside that class when one is named; a Rust test is a `#[test]` `fn`);
  `<backlog id> (<milestone>)` (the row exists in design/backlog.md, is `open` or `in-progress`, and its milestone is
  that one, so a slice that closes the row must cite its test here instead);
  `same as <row id>` alone (a row of the same program that is neither `same as` nor `n/a`; every other cell of the
  row says the same `same as <row id>`);
  `n/a: <reason>` alone (a reason of at least four words);
- in the engine's table, a States cell that is not `n/a: <reason>` or a `;`-separated list of transitions in one form,
  `` `From` → `To` [or `To` ...] on `Event` [or `Event` ...] ``, each (From, Event, To) a transition of the
  statechart reference's Transitions table (design/generated/statecharts/engine-supervision.md).

Usage: python3 scripts/check_failure_modes.py [spec]
Exit 0 = ok (prints the row counts), 1 = refused.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "design/specs/failure-modes.md"
BACKLOG = REPO / "design/backlog.md"
STATECHART = REPO / "design/generated/statecharts/engine-supervision.md"

PROGRAMS = {"S": "the Tauri shell", "W": "the frontend webview", "M": "the main sidecar", "E": "the ledger engine"}
FAILURES = ("crash", "hang", "slow", "killed for memory", "parent gone", "fails to start", "told to stop")
STATES_PROGRAM = "E"
CELLS = {letter: 6 if letter == STATES_PROGRAM else 5 for letter in PROGRAMS}  # after the ID
OPEN = frozenset({"open", "in-progress"})
STATUSES = frozenset({"open", "in-progress", "done", "removed", "post-v0.4", "parked"})

ROW_LIKE = re.compile(r"^[A-Za-z]+\d+$")
ROW_ID = re.compile(r"^([SWME])(\d+)$")
TEST_REF = re.compile(r"^`(?P<path>[\w./-]+)::(?:(?P<cls>[A-Za-z_]\w*)::)?(?P<name>[A-Za-z_]\w*)`$")
BACKLOG_REF = re.compile(r"^(?P<id>[A-Z][A-Za-z0-9.]*(?:-[A-Za-z0-9.]+)+) \((?P<milestone>M\d+[ab]?)\)$")
SAME_AS = re.compile(r"^same as (?P<id>[A-Za-z]+\d+)$")
NOT_APPLICABLE = re.compile(r"^n/a: (?P<reason>.+)$")
TICKED = re.compile(r"`([^`]+)`")
NAME = re.compile(r"^`([A-Za-z]\w*)`$")
TRANSITION = re.compile(r"^(?P<src>`[A-Za-z]\w*`) → (?P<to>.+) on (?P<on>.+)$")
FENCE = re.compile(r"^\s*(```|~~~)")
MIN_REASON_WORDS = 4


@dataclass(frozen=True)
class Row:
    id: str
    cells: tuple[str, ...]
    line: int

    @property
    def test(self) -> str:
        return self.cells[-1]


def cells_of(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def visible_lines(text: str) -> Iterator[tuple[int, str]]:
    """(line number, line) outside fenced code blocks and HTML comments. Conservative: a line that opens or closes a
    comment, or holds a whole one, is hidden whole, so a row can only be hidden, never half-read."""
    fenced = commented = False
    for n, line in enumerate(text.splitlines(), 1):
        if not commented and FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if commented:
            commented = "-->" not in line
            continue
        if "<!--" in line:
            commented = "-->" not in line.split("<!--", 1)[1]
            continue
        yield n, line


def spec_rows(text: str) -> tuple[list[Row], list[str]]:
    """The rows, and the problems of table rows that look like rows of no known program."""
    rows: list[Row] = []
    problems: list[str] = []
    for n, line in visible_lines(text):
        if not line.lstrip().startswith("|"):
            continue
        cells = cells_of(line)
        if not cells or not ROW_LIKE.match(cells[0]):
            continue
        if not ROW_ID.match(cells[0]):
            problems.append(f"line {n}: {cells[0]}: not a row of a known program ({', '.join(PROGRAMS)})")
            continue
        rows.append(Row(cells[0], tuple(cells[1:]), n))
    return rows, problems


def backlog_rows(text: str) -> dict[str, tuple[str, str]]:
    """Backlog ID -> (milestone, status), from every ID-headed table row: the status is the first cell after the title
    that is a status value, the milestone the cell before it (tables differ in their middle columns)."""
    out: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        cells = cells_of(line)
        if len(cells) < 4 or not BACKLOG_REF.match(f"{cells[0]} (M0)"):
            continue
        for i in range(2, len(cells)):
            if cells[i] in STATUSES:
                milestone = cells[i - 1].split()[0] if cells[i - 1].split() else ""
                out[cells[0]] = (milestone, cells[i])
                break
    return out


def statechart_transitions(text: str) -> dict[tuple[str, str], frozenset[str]]:
    """(From, Event) -> the states it may lead to, from the reference's `## Transitions` table."""
    out: dict[tuple[str, str], frozenset[str]] = {}
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section != "Transitions" or not line.startswith("| `"):
            continue
        cells = cells_of(line)
        src, event = NAME.match(cells[0]), NAME.match(cells[1])
        if src and event:
            out[(src.group(1), event.group(1))] = frozenset(TICKED.findall(cells[2]))
    return out


def test_exists(path: str, cls: str | None, name: str, repo: Path) -> str | None:
    """None when the test exists, else why not."""
    parts = PurePosixPath(path).parts
    file = (repo / path).resolve()
    if path.startswith("/") or ".." in parts or not file.is_relative_to(repo.resolve()):
        return f"{path} is outside the repo"
    if not file.is_file():
        return f"no file {path}"
    text = file.read_text(encoding="utf-8")
    if path.endswith(".py"):
        if not name.startswith("test_"):
            return f"{name} is not a test name"
        body = text
        if cls is not None:
            m = re.search(rf"^class {re.escape(cls)}\b.*?(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
            if m is None:
                return f"no class {cls} in {path}"
            body = m.group(0)
        indent = r"\s+" if cls is not None else ""
        if not re.search(rf"^{indent}(?:async\s+)?def {re.escape(name)}\(", body, re.MULTILINE):
            return f"no test {name} in {path}" + (f" class {cls}" if cls else "")
        return None
    if path.endswith(".rs"):
        if cls is not None:
            return f"a Rust test is named by its fn only: {path}::{name}"
        if not re.search(rf"#\[test\]\s*\n\s*fn {re.escape(name)}\(", text):
            return f"no #[test] fn {name} in {path}"
        return None
    return f"{path}: only Python and Rust tests are known"


def check(spec: str, backlog: str, statechart: str, repo: Path = REPO) -> tuple[list[str], Counter[str]]:
    counts: Counter[str] = Counter()
    rows, problems = spec_rows(spec)
    by_id: dict[str, Row] = {}
    for row in rows:
        if row.id in by_id:
            problems.append(f"line {row.line}: row {row.id} appears twice")
        by_id[row.id] = row
    seen: Counter[tuple[str, str]] = Counter()
    shaped: list[Row] = []
    for row in rows:
        m = ROW_ID.match(row.id)
        assert m is not None  # noqa: S101 (spec_rows kept only matching ids)
        letter, number = m.group(1), int(m.group(2))
        if len(row.cells) != CELLS[letter]:
            problems.append(f"line {row.line}: {row.id}: {len(row.cells)} cells after the ID, its table has {CELLS[letter]}")
            continue
        shaped.append(row)
        failure = row.cells[0]
        if failure not in FAILURES:
            problems.append(f"line {row.line}: {row.id}: unknown failure {failure!r}")
            continue
        if FAILURES.index(failure) + 1 != number:
            problems.append(f"line {row.line}: {row.id} is {failure!r}, which is number {FAILURES.index(failure) + 1}")
        seen[(letter, failure)] += 1
    for letter, program in PROGRAMS.items():
        for failure in FAILURES:
            n = seen[(letter, failure)]
            if n == 0:
                problems.append(f"no row for {program}: {failure}")
            elif n > 1:
                problems.append(f"{n} rows for {program}: {failure}")
    items = backlog_rows(backlog)
    transitions = statechart_transitions(statechart)
    for row in shaped:
        problems += [f"line {row.line}: {row.id}: {p}" for p in check_test_cell(row, by_id, items, repo, counts)]
        if row.id.startswith(STATES_PROGRAM) and not SAME_AS.match(row.test):
            problems += [f"line {row.line}: {row.id}: {p}" for p in check_states(row.cells[-2], transitions)]
    return problems, counts


def check_test_cell(
    row: Row, by_id: dict[str, Row], items: dict[str, tuple[str, str]], repo: Path, counts: Counter[str]
) -> list[str]:
    cell = row.test
    if not cell:
        return ["the Test cell is empty"]
    refs = [r.strip() for r in cell.split(";")]
    if m := SAME_AS.match(cell):
        return check_same_as(row, m.group("id"), by_id, counts)
    if m := NOT_APPLICABLE.match(cell):
        if len(m.group("reason").split()) < MIN_REASON_WORDS:
            return ["n/a needs a reason"]
        counts["n/a"] += 1
        return []
    problems: list[str] = []
    tested = planned = 0
    for ref in refs:
        if t := TEST_REF.match(ref):
            why = test_exists(t.group("path"), t.group("cls"), t.group("name"), repo)
            if why is not None:
                problems.append(why)
            tested += 1
        elif b := BACKLOG_REF.match(ref):
            item = items.get(b.group("id"))
            if item is None:
                problems.append(f"no backlog row {b.group('id')}")
            elif item[1] not in OPEN:
                problems.append(f"{b.group('id')} is {item[1]}: cite the test that closed it")
            elif item[0] != b.group("milestone"):
                problems.append(f"{b.group('id')} is due in {item[0]}, not {b.group('milestone')}")
            planned += 1
        else:
            problems.append(f"not a test or backlog reference: {ref!r}")
    if not problems:
        counts["tested and planned" if tested and planned else "tested" if tested else "planned"] += 1
    return problems


def check_same_as(row: Row, target_id: str, by_id: dict[str, Row], counts: Counter[str]) -> list[str]:
    target = by_id.get(target_id)
    if target is None:
        return [f"same as {target_id}: no such row"]
    if target_id[0] != row.id[0]:
        return [f"same as {target_id}: a row of another program"]
    if SAME_AS.match(target.test) or NOT_APPLICABLE.match(target.test):
        return [f"same as {target.id}, which is itself {target.test.split(':')[0]!r}"]
    if any(c not in ("", row.test) for c in row.cells[1:]):
        return [f"every cell of a same-as row says {row.test!r}"]
    counts["same as"] += 1
    return []


def check_states(cell: str, transitions: dict[tuple[str, str], frozenset[str]]) -> list[str]:
    if m := NOT_APPLICABLE.match(cell):
        return [] if len(m.group("reason").split()) >= MIN_REASON_WORDS else ["n/a needs a reason"]
    problems: list[str] = []
    for part in [p.strip() for p in cell.split(";")]:
        claim = parse_transition(part)
        if claim is None:
            problems.append(f"not a transition (`From` → `To` [or `To`] on `Event` [or `Event`]): {part!r}")
            continue
        src, targets, events = claim
        for event in events:
            allowed = transitions.get((src, event))
            if allowed is None:
                problems.append(f"`{src}` on `{event}` is not in the statechart")
                continue
            problems += [f"`{src}` on `{event}` does not lead to `{t}`" for t in targets if t not in allowed]
    return problems


def parse_transition(part: str) -> tuple[str, list[str], list[str]] | None:
    m = TRANSITION.match(part)
    if m is None:
        return None
    names: list[list[str]] = []
    for side in (m.group("to"), m.group("on")):
        found = [NAME.match(x) for x in side.split(" or ")]
        if not all(found):
            return None
        names.append([f.group(1) for f in found if f])
    src = NAME.match(m.group("src"))
    assert src is not None  # noqa: S101 (TRANSITION matched it)
    return src.group(1), names[0], names[1]


def main(argv: list[str]) -> int:
    spec = Path(argv[0]) if argv else SPEC
    problems, counts = check(
        spec.read_text(encoding="utf-8"),
        BACKLOG.read_text(encoding="utf-8"),
        STATECHART.read_text(encoding="utf-8"),
    )
    if problems:
        for p in problems:
            sys.stderr.write(f"check_failure_modes: {p}\n")
        return 1
    summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    sys.stdout.write(f"check_failure_modes: ok: {sum(counts.values())} rows ({summary})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
