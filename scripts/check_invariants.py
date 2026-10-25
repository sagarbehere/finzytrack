#!/usr/bin/env python3
"""Invariant coverage check (design/INVARIANTS.md; design/process/PROCESS.md, "Invariants").

Rules:
  - headings `### INV-NN: <title>`, numbered 01..N without gaps or duplicates;
  - each invariant has **Rule**, **Why**, **Enforced by** and **Status** bullets;
  - Status is `enforced`, `pending (BL-nnnn, by Mx)` or `review-only`;
  - `enforced`: every backticked file path on the Enforced-by line exists and mentions the ID, and at least
    one of them is a test, or a check script that a hook or CI workflow runs;
  - `pending`: the backlog item exists in design/backlog.md and is not marked done; with
    FT_MILESTONE_EXIT=<Mx> set (a milestone-exit run), a pending invariant due by Mx or earlier fails;
  - backlog items (every row of a design/backlog.md table whose header starts with `ID` and has `Milestone` and
    `Status` columns): the status is one of STATUSES, and an open or in-progress item names a milestone; in a
    milestone-exit run, an open or in-progress item due by Mx or earlier fails (a bare `M1` counts as due by
    `M1a` until it is split, ADR-0046). Review findings are closed by slices, not by the passage of time (S0023);
  - fixme markers in the test trees (S0029): each names an open or in-progress backlog row with the same
    milestone (so the row keeps a milestone-exit run red), and a milestone-exit run fails on a fixme due by it.
Exit 0 = ok, 1 = problems.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MILESTONES = ["M0", "M1a", "M1b", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11", "M12"]
HEAD = re.compile(r"^### (INV-(\d{2})): (.+)$")
BULLET = re.compile(r"^- \*\*(Rule|Why|Enforced by|Status):\*\*\s*(.*)$")
PENDING = re.compile(r"^pending \((BL-\d{4}), by (M\d+[ab]?)\)$")
PATH_TOKEN = re.compile(r"`([^`\s]+)`")
STATUSES = {"open", "in-progress", "done", "removed", "post-v0.4", "parked"}
DUE = re.compile(r"^(M\d+[ab]?)\b")
RUNNERS = [".githooks/pre-commit", ".githooks/pre-push", ".github/workflows/ci.yml"]


def parse(text: str) -> list[dict]:
    invs: list[dict] = []
    for line in text.splitlines():
        if m := HEAD.match(line):
            invs.append({"id": m.group(1), "num": int(m.group(2)), "title": m.group(3), "fields": {}})
        elif invs and (b := BULLET.match(line)):
            invs[-1]["fields"][b.group(1)] = b.group(2).strip()
    return invs


def looks_like_path(tok: str) -> bool:
    return "/" in tok and not tok.startswith(("http", "~")) and "." in tok.rsplit("/", 1)[-1]


def is_test(path: str) -> bool:
    name = Path(path).name
    return "/tests/" in f"/{path}" and (name.startswith("test_") or ".test." in name or ".spec." in name)


def runs_somewhere(path: str, root: Path) -> bool:
    for r in RUNNERS:
        p = root / r
        if p.exists() and path in p.read_text(encoding="utf-8"):
            return True
    return False


def backlog_state(backlog: str, bl_id: str) -> str | None:
    """The table row that defines the backlog item (its first cell is the ID), or None if absent."""
    for line in backlog.splitlines():
        if re.match(rf"^\|\s*{re.escape(bl_id)}\s*\|", line):
            return line
    return None


# Tables with a Milestone or Status column that are not backlog items (the status legend, the doubles lookup).
REFERENCE_TABLES = {("Status", "Meaning"), ("Double", "Canonical", "Milestone")}


def table_rows(backlog: str, problems: list[str] | None = None) -> list[dict[str, str]]:
    """Rows of every backlog table whose header starts with `ID` and has `Milestone` and `Status` columns.

    Fails closed on shape (S0023 verifier F1): any other table that mentions a milestone or a status (a renamed,
    indented or quoted header) is reported in `problems`, so its rows cannot silently drop out of an exit run.
    """
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in backlog.splitlines():
        if not line.lstrip(" >").startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().lstrip(">").strip().strip("|").split("|")]
        if header is None:
            header = cells
            item_table = line.startswith("|") and header[0] == "ID" and {"Milestone", "Status"} <= set(header)
            mentions = any(re.search(r"milestone|status", c, re.I) for c in header)
            if mentions and not item_table and tuple(header) not in REFERENCE_TABLES and problems is not None:
                problems.append(f"backlog table {' | '.join(header)!r}: an item table starts at column 0 with the "
                                "header cells `ID`, `Milestone` and `Status` exactly")
        elif not set(line) <= set("|-: >") and line.startswith("|") and header[0] == "ID" \
                and {"Milestone", "Status"} <= set(header):
            rows.append(dict(zip(header, cells)))
    return rows


def due_milestone(cell: str) -> str | None:
    m = DUE.match(cell)
    if not m:
        return None
    return "M1a" if m.group(1) == "M1" else m.group(1)


def check_backlog(backlog: str, milestone_exit: str | None) -> list[str]:
    errors: list[str] = []
    for row in table_rows(backlog, errors):
        bid, status, cell = row.get("ID", ""), row.get("Status", ""), row.get("Milestone", "")
        if status not in STATUSES:
            errors.append(f"backlog {bid}: status {status!r} is not one of {sorted(STATUSES)}")
            continue
        if status not in ("open", "in-progress"):
            continue
        due = due_milestone(cell)
        if due not in MILESTONES:
            errors.append(f"backlog {bid}: {status} item needs a milestone (one of {', '.join(MILESTONES)}), not {cell!r}")
        elif milestone_exit in MILESTONES and MILESTONES.index(due) <= MILESTONES.index(milestone_exit):
            errors.append(f"backlog {bid}: still {status} at the exit of {milestone_exit} (due by {due})")
    return errors


# Fixme markers (S0029; review:27 §1(d)). A test written before its capability exists carries
#   Python:      @pytest.mark.fixme(id="L4-01", due="M6", reason="capability not built")
#   vitest:      fixme({ id: 'L4-01', due: 'M6', reason: 'capability not built' }, title, body)   (wraps it.fails)
#   Playwright:  test.fixme(title, fixme({ id: 'BL-0068', due: 'M6', reason: '…' }), body)  (details from the helper,
#                so the report names the spec file, not the helper)
# (the helpers are FIXME_HELPERS; nothing else may call it.fails, test.fail or test.fixme). The ID names a backlog row that
# is open or in progress with the same milestone; that row keeps the exit run red until the test stops being fixme.
# Out of reach, like the INV-07 aliasing: getattr(pytest.mark, "fixme") and other string indirection.
FIXME_ROOTS = ["backend/tests", "frontend/tests", "frontend/e2e"]
# The two helpers, and the test that pins what they do (it names the runner markers in order to check them).
FIXME_HELPERS = {"frontend/tests/fixme.ts", "frontend/e2e/fixme.ts", "frontend/tests/unit/fixmeHelpers.test.ts"}
FIXME_KEYS = ("id", "due", "reason")
TS_SOURCES = (".ts", ".mts", ".cts", ".tsx", ".js", ".mjs", ".vue")
# Every use of a runner's known-failure marker, or of the helper's name, in a TypeScript test file.
TS_MARKER_USE = re.compile(r"(?<![\w$])fixme\b|\.\s*(?:fixme|fails?)\b|\[\s*['\"`](?:fixme|fails?)['\"`]\s*\]")
TS_HELPER_CALL = re.compile(r"(?<![\w$.])fixme\s*\(\s*\{\s*id:\s*'([^'\n]*)',\s*due:\s*'([^'\n]*)',"
                            r"\s*reason:\s*'([^'\n]*)',?\s*\}\s*[,)]")
# Playwright's test.fixme is allowed only as test.fixme(<string title>, fixme({...}), ...): the helper call is then
# validated on its own.
TS_TEST_FIXME = re.compile(r"\.\s*fixme\(\s*(?:'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`[^`]*`)\s*,\s*(?=fixme\s*\()")
TS_IMPORT = re.compile(r"^\s*import\s+\{[^}]*\}\s+from\s+'[^'\n]*'", re.M)


def _py_markers(rel: str, text: str) -> tuple[list[dict[str, str]], list[str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [], [f"{rel}: cannot parse ({exc.msg}, line {exc.lineno}); fixme markers fail closed"]
    calls = {id(n.func): n for n in ast.walk(tree) if isinstance(n, ast.Call)}
    markers: list[dict[str, str]] = []
    errors: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "fixme"):
            continue
        call = calls.get(id(node))
        where = f"{rel}:{node.lineno}"
        kw = {k.arg: k.value for k in call.keywords} if call else {}
        if call is None or call.args or set(kw) != set(FIXME_KEYS) or not all(
                isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value for v in kw.values()):
            errors.append(f"{where}: a fixme marker is a call with exactly id=, due= and reason= as non-empty "
                          f"string literals: {ast.unparse(call or node)}")
            continue
        markers.append({"where": where, **{k: v.value for k, v in kw.items()}})
    return markers, errors


def _ts_markers(rel: str, text: str) -> tuple[list[dict[str, str]], list[str]]:
    imports = [(m.start(), m.end()) for m in TS_IMPORT.finditer(text)]
    markers: list[dict[str, str]] = []
    errors: list[str] = []
    for use in TS_MARKER_USE.finditer(text):
        where = f"{rel}:{text.count(chr(10), 0, use.start()) + 1}"
        if any(a <= use.start() < b for a, b in imports):
            continue
        if text[max(0, use.start() - 4):use.start()] == "test" and TS_TEST_FIXME.match(text, use.start()):
            continue  # the details argument is the helper call, checked when the scan reaches it
        call = TS_HELPER_CALL.match(text, use.start())
        if call is None or not all(call.groups()):
            snippet = text[use.start():use.start() + 60].split("\n")[0]
            errors.append(f"{where}: only the fixme helper, called as fixme({{ id: '…', due: '…', reason: '…' }}, "
                          f"title, body) or test.fixme(title, fixme({{ … }}), body), marks a test known not to work "
                          f"yet: {snippet!r}")
            continue
        markers.append({"where": where, "id": call.group(1), "due": call.group(2), "reason": call.group(3)})
    return markers, errors


def fixme_markers(root: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Every fixme marker under FIXME_ROOTS, and the uses that are not a well-formed marker."""
    markers: list[dict[str, str]] = []
    errors: list[str] = []
    for base in FIXME_ROOTS:
        for path in sorted((root / base).rglob("*")) if (root / base).is_dir() else []:
            rel = path.relative_to(root).as_posix()
            if not path.is_file() or "node_modules" in path.parts or rel in FIXME_HELPERS:
                continue
            if path.suffix == ".py":
                found = _py_markers(rel, path.read_text(encoding="utf-8", errors="replace"))
            elif path.suffix in TS_SOURCES:
                found = _ts_markers(rel, path.read_text(encoding="utf-8", errors="replace"))
            else:
                continue
            markers += found[0]
            errors += found[1]
    return markers, errors


