#!/usr/bin/env python3
"""T1 capability-map check (design/process/PROCESS.md, "Project memory").

Checks design/capabilities.toml:
  - every row has the required fields with allowed values; IDs are unique and look like CAP-<AREA>-<NAME>;
  - planned/partial/built rows name a milestone M0-M12; cut and deferred rows block nothing and cite the ADR
    that decided them (BL-0039);
  - every path in a row's `code` and `tests` lists exists: a file, or a folder written with a trailing `/`; relative,
    inside the repository (BL-0184);
  - every `built` row has at least one real test tagged with its ID (BL-0077): `pytest.mark.cap("<ID>")` as a
    decorator of a test function, a test method or a test class (named `Test...` or a `TestCase`), or in a module's
    `pytestmark` beside at least one test; or a `describe`, `it`, `test` or `test.describe` call whose literal title
    holds `[<ID>]`, outside comments. The file is a tracked test file (`test_*.py`, `*_test.py`, `*.test.*`,
    `*.spec.*`) in a `tests/` or `e2e/` folder under backend/, frontend/, shell/ or scripts/tests/, and holds no fixme
    marker and no switched-off test (the enforcement-file rules of check_invariants.py). Stated limits: a TypeScript
    regex literal holding a quote or a comment opener is not told apart from a string or comment; a mark reached
    through a name (`cap = pytest.mark.cap`) does not count (fails closed);
  - every ADR it cites exists in design/decisions/; every backlog ID it cites exists in design/backlog.md
    (reported, not failed, until the backlog is seeded);
  - design/capabilities.md is up to date (via scripts/check_generated.py).
Exit 0 = ok, 1 = problems.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_invariants  # noqa: E402  (the enforcement-file rules: fixme markers and switched-off tests)

REQUIRED = ["id", "area", "capability", "v04", "status", "release_blocking"]
V04 = {"keep", "change", "cut", "new", "deferred"}
STATUS = {"planned", "partial", "built", "cut", "deferred"}
BLOCKING = {"yes", "no", "n/a"}
MILESTONE = re.compile(r"^M(1[0-2]|[0-9])$")
CAP_ID = re.compile(r"^CAP-[A-Z0-9]+(-[A-Z0-9]+)+$")
TEST_DIRS = ("backend", "frontend", "shell", "scripts/tests")


def candidate_files(root: Path) -> list[str]:
    """The tracked files under TEST_DIRS that name a capability ID: the old rule's candidates (S0105)."""
    dirs = [d for d in TEST_DIRS if (root / d).exists()]
    if not dirs:
        return []
    r = subprocess.run(["git", "grep", "-l", "-F", "CAP-", "--", *dirs], cwd=root, capture_output=True, text=True)
    return [line for line in r.stdout.splitlines() if "test" in line]


PY_TEST_FILE = re.compile(r"^(?:test_.*|.*_test)\.py$")
TS_TEST_FILE = re.compile(r"\.(?:test|spec)\.(?:ts|mts|cts|tsx|js|mjs)$")
# describe/it/test or test.describe, then a quoted literal title (no `${` in a template)
TS_TITLE = re.compile(r"(?<![\w$.])(?:describe|it|test|test\.describe)\s*\(\s*"
                      r"('(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\$]|\\.)*`)")


def _test_file(rel: str) -> bool:
    path = PurePosixPath(rel)
    in_tests = any(part in ("tests", "e2e") for part in path.parts[:-1])
    under = rel.startswith(tuple(f"{d}/" for d in TEST_DIRS))
    return in_tests and under and (PY_TEST_FILE.match(path.name) is not None or TS_TEST_FILE.search(path.name) is not None)


