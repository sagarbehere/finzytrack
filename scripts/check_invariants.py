#!/usr/bin/env python3
"""Invariant coverage check (design/INVARIANTS.md; design/process/PROCESS.md, "Invariants").

Rules:
  - headings `### INV-NN: <title>`, numbered 01..N without gaps or duplicates;
  - each invariant has **Rule**, **Why**, **Enforced by** and **Status** bullets;
  - Status is `enforced`, `pending (BL-nnnn, by Mx)` or `review-only`;
  - `enforced`: every backticked file path on the Enforced-by line exists and mentions the ID, and at least
    one of them is a test in a folder the pre-push hook runs, or a check script a git hook runs (S0031: the
    manual CI, ci.yml, does not count; it runs before releases only, ADR-0060);
    an enforcement file that still holds a fixme test does not count (S0029 verifier F4);
    no enforcement file switches a test off (S0031, the M0 gate red team M2): no skip, skipif, xfail,
    importorskip, unittest skip, skipIf, skipUnless, expectedFailure, SkipTest, skipTest or load_tests in
    Python (the one exception: a skip that cannot fire on macOS, where the hooks run, written exactly as
    `unittest.skipUnless(sys.platform == "darwin", ...)`), no skip, todo, skipIf or runIf in TypeScript, and no
    collect_ignore, pytest_ignore_collect or skip form in a conftest.py above a Python enforcement test;
    stated limits: string indirection (getattr), a conftest hook that filters or marks items, and the pytest
    configuration are out of this static scan;
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
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MILESTONES = ["M0", "M1a", "M1b", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11", "M12"]
HEAD = re.compile(r"^### (INV-(\d{2})): (.+)$")
BULLET = re.compile(r"^- \*\*(Rule|Why|Enforced by|Status):\*\*\s*(.*)$")
PENDING = re.compile(r"^pending \((BL-\d{4}), by (M\d+[ab]?)\)$")
PATH_TOKEN = re.compile(r"`([^`\s]+)`")
STATUSES = {"open", "in-progress", "done", "removed", "post-v0.4", "parked"}
DUE = re.compile(r"^(M\d+[ab]?)\b")
# The git hooks that run checks (S0031, M4): a check run only by the manual CI does not count as enforcing.
RUNNERS = [".githooks/pre-commit", ".githooks/pre-push"]
# Test folders, and the text in .githooks/pre-push that runs each (S0031): a test counts as enforcement only
# when its folder is run at every push.
PUSHED_TEST_ROOTS = {
    "backend/tests/": ".venv/bin/pytest",
    "frontend/tests/": "node_modules/.bin/vitest run",
    "scripts/tests/": "discover -s scripts/tests",
    "scripts/oracle/tests/": "discover -s scripts/oracle/tests",
}


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


def runs_at_push(path: str, root: Path) -> bool:
    """The test's folder is run by the pre-push hook."""
    hook = root / ".githooks/pre-push"
    text = hook.read_text(encoding="utf-8") if hook.is_file() else ""
    return any(path.startswith(folder) and marker in text for folder, marker in PUSHED_TEST_ROOTS.items())


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
# A test may not be switched off any other way where it matters (S0029 verifier F3): in the frontend trees no
# skip, todo, skipIf or runIf at all (none is used); in Python, in an `integrity/` folder, in a file with a fixme
# marker or in a file naming a P1 finding, no skip, skipif, importorskip, imperative xfail or xfail marker (a fixme is
# the only way to say "not yet"), and nowhere a soft `xfail(strict=False)`. Other Python skips stay BL-0040 (parked).
# Out of reach, like the INV-07 aliasing: getattr(pytest.mark, "fixme") and other string indirection.
FIXME_ROOTS = ["backend/tests", "frontend/tests", "frontend/e2e", "scripts/tests", "scripts/oracle/tests"]
FIXME_HELPERS = {"frontend/tests/fixme.ts", "frontend/e2e/fixme.ts"}
# review:27 §1(d): the P1 integrity findings.
P1_FINDINGS = ("I4", "I5", "I6", "L1-04", "L4-01", "L4-02", "L4-03", "L4-06", "L3-01", "L3-11", "L3-14", "L10-02",
               "L10.M-1", "L10.M-2", "L10-06", "L11.M-2", "L6-12", "I7", "I8", "L9-01", "L9.M-1", "L9.M-3", "L8-01",
               "L8-02", "L8.M-1")
