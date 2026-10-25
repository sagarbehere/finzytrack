#!/usr/bin/env python3
"""Structural check of the mirror surface spec (S0021; design/specs/mirror/surface.toml). Stdlib only.

Refuses:
  - a table or column with a missing, extra or empty field, or a value outside the closed vocabularies; money and
    other exact numbers (`decimal`) that are not TEXT or lack a sign convention, and a sign on anything else;
    a format stored under the wrong type; duplicate names; a base table not named m_<name>; a public name that
    starts with m_ or r_;
  - a v0.2.2 table, column or compute function the spec does not serve under its v0.2.2 name (ADR-0048), or one
    listed as dropped;
  - a v0.3.0 table, column or function that is neither kept nor dropped, or both; a drop without a reason and a
    citation, or of something no version had;
  - an origin the legacy inventories do not confirm; `same`/`changed` on a new column, `new` on an old one;
  - a change without a reason and a citation; a citation that does not resolve (ADR file, capability row,
    invariant, backlog ID, or `owner:<file>` open in design/inbox/);
  - an `owner:` status or as-of without an open inbox file; a decided or draft item whose text or citations
    point at an owner question; a column marked decided or draft in a table that waits for the owner;
  - a `same` column whose type differs from its legacy type; a v0.2.2 table (other than the internal `meta`)
    with no public column, which would lose its r_* view.
With --legacy it also compares the pinned legacy inventories with the exporters' DDL at the legacy tags.

Usage: python3 scripts/check_mirror_surface.py [--legacy]
Exit 0 = ok, 1 = refused.
"""

from __future__ import annotations

import argparse
import ast
import re
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "design/specs/mirror/surface.toml"

TABLE_FIELDS = {"name", "base", "row", "as_of", "origin", "status", "cites", "note", "column"}
COLUMN_FIELDS = {"name", "type", "format", "meaning", "source", "sign", "origin", "change", "cites", "why",
                 "user_sql", "status"}
FUNCTION_FIELDS = {"name", "served_by", "message", "located", "milestone", "cites", "status"}
DROPPED_FIELDS = {"item", "origin", "reason", "cites"}
# format -> the SQLite type it is stored as. `decimal` is an exact number kept as TEXT (ADR-0023, INV-06).
FORMATS = {"decimal": "TEXT", "date": "TEXT", "text": "TEXT", "account": "TEXT", "currency": "TEXT",
           "flag": "TEXT", "hash": "TEXT", "path": "TEXT", "id": "TEXT", "json-meta": "TEXT", "json-list": "TEXT",
           "json-custom": "TEXT", "json-entry": "TEXT", "json-value": "TEXT", "ordinal": "INTEGER",
           "count": "INTEGER", "bool": "INTEGER", "int": "INTEGER"}
AS_OF = {"entry", "ledger-end", "load", "today"}
ORIGINS = ("v0.2.2", "v0.3.0", "v0.4")
CHANGES = {"same", "changed", "new"}
OPEN_QUESTION = ("owner:", "design/inbox")
# v0.2.2 tables that were never documented for user SQL, served from their base table only.
INTERNAL_V022 = {"meta"}
EXPORTER = "backend/app/services/sqlite_exporter.py"
COMPUTE_ROUTER = "backend/app/api/routers/compute.py"
COMPUTE_FUNCTIONS = "backend/app/compute/functions"


