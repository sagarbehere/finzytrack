#!/usr/bin/env python3
"""Retired-terms check (S0047): the current-state documents do not describe an abandoned design as current.

Usage: python3 scripts/check_retired_terms.py [ROOT]      (ROOT defaults to this repository)

ARCHITECTURE.md §0 states where the design converged on 2026-10-27 and lists what was abandoned (Share workspaces,
an engine pool and its admission, the heavy-load semaphore and the write limiter, territories, several open
workspaces, ...). The convergence audit of that day found those words left in documents that state the current
design; this check refuses them there, so the old designs cannot come back by copy and paste.

Since S0055 it also refuses the React target of ADR-0006, which ADR-0053 replaced by Vue 3 on 2026-10-25: the
documents described React in their body for four days under a "read the Vue equivalents" banner, until the owner
found it (2026-10-29). The Vue stack's own names (vue-sonner, `vue-tsc --noEmit`, shadcn-vue on Reka UI) pass, and so
do bare "hooks" (git hooks, Vue Query's own word) and the verb "react". Since S0056 a `hooks/` folder of the frontend
is refused (it is `composables/`, owner 2026-10-29); git's and Claude Code's hook paths, and prose such as
"hooks/guards", pass.

Checked (the current-state documents): `design/ARCHITECTURE.md`, `design/INVARIANTS.md`, `design/ENGINEERING.md`,
`design/glossary.md`, every Markdown file under `design/specs/`, `design/capabilities.toml` and `design/backlog.md`;
since S0068 also `design/process/escalation-rubric.md`, the journal, inbox and generated READMEs and the
engine-jobs model's `HOW_TO_RUN.md`; since S0068's review round 1 also every model's Markdown
(`formal/tla/<model>/*.md`; its `results/` are run output), the
generated statecharts, the ADR index (`design/decisions/README.md`, but its title cells) and the root, security,
corpora and typings READMEs (`design/capabilities.md` is generated from the checked `capabilities.toml`);
since S0055 also the files agents read first: `design/README.md`, `design/process/PROCESS.md`, the `CLAUDE.md` files
(root, `backend/`, `engine/`, `frontend/`), every `.claude/agents/*.md` and every `.claude/skills/*/SKILL.md`.
Not checked, because they are history: journals, the inbox, `IMPROVEMENTS.md`, and the ADRs (append-only: a
superseded decision stays in an ADR's body, with its amendment section saying what replaced it).

Exceptions inside the checked documents:
- ARCHITECTURE.md's "**Abandoned, so stale wherever it is stated as current:**" paragraph, which names them;
- the engine-jobs model's MAPPING.md §5 (the model's findings, each dated, with the fix of its time);
- a `cut` capability row (it is kept for the record);
- a backlog item row whose status is `done`, `removed` or `post-v0.4`; a row whose cells do not line up with its
  header, or whose status is not a known one, is checked (fails closed); text outside item tables is checked;
- the owner's words quoted verbatim, in the attribution forms this repository uses and no other: `owner: "..."`,
  `Owner A: "..."` (a labelled answer), `(owner, <date>: "...")`, `Owner, <date>, in session: "..."` and
  `the owner's approval, <date>, ADR-nnnn: "..."` (between "owner" and the colon only a label letter, "'s approval",
  dates, ADR IDs and "in session"); only the quoted text is exempt (S0047 verifier V2: a quote merely near the word
  "owner", such as an owner-facing dialog's text, is not the owner's);
- `ALLOW`, one entry per line, each with the term it allows, a fragment of that line and a reason. An entry that
  matches nothing is a problem, so the list cannot outlive the text it was written for.

The terms are chosen so that the current design's own words pass: a bare slot, lane, gate, admit, warm, window,
share, territory ("tax territory"), stall, heartbeat, timeout or Busy is never refused, and `RECOVERING` is a
question of meaning that the verifier, not a word list, judges. Each checked line is also read joined with the next
checked line, so a multi-word term split by a line break is still found (reported on its first line). Since S0057
the terms the engine-jobs slice retires (the per-request and handshake time limits and their bounds, the `-I`
engine command) are refused too; until then they waited in a second, inactive list.

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
    "design/README.md",
    "design/process/PROCESS.md",
    "CLAUDE.md",
    "backend/CLAUDE.md",
    "engine/CLAUDE.md",
    "frontend/CLAUDE.md",
    # S0068: the other live documents an agent reads for guidance (the owner's requirement of 2026-10-31 above)
    "design/process/escalation-rubric.md",
    "design/journal/README.md",
    "design/inbox/README.md",
    "design/generated/README.md",
    "formal/tla/engine-jobs/HOW_TO_RUN.md",
    # S0068 verifier r1 F2: the rest of the live Markdown outside the history (the model's documents by GLOBS)
    "design/decisions/README.md",
    "README.md",
    "SECURITY.md",
    "corpora/README.md",
    "engine/typings/README.md",
)
SPECS = "design/specs"
# each must match at least one file
GLOBS = (".claude/agents/*.md", ".claude/skills/*/SKILL.md", "formal/tla/*/*.md", "design/generated/statecharts/*.md")
ABANDONED = "**Abandoned, so stale wherever it is stated as current:**"
ADR_INDEX = "design/decisions/README.md"  # its rows' title cells are history (S0068 verifier r1 F2)
# a dated history section inside a live document: the model's findings, each with its date and the fix of its time
HISTORY_SECTIONS = {"formal/tla/engine-jobs/MAPPING.md": "## 5. "}
HISTORY_STATUSES = frozenset({"done", "removed", "post-v0.4"})
KNOWN_STATUSES = HISTORY_STATUSES | {"open", "in-progress", "parked"}
OWNER_QUOTE = re.compile(
    r"\b[Oo]wner(?:'s approval)?(?: [A-Z])?(?:,\s*(?:\d{4}-\d{2}-\d{2}|ADR-\d{4}|in session))*\s*:\s*\"[^\"\n]*\"")
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
    # A word, never a suffix ("delimiter" passes, S0047 verifier V4): "limiter", or a CamelCase name ending in it.
    Term("limiter", r"(?<!rate )(?<!rate-)(?<![\w-])(?:[Ll]imiters?|[A-Z]\w*Limiters?)\b", case=True),
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
    # S0055: the React target of ADR-0006, superseded by ADR-0053 (Vue 3). The capitalised name, and `react` joined to
    # a package or rule name (react-router, @xstate/react, lucide-react, react/no-danger); the verb "react" passes.
    Term("React", r"\bReact\b", case=True),
    Term("react package or rule", r"[@/-]react\b|\breact[-/@]", case=True),
    Term("JSX or TSX", r"\b(?:jsx|tsx)\b"),
    Term("Radix", r"\bRadix\b|\bradix-ui\b", case=True),
    Term("shadcn/ui", r"\bshadcn/ui\b"),
    Term("sonner", r"(?<![\w-])sonner\b"),  # vue-sonner passes
    Term("React hook API", r"\buse(?:State|Effect|LayoutEffect|InsertionEffect|Memo|Callback|Reducer|Context|Ref"
                           r"|SyncExternalStore|Transition|DeferredValue|ImperativeHandle|ActionState|Optimistic)\b",
         case=True),
    code("QueryClientProvider"),
    Term("custom hook", r"\bcustom hooks?\b"),
    Term("function or class component", r"\b(?:function|class) components?\b"),
    Term("tsc type gate", r"(?<![\w-])tsc --noEmit\b", case=True),  # vue-tsc --noEmit passes
    Term("S13 pending", r"\bpending S13\b|\b(?:if|until) (?:spike )?S13\b|\bS13 (?:keeps|fails)\b"),
    Term("Vue fallback", r"\bVue fallback\b"),
    # S0056: the features' folder of composables is `composables/` (owner, 2026-10-29). Refused: `src/hooks`
    # (`frontend/src/hooks/` too), `features/<x>/hooks`, a bare `hooks/` that names the folder alone (a tree or list
    # entry, "`hooks/`") and "`hooks` folder". A `hooks/` followed by a name passes, so "hooks/guards", "hooks/skills"
    # and `hooks/guards.py` (Claude Code's) pass, and so do `.githooks/`, `.claude/hooks/` and `core.hooksPath`
    # (S0056 verifier round 1, F4). Stated limit: a bare `hooks/useX.ts` with no `src/` or `features/` before it passes.
    Term("hooks/ folder", r"\bsrc/hooks\b|\bfeatures/[^\s/`'\"]+/hooks\b|(?<![\w./~-])hooks/(?![\w.*-])"
                          r"|`hooks`\s+(?:folders?|director(?:y|ies))\b"),
    # S0057 (BL-0150): the engine client has no time limits and no bounds derived from them (engine-jobs spec
    # §5.4, §5.5), and the engine is never started with `-I` (§5.1); these were `AFTER_ENGINE_JOBS` until then.
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
    # S0068 (ADR-0067, owner 2026-10-31: "Make sure no docs inadvertently reference the old design. We don't want AI
    # agents implementing future features to get confused and build in what we just dialled down"): R4 gates nothing
    # and no accounting error refuses a write. The R4 gate's codes, records, ops, texts and model properties, and the
    # intent table's "never acknowledgeable" refusals, are refused in the current-state documents.
    code("check_pending"),
    code("check_failed"),
    code("could_not_check"),
    Term("could not check (R4)", r"\bcould not check your last change\b"),
    Term("Resume saving", r"\bresume saving\b"),
    Term("R4 attempt count", r"\br4[-_]attempts?\b|\.r4-attempt"),
    code("r4_confirm"),
    code("r4_resume"),
    Term(".mismatch record", r"`\.mismatch`|\.mismatch\b(?! between)"),
    Term("R4 mismatch outcome", r"`mismatch`"),
    Term("R4 nondeterministic outcome", r"`nondeterministic`"),
    code("superseded_build"),
    Term("owed R4", r"\bowed R4\b|\bR4 (?:is )?owed\b|\bowes an R4\b"),
    Term("R4 first", r"\bR4 first\b|\bchecking your last change first\b"),
    Term("R4 gate", r"\bR4 gate\b|\bR4 gates the next save\b|\bgates only the next save\b"),
    Term("never acknowledgeable", r"\bnever acknowledgeable\b"),
    Term("May ask to acknowledge", r"\bmay ask to acknowledge\b"),
    # INV-01 reworded (owner, 2026-10-31, "Reword (Recommended)"): new errors are listed in a preview and written only
    # if the user saves anyway; "unless acknowledged" read as per-mutator acknowledgement lists.
    Term("unless acknowledged", r"\bunless (?:explicitly |the user )?acknowledged\b"),
    # the R4 pause's own words only; the unfinished save's `WRITES_PAUSED` text is current (S0068 verifier r1 F7)
    Term("pause on R4", r"\bfound a difference\. saving is paused\b|\bsaving is paused until\b|\bR4 pauses saving\b"),
    code("NoSaveWhileR4Owed"),
    code("R4GateLiftedOnlyByUser"),
    code("OwedR4BeforeNextSave"),
    code("NoFalseMismatch"),
)

# TODO(BL-0109): the engine-jobs slice removes the per-request time limits (spec §5.5); it moves these into ACTIVE
# in the same commit, with the documents brought in line.


@dataclass(frozen=True)
class Allow:
    path: str
    term: str
    fragment: str
    reason: str


# One entry per allowed line: the line must contain `fragment`, and only `term` is allowed there.
ALLOW: tuple[Allow, ...] = (
    Allow("engine/CLAUDE.md", "-I -m ledger_engine", "ledger_engine.mirror.core.schema > ../contracts/mirror-ddl.sql",
          "a generator run under -I, not the engine process (S0057: the engine itself never runs under -I)"),
    Allow("engine/CLAUDE.md", "-I -m ledger_engine", "ledger_engine.protocol.schema > ../contracts/engine-protocol",
          "a generator run under -I, not the engine process (S0057: the engine itself never runs under -I)"),
    Allow("design/specs/engine-jobs.md", "R4 gate", '"Owner A" is the owner\'s design of 2026-10-27 quoted in ADR-0064',
          "the owner's words of 2026-10-27, quoted verbatim, followed on the same line by their supersession (ADR-0067)"),
    Allow("design/INVARIANTS.md", "unless acknowledged", "*(Reworded with the owner's approval, 2026-10-31, ADR-0067",
          "INV-01's supersession note names the wording it replaced (owner, 2026-10-31)"),
    Allow("design/specs/engine-jobs.md", "admission", "There is no admission or memory check",
          "the spec states that the abandoned mechanism is absent (ADR-0064 decision 6)"),
    Allow("design/specs/engine-jobs.md", "semaphore", "ADR-0011's purpose for the semaphore",
          "the line's source note: the memory bound is what ADR-0011's retired semaphore was for"),
    Allow("design/specs/engine-jobs.md", "scheduler spec or slice", '("the scheduler spec draft 5 defines it")',
          "quotes ADR-0063 decision 2's own words, which predate the rename"),
    Allow("design/specs/engine-jobs.md", "scheduler spec or slice", '"Yes, add it to the scheduler spec as a requirement.',
          "the owner's words of 2026-10-26, in an attribution form (`The owner's requirement (<date>: ...`) the quote "
          "rule does not take"),
    Allow("formal/tla/engine-jobs/MAPPING.md", "engine-scheduler.md", "(d8ca7823, then named `engine-scheduler.md`)",
          "the model's source line names the spec's file name at the commit it was built from"),
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
    if path == ADR_INDEX:
        return [(n, _without_title(t)) for n, t in enumerate(lines, 1)]
    if path in HISTORY_SECTIONS:
        return _outside_section(lines, HISTORY_SECTIONS[path])
    return list(enumerate(lines, 1))


def _without_title(line: str) -> str:
    """An ADR index row with its title cell and link target blanked: both are the ADR's own, fixed when it was
    decided."""
    if not line.startswith("| [ADR-"):
        return line
    cells = UNESCAPED_PIPE.split(line)
    if len(cells) <= 3:
        return line
    link = re.sub(r"\]\([^)]*\)", "]", cells[1])  # the ADR's file name, fixed when it was written
    return "|".join([cells[0], link, " ", *cells[3:]])


def _outside_section(lines: list[str], heading: str) -> list[tuple[int, str]]:
    """The lines outside the `## ` section that starts with `heading` (a dated history inside a live document)."""
    out: list[tuple[int, str]] = []
    inside = False
    for n, t in enumerate(lines, 1):
        if t.startswith("## "):
            inside = t.startswith(heading)
        if not inside:
            out.append((n, t))
    return out


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


def _spanning(first: str, second: str, terms: tuple[Term, ...]) -> list[str]:
    """Terms that start on `first` and end on `second`, read as one line (S0047 verifier V6)."""
    joined, cut = f"{first} {second}", len(first)
    quoted = [(m.start(), m.end()) for m in OWNER_QUOTE.finditer(joined)]
    found: list[str] = []
    for term in terms:
        for m in term.regex.finditer(joined):
            if m.start() < cut < m.end() and not any(a <= m.start() and m.end() <= b for a, b in quoted):
                found.append(term.name)
                break
    return found


def scan_text(path: str, text: str, terms: tuple[Term, ...]) -> list[Hit]:
    lines = checked_lines(path, text)
    out = [Hit(path, n, name, t) for n, t in lines for name in line_hits(t, terms)]
    for (n, t), (m, u) in zip(lines, lines[1:]):
        if m == n + 1:
            out += [Hit(path, n, name, f"{t} {u}") for name in _spanning(t, u, terms)]
    return sorted(out, key=lambda h: h.line)


def documents(root: Path) -> tuple[list[str], list[str]]:
    """The checked documents under `root` (relative paths) and the problems finding them."""
    problems = [f"{p}: missing (a current-state document)" for p in FIXED_FILES if not (root / p).is_file()]
    paths = [p for p in FIXED_FILES if (root / p).is_file()]
    specs = sorted(q.relative_to(root).as_posix() for q in (root / SPECS).rglob("*.md")) if (root / SPECS).is_dir() \
        else []
    if not specs:
        problems.append(f"{SPECS}: no specs found")
    globbed: list[str] = []
    for pattern in GLOBS:
        found = sorted(q.relative_to(root).as_posix() for q in root.glob(pattern) if q.is_file())
        if not found:
            problems.append(f"{pattern}: no files found")
        globbed += found
    return list(dict.fromkeys(paths + specs + globbed)), problems  # a fixed file a glob also finds, once


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