P1_MENTION = re.compile(r"(?<![\w.-])(?:" + "|".join(re.escape(i) for i in P1_FINDINGS) + r")(?![\w-])")
PY_DISABLE = {"skip", "skipif", "xfail", "importorskip", "skipIf", "skipUnless", "expectedFailure", "SkipTest",
              "skipTest"}
# In an enforcement file (S0031): every way to switch a test off, also unittest's module hook that picks tests.
# S0031 verifier F1: also the attributes unittest and pytest read to skip (`__unittest_skip__`,
# `__unittest_expecting_failure__`, `__test__ = False`); an empty parametrize list is refused separately.
PY_UNITTEST_ATTRS = {"__unittest_skip__", "__unittest_skip_why__", "__unittest_expecting_failure__", "__test__"}
PY_DISABLE_STRICT = PY_DISABLE | {"load_tests"} | PY_UNITTEST_ATTRS
# In a conftest.py above an enforcement test: what removes tests from collection or skips them (xfail is left to
# the fixme hook of backend/tests/conftest.py; a strict xfail fails a passing test).
CONFTEST_DISABLE = (PY_DISABLE - {"xfail"}) | {"collect_ignore", "collect_ignore_glob", "pytest_ignore_collect"} \
    | PY_UNITTEST_ATTRS
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
# Switching a frontend test off: a member or indexed skip/todo/skipIf/runIf, or a runner marker taken by
# destructuring (`const { fails: later } = it`).
# `.only` is not matched here: the runners refuse it at run time, whatever its spelling (S0031 verifier R2-2): the
# pre-push hook runs vitest with --allowOnly=false, CI runs it with CI set, and Playwright has forbidOnly.
TS_DISABLE = re.compile(r"\.\s*(?:skip|todo|skipIf|runIf)\b|\[\s*['\"`](?:skip|todo|skipIf|runIf)['\"`]\s*\]"
                        r"|(?<![\w$.'\"`])(?:fails?|fixme|skip|todo|skipIf|runIf)\s*(?=[:,}])")