def load(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def known_refs(root: Path) -> set[str]:
    refs = {"-".join(p.name.split("-")[:2]) for p in (root / "design/decisions").glob("ADR-*.md")}
    caps = load(root / "design/capabilities.toml")
    refs |= {c["id"] for v in caps.values() if isinstance(v, list) for c in v if isinstance(c, dict) and "id" in c}
    refs |= set(re.findall(r"(?m)^### (INV-\d{2}):", (root / "design/INVARIANTS.md").read_text(encoding="utf-8")))
    refs |= backlog_ids((root / "design/backlog.md").read_text(encoding="utf-8"))
    return refs


def backlog_ids(text: str) -> set[str]:
    """First cells of the rows of backlog tables whose header's first cell is `ID` (finding and BL tables);
    header rows and other tables (status legends, the doubles map) give nothing."""
    ids: set[str] = set()
    in_id_table = False
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else None
        if cells is None:
            in_id_table = False
        elif cells[0] == "ID":
            in_id_table = True
        elif in_id_table and re.fullmatch(r"[A-Z][A-Za-z0-9.-]*", cells[0]):
            ids.add(cells[0])
    return ids


def open_inbox(root: Path) -> set[str]:
    return {p.name for p in (root / "design/inbox").glob("*.md") if p.name != "README.md"}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _status_ok(value: object, inbox: set[str]) -> bool:
    if value in ("decided", "draft"):
        return True
    return isinstance(value, str) and value.startswith("owner:") and value.removeprefix("owner:") in inbox


def _cites_problems(where: str, cites: object, refs: set[str], inbox: set[str]) -> list[str]:
    if not isinstance(cites, list):
        return [f"{where}: cites must be a list"]
    out = []
    for c in cites:
        if not isinstance(c, str):
            out.append(f"{where}: citation {c!r} is not text")
        elif c.startswith("owner:"):
            if c.removeprefix("owner:") not in inbox:
                out.append(f"{where}: {c} is not an open file in design/inbox/")
        elif c not in refs:
            out.append(f"{where}: citation {c!r} does not resolve (an ADR file, invariant, capability row or backlog ID)")
    return out


def _scalars(item: dict) -> str:
    """The item's own text, lists (cites) included; a table's columns are checked on their own."""
    return " ".join(" ".join(map(str, v)) if isinstance(v, list) else str(v)
                    for k, v in item.items() if k != "column" and not isinstance(v, dict))


def _field_problems(where: str, item: object, fields: set[str]) -> list[str]:
    if not isinstance(item, dict):
        return [f"{where}: not a table"]
    out = []
    if set(item) != fields:
        missing, extra = sorted(fields - set(item)), sorted(set(item) - fields)
        out.append(f"{where}: fields differ (missing {missing}, extra {extra})")
    return out


def _column_problems(t: str, c: dict, refs: set[str], inbox: set[str], table_status: str) -> list[str]:
    where = f"{t}.{c.get('name')}"
    out = _field_problems(where, c, COLUMN_FIELDS)
    if out:
        return out
    for f in ("name", "meaning", "source", "sign"):
        if not _text(c[f]):
            out.append(f"{where}: {f} is empty")
    fmt, typ = c["format"], c["type"]
    if fmt not in FORMATS:
        out.append(f"{where}: unknown format {fmt!r}")
    elif typ != FORMATS[fmt]:
        out.append(f"{where}: format {fmt} is stored as {FORMATS[fmt]}, not {typ!r}")
    if fmt == "decimal" and _text(c["sign"]) in ("", "n/a"):
        out.append(f"{where}: an exact number needs a sign convention")
    if fmt != "decimal" and c["sign"] != "n/a":
        out.append(f"{where}: sign is only for exact numbers (use \"n/a\")")
    if c["origin"] not in ORIGINS:
        out.append(f"{where}: unknown origin {c['origin']!r}")
    if c["change"] not in CHANGES:
        out.append(f"{where}: unknown change {c['change']!r}")
    elif (c["change"] == "new") != (c["origin"] == "v0.4"):
        out.append(f"{where}: change `new` is for v0.4 columns only, and every v0.4 column is `new`")
    if c["change"] == "changed" and (not c["cites"] or not _text(c["why"])):
        out.append(f"{where}: a changed column needs a reason (why) and a citation")
    if not isinstance(c["user_sql"], bool):
        out.append(f"{where}: user_sql must be true or false")
    if not _status_ok(c["status"], inbox):
        out.append(f"{where}: status must be decided, draft or owner:<open inbox file>")
    elif c["status"] in ("decided", "draft"):
        if any(s in _scalars(c) for s in OPEN_QUESTION):
            out.append(f"{where}: a {c['status']} column cannot point at an open owner question")
        if table_status.startswith("owner:"):
            out.append(f"{where}: its table waits for the owner ({table_status}), so the column does too")
    out += _cites_problems(where, c["cites"], refs, inbox)
    return out


def _table_problems(t: dict, refs: set[str], inbox: set[str]) -> list[str]:
    name = t.get("name") if isinstance(t, dict) else None
    out = _field_problems(str(name), t, TABLE_FIELDS)
    if out:
        return out
    for f in ("name", "row"):
        if not _text(t[f]):
            out.append(f"{name}: {f} is empty")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name.startswith(("m_", "r_")):
        out.append(f"{name}: a public name is lower_snake_case and never starts with m_ or r_")
    if t["base"] != f"m_{name}":
        out.append(f"{name}: base must be m_{name}")
    as_of = t["as_of"]
    if as_of not in AS_OF and not (isinstance(as_of, str) and as_of.startswith("owner:")
                                   and as_of.removeprefix("owner:") in inbox):
        out.append(f"{name}: as_of must be one of {sorted(AS_OF)} or owner:<open inbox file>")
    if t["origin"] not in ORIGINS:
        out.append(f"{name}: unknown origin {t['origin']!r}")
    if not _status_ok(t["status"], inbox):
        out.append(f"{name}: status must be decided, draft or owner:<open inbox file>")
    elif t["status"] in ("decided", "draft") and any(s in _scalars(t) for s in OPEN_QUESTION):
        out.append(f"{name}: a {t['status']} table cannot point at an open owner question")
    out += _cites_problems(str(name), t["cites"], refs, inbox)
    if not isinstance(t["column"], list) or not t["column"]:
        return out + [f"{name}: no columns"]
    seen: set[str] = set()
    for c in t["column"]:
        cname = c.get("name") if isinstance(c, dict) else None
        if cname in seen:
            out.append(f"{name}.{cname}: duplicate column")
        seen.add(str(cname))
        out += _column_problems(str(name), c, refs, inbox, str(t["status"]))
    return out


def _inventory(spec: dict, version: str) -> tuple[dict[str, list[str]], list[str], dict[tuple[str, str], str]]:
    """Column names per table, compute functions, and the declared type of each (table, column)."""
    inv = spec.get("legacy", {}).get(version, {})
    tables = inv.get("tables", {})
    if not isinstance(tables, dict):
        tables = {}
    pairs = {t: [c for c in cols if isinstance(c, list) and len(c) == 2] for t, cols in tables.items()
             if isinstance(cols, list)}
    names = {t: [c[0] for c in cols] for t, cols in pairs.items()}
    types = {(t, c[0]): c[1] for t, cols in pairs.items() for c in cols}
    return names, inv.get("functions", []), types


def _accounting_problems(spec: dict, refs: set[str], inbox: set[str]) -> list[str]:
    out: list[str] = []
    t022, f022, ty022 = _inventory(spec, "v0.2.2")
    t030, f030, ty030 = _inventory(spec, "v0.3.0")
    if not t022 or not t030 or not f022:
        return ["legacy: the v0.2.2 and v0.3.0 inventories are required"]
    tables = {t["name"]: t for t in spec.get("table", []) if isinstance(t, dict) and "name" in t}
    kept_cols = {(n, c.get("name")): c for n, t in tables.items() for c in t.get("column", []) if isinstance(c, dict)}
    functions = {f.get("name"): f for f in spec.get("compat_function", []) if isinstance(f, dict)}
    dropped: dict[str, dict] = {}
    for d in spec.get("dropped", []):
        item = d.get("item") if isinstance(d, dict) else None
        out += _field_problems(f"dropped {item}", d, DROPPED_FIELDS)
        if isinstance(item, str):
            dropped[item] = d

    def origin_of(kind: str, key: tuple[str, ...]) -> str:
        def has(tabs: dict[str, list[str]], funcs: list[str]) -> bool:
            if kind == "table":
                return key[0] in tabs
            if kind == "column":
                return key[1] in tabs.get(key[0], [])
            return key[0] in funcs
        return "v0.2.2" if has(t022, f022) else "v0.3.0" if has(t030, f030) else "v0.4"

    # Origins the inventories confirm, for every kept table and column.
    for n, t in tables.items():
        if t.get("origin") in ORIGINS and t["origin"] != origin_of("table", (n,)):
            out.append(f"{n}: origin {t['origin']} is not what the legacy inventories say ({origin_of('table', (n,))})")
    for (n, cn), c in kept_cols.items():
        if c.get("origin") in ORIGINS and c["origin"] != origin_of("column", (n, cn)):
            out.append(f"{n}.{cn}: origin {c['origin']} is not what the legacy inventories say "
                       f"({origin_of('column', (n, cn))})")
        legacy_type = {"v0.2.2": ty022, "v0.3.0": ty030}.get(str(c.get("origin")), {}).get((n, cn))
        if legacy_type is not None and c.get("change") == "same" and c.get("type") != legacy_type:
            out.append(f"{n}.{cn}: type {c.get('type')} differs from {c['origin']}'s {legacy_type}, "
                       "so the column cannot be `same`")
    # v0.2.2: everything served under its name, nothing dropped (ADR-0048).
    for n, cols in t022.items():
        if n not in tables:
            out.append(f"{n}: a v0.2.2 table must keep its name (ADR-0048)")
            continue
        public = any(c.get("user_sql") is True for c in tables[n].get("column", []) if isinstance(c, dict))
        if not public and n not in INTERNAL_V022:
            out.append(f"{n}: a v0.2.2 table keeps its public view r_{n}; mark its documented columns user_sql")
        for cn in cols:
            if (n, cn) not in kept_cols:
                out.append(f"{n}.{cn}: a v0.2.2 column must stay in its table (ADR-0048)")
            elif public and kept_cols[(n, cn)].get("user_sql") is not True:
                out.append(f"{n}.{cn}: the compatibility view reads r_{n}, so a v0.2.2 column must be user_sql")
    for fn in f022:
        f = functions.get(fn)
        if f is None:
            out.append(f"{fn}: a v0.2.2 compute function needs a compatibility entry (ADR-0048)")
            continue
        out += _field_problems(fn, f, FUNCTION_FIELDS)
        if set(f) == FUNCTION_FIELDS:
            for k in ("served_by", "message", "located", "milestone"):
                if not _text(f[k]):
                    out.append(f"{fn}: {k} is empty (a removed function keeps a located deprecation message)")
            if not f["cites"]:
                out.append(f"{fn}: a compatibility entry needs a citation")
            if not _status_ok(f["status"], inbox):
                out.append(f"{fn}: status must be decided, draft or owner:<open inbox file>")
            out += _cites_problems(fn, f["cites"], refs, inbox)
    for fn in functions:
        if fn not in f022:
            out.append(f"{fn}: only v0.2.2 compute functions get a compatibility entry (ADR-0048)")
    # Dropped items: real, not v0.2.2, cited, not also kept.
    for item, d in dropped.items():
        kind, _, rest = item.partition(":")
        key = tuple(rest.split(".", 1)) if kind == "column" else (rest,)
        valid = (kind == "table" and rest in t030) or (kind == "function" and rest in f030) or \
                (kind == "column" and len(key) == 2 and key[1] in t030.get(key[0], []))
        if not valid:
            out.append(f"dropped {item}: no legacy version had it (items are table:<t>, column:<t>.<c>, function:<f>)")
            continue
        if origin_of(kind, key) == "v0.2.2":
            out.append(f"dropped {item}: v0.2.2 items keep working through 0.4.x and cannot be dropped (ADR-0048) "
                       f"[{'.'.join(key)}]")
        kept = (kind == "table" and rest in tables) or (kind == "column" and key in kept_cols) or \
               (kind == "function" and rest in functions)
        if kept:
            out.append(f"dropped {item}: also kept [{'.'.join(key)}]")
        if set(d) == DROPPED_FIELDS:
            if d["origin"] != origin_of(kind, key):
                out.append(f"dropped {item}: origin {d['origin']} is not {origin_of(kind, key)}")
            if not _text(d["reason"]) or not d["cites"]:
                out.append(f"dropped {item}: a drop needs a reason and a citation")
            out += _cites_problems(f"dropped {item}", d["cites"], refs, inbox)
    # v0.3.0: kept or dropped.
    for n, cols in t030.items():
        if n not in tables and f"table:{n}" not in dropped:
            out.append(f"{n}: a v0.3.0 table must be kept or dropped with a citation")
        if n in tables:
            for cn in cols:
                if (n, cn) not in kept_cols and f"column:{n}.{cn}" not in dropped:
                    out.append(f"{n}.{cn}: a v0.3.0 column must be kept or dropped with a citation")
    for fn in f030:
        if fn not in functions and f"function:{fn}" not in dropped:
            out.append(f"{fn}: a v0.3.0 compute function must be kept or dropped with a citation")
    return out


def problems(spec: dict, refs: set[str], inbox: set[str]) -> list[str]:
    out: list[str] = []
    if not isinstance(spec.get("surface_version"), int) or spec["surface_version"] < 1:
        out.append("surface_version must be a positive integer")
    tables = spec.get("table")
    if not isinstance(tables, list) or not tables:
        return out + ["no tables"]
    names = [t.get("name") for t in tables if isinstance(t, dict)]
    for n in sorted({n for n in names if names.count(n) > 1}, key=str):
        out.append(f"{n}: duplicate table")
    for t in tables:
        out += _table_problems(t, refs, inbox)
    out += _accounting_problems(spec, refs, inbox)
    return out


# ── Legacy inventories, built by behaviour ───────────────────────────────────────────────────────────────

def derive_tables(source: str) -> dict[str, list[str]]:
    """Run every string literal of `source` that is a CREATE TABLE statement in an in-memory SQLite database and
    return each table's columns in order as [name, declared type]. Comments and non-statement strings create
    nothing."""
    con = sqlite3.connect(":memory:")
    try:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and re.match(r"\s*CREATE\s+TABLE\b", node.value, re.IGNORECASE):
                con.execute(node.value)
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: [[r[1], r[2]] for r in con.execute(f'PRAGMA table_info("{n}")')] for n in names}
    finally:
        con.close()


