#!/usr/bin/env python3
"""Retired-terms check (S0047): the current-state documents do not describe an abandoned design as current.

Usage: python3 scripts/check_retired_terms.py [ROOT]      (ROOT defaults to this repository)

ARCHITECTURE.md §0 states where the design converged on 2026-10-27 and lists what was abandoned (Share workspaces,
an engine pool and its admission, the heavy-load semaphore and the write limiter, territories, several open
workspaces, ...). The convergence audit of that day found those words left in documents that state the current
design; this check refuses them there, so the old designs cannot come back by copy and paste.

Checked (the current-state documents): `design/ARCHITECTURE.md`, `design/INVARIANTS.md`, `design/ENGINEERING.md`,
`design/glossary.md`, every Markdown file under `design/specs/`, `design/capabilities.toml` and `design/backlog.md`.
Not checked, because they are history: journals, the inbox, `IMPROVEMENTS.md`, and the ADRs (append-only: a
superseded decision stays in an ADR's body, with its amendment section saying what replaced it).

Exceptions inside the checked documents:
- ARCHITECTURE.md's "**Abandoned, so stale wherever it is stated as current:**" paragraph, which names them;
- a `cut` capability row (it is kept for the record);
- a backlog item row whose status is `done`, `removed` or `post-v0.4`; a row whose cells do not line up with its
  header, or whose status is not a known one, is checked (fails closed); text outside item tables is checked;
- the owner's words quoted verbatim (`owner: "..."`, `Owner: "..."`): only the quoted text;
- `ALLOW`, one entry per line, each with the term it allows, a fragment of that line and a reason. An entry that
  matches nothing is a problem, so the list cannot outlive the text it was written for.

The terms are chosen so that the current design's own words pass: a bare slot, lane, gate, admit, warm, window,
share, territory ("tax territory"), stall, heartbeat, timeout or Busy is never refused, and `RECOVERING` is a
question of meaning that the verifier, not a word list, judges. `AFTER_ENGINE_JOBS` lists the terms that the
engine-jobs slice retires (the per-request time limits and their bounds); it is inactive until that slice lands.

Exit 0 = nothing refused, 1 = refused or a problem with the check's own lists, 2 = the root cannot be read.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXED_FILES = (
    "design/ARCHITECTURE.md",
    "design/INVARIANTS.md",
    "design/ENGINEERING.md",
    "design/glossary.md",
    "design/capabilities.toml",
    "design/backlog.md",
)
SPECS = "design/specs"
ABANDONED = "**Abandoned, so stale wherever it is stated as current:**"
HISTORY_STATUSES = frozenset({"done", "removed", "post-v0.4"})
KNOWN_STATUSES = HISTORY_STATUSES | {"open", "in-progress", "parked"}
OWNER_QUOTE = re.compile(r"\bowner\b[^\"\n]{0,60}?\"[^\"\n]*\"", re.I)
UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


@dataclass(frozen=True)
class Term:
    name: str
    pattern: str
    case: bool = False  # a code is matched as written; prose in any case
    regex: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "regex", re.compile(self.pattern, 0 if self.case else re.I))


def code(name: str) -> Term:
    return Term(name, rf"\b{name}\b", case=True)


# Refused now (the convergence audit of 2026-10-27, plus "heavy-load gate" and the spec's old name).
ACTIVE: tuple[Term, ...] = (
    Term("semaphore", r"\bsemaphores?\b"),
    Term("limiter", r"(?<!rate )(?<!rate-)\b\w*limiters?\b"),
    Term("heavy-load gate", r"\bheavy[- ]load gate\b"),
    Term("engine pool", r"\bengine pools?\b"),
    Term("slot count", r"\bslot counts?\b"),
    Term("engine slots", r"\bengine slots\b"),
    Term("admission", r"\badmission\b"),
    Term("memory pressure", r"\bmemory[- ]pressure\b"),
    Term("pressure level", r"\bpressure levels?\b"),
    Term("Run anyway", r"\brun anyway\b"),
    Term("runaway", r"\brunaway\b"),
    Term("learned usual", r"\blearned\s+[\"“']?usual\b"),
    Term("engine or workspace lane", r"\b(?:engine|per-workspace|workspace)\s+lanes?\b"),
    Term("Share workspace or mode", r"\bshare\s+(?:workspaces?|mode)\b"),
    Term("coexistence guard", r"\bcoexistence guards?\b"),
    Term("legacy-lock wait", r"\blegacy[- ]lock waits?\b"),
    Term("territory", r"\bterritories\b|\bterritory\s+(?:rule|text|check)s?\b"),
    code("LEDGER_SHARED"),
    code("LEDGER_OUTSIDE_FOLDER"),
    code("FOLDER_IN_OTHER_WORKSPACE"),
    Term("journal index", r"\bjournal index\b"),
    Term("cross-workspace journal", r"\bcross-workspace journals?\b"),
    Term("several open workspaces", r"\bseveral open workspaces\b"),
    Term("several native windows", r"\bseveral native windows\b"),
    Term("multi-window", r"\bmulti[- ]window\b"),
    Term("warm container", r"\bwarm (?:idle )?containers?\b"),
    Term("kept warm", r"\bkept warm\b"),
    code("WRITE_IN_PROGRESS"),
    Term("phase C", r"\bphase C\b", case=True),
    # The spec "Engine jobs and saves" was the engine-scheduler spec until S0047 (owner: "Yes", 2026-10-27), and the
    # model's folder followed it to `formal/tla/engine-jobs/` (BL-0111), so the old folder name is retired too; the
    # spec's own component, "the scheduler thread", is not retired.
    Term("engine-scheduler.md", r"\bengine-scheduler\.md\b"),
    Term("engine scheduler", r"(?<![\w-])engine[- ]scheduler(?![\w-])(?!\.md)"),
    Term("scheduler spec or slice", r"\bscheduler(?:'s)?\s+(?:spec|slice|design)\b"),
)

# TODO(BL-0109): the engine-jobs slice removes the per-request time limits (spec §5.5); it moves these into ACTIVE
# in the same commit, with the documents brought in line.
AFTER_ENGINE_JOBS: tuple[Term, ...] = (
    Term("per-request time limit", r"\bper-request (?:time ?limits?|timeouts?)\b"),
    Term("handshake time limit", r"\bhandshake (?:time ?limits?|timeouts?)\b"),
    code("start_timeout"),
    code("close_timeout"),
    code("longest_call"),
    code("longest_start"),
    code("TimedOut"),
    Term("-I -m ledger_engine", r"-I -m ledger_engine\b"),
    Term("outcome unknown", r"\boutcome unknown\b"),
    Term("quit grace", r"\bquit grace\b"),
)


@dataclass(frozen=True)
class Allow:
    path: str
    term: str
    fragment: str
    reason: str


# One entry per allowed line: the line must contain `fragment`, and only `term` is allowed there.
ALLOW: tuple[Allow, ...] = (
    Allow("design/specs/engine-jobs.md", "admission", "There is no admission or memory check",
          "the spec states that the abandoned mechanism is absent (ADR-0064 decision 6)"),
    Allow("design/specs/engine-jobs.md", "semaphore", "ADR-0011's purpose for the semaphore",
          "the line's source note: the memory bound is what ADR-0011's retired semaphore was for"),
    Allow("design/specs/engine-jobs.md", "scheduler spec or slice", '("the scheduler spec draft 5 defines it")',
          "quotes ADR-0063 decision 2's own words, which predate the rename"),
    Allow("design/specs/README.md", "engine-scheduler.md", "**Renamed:** `engine-scheduler.md`",
          "the redirect from the spec's old name (owner, 2026-10-27)"),
    Allow("design/specs/README.md", "engine scheduler", "**Renamed:** `engine-scheduler.md`",
          "the redirect from the spec's old name (owner, 2026-10-27)"),
    Allow("design/specs/README.md", "scheduler spec or slice", "**Renamed:** `engine-scheduler.md`",
          "the redirect from the spec's old name (owner, 2026-10-27)"),
    Allow("design/specs/roadmap.md", "Share workspace or mode", "v0.4 has no Share mode (ADR-0062)",
          "the dogfood rule R5 tells the owner that Share mode does not exist"),
    Allow("design/ARCHITECTURE.md", "Share workspace or mode",
          "the design grew (parallel engines, Share workspaces, shared files between workspaces)",
          "§0's own history sentence, which introduces the Abandoned paragraph"),
)


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    term: str
    text: str


def line_hits(text: str, terms: tuple[Term, ...]) -> list[str]:
    """The names of the terms in one line, outside the owner's quoted words."""
    quoted = [(m.start(), m.end()) for m in OWNER_QUOTE.finditer(text)]
    found: list[str] = []
    for term in terms:
        for m in term.regex.finditer(text):
            if not any(a <= m.start() and m.end() <= b for a, b in quoted):
                found.append(term.name)
                break
    return found


