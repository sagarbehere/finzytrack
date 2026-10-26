#!/usr/bin/env python3
"""The failure-mode spec check (S0041, narrowed in S0043; spec: design/specs/failure-modes.md). Stdlib only.

It keeps the spec honest about what is tested; it does not police the Markdown. A row is a table line whose first
cell is `<program letter><n>` (S shell, W webview, M main sidecar, E ledger engine); its second cell is the failure,
its last cell (Test) the evidence, and on an engine row the cell before it (States) the transitions. Lines inside a
fenced code block or an HTML comment are not read (a line that opens or closes a comment is hidden whole). Refused:
- a (program, failure) pair, for each failure of FAILURES, with no row or with two;
- a table line whose first cell looks like a row ID (letters then digits, any case) of no known program;
- a Test cell that is not `same as <row>` alone, `n/a: <reason>` alone, or `;`-separated references, each
  `` `path::test_name` `` or `` `path::Class::test_name` `` (the file is inside the repo; a Python `def test_...`,
  inside that class when one is named; a Rust `#[test] fn`) or `<backlog id> (<milestone>)` (the backlog row is open
  or in progress and due in that milestone, so a slice that closes it must cite its test here);
- `same as` a row that is missing, of another program, or itself `same as` or `n/a` (it would claim no test);
- an engine row's States cell that is not `n/a: <reason>` or `;`-separated transitions in the form
  `` `From` → `To` [or `To` ...] on `Event` [or `Event` ...] ``, each one in the Transitions table of the statechart
  reference (design/generated/statecharts/engine-supervision.md).

Usage: python3 scripts/check_failure_modes.py [spec]
Exit 0 = ok (prints the row counts), 1 = refused.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "design/specs/failure-modes.md"
BACKLOG = REPO / "design/backlog.md"
STATECHART = REPO / "design/generated/statecharts/engine-supervision.md"

PROGRAMS = {"S": "the Tauri shell", "W": "the frontend webview", "M": "the main sidecar", "E": "the ledger engine"}
FAILURES = ("crash", "hang", "slow", "killed for memory", "parent gone", "fails to start", "told to stop")
STATES_PROGRAM = "E"
OPEN = frozenset({"open", "in-progress"})
STATUSES = frozenset({"open", "in-progress", "done", "removed", "post-v0.4", "parked"})

ROW_LIKE = re.compile(r"^[A-Za-z]+\d+$")
ROW_ID = re.compile(r"^[SWME]\d+$")
TEST_REF = re.compile(r"^`(?P<path>[\w./-]+)::(?:(?P<cls>[A-Za-z_]\w*)::)?(?P<name>[A-Za-z_]\w*)`$")
BACKLOG_REF = re.compile(r"^(?P<id>[A-Z][A-Za-z0-9.]*(?:-[A-Za-z0-9.]+)+) \((?P<milestone>M\d+[ab]?)\)$")
SAME_AS = re.compile(r"^same as (?P<id>[A-Za-z]+\d+)$")
NOT_APPLICABLE = re.compile(r"^n/a: \S")
NAME = re.compile(r"^`([A-Za-z]\w*)`$")
TRANSITION = re.compile(r"^(?P<src>`[A-Za-z]\w*`) → (?P<to>.+) on (?P<on>.+)$")
FENCE = re.compile(r"^\s*(```|~~~)")


def cells_of(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def visible_lines(text: str) -> Iterator[tuple[int, str]]:
    """(line number, line) outside fenced code blocks and HTML comments; a line that touches a comment is hidden."""
    fenced = commented = False
    for n, line in enumerate(text.splitlines(), 1):
        if not commented and FENCE.match(line):
            fenced = not fenced
        elif commented:
            commented = "-->" not in line
        elif "<!--" in line:
            commented = "-->" not in line.split("<!--", 1)[1]
        elif not fenced:
            yield n, line


def spec_rows(text: str) -> tuple[dict[str, list[str]], list[tuple[int, str, list[str]]], list[str]]:
    """(row ID -> its cells after the ID, every row as (line, ID, cells), problems of rows of no known program)."""
    by_id: dict[str, list[str]] = {}
    rows: list[tuple[int, str, list[str]]] = []
    problems: list[str] = []
    for n, line in visible_lines(text):
        cells = cells_of(line) if line.lstrip().startswith("|") else []
        if not cells or not ROW_LIKE.match(cells[0]):
            continue
        if not ROW_ID.match(cells[0]):
            problems.append(f"line {n}: {cells[0]}: not a row of a known program ({', '.join(PROGRAMS)})")
            continue
        by_id[cells[0]] = cells[1:]
        rows.append((n, cells[0], cells[1:]))
    return by_id, rows, problems


def backlog_rows(text: str) -> dict[str, tuple[str, str]]:
    """Backlog ID -> (milestone, status), from every ID-headed table row: the status is the first cell after the title
    that is a status value, the milestone the cell before it (tables differ in their middle columns)."""
    out: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        cells = cells_of(line) if line.startswith("| ") else []
        if len(cells) < 4 or not BACKLOG_REF.match(f"{cells[0]} (M0)"):
            continue
        for i in range(2, len(cells)):
            if cells[i] in STATUSES:
                out[cells[0]] = ((cells[i - 1].split() or [""])[0], cells[i])
                break
    return out


def statechart_transitions(text: str) -> dict[tuple[str, str], frozenset[str]]:
    """(From, Event) -> the states it may lead to, from the reference's `## Transitions` table."""
    out: dict[tuple[str, str], frozenset[str]] = {}
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif section == "Transitions" and line.startswith("| `"):
            cells = cells_of(line)
            src, event = NAME.match(cells[0]), NAME.match(cells[1])
            if src and event:
                out[(src.group(1), event.group(1))] = frozenset(re.findall(r"`([^`]+)`", cells[2]))
    return out


