#!/usr/bin/env python3
"""The failure-mode spec check (S0041; spec: design/specs/failure-modes.md). Stdlib only.

The spec has one table per program (shell S, webview W, main sidecar M, ledger engine E), one row per failure:
`| <letter><n> | <failure> | ... | Test |`, the failure's number fixed by FAILURES. This check refuses:
- a (program, failure) pair with no row or with two; a row whose ID number and failure disagree; an unknown failure;
- a row whose last cell (Test) is empty or holds anything but `;`-separated references, each one of:
  `` `path::test_name` `` or `` `path::Class::test_name` `` (the file exists; a Python test is a `def test_...` in it,
  inside that class when one is named; a Rust test is a `#[test]` `fn`);
  `<backlog id> (<milestone>)` (the row exists in design/backlog.md, is `open` or `in-progress`, and its milestone is
  that one, so a slice that closes the row must cite its test here instead);
  `same as <row id>` alone (the row exists and is neither `same as` nor `n/a`);
  `n/a: <reason>` alone (a reason of at least four words);
- in the engine's table, a States cell that names no state of the statechart reference
  (design/generated/statecharts/engine-supervision.md) or names a state or event it does not list, unless it is
  `n/a: <reason>`.

Usage: python3 scripts/check_failure_modes.py [spec]
Exit 0 = ok (prints the row counts), 1 = refused.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "design/specs/failure-modes.md"
BACKLOG = REPO / "design/backlog.md"
STATECHART = REPO / "design/generated/statecharts/engine-supervision.md"

PROGRAMS = {"S": "the Tauri shell", "W": "the frontend webview", "M": "the main sidecar", "E": "the ledger engine"}
FAILURES = ("crash", "hang", "slow", "killed for memory", "parent gone", "fails to start", "told to stop")
STATES_PROGRAM = "E"
OPEN = frozenset({"open", "in-progress"})
STATUSES = frozenset({"open", "in-progress", "done", "removed", "post-v0.4", "parked"})

ROW_ID = re.compile(r"^([SWME])(\d+)$")
TEST_REF = re.compile(r"^`(?P<path>[\w./-]+)::(?:(?P<cls>[A-Za-z_]\w*)::)?(?P<name>[A-Za-z_]\w*)`$")
BACKLOG_REF = re.compile(r"^(?P<id>[A-Z][A-Za-z0-9.]*(?:-[A-Za-z0-9.]+)+) \((?P<milestone>M\d+[ab]?)\)$")
SAME_AS = re.compile(r"^same as (?P<id>[SWME]\d+)$")
NOT_APPLICABLE = re.compile(r"^n/a: (?P<reason>.+)$")
TICKED = re.compile(r"`([^`]+)`")
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


def spec_rows(text: str) -> list[Row]:
    rows: list[Row] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.startswith("|"):
            continue
        cells = cells_of(line)
        if cells and ROW_ID.match(cells[0]):
            rows.append(Row(cells[0], tuple(cells[1:]), n))
    return rows


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


def statechart_names(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """(states, events) from the reference's `## States` and `## Events` tables."""
    found: dict[str, set[str]] = {"States": set(), "Events": set()}
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section in found and line.startswith("| `"):
            m = TICKED.match(cells_of(line)[0])
            if m:
                found[section].add(m.group(1))
    return frozenset(found["States"]), frozenset(found["Events"])


def test_exists(path: str, cls: str | None, name: str, repo: Path) -> str | None:
    """None when the test exists, else why not."""
    file = repo / path
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
    problems: list[str] = []
    counts: Counter[str] = Counter()
    rows = spec_rows(spec)
    by_id: dict[str, Row] = {}
    for row in rows:
        if row.id in by_id:
            problems.append(f"line {row.line}: row {row.id} appears twice")
        by_id[row.id] = row
    seen: Counter[tuple[str, str]] = Counter()
    for row in rows:
        m = ROW_ID.match(row.id)
        assert m is not None  # noqa: S101 (spec_rows kept only matching ids)
        letter, number = m.group(1), int(m.group(2))
        failure = row.cells[0] if row.cells else ""
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
    states, events = statechart_names(statechart)
    for row in rows:
        problems += [f"line {row.line}: {row.id}: {p}" for p in check_test_cell(row, by_id, items, repo, counts)]
        if row.id.startswith(STATES_PROGRAM):
            problems += [f"line {row.line}: {row.id}: {p}" for p in check_states(row, states, events)]
    return problems, counts


def check_test_cell(
    row: Row, by_id: dict[str, Row], items: dict[str, tuple[str, str]], repo: Path, counts: Counter[str]
) -> list[str]:
    cell = row.test
    if not cell:
        return ["the Test cell is empty"]
    refs = [r.strip() for r in cell.split(";")]
    if m := SAME_AS.match(cell):
        target = by_id.get(m.group("id"))
        if target is None:
            return [f"same as {m.group('id')}: no such row"]
        if SAME_AS.match(target.test) or NOT_APPLICABLE.match(target.test):
            return [f"same as {target.id}, which is itself {target.test.split(':')[0]!r}"]
        counts["same as"] += 1
        return []
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


def check_states(row: Row, states: frozenset[str], events: frozenset[str]) -> list[str]:
    if len(row.cells) < 2:
        return ["no States cell"]
    cell = row.cells[-2]
    if m := NOT_APPLICABLE.match(cell):
        return [] if len(m.group("reason").split()) >= MIN_REASON_WORDS else ["n/a needs a reason"]
    names = TICKED.findall(cell)
    problems = [f"{n!r} is not a state or event of the statechart" for n in names if n not in states | events]
    if not any(n in states for n in names):
        problems.append("names no state of the statechart")
    return problems


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