def checked_lines(path: str, text: str) -> list[tuple[int, str]]:
    """The (line number, text) pairs of `text` that state the current design, by the kind of document."""
    lines = text.splitlines()
    if path == "design/capabilities.toml":
        return _capability_lines(lines)
    if path == "design/backlog.md":
        return _backlog_lines(lines)
    if path == "design/ARCHITECTURE.md":
        return [(n, t) for n, t in enumerate(lines, 1) if not t.startswith(ABANDONED)]
    return list(enumerate(lines, 1))


def _capability_lines(lines: list[str]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    block: list[tuple[int, str]] = []

    def flush() -> None:
        status = [t for _, t in block if re.match(r'^status\s*=\s*"cut"\s*$', t)]
        if not status:
            out.extend(block)

    for n, t in enumerate(lines, 1):
        if t.strip() == "[[capability]]":
            flush()
            block = []
        block.append((n, t))
    flush()
    return out


def _cells(line: str) -> list[str]:
    return [c.strip() for c in UNESCAPED_PIPE.split(line.strip().strip("|"))]


def _backlog_lines(lines: list[str]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    header: list[str] | None = None
    for n, t in enumerate(lines, 1):
        if not t.startswith("|"):
            header = None
            out.append((n, t))
            continue
        cells = _cells(t)
        if header is None:
            header = cells
            out.append((n, t))
            continue
        if "Status" in header and header[0] == "ID" and len(cells) == len(header):
            status = cells[header.index("Status")]
            if status in HISTORY_STATUSES:
                continue
        out.append((n, t))  # prose tables, live rows, and rows that do not line up or carry an unknown status
    return out


def scan_text(path: str, text: str, terms: tuple[Term, ...]) -> list[Hit]:
    return [Hit(path, n, name, t) for n, t in checked_lines(path, text) for name in line_hits(t, terms)]


def documents(root: Path) -> tuple[list[str], list[str]]:
    """The checked documents under `root` (relative paths) and the problems finding them."""
    problems = [f"{p}: missing (a current-state document)" for p in FIXED_FILES if not (root / p).is_file()]
    paths = [p for p in FIXED_FILES if (root / p).is_file()]
    specs = sorted(q.relative_to(root).as_posix() for q in (root / SPECS).rglob("*.md")) if (root / SPECS).is_dir() \
        else []
    if not specs:
        problems.append(f"{SPECS}: no specs found")
    return paths + specs, problems


def check(root: Path, terms: tuple[Term, ...] = ACTIVE, allow: tuple[Allow, ...] = ALLOW) -> list[str]:
    paths, problems = documents(root)
    known = {t.name for t in terms}
    for a in allow:
        if not a.reason.strip():
            problems.append(f"allowlist entry {a.path} {a.term!r}: no reason")
        if a.term not in known:
            problems.append(f"allowlist entry {a.path} {a.term!r}: unknown term")
        if not a.fragment.strip():
            problems.append(f"allowlist entry {a.path} {a.term!r}: an empty line fragment")
    used: set[Allow] = set()
    for path in paths:
        for h in scan_text(path, (root / path).read_text(encoding="utf-8"), terms):
            match = [a for a in allow if a.path == h.path and a.term == h.term and a.fragment.strip()
                     and a.fragment in h.text]
            if match:
                used.update(match)
                continue
            problems.append(f"{h.path}:{h.line}: retired term {h.term!r} (ARCHITECTURE.md §0): {h.text.strip()[:160]}")
    problems += [f"stale allowlist entry {a.path} {a.term!r} {a.fragment!r}: no line matches it"
                 for a in allow if a not in used and a.reason.strip() and a.term in known and a.fragment.strip()]
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else ROOT
    if not (root / "design").is_dir():
        print(f"retired terms: {root}/design is not a folder", file=sys.stderr)
        return 2
    problems = check(root)
    for p in problems:
        print(p)
    paths, _ = documents(root)
    print(f"retired terms: {len(paths)} documents, {len(ACTIVE)} terms, {len(problems)} refused")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
