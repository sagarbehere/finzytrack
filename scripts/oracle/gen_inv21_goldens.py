"""Produce `corpora/ledgers/inv21/goldens.json` from the RELEASED v0.2.2 code (S0022; EP-29: the oracle, not our code).

The functions are read from the `v0.2.2` tag of this repository (`git show`, read-only), extracted verbatim by syntax
tree and run on the case ledgers (copied to a temp folder) in Beancount 3.2.0:
  backend/app/libs/content_hash.py          the whole module (compute_content_hash)
  backend/app/core/constants.py             SOURCE_ACCOUNT_TYPES, SOURCE_ACCOUNT_PREFIXES
  backend/app/core/beancount_engine.py      BeancountEngine.compute_hash_from_transaction
  backend/app/services/sqlite_exporter.py   SQLiteExporter._get_transaction_id, _compute_fallback_id_map, _json_key,
                                            _has_unsafe_keys, _convert_value_to_json_serializable, _metadata_to_json
The hash input is captured by giving the extracted content_hash module a `hashlib` whose sha256 records its input.
`transaction_id` is v0.2.2's id, replaced by the case's `v04_overrides` (cases.toml) where v0.4 differs.

Nothing in CI needs this: the goldens are committed as data, and the test re-derives them from the spec
(`inv21.py`). When the tag is in the clone, the test also reruns this with --check.

Usage (oracle env):  uv run --project scripts/oracle --locked python scripts/oracle/gen_inv21_goldens.py [--check]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import types
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import beancount
from beancount import loader
from beancount.core.data import Transaction

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "corpora" / "ledgers" / "inv21"
TAG = "v0.2.2"


def show(path: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), "show", f"{TAG}:{path}"], check=True, capture_output=True,
                          text=True).stdout


def methods(src: str, cls: str, names: list[str]) -> dict[str, tuple[str, int, int]]:
    """{name: (dedented source of the def, first line, last line)} for methods of `cls`, decorators left out."""
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == cls)
    lines = src.splitlines()
    out = {n.name: (textwrap.dedent("\n".join(lines[n.lineno - 1:n.end_lineno])), n.lineno, n.end_lineno)
           for n in node.body if isinstance(n, ast.FunctionDef) and n.name in names}
    if set(out) != set(names):
        raise SystemExit(f"{cls}: missing {set(names) - set(out)} at {TAG}")
    return out


def as_static_class(name: str, funcs: dict[str, tuple[str, int, int]], order: list[str], ns: dict[str, Any]) -> Any:
    body = []
    for k in order:
        body.append("@staticmethod" if name == "SQLiteExporter" else "")
        body.extend(funcs[k][0].splitlines())
    exec(f"class {name}:\n" + "\n".join("    " + line if line.strip() else "" for line in body), ns)
    return ns[name]


class Oracle:
    """The v0.2.2 functions, ready to run."""

    def __init__(self) -> None:
        self.ranges: dict[str, str] = {}
        self.captured: list[bytes] = []
        ch_src = show("backend/app/libs/content_hash.py")
        ch = types.ModuleType("content_hash_v022")
        exec(compile(ch_src, "content_hash.py", "exec"), ch.__dict__)
        captured = self.captured

        class Recorder:
            @staticmethod
            def sha256(b: bytes) -> Any:
                captured.append(b)
                return hashlib.sha256(b)

        ch.hashlib = Recorder  # type: ignore[attr-defined]
        self.ranges["backend/app/libs/content_hash.py"] = f"1-{len(ch_src.splitlines())} (whole file)"

        const_src = show("backend/app/core/constants.py")
        cns: dict[str, Any] = {}
        used = []
        for n in ast.parse(const_src).body:
            if isinstance(n, ast.Assign) and {getattr(t, "id", "") for t in n.targets} & {"SOURCE_ACCOUNT_TYPES",
                                                                                           "SOURCE_ACCOUNT_PREFIXES"}:
                exec(compile(ast.Module([n], []), "constants.py", "exec"), cns)
                used.append(str(n.lineno))
        self.ranges["backend/app/core/constants.py"] = "lines " + ", ".join(used)

        eng = methods(show("backend/app/core/beancount_engine.py"), "BeancountEngine", ["compute_hash_from_transaction"])
        self.ranges["backend/app/core/beancount_engine.py"] = "; ".join(
            f"BeancountEngine.{k} {v[1]}-{v[2]}" for k, v in eng.items())
        self.engine = as_static_class("BeancountEngine", eng, list(eng), {
            "Transaction": Transaction, "Tuple": Tuple, "SOURCE_ACCOUNT_PREFIXES": cns["SOURCE_ACCOUNT_PREFIXES"],
            "compute_content_hash": ch.compute_content_hash})()

        names = ["_get_transaction_id", "_compute_fallback_id_map", "_json_key", "_has_unsafe_keys",
                 "_convert_value_to_json_serializable", "_metadata_to_json"]
        exp = methods(show("backend/app/services/sqlite_exporter.py"), "SQLiteExporter", names)
        self.ranges["backend/app/services/sqlite_exporter.py"] = "; ".join(
            f"SQLiteExporter.{k} {exp[k][1]}-{exp[k][2]}" for k in names)
        self.exporter = as_static_class("SQLiteExporter", exp, names, {
            "Transaction": Transaction, "Optional": Optional, "Dict": Dict, "List": List, "Any": Any,
            "Decimal": Decimal, "date": date, "datetime": datetime, "json": json,
            "logger": logging.getLogger("inv21.v022")})

    def fallback_ids(self, txns: list[Transaction]) -> dict[int, str]:
        # _compute_fallback_id_map imports BeancountEngine from app.core.beancount_engine: serve ours, then restore.
        names = ("app", "app.core", "app.core.beancount_engine")
        saved = {n: sys.modules.get(n) for n in names}
        try:
            for n in names:
                sys.modules[n] = types.ModuleType(n)
            sys.modules["app.core.beancount_engine"].BeancountEngine = type(self.engine)  # type: ignore[attr-defined]
            return self.exporter._compute_fallback_id_map(txns)
        finally:
            for n, m in saved.items():
                if m is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = m

    def hash_and_input(self, txn: Transaction) -> tuple[str, str]:
        self.captured.clear()
        _src, h = self.engine.compute_hash_from_transaction(txn)
        if len(self.captured) != 1:
            raise SystemExit("compute_hash_from_transaction did not hash exactly once")
        return h, self.captured[0].decode("utf-8")


def rel_meta_json(js: str | None, root: str) -> str | None:
    """The one normalization: `filename` relative to the case folder."""
    if js is None:
        return None
    d = json.loads(js)
    if isinstance(d.get("filename"), str) and d["filename"].startswith(root + os.sep):
        d["filename"] = d["filename"][len(root) + 1:].replace(os.sep, "/")
    return json.dumps(d)


def spec_only_case(oracle: Oracle, cfg: dict[str, Any], txns: list[Transaction], root: str, at: Any) -> dict[str, Any]:
    """A ledger v0.2.2 cannot read (cases.toml `v022_fails`): confirm that it fails, then take the hash input of each
    transaction from the hand-written `v04_expected` (README "Ids"). Kept simple on purpose: every transaction must
    reach the content fallback with a distinct hash input, so its TransactionId is its hash."""
    try:
        oracle.fallback_ids(txns)
    except Exception:
        pass
    else:
        raise SystemExit("a v022_fails case that v0.2.2 reads without error")
    expected = cfg["v04_expected"]
    rows = []
    for t in txns:
        if {"id", "transaction_id"} & set(t.meta) or any("import-key" in (p.meta or {}) for p in t.postings):
            raise SystemExit("a v022_fails case may hold only entries that reach the content fallback")
        text = expected[at(t.meta)]
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rows.append({
            "at": at(t.meta), "flag": t.flag, "transaction_id": h, "v022_transaction_id": None,
            "sha256": h, "hash_input": text,
            "metadata_json": rel_meta_json(oracle.exporter._metadata_to_json(t.meta), root),
            "posting_metadata_json": [rel_meta_json(oracle.exporter._metadata_to_json(p.meta), root)
                                      if p.meta else None for p in t.postings],
        })
    if len({r["hash_input"] for r in rows}) != len(rows):
        raise SystemExit("a v022_fails case needs distinct hash inputs")
    return {"transactions": rows}


def generate() -> str:
    oracle = Oracle()
    cases = tomllib.loads((CORPUS / "cases.toml").read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name in sorted(cases):
            dst = Path(tmp) / name
            shutil.copytree(CORPUS / name, dst)
            root = os.path.abspath(dst)
            entries, errors, _ = loader.load_file(os.path.join(root, "main.beancount"))
            if errors:
                raise SystemExit(f"{name}: Beancount reports errors: {errors}")

            def at(meta: dict[str, Any]) -> str:
                return f"{os.path.relpath(meta['filename'], root).replace(os.sep, '/')}:{meta['lineno']}"

            txns = [e for e in entries if isinstance(e, Transaction)]
            if "v022_fails" in cases[name]:
                out[name] = spec_only_case(oracle, cases[name], txns, root, at)
                continue
            fallback = oracle.fallback_ids(txns)
            overrides = cases[name].get("v04_overrides", {})
            rows = []
            for i, t in enumerate(txns):
                v022 = oracle.exporter._get_transaction_id(t, fallback.get(i))
                h, text = oracle.hash_and_input(t)
                rows.append({
                    "at": at(t.meta), "flag": t.flag,
                    "transaction_id": overrides.get(at(t.meta), v022), "v022_transaction_id": v022,
                    "sha256": h, "hash_input": text,
                    "metadata_json": rel_meta_json(oracle.exporter._metadata_to_json(t.meta), root),
                    "posting_metadata_json": [rel_meta_json(oracle.exporter._metadata_to_json(p.meta), root)
                                              if p.meta else None for p in t.postings],
                })
            case: dict[str, Any] = {"transactions": rows}
            if cases[name].get("directive_metadata"):
                case["directives"] = [
                    {"at": at(e.meta), "type": type(e).__name__,
                     "metadata_json": rel_meta_json(oracle.exporter._metadata_to_json(e.meta), root)}
                    for e in entries if not isinstance(e, Transaction)
                    and str(e.meta.get("filename", "")).startswith(root + os.sep)]
            out[name] = case
    doc = {
        "about": "INV-21 goldens (S0022): the released v0.2.2 code run on these ledgers in Beancount; "
                 "`transaction_id` is v0.4's expected TransactionId (v0.2.2's, except the cases.toml v04_overrides). "
                 "Written by scripts/oracle/gen_inv21_goldens.py; spec design/specs/mirror/inv21-goldens.md. "
                 "Never edit by hand.",
        "legacy_tag": TAG,
        "legacy_code": oracle.ranges,
        "beancount": beancount.__version__,
        "cases": out,
    }
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare with the committed goldens instead of writing")
    args = ap.parse_args()
    text = generate()
    target = CORPUS / "goldens.json"
    if args.check:
        same = target.read_text(encoding="utf-8") == text
        print("goldens.json matches v0.2.2" if same else "goldens.json differs from what v0.2.2 gives")
        return 0 if same else 1
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
