"""Produce the INV-21 goldens from the RELEASED v0.2.2 code (S0022, S0095; EP-29: the oracle, not our code).

One file, `corpora/ledgers/inv21/goldens.json`: `cases` (the ledgers of `cases.toml`) and `installs` (every ledger root
of the v0.2.2 install fixtures in `corpora/workspaces/legacy/`, made by `gen_v022_install.py`; S0095).

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
import glob
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

import gen_v022_install

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "corpora" / "ledgers" / "inv21"
INSTALLS = REPO / "corpora" / "workspaces" / "legacy"
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


def released_case(oracle: Oracle, entries: list[Any], root: str, overrides: dict[str, str],
                  directive_metadata: bool) -> dict[str, Any]:
    """What v0.2.2 gives a loaded ledger: each transaction's row and, if asked, every other directive's metadata JSON
    (those in files under `root`). `transaction_id` is v0.2.2's id unless `overrides` names the entry."""

    def at(meta: dict[str, Any]) -> str:
        return f"{os.path.relpath(meta['filename'], root).replace(os.sep, '/')}:{meta['lineno']}"

    txns = [e for e in entries if isinstance(e, Transaction)]
    fallback = oracle.fallback_ids(txns)
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
    if directive_metadata:
        case["directives"] = [
            {"at": at(e.meta), "type": type(e).__name__,
             "metadata_json": rel_meta_json(oracle.exporter._metadata_to_json(e.meta), root)}
            for e in entries if not isinstance(e, Transaction)
            and str(e.meta.get("filename", "")).startswith(root + os.sep)]
    return case


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
            out[name] = released_case(oracle, entries, root, cases[name].get("v04_overrides", {}),
                                      bool(cases[name].get("directive_metadata")))
    doc = {
        "about": "INV-21 goldens (S0022): the released v0.2.2 code run on these ledgers in Beancount; "
                 "`transaction_id` is v0.4's expected TransactionId (v0.2.2's, except the cases.toml v04_overrides). "
                 "`installs`: every ledger root of the v0.2.2 install fixtures (corpora/workspaces/legacy/, S0095). "
                 "Written by scripts/oracle/gen_inv21_goldens.py; spec design/specs/mirror/inv21-goldens.md. "
                 "Never edit by hand.",
        "legacy_tag": TAG,
        "legacy_code": oracle.ranges,
        "beancount": beancount.__version__,
        "cases": out,
        "installs": installs(oracle),
    }
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def configured_root(install: Path) -> str:
    """The ledger the install's config names, resolved as v0.2.2's `Config.resolve_relative_paths` does (relative to
    the install folder, its run-time working directory), relative to that folder with `/`."""
    from ruamel.yaml import YAML  # the oracle env pins v0.2.2's ruamel.yaml (S0095)

    ledger_file = str(YAML(typ="safe").load((install / "config" / "config.yaml").read_text(encoding="utf-8"))
                      ["ledger_file"])
    if os.path.isabs(ledger_file):
        raise SystemExit(f"{install.name}: an absolute ledger_file has no place in a committed fixture")
    return os.path.normpath(ledger_file).replace(os.sep, "/")


def installs(oracle: Oracle) -> dict[str, Any]:
    """The goldens of the v0.2.2 install fixtures (`corpora/workspaces/legacy/<install>/`, S0095): every ledger root
    the install holds (a `.beancount` file no other file of its folder tree includes), with what v0.2.2 gives each:
    the transactions' rows and every other directive's metadata JSON. `filename` and `at` are relative to the
    install folder. No v0.4 override applies: no released version wrote `import-key`."""
    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for folder in sorted(p for p in INSTALLS.iterdir() if p.is_dir()):
            dst = gen_v022_install.materialize(folder, Path(tmp) / folder.name)  # `_data/` named `data/` again
            root = os.path.abspath(dst)
            roots: dict[str, Any] = {}
            for rel in ledger_roots(dst):
                entries, errors, _ = loader.load_file(os.path.join(root, rel))
                if errors:
                    raise SystemExit(f"{folder.name}/{rel}: Beancount reports errors: {errors}")
                roots[rel] = parsed_metadata(released_case(oracle, entries, root, {}, True))
            out[folder.name] = {"configured_root": configured_root(dst), "roots": roots}
    return out


def parsed_metadata(case: dict[str, Any]) -> dict[str, Any]:
    """The install goldens hold each metadata JSON parsed, so the file puts one key per line: the privacy scan reads a
    UUID-shaped legacy id on one line with an account word (`source_account`) as an account number (S0095 journal).
    Lossless, checked here: `json.dumps` of the parsed value gives back exactly the text."""

    def parse(text: str | None) -> Any:
        if text is None:
            return None
        value = json.loads(text)
        if json.dumps(value) != text:
            raise SystemExit(f"metadata JSON does not round-trip: {text}")
        return value

    for t in case["transactions"]:
        t["metadata_json"] = parse(t["metadata_json"])
        t["posting_metadata_json"] = [parse(p) for p in t["posting_metadata_json"]]
    for d in case["directives"]:
        d["metadata_json"] = parse(d["metadata_json"])
    return case


def ledger_roots(folder: Path) -> list[str]:
    """Every `.beancount` file under `folder` that no other one includes (by Beancount's own parse, globs expanded
    as its loader expands them), relative with `/`, sorted."""
    from beancount.parser import parser

    files = sorted(p.resolve() for p in folder.rglob("*.beancount"))
    included: set[Path] = set()
    for f in files:
        _, _, options = parser.parse_file(str(f))
        for pattern in options.get("include", []):
            target = pattern if os.path.isabs(pattern) else os.path.join(os.path.dirname(f), pattern)
            included.update(Path(m).resolve() for m in glob.glob(target, recursive=True))
    return [f.relative_to(folder.resolve()).as_posix() for f in files if f not in included]


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