def check_fixme(root: Path, backlog: str, milestone_exit: str | None) -> list[str]:
    markers, errors = fixme_markers(root)
    rows = {r.get("ID", ""): r for r in table_rows(backlog)}
    for m in markers:
        where, bid, due = m["where"], m["id"], m["due"]
        row = rows.get(bid)
        if row is None:
            errors.append(f"{where}: fixme names {bid!r}, which is not a backlog item")
            continue
        status, row_due = row.get("Status", ""), due_milestone(row.get("Milestone", ""))
        if status not in ("open", "in-progress"):
            errors.append(f"{where}: fixme names {bid}, which is {status!r}: a closed item cannot keep a test fixme")
        elif due != row_due:
            errors.append(f"{where}: fixme {bid} says due {due!r}, the backlog row says {row_due}")
        if milestone_exit in MILESTONES and due in MILESTONES \
                and MILESTONES.index(due) <= MILESTONES.index(milestone_exit):
            errors.append(f"{where}: fixme {bid} is still fixme at the exit of {milestone_exit} (due by {due})")
    return errors


def check(text: str, root: Path, backlog: str | None, milestone_exit: str | None) -> list[str]:
    errors: list[str] = []
    invs = parse(text)
    if not invs:
        return ["no invariants found"]
    nums = [i["num"] for i in invs]
    if nums != list(range(1, len(invs) + 1)):
        errors.append(f"invariant numbers must run 01..{len(invs):02d} without gaps or duplicates: {nums}")
    for inv in invs:
        iid, f = inv["id"], inv["fields"]
        for field in ("Rule", "Why", "Enforced by", "Status"):
            if not f.get(field):
                errors.append(f"{iid}: missing **{field}:**")
        status = f.get("Status", "")
        if status == "enforced":
            paths = [t for t in PATH_TOKEN.findall(f.get("Enforced by", "")) if looks_like_path(t)]
            if not paths:
                errors.append(f"{iid}: enforced, but Enforced by names no file")
            ok_runner = False
            for p in paths:
                fp = root / p
                if not fp.is_file():
                    errors.append(f"{iid}: enforcement file {p} does not exist")
                    continue
                if iid not in fp.read_text(encoding="utf-8", errors="replace"):
                    errors.append(f"{iid}: enforcement file {p} does not mention {iid}")
                if is_test(p) or runs_somewhere(p, root):
                    ok_runner = True
            if paths and not ok_runner:
                errors.append(f"{iid}: no enforcement file is a test or a check run by a hook or CI")
        elif status == "review-only":
            pass
        elif m := PENDING.match(status):
            bl_id, due = m.groups()
            if due not in MILESTONES:
                errors.append(f"{iid}: unknown milestone {due} (use one of {', '.join(MILESTONES)})")
            if backlog is None:
                errors.append(f"{iid}: pending on {bl_id}, but design/backlog.md does not exist")
            else:
                line = backlog_state(backlog, bl_id)
                if line is None:
                    errors.append(f"{iid}: pending on {bl_id}, which is not in design/backlog.md")
                elif re.search(r"\b(done|closed|removed)\b", line, re.I):
                    errors.append(f"{iid}: {bl_id} is closed but the invariant is still pending")
            if milestone_exit and due in MILESTONES and milestone_exit in MILESTONES \
                    and MILESTONES.index(due) <= MILESTONES.index(milestone_exit):
                errors.append(f"{iid}: still pending at the exit of {milestone_exit} (due by {due})")
        else:
            errors.append(f"{iid}: Status must be 'enforced', 'pending (BL-nnnn, by Mx)' or 'review-only', not {status!r}")
    return errors


def main(root: Path = ROOT) -> int:
    text = (root / "design/INVARIANTS.md").read_text(encoding="utf-8")
    bl = root / "design/backlog.md"
    backlog = bl.read_text(encoding="utf-8") if bl.exists() else None
    milestone_exit = os.environ.get("FT_MILESTONE_EXIT")
    if milestone_exit and milestone_exit not in MILESTONES:
        print(f"check-invariants: FT_MILESTONE_EXIT={milestone_exit!r} is not one of {', '.join(MILESTONES)}", file=sys.stderr)
        return 1
    errors = check(text, root, backlog, milestone_exit)
    if backlog:
        errors += check_backlog(backlog, milestone_exit) + check_fixme(root, backlog, milestone_exit)
    for e in errors:
        print(f"check-invariants: {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
