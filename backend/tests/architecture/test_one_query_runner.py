"""ADR-0016: one hardened `QueryRunner` for every user query, and no second query path (L1-10; S0103).

@regression-guard (S0103: `next` already meets the rule since S0052; this pins it). The import contract
`one-sqlite-home` keeps `sqlite3` inside `app.mirror`. This file holds what an import contract cannot see:

- every SQL statement executed anywhere in `app` (`execute`, `executemany`, `executescript`, on any object) is a
  text constant of ours (a literal, or a module-level name bound to one, `LiteralString` by convention), except the
  reviewed sites in `SQL_SITES`; the one site that runs SQL a user wrote is `QueryRunner.run_user_sql`;
- every SQLite connection is opened at a reviewed site (`CONNECT_SITES`): the checked read-only open of the mirror
  (`files.open_mirror`, used by the reader and the runner) and the runner's empty in-memory resolver.

Default-deny: an unreviewed site fails, and so does a reviewed site that is gone. The runner's own guards (the
authorizer, the limits, the time limit, the row and result caps) are tested in `tests/mirror/test_query.py`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

import pytest

from tests.architecture.loop_calls import SRC, module_name

EXECUTE = frozenset({"execute", "executemany", "executescript"})

SQL_SITES: dict[tuple[str, str], str] = {
    (
        "app.mirror.query",
        "QueryRunner.run_user_sql",
    ): "THE user query: `sql: UserSql`, run on a confined connection (ADR-0016)",
    ("app.mirror.query", "_Resolver._ask"): (
        "a quoted name prepared on an empty in-memory connection, to ask SQLite whether it resolves (never the mirror)"
    ),
    ("app.mirror.query", "confine"): "`PRAGMA hard_heap_limit` with an int of the configured limits",
}
"""(module, function) -> why its SQL that is not a constant of ours is safe."""

CONNECT_SITES: dict[tuple[str, str], str] = {
    ("app.mirror.files", "open_mirror"): "the checked read-only open of a pinned generation, `query_only` set",
    ("app.mirror.query", "_Resolver._ask"): "an empty in-memory database: no file",
}


@dataclass(frozen=True, slots=True)
class Found:
    module: str
    function: str
    kind: str  # "sql" or "connect"
    line: int


def _constants(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value, targets = node.value, list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            value, targets = node.value, [node.target]
        else:
            value, targets = None, []
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            out.update(t.id for t in targets if isinstance(t, ast.Name))
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            out.update(a.asname or a.name for a in node.names if a.name.isupper())
    return out


def _constant_sql(arg: ast.expr, constants: set[str]) -> bool:
    return (isinstance(arg, ast.Constant) and isinstance(arg.value, str)) or (
        isinstance(arg, ast.Name) and arg.id in constants
    )


def _connect_names(tree: ast.Module) -> set[str]:
    """Local spellings of `sqlite3.connect`."""
    out = {"sqlite3.connect"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            out.update(a.asname or a.name for a in node.names if a.name == "connect")
        if isinstance(node, ast.Import):
            out.update(f"{a.asname}.connect" for a in node.names if a.name == "sqlite3" and a.asname)
    return out


def scan(source: str, module: str) -> list[Found]:
    tree = ast.parse(source)
    constants = _constants(tree)
    connects = _connect_names(tree)
    out: list[Found] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(child, f"{scope}.{child.name}" if scope else child.name)
                continue
            if isinstance(child, ast.Call):
                func = child.func
                text = ast.unparse(func)
                if isinstance(func, ast.Attribute) and func.attr in EXECUTE:
                    if not child.args or not _constant_sql(child.args[0], constants):
                        out.append(Found(module, scope, "sql", child.lineno))
                elif text in connects:
                    out.append(Found(module, scope, "connect", child.lineno))
            visit(child, scope)

    visit(tree, "")
    return out


def problems(found: list[Found]) -> list[str]:
    out: list[str] = []
    for kind, sites in (("sql", SQL_SITES), ("connect", CONNECT_SITES)):
        seen = {(f.module, f.function) for f in found if f.kind == kind}
        what = "SQL that is not a constant of ours" if kind == "sql" else "a SQLite connection"
        out.extend(
            f"{f.module}:{f.function} line {f.line}: {what} outside the reviewed sites"
            for f in found
            if f.kind == kind and (f.module, f.function) not in sites
        )
        out.extend(f"stale {kind} site: {m}:{fn}" for m, fn in sorted(sites.keys() - seen))
    return sorted(out)


def tree_findings() -> list[Found]:
    out: list[Found] = []
    for path in sorted((SRC / "app").rglob("*.py")):
        out.extend(scan(path.read_text(), module_name(path, SRC)))
    return out


def test_only_the_runner_runs_sql_that_is_not_ours_and_connections_open_at_reviewed_sites() -> None:
    assert problems(tree_findings()) == []


def test_the_scan_sees_the_runners_user_query() -> None:
    assert any(
        (f.module, f.function, f.kind) == ("app.mirror.query", "QueryRunner.run_user_sql", "sql")
        for f in tree_findings()
    )


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("def run(con, sql):\n    return con.execute(sql)\n", "sql"),
        ("def run(con, t):\n    return con.execute(f'SELECT * FROM {t}')\n", "sql"),
        ("def run(con, a):\n    return con.execute('SELECT ' + a)\n", "sql"),
        ("def run(cur, rows, sql):\n    cur.executemany(sql, rows)\n", "sql"),
        ("def run(con, script):\n    con.executescript(script)\n", "sql"),
        ("class R:\n    def read(self, q):\n        return self.con.execute(q.text)\n", "sql"),
        ("import sqlite3\n\ndef open_db(p):\n    return sqlite3.connect(p)\n", "connect"),
        ("from sqlite3 import connect\n\ndef open_db(p):\n    return connect(p)\n", "connect"),
        ("import sqlite3 as lite\n\ndef open_db(p):\n    return lite.connect(p)\n", "connect"),
    ],
)
def test_a_second_query_path_is_refused(source: str, kind: str) -> None:
    found = scan(source, "app.api.planted")
    assert [f.kind for f in found] == [kind]
    assert problems(found + tree_findings()) != []


@pytest.mark.parametrize(
    "source",
    [
        "SQL = 'SELECT 1'\n\ndef run(con):\n    return con.execute(SQL)\n",
        "def run(con):\n    return con.execute('SELECT 2', (1,))\n",
        "from app.mirror.files import META_SQL\n\ndef run(con):\n    return con.execute(META_SQL)\n",
    ],
)
def test_constant_sql_of_ours_passes(source: str) -> None:
    assert scan(source, "app.mirror.planted") == []


def test_a_reviewed_site_that_is_gone_is_stale() -> None:
    found = [f for f in tree_findings() if f.function != "QueryRunner.run_user_sql"]
    assert "stale sql site: app.mirror.query:QueryRunner.run_user_sql" in problems(found)


def test_the_tree_is_scanned() -> None:
    """Not vacuous: the mirror's three modules are read."""
    names = {module_name(p, SRC) for p in (SRC / "app" / "mirror").rglob("*.py")}
    assert {"app.mirror.query", "app.mirror.reader", "app.mirror.files"} <= names
