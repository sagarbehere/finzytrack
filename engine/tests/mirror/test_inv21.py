"""The mirror passes the INV-21 goldens (BL-0021; spec design/specs/mirror/inv21-goldens.md, README "Ids" and
"Metadata JSON"; ADR-0026, ADR-0048, ADR-0055).

`corpora/ledgers/inv21/goldens.json` holds, per case, every transaction in Beancount's order with its v0.4
TransactionId, its content hash (`sha256`, the `transaction_content_hash` column) and its metadata JSON (transaction
and each posting), taken from v0.2.2's own code and re-derived from the spec (S0022). Each case is copied to a temp
folder, loaded with the overlay load and built; the mirror's `postings` rows, posting by posting, must equal the
goldens. The goldens store `filename` relative to the case folder (their one normalization); the mirror stores the
absolute path, so the test makes it relative the same way before comparing. The `metadata-keys` case also pins every
other directive's metadata JSON; those with a table are compared with it.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from .support import build_loaded, load_root, rows

pytestmark = pytest.mark.inv("INV-21")

CORPUS = Path(__file__).resolve().parents[3] / "corpora" / "ledgers" / "inv21"
GOLDENS: dict[str, Any] = json.loads((CORPUS / "goldens.json").read_text(encoding="utf-8"))
# The directive types the metadata-keys case pins, and the table and key columns that hold their metadata. `Close`
# has no table in the surface; `Commodity` rows wait for the owner's ISO list (BL-0120).
DIRECTIVE_TABLES = {"Open": ("m_accounts", "metadata_json")}
NO_ROWS_YET = {"Close", "Commodity"}


def relative(text: str | None, root: str) -> str | None:
    """The goldens' normalization (inv21-goldens.md): `filename` relative to the case folder, with `/`."""
    if text is None:
        return None
    meta = json.loads(text)
    name = meta.get("filename")
    if isinstance(name, str) and name.startswith(root + os.sep):
        meta["filename"] = name[len(root) + 1 :].replace(os.sep, "/")
    return json.dumps(meta)


def build_case(name: str, tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    folder = tmp_path / name
    shutil.copytree(CORPUS / name, folder)
    loaded = load_root(folder / "main.beancount")
    assert loaded.errors == []
    return build_loaded(loaded, tmp_path / f"{name}.db"), str(folder)


def test_every_case_passes_its_goldens(tmp_path: Path) -> None:
    failures: list[str] = []
    postings = 0
    for name, golden in sorted(GOLDENS["cases"].items()):
        con, root = build_case(name, tmp_path)
        want = [
            (t["transaction_id"], t["sha256"], t["metadata_json"], p)
            for t in golden["transactions"]
            for p in t["posting_metadata_json"]
        ]
        have = [
            (tid, digest, relative(meta, root), relative(pmeta, root))
            for tid, digest, meta, pmeta in rows(
                con,
                "SELECT transaction_id, transaction_content_hash, transaction_metadata_json, posting_metadata_json"
                " FROM postings ORDER BY posting_id",
            )
        ]
        postings += len(have)
        if have != want:
            first = next(
                (i for i, (h, w) in enumerate(zip(have, want, strict=False)) if h != w), min(len(have), len(want))
            )
            failures.append(f"{name}: posting {first + 1}: {have[first : first + 1]} != {want[first : first + 1]}")
    assert failures == []
    assert len(GOLDENS["cases"]) == 12
    assert postings > 77  # 77 transactions, each with two or more postings


def test_the_directive_metadata_goldens(tmp_path: Path) -> None:
    golden = GOLDENS["cases"]["metadata-keys"]["directives"]
    assert {d["type"] for d in golden} == set(DIRECTIVE_TABLES) | NO_ROWS_YET
    con, root = build_case("metadata-keys", tmp_path)
    for kind, (table, column) in DIRECTIVE_TABLES.items():
        want = [d["metadata_json"] for d in golden if d["type"] == kind]
        have = [relative(r[0], root) for r in rows(con, f"SELECT {column} FROM {table} ORDER BY rowid")]  # noqa: S608
        assert have == want, kind
