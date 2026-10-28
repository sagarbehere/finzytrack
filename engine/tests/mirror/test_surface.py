"""The built mirror's DDL equals the mirror surface spec (BL-0057, DDL half; spec design/specs/mirror/surface.toml and
README.md "Three layers" and "Rules" 1; ADR-0015, ADR-0048; INV-05).

The check reads the built file the way an outside tool would (`sqlite_master`, `PRAGMA table_info`) and compares it
with the TOML, never with the build's own table list:
- one base table `m_<name>` per spec table, with exactly the spec's columns, in order, with their declared types;
- one public view `r_<name>` per table with public columns, holding exactly the columns marked `user_sql`, in order,
  each a plain reference to the base column (a view column that is an expression has no declared type, so its
  affinity would be lost: `WHERE amount > 0` would behave differently from v0.2.2);
- one compatibility view per v0.2.2 table name, with exactly v0.2.2's columns in v0.2.2's order and types, read from
  `r_<name>` (`meta`, which has no public view, from `m_meta`);
- nothing else.
Each rule has a planted defect the check must report (EP-29).
"""

from __future__ import annotations

import re
import sqlite3
import tomllib
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.mirror.core import schema

from .support import build_text

pytestmark = pytest.mark.inv("INV-05")

SPEC_PATH = Path(__file__).resolve().parents[3] / "design" / "specs" / "mirror" / "surface.toml"
SPEC: dict[str, Any] = tomllib.loads(SPEC_PATH.read_text(encoding="utf-8"))
SOURCE = re.compile(r"\bFROM\s+\"?(\w+)\"?\s*;?\s*$", re.IGNORECASE)


def declared(con: sqlite3.Connection, name: str) -> list[tuple[str, str]]:
    return [(r[1], r[2]) for r in con.execute(f'PRAGMA table_info("{name}")')]


def surface_problems(con: sqlite3.Connection, spec: dict[str, Any]) -> list[str]:
    """Every way the file's tables and views differ from the spec."""
    objects = {
        name: (kind, sql)
        for kind, name, sql in con.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND type IN ('table', 'view')"
        )
    }
    legacy: dict[str, list[list[str]]] = spec["legacy"]["v0.2.2"]["tables"]
    wanted: dict[str, tuple[str, list[tuple[str, str]], str | None]] = {}
    for table in spec["table"]:
        columns = table["column"]
        wanted[table["base"]] = ("table", [(c["name"], c["type"]) for c in columns], None)
        public = [(c["name"], c["type"]) for c in columns if c["user_sql"]]
        view = f"r_{table['name']}"
        if public:
            wanted[view] = ("view", public, table["base"])
        if table["name"] in legacy:
            source = view if public else table["base"]
            wanted[table["name"]] = ("view", [(n, t) for n, t in legacy[table["name"]]], source)
    problems: list[str] = []
    for name in sorted(set(objects) - set(wanted)):
        problems.append(f"{name}: not in the spec")
    for name, (kind, columns, source) in sorted(wanted.items()):
        if name not in objects:
            problems.append(f"{name}: missing")
            continue
        have_kind, sql = objects[name]
        if have_kind != kind:
            problems.append(f"{name}: a {have_kind}, the spec has a {kind}")
        try:
            have = declared(con, name)
        except sqlite3.OperationalError as e:  # a view over a column that is not there
            problems.append(f"{name}: cannot be read ({e})")
            continue
        if have != columns:
            problems.append(f"{name}: columns {have} != spec {columns}")
        if source is not None:
            match = SOURCE.search(" ".join(sql.split()))
            if match is None or match.group(1) != source:
                problems.append(f"{name}: does not read {source} alone")
    return problems


def built_from(tmp_path: Path, ddl: str) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "planted.db")
    con.executescript(ddl)
    return con


def test_the_built_file_matches_the_spec(tmp_path: Path) -> None:
    con = build_text(tmp_path, "2020-01-01 open Assets:Bank\n")
    assert surface_problems(con, SPEC) == []
    assert len(SPEC["table"]) == 18  # a silently shrunk spec would pass a vacuous check


def test_the_snapshot_ddl_is_what_the_build_writes(tmp_path: Path) -> None:
    con = build_text(tmp_path, "2020-01-01 open Assets:Bank\n")
    planted = built_from(tmp_path, schema.ddl())
    assert surface_problems(planted, SPEC) == []
    query = "SELECT type, name, sql FROM sqlite_master ORDER BY name"
    assert con.execute(query).fetchall() == planted.execute(query).fetchall()


def test_the_spec_keeps_every_legacy_column_public() -> None:
    # README "Three layers": a compatibility view reads its columns from r_<name>, so each must be public.
    public = {t["name"]: {c["name"] for c in t["column"] if c["user_sql"]} for t in SPEC["table"]}
    for name, columns in SPEC["legacy"]["v0.2.2"]["tables"].items():
        if name != "meta":
            assert {c for c, _ in columns} <= public[name], name


# ---- must not pass: planted defects ------------------------------------------------------------------------------


def planted(tmp_path: Path, old: str, new: str) -> list[str]:
    ddl = schema.ddl()
    assert old in ddl, old
    return surface_problems(built_from(tmp_path, ddl.replace(old, new, 1)), SPEC)


def test_a_missing_base_column_is_found(tmp_path: Path) -> None:
    problems = planted(tmp_path, "cost_label TEXT", "cost_label_x TEXT")
    assert any(p.startswith("m_postings: columns") for p in problems), problems


def test_a_wrong_base_type_is_found(tmp_path: Path) -> None:
    problems = planted(tmp_path, "document_count INTEGER", "document_count TEXT")
    assert any(p.startswith("m_postings: columns") for p in problems), problems


def test_an_expression_in_a_public_view_is_found(tmp_path: Path) -> None:
    problems = planted(
        tmp_path, "SELECT posting_id, transaction_id,", "SELECT posting_id + 0 AS posting_id, transaction_id,"
    )
    assert any(p.startswith("r_postings: columns") for p in problems), problems


def test_a_reordered_compatibility_view_is_found(tmp_path: Path) -> None:
    problems = planted(
        tmp_path,
        "CREATE VIEW training_data AS SELECT id, description, category",
        "CREATE VIEW training_data AS SELECT id, category, description",
    )
    assert any(p.startswith("training_data: columns") for p in problems), problems


def test_a_view_reading_the_wrong_table_is_found(tmp_path: Path) -> None:
    problems = planted(tmp_path, "category FROM r_training_data", "category FROM m_training_data")
    assert "training_data: does not read r_training_data alone" in problems, problems


def test_an_extra_object_is_found(tmp_path: Path) -> None:
    problems = planted(tmp_path, "CREATE TABLE m_meta", "CREATE TABLE m_extra (x TEXT);\nCREATE TABLE m_meta")
    assert "m_extra: not in the spec" in problems, problems


def test_a_missing_view_is_found(tmp_path: Path) -> None:
    ddl = schema.ddl()
    line = next(s for s in ddl.split(";\n") if s.strip().startswith("CREATE VIEW notes "))
    problems = surface_problems(built_from(tmp_path, ddl.replace(line + ";\n", "", 1)), SPEC)
    assert "notes: missing" in problems, problems