def _py_markers(rel: str, text: str) -> tuple[list[dict[str, str]], list[str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [], [f"{rel}: cannot parse ({exc.msg}, line {exc.lineno}); fixme markers fail closed"]
    calls = {id(n.func): n for n in ast.walk(tree) if isinstance(n, ast.Call)}
    # a bare decorator (`@expectedFailure`) or a raised name (`raise SkipTest`) switches a test off without a call
    bare = {id(d) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for d in n.decorator_list} | {id(n.exc) for n in ast.walk(tree) if isinstance(n, ast.Raise) and n.exc}
    markers: list[dict[str, str]] = []
    errors: list[str] = []
    protected = "/integrity/" in f"/{rel}" or bool(P1_MENTION.search(text)) or any(
        isinstance(n, ast.Attribute) and n.attr == "fixme" for n in ast.walk(tree))
    for node in ast.walk(tree):
        name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else ""
        if name not in PY_DISABLE or isinstance(node, ast.Name) and id(node) not in calls and id(node) not in bare:
            continue
        call = calls.get(id(node))
        soft = name == "xfail" and call is not None and any(
            k.arg == "strict" and not (isinstance(k.value, ast.Constant) and k.value.value is True) for k in call.keywords)
        if protected or soft:
            errors.append(f"{rel}:{node.lineno}: a test is not switched off here by `{ast.unparse(node)}`"
                          + (" (a soft xfail)" if soft else "; mark it fixme with its backlog row instead"))
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
    for off in TS_DISABLE.finditer(text):
        if not any(a <= off.start() < b for a, b in imports):
            line = text.count(chr(10), 0, off.start()) + 1
            errors.append(f"{rel}:{line}: a frontend test is not switched off ({off.group(0).strip()!r}); "
                          "mark it fixme with its backlog row instead")
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


def _darwin_only(call: ast.Call | None) -> bool:
    """`unittest.skipUnless(sys.platform == "darwin", <reason>)`: a skip that cannot fire where the hooks run."""
    if call is None or len(call.args) != 2 or call.keywords or not isinstance(call.func, ast.Attribute):
        return False
    cond, func = call.args[0], call.func
    return (func.attr == "skipUnless" and isinstance(func.value, ast.Name) and func.value.id == "unittest"
            and isinstance(cond, ast.Compare) and len(cond.ops) == 1 and isinstance(cond.ops[0], ast.Eq)
            and ast.unparse(cond.left) == "sys.platform" and isinstance(cond.comparators[0], ast.Constant)
            and cond.comparators[0].value == "darwin")


def _py_disabling(rel: str, text: str, names: set[str], allow_darwin: bool) -> list[str]:
    """Every Name, attribute, import or definition in `names` (S0031: an enforcement file, a conftest above one)."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"{rel}: cannot parse ({exc.msg}, line {exc.lineno}); it fails closed"]
    calls = {id(n.func): n for n in ast.walk(tree) if isinstance(n, ast.Call)}
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            found = [node.attr]
        elif isinstance(node, ast.Name):
            found = [node.id]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            found = [a.name.rsplit(".", 1)[-1] for a in node.names] + [a.asname for a in node.names if a.asname]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found = [node.name]
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found = [t.id for t in targets if isinstance(t, ast.Name)]
        else:
            continue
        for name in found:
            if name not in names:
                continue
            if allow_darwin and isinstance(node, ast.Attribute) and _darwin_only(calls.get(id(node))):
                continue
            out.append(f"{rel}:{getattr(node, 'lineno', '?')}: `{name}` switches a test off in an enforcement file")
    for call in calls.values():  # S0031 verifier F1: an empty parameter list makes pytest skip the test
        func = call.func
        if (func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")) != "parametrize":
            continue
        values = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "argvalues"), None)
        if not (isinstance(values, (ast.List, ast.Tuple)) and values.elts):
            out.append(f"{rel}:{call.lineno}: a parametrize without a non-empty literal list switches a test off in an "
                       "enforcement file (an empty list skips the test, and a name can be empty)")
    return out


def switched_off(rel: str, text: str, root: Path) -> list[str]:
    """S0031 (M2): the ways a test is switched off, in an enforcement file and the conftest.py files above it."""
    if not is_test(rel):  # S0031 verifier F8: the name rules are for test files; `skip = None` in a check is fine
        return []
    if rel.endswith(".py"):
        errors = _py_disabling(rel, text, PY_DISABLE_STRICT, allow_darwin=True)
        parent = PurePosixPath(rel).parent
        for folder in [parent, *parent.parents]:
            conf = root / folder / "conftest.py"
            if conf.is_file():
                errors += _py_disabling(conf.relative_to(root).as_posix(),
                                        conf.read_text(encoding="utf-8", errors="replace"), CONFTEST_DISABLE, False)
        return errors
    if rel.endswith(TS_SOURCES):
        imports = [(m.start(), m.end()) for m in TS_IMPORT.finditer(text)]
        return [f"{rel}:{text.count(chr(10), 0, m.start()) + 1}: {m.group(0).strip()!r} switches a test off in an "
                "enforcement file" for m in TS_DISABLE.finditer(text) if not any(a <= m.start() < b for a, b in imports)]
    return []


def _has_fixme(rel: str, text: str) -> bool:
    """Any fixme marker or fixme-shaped use in a file, well formed or not."""
    if rel.endswith(".py"):
        markers, errors = _py_markers(rel, text)
        return bool(markers) or any("fixme" in e for e in errors)
    if rel.endswith(TS_SOURCES):
        return bool(TS_MARKER_USE.search(text))
    return False


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
                content = fp.read_text(encoding="utf-8", errors="replace")
                if iid not in content:
                    errors.append(f"{iid}: enforcement file {p} does not mention {iid}")
                if _has_fixme(p, content):  # S0029 verifier F4: a pending test enforces nothing
                    errors.append(f"{iid}: enforcement file {p} holds fixme tests; it enforces once none is left")
                errors += [f"{iid}: {e}" for e in switched_off(p, content, root)]
                if (is_test(p) and runs_at_push(p, root)) or (not is_test(p) and runs_somewhere(p, root)):
                    ok_runner = True
            if paths and not ok_runner:
                errors.append(f"{iid}: no enforcement file is a test the pre-push hook runs or a check a git hook runs")
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