def test_exists(path: str, cls: str | None, name: str, repo: Path) -> str | None:
    """None when the test exists, else why not."""
    file = (repo / path).resolve()
    if path.startswith("/") or ".." in PurePosixPath(path).parts or not file.is_relative_to(repo.resolve()):
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
    if path.endswith(".rs") and cls is None:
        found = re.search(rf"#\[test\]\s*\n\s*fn {re.escape(name)}\(", text)
        return None if found else f"no #[test] fn {name} in {path}"
    return f"{path}: only Python tests and Rust #[test] fns are known"


def check(spec: str, backlog: str, statechart: str, repo: Path = REPO) -> tuple[list[str], Counter[str]]:
    by_id, rows, problems = spec_rows(spec)
    seen = Counter((rid[0], cells[0]) for _, rid, cells in rows if cells)
    for letter, program in PROGRAMS.items():
        for failure in FAILURES:
            if (k := seen[(letter, failure)]) != 1:
                problems.append(f"{f'{k} rows' if k else 'no row'} for {program}: {failure}")
    items, transitions = backlog_rows(backlog), statechart_transitions(statechart)
    counts: Counter[str] = Counter()
    for n, rid, cells in rows:
        test = cells[-1] if cells else ""
        found, kind = check_test_cell(rid, test, by_id, items, repo)
        if rid[0] == STATES_PROGRAM and not SAME_AS.match(test):
            found += check_states(cells[-2] if len(cells) > 1 else "", transitions)
        problems += [f"line {n}: {rid}: {p}" for p in found]
        counts[kind] += not found
    return problems, +counts


def check_test_cell(
    rid: str, cell: str, by_id: dict[str, list[str]], items: dict[str, tuple[str, str]], repo: Path
) -> tuple[list[str], str]:
    """(problems, the kind of evidence) for one Test cell."""
    if m := SAME_AS.match(cell):
        target = by_id.get(m.group("id"))
        if target is None:
            return [f"same as {m.group('id')}: no such row"], "same as"
        if m.group("id")[0] != rid[0]:
            return [f"same as {m.group('id')}: a row of another program"], "same as"
        if SAME_AS.match(target[-1]) or NOT_APPLICABLE.match(target[-1]):
            return [f"same as {m.group('id')}, which cites no test itself"], "same as"
        return [], "same as"
    if NOT_APPLICABLE.match(cell):
        return [], "n/a"
    problems: list[str] = []
    kinds = set()
    for ref in (r.strip() for r in cell.split(";")):
        if t := TEST_REF.match(ref):
            kinds.add("tested")
            if why := test_exists(t.group("path"), t.group("cls"), t.group("name"), repo):
                problems.append(why)
        elif b := BACKLOG_REF.match(ref):
            kinds.add("planned")
            milestone, status = items.get(b.group("id"), ("", ""))
            if not status:
                problems.append(f"no backlog row {b.group('id')}")
            elif status not in OPEN:
                problems.append(f"{b.group('id')} is {status}: cite the test that closed it")
            elif milestone != b.group("milestone"):
                problems.append(f"{b.group('id')} is due in {milestone}, not {b.group('milestone')}")
        else:
            problems.append(f"not a test or backlog reference: {ref!r}")
    return problems, " and ".join(sorted(kinds, reverse=True))


def check_states(cell: str, transitions: dict[tuple[str, str], frozenset[str]]) -> list[str]:
    if NOT_APPLICABLE.match(cell):
        return []
    problems: list[str] = []
    for part in (p.strip() for p in cell.split(";")):
        m = TRANSITION.match(part)
        sides = [[NAME.match(x) for x in m.group(g).split(" or ")] for g in ("src", "to", "on")] if m else []
        if not sides or not all(all(side) for side in sides):
            problems.append(f"not a transition (`From` → `To` [or `To`] on `Event` [or `Event`]): {part!r}")
            continue
        (src,), targets, events = ([x.group(1) for x in side if x] for side in sides)
        for event in events:
            allowed = transitions.get((src, event))
            if allowed is None:
                problems.append(f"`{src}` on `{event}` is not in the statechart")
            else:
                problems += [f"`{src}` on `{event}` does not lead to `{t}`" for t in targets if t not in allowed]
    return problems


def main(argv: list[str]) -> int:
    spec = Path(argv[0]) if argv else SPEC
    texts = (p.read_text(encoding="utf-8") for p in (spec, BACKLOG, STATECHART))
    problems, counts = check(*texts)
    for p in problems:
        sys.stderr.write(f"check_failure_modes: {p}\n")
    if problems:
        return 1
    summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    sys.stdout.write(f"check_failure_modes: ok: {sum(counts.values())} rows ({summary})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