def derive_functions(router_source: str, function_sources: list[str]) -> list[str]:
    """Names of the compute functions the router registers: `registry.register(<Class>(...))` calls (code, not
    comments), mapped to the `name` attribute of that class in the function modules."""
    registered = []
    for node in ast.walk(ast.parse(router_source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "register" \
                and node.args and isinstance(node.args[0], ast.Call) and isinstance(node.args[0].func, ast.Name):
            registered.append(node.args[0].func.id)
    names: dict[str, str] = {}
    for src in function_sources:
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "name" for t in stmt.targets) \
                            and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                        names[node.name] = stmt.value.value
    return [names[c] for c in registered if c in names]


def inventory_problems(version: str, pinned: dict, tables: dict[str, list[list[str]]], functions: list[str]) -> list[str]:
    out = []
    if pinned.get("tables") != tables:
        out.append(f"legacy {version}: pinned tables (names, types, order) differ from the exporter's DDL at "
                   f"{pinned.get('tag')}")
    if sorted(pinned.get("functions", [])) != sorted(functions):
        out.append(f"legacy {version}: pinned compute functions differ from the registry at {pinned.get('tag')}")
    return out


def _git_show(root: Path, tag: str, path: str) -> str:
    return subprocess.run(["git", "-C", str(root), "show", f"{tag}:{path}"], check=True, capture_output=True,
                          text=True).stdout


def legacy_problems(spec: dict, root: Path) -> list[str]:
    out = []
    for version, pinned in spec.get("legacy", {}).items():
        tag = pinned["tag"]
        listing = subprocess.run(["git", "-C", str(root), "ls-tree", "--name-only", tag, f"{COMPUTE_FUNCTIONS}/"],
                                 check=True, capture_output=True, text=True).stdout.split()
        sources = [_git_show(root, tag, p) for p in listing if p.endswith(".py")]
        out += inventory_problems(version, pinned, derive_tables(_git_show(root, tag, EXPORTER)),
                                  derive_functions(_git_show(root, tag, COMPUTE_ROUTER), sources))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy", action="store_true", help="also compare the pinned inventories with the legacy tags")
    args = ap.parse_args(argv)
    spec = load(SPEC)
    found = problems(spec, known_refs(ROOT), open_inbox(ROOT))
    if args.legacy:
        found += legacy_problems(spec, ROOT)
    for p in found:
        print(f"check-mirror-surface: {p}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