def _cap_mark(node: ast.AST) -> str | None:
    """The ID of an exact `pytest.mark.cap("<ID>")` call, else None."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "cap"):
        return None
    mark = node.func.value
    if not (isinstance(mark, ast.Attribute) and mark.attr == "mark" and isinstance(mark.value, ast.Name)
            and mark.value.id == "pytest"):
        return None
    if node.keywords or len(node.args) != 1 or not isinstance(node.args[0], ast.Constant) \
            or not isinstance(node.args[0].value, str):
        return None
    return node.args[0].value


def _is_test_class(node: ast.ClassDef) -> bool:
    bases = [b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "") for b in node.bases]
    return node.name.startswith("Test") or any(b.endswith("TestCase") for b in bases)


def _test_methods(node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [f for f in node.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name.startswith("test")]


def _py_tags(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()  # fails closed: a file that does not parse holds no test
    found: set[str] = set()
    has_test = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            has_test = True
            found |= {cid for d in node.decorator_list if (cid := _cap_mark(d))}
        elif isinstance(node, ast.ClassDef) and _is_test_class(node) and _test_methods(node):
            has_test = True
            found |= {cid for d in node.decorator_list if (cid := _cap_mark(d))}
            found |= {cid for f in _test_methods(node) for d in f.decorator_list if (cid := _cap_mark(d))}
    if has_test:
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets) and node.value is not None:
                values = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
                found |= {cid for v in values if (cid := _cap_mark(v))}
    return found


def _ts_code(text: str) -> tuple[str, list[tuple[int, int]]]:
    """`text` with its comments blanked (newlines kept), and the spans of its strings and template literals."""
    out: list[str] = []
    strings: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c and not (c != "`" and text[j] == "\n"):
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            strings.append((i, j + 1))
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out), strings


def _ts_tags(text: str) -> set[str]:
    code, strings = _ts_code(text)
    titles = [m.group(1) for m in TS_TITLE.finditer(code) if not any(a <= m.start() < b for a, b in strings)]
    return {m.group(0) for title in titles for m in re.finditer(r"CAP-[A-Z0-9-]+", title) if f"[{m.group(0)}]" in title}


def real_tags(root: Path, files: list[str]) -> dict[str, list[str]]:
    """capability ID -> the files among `files` that hold a real test tagged with it (BL-0077)."""
    tags: dict[str, list[str]] = {}
    for rel in files:
        path = root / rel
        if not _test_file(rel) or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if check_invariants._has_fixme(rel, text) \
                or check_invariants.switched_off(rel, text, root, parametrize=False):  # noqa: SLF001
            continue
        found = _py_tags(text) if rel.endswith(".py") else _ts_tags(text)
        for cid in sorted(found):
            tags.setdefault(cid, []).append(rel)
    return tags


def path_errors(c: dict, root: Path) -> list[str]:
    """BL-0184: each path of the row's `code` and `tests` lists exists, inside the repository."""
    where = c.get("id", "a row")
    errors: list[str] = []
    for field in ("code", "tests"):
        value = c.get(field, [])
        if not isinstance(value, list):
            errors.append(f"{where}: {field} must be a list of paths")
            continue
        for p in value:
            if not isinstance(p, str) or not p.strip():
                errors.append(f"{where}: {field} holds {p!r}, not a path")
                continue
            pure = PurePosixPath(p)
            if pure.is_absolute() or ".." in pure.parts or p.startswith("~"):
                errors.append(f"{where}: {field} path {p} must be relative and inside the repository")
            elif p.endswith("/") and not (root / p).is_dir():
                errors.append(f"{where}: {field} folder {p} does not exist")
            elif not p.endswith("/") and not (root / p).is_file():
                errors.append(f"{where}: {field} file {p} does not exist" + (
                    " (a folder is written with a trailing /)" if (root / p).is_dir() else ""))
    return errors


def check(caps: list[dict], adr_ids: set[str], backlog_text: str | None, root: Path = ROOT,
          tags: dict[str, list[str]] | None = None) -> tuple[list[str], list[str]]:
    if tags is None:
        tags = real_tags(root, candidate_files(root))
    errors: list[str] = []
    notes: list[str] = []
    seen: set[str] = set()
    for i, c in enumerate(caps):
        where = c.get("id", f"row {i + 1}")
        for f in REQUIRED:
            if not c.get(f):
                errors.append(f"{where}: missing field '{f}'")
        cid = c.get("id", "")
        if cid in seen:
            errors.append(f"{cid}: duplicate id")
        seen.add(cid)
        if cid and not CAP_ID.match(cid):
            errors.append(f"{cid}: id must look like CAP-<AREA>-<NAME>")
        if c.get("v04") not in V04:
            errors.append(f"{where}: v04 must be one of {sorted(V04)}")
        status = c.get("status")
        if status not in STATUS:
            errors.append(f"{where}: status must be one of {sorted(STATUS)}")
        if c.get("release_blocking") not in BLOCKING:
            errors.append(f"{where}: release_blocking must be one of {sorted(BLOCKING)}")
        if status in {"planned", "partial", "built"} and not MILESTONE.match(c.get("milestone", "")):
            errors.append(f"{where}: a {status} row needs a milestone M0-M12")
        if status in {"cut", "deferred"} and c.get("release_blocking") == "yes":
            errors.append(f"{where}: a {status} row cannot block the release")
        if status in {"cut", "deferred"} and not c.get("adrs"):
            errors.append(f"{where}: a {status} row must cite the ADR that decided it (adrs)")
        if status == "built" and not tags.get(cid):
            errors.append(f"{cid}: status built but no real test is tagged with {cid} (a test, not a comment, "
                          "helper, fixme or skipped test)")
        errors += path_errors(c, root)
        for adr in c.get("adrs", []):
            if adr not in adr_ids:
                errors.append(f"{where}: cites {adr}, which does not exist in design/decisions/")
        if backlog_text is not None:
            for b in c.get("backlog", []):
                if not re.search(rf"(?<![\w.-]){re.escape(b)}(?![\w-])", backlog_text):
                    notes.append(f"{where}: backlog ID {b} not found in design/backlog.md")
    return errors, notes


def main() -> int:
    with (ROOT / "design/capabilities.toml").open("rb") as fh:
        caps = tomllib.load(fh)["capability"]
    adr_ids = {m.group(0) for p in (ROOT / "design/decisions").glob("ADR-*.md") if (m := re.match(r"ADR-\d{4}", p.name))}
    backlog = ROOT / "design/backlog.md"
    errors, notes = check(caps, adr_ids, backlog.read_text(encoding="utf-8") if backlog.exists() else None)
    for n in notes:
        print(f"check-capabilities: note: {n}", file=sys.stderr)
    for e in errors:
        print(f"check-capabilities: {e}", file=sys.stderr)
    gen = subprocess.run([sys.executable, str(ROOT / "scripts/check_generated.py"), "--only", "design/capabilities.md"])
    return 1 if errors or gen.returncode else 0


if __name__ == "__main__":
    sys.exit(main())
