"""The mirror passes the INV-21 goldens (BL-0021; spec design/specs/mirror/inv21-goldens.md, README "Ids" and
"Metadata JSON"; ADR-0026, ADR-0048, ADR-0055).

`corpora/ledgers/inv21/goldens.json` holds, per case, every transaction in Beancount's order with its v0.4
TransactionId, its content hash (`sha256`, the `transaction_content_hash` column) and its metadata JSON (transaction
and each posting), taken from v0.2.2's own code and re-derived from the spec (S0022). Each case is copied to a temp
folder, loaded with the overlay load and built; the mirror's `postings` rows, posting by posting, must equal the
goldens. The goldens store `filename` relative to the case folder (their one normalization); the mirror stores the
absolute path, so the test makes it relative the same way before comparing. The `metadata-keys` case also pins every
other directive's metadata JSON; those with a table are compared with it.

The v0.2.2 install fixture (`corpora/workspaces/legacy/`, S0095): the folder a v0.2.2 install holds after its setup
wizard, made by v0.2.2's own seed code, with goldens from v0.2.2's code for each ledger root it holds. Opened in place
(the overlay load and the build of each root, as v0.4 opens a ledger), every posting and every directive with a
metadata column must equal them, and the install must be byte-identical afterwards. Seeded changes to the install's
ledger (an id, a hash input, a key renamed or dropped) must each be caught. The same goldens through Copy are
`tests/test_copy_v022_install.py` (macOS-only, as the copy's port).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from .support import build_loaded, load_root, rows

pytestmark = pytest.mark.inv("INV-21")

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "corpora" / "ledgers" / "inv21"
GOLDENS: dict[str, Any] = json.loads((CORPUS / "goldens.json").read_text(encoding="utf-8"))
INSTALLS = REPO / "corpora" / "workspaces" / "legacy"
INSTALL_GOLDENS: dict[str, Any] = json.loads((INSTALLS / "goldens.json").read_text(encoding="utf-8"))["installs"]
# The directive types the metadata-keys case pins, the table and column that hold their metadata, and the order that
# matches the goldens' (file order). `commodities` is sorted by code and has rows for used, never declared codes (NULL
# metadata), so its declared rows are read in line order. `Close` has no table in the surface.
DIRECTIVE_TABLES = {
    "Open": ("m_accounts", "metadata_json", "rowid"),
    "Commodity": ("m_commodities", "metadata_json", "json_extract(metadata_json, '$.lineno')"),
}
NO_TABLE = {"Close"}
# The install's ledgers hold these directive types too; each table's rows are in Beancount's entry order.
INSTALL_TABLES = DIRECTIVE_TABLES | {
    "Balance": ("m_balance_assertions", "metadata_json", "rowid"),
    "Pad": ("m_pad_directives", "metadata_json", "rowid"),
    "Custom": ("m_custom_directives", "metadata_json", "rowid"),
}
POSTINGS = (
    "SELECT transaction_id, transaction_content_hash, transaction_metadata_json, posting_metadata_json"
    " FROM postings ORDER BY posting_id"
)


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


def as_text(value: Any) -> Any:
    """A golden metadata JSON as its text: the install goldens hold it parsed (one key per line for the privacy scan;
    `scripts/oracle/gen_inv21_goldens.py` checks that `json.dumps` gives back the text)."""
    return value if value is None or isinstance(value, str) else json.dumps(value)


def golden_postings(golden: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [
        (t["transaction_id"], t["sha256"], as_text(t["metadata_json"]), as_text(p))
        for t in golden["transactions"]
        for p in t["posting_metadata_json"]
    ]


def mirror_postings(con: sqlite3.Connection, root: str) -> list[tuple[Any, ...]]:
    return [
        (tid, digest, relative(meta, root), relative(pmeta, root)) for tid, digest, meta, pmeta in rows(con, POSTINGS)
    ]


def first_difference(name: str, have: list[Any], want: list[Any]) -> list[str]:
    if have == want:
        return []
    first = next((i for i, (h, w) in enumerate(zip(have, want, strict=False)) if h != w), min(len(have), len(want)))
    return [f"{name}: row {first + 1}: {have[first : first + 1]} != {want[first : first + 1]}"]


def test_every_case_passes_its_goldens(tmp_path: Path) -> None:
    failures: list[str] = []
    postings = 0
    for name, golden in sorted(GOLDENS["cases"].items()):
        con, root = build_case(name, tmp_path)
        have = mirror_postings(con, root)
        postings += len(have)
        failures += first_difference(name, have, golden_postings(golden))
    assert failures == []
    assert len(GOLDENS["cases"]) == 12
    assert postings > 77  # 77 transactions, each with two or more postings


def test_the_directive_metadata_goldens(tmp_path: Path) -> None:
    golden = GOLDENS["cases"]["metadata-keys"]["directives"]
    assert {d["type"] for d in golden} == set(DIRECTIVE_TABLES) | NO_TABLE
    con, root = build_case("metadata-keys", tmp_path)
    for kind, (table, column, order) in DIRECTIVE_TABLES.items():
        want = [d["metadata_json"] for d in golden if d["type"] == kind]
        sql = f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL ORDER BY {order}"  # noqa: S608
        have = [relative(r[0], root) for r in rows(con, sql)]
        assert have == want, kind


# ---- the v0.2.2 install fixture (S0095) ----------------------------------------------------------------------------


def snapshot(folder: Path) -> dict[str, str]:
    """Every entry under `folder`: a file's SHA-256, a folder's marker."""
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
        for p in sorted(folder.rglob("*"))
    }


def install_failures(install: Path, golden: dict[str, Any], out: Path) -> list[str]:
    """Each ledger root of `install`, opened in place (the overlay load, then the build into `out`), against its
    goldens: every posting's TransactionId, content hash and metadata JSON, and every directive's metadata JSON."""
    failures: list[str] = []
    root = str(install)
    for rel, want in sorted(golden["roots"].items()):
        loaded = load_root(install / rel)
        failures += [f"{rel}: load error {e}" for e in loaded.errors]
        con = build_loaded(loaded, out / (rel.replace("/", "_") + ".db"))
        failures += first_difference(rel, mirror_postings(con, root), golden_postings(want))
        kinds = {d["type"] for d in want["directives"]}
        failures += [f"{rel}: no table holds the metadata of {k}" for k in sorted(kinds - set(INSTALL_TABLES))]
        for kind, (table, column, order) in INSTALL_TABLES.items():
            expected = [as_text(d["metadata_json"]) for d in want["directives"] if d["type"] == kind]
            sql = f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL ORDER BY {order}"  # noqa: S608
            failures += first_difference(f"{rel} {kind}", [relative(r[0], root) for r in rows(con, sql)], expected)
        con.close()
    return failures


def copy_install(tmp_path: Path, name: str = "v022-fresh") -> Path:
    """The fixture copied to `tmp_path` as the install it is: the repository ignores every folder named `data`
    (INV-23), so the fixture stores the install's `data/` as `_data/` (`scripts/oracle/gen_v022_install.py`)."""
    install = tmp_path / name
    shutil.copytree(INSTALLS / name, install)
    (install / "_data").rename(install / "data")
    (tmp_path / "out").mkdir()
    return install


def test_a_v022_install_opened_in_place_passes_its_goldens_and_stays_untouched(tmp_path: Path) -> None:
    """@regression-guard (S0095): with the fixture present it holds on the base 6f374bab (the build has passed the
    goldens since S0050); it fails there without the fixture. It pins the build against a whole v0.2.2 install."""
    assert sorted(INSTALL_GOLDENS) == ["v022-fresh"]
    install = copy_install(tmp_path)
    before = snapshot(install)
    golden = INSTALL_GOLDENS["v022-fresh"]
    assert golden["configured_root"] in golden["roots"]
    assert install_failures(install, golden, tmp_path / "out") == []
    assert snapshot(install) == before, "opening a v0.2.2 install writes nothing there (INV-21 (b), INV-30)"
    assert (install / "data" / ".write.lock").read_bytes() == b"", "v0.2.2's empty lock file, left as it is"
    # Not vacuous: the roots hold transactions with v0.2.2's ids and keys, and directives with metadata.
    postings = sum(len(golden_postings(r)) for r in golden["roots"].values())
    assert postings == 46
    assert sum(len(r["directives"]) for r in golden["roots"].values()) == 50


JAN = "data/ledgers/fake-multi/2026/jan.beancount"
FIRST_ID = '  id: "019fa1ed-2026-7001-8000-000000000001"\n'
FIRST_SOURCE = (
    FIRST_ID
    + '  content_hash: "11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc"\n'
    + ('  source_account: "Assets:Bank:Checking"\n')
)
# Seeded changes to the install's ledger (EP-29): each changes what INV-21 keeps (the id, a hash input, a key), so the
# goldens must catch it. Each is (file, old text, new text), the old text found exactly once.
SEEDS: dict[str, tuple[str, str, str]] = {
    "an id changed": (JAN, FIRST_ID, FIRST_ID.replace("0001", "0009")),
    "the hash input's source account": (JAN, FIRST_SOURCE, FIRST_SOURCE.replace("Checking", "Savings")),
    "the hash input's amount": (
        JAN,
        "  Assets:Bank:Checking   5000.00 USD\n  Income:Salary         -5000.00 USD\n",
        "  Assets:Bank:Checking   5000.01 USD\n  Income:Salary         -5000.01 USD\n",
    ),
    "a key renamed": (JAN, FIRST_SOURCE, FIRST_SOURCE.replace("source_account", "source-account")),
    "a key dropped": (JAN, '  content_hash: "11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc11cc"\n', ""),
    "a key added": (JAN, FIRST_ID, FIRST_ID + '  stmt_ref: "x"\n'),
    "a key added on an open": (
        "data/ledgers/one.beancount",
        "1970-01-01 open Assets:Cash                EUR\n",
        '1970-01-01 open Assets:Cash                EUR\n  account_number: "1"\n',
    ),
}


def test_every_seed_is_run() -> None:
    # The parametrize list is a literal (an INV-21 enforcement file may not build it: scripts/check_invariants.py).
    marks: list[pytest.Mark] = vars(test_each_seeded_change_to_the_install_is_caught)["pytestmark"]
    assert [sorted(m.args[1]) for m in marks if m.name == "parametrize"] == [sorted(SEEDS)]


def seeded(path: Path, old: str, new: str) -> Callable[[], None]:
    def apply() -> None:
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, old
        path.write_text(text.replace(old, new), encoding="utf-8")

    return apply


@pytest.mark.parametrize(
    "seed",
    [
        "a key added",
        "a key added on an open",
        "a key dropped",
        "a key renamed",
        "an id changed",
        "the hash input's amount",
        "the hash input's source account",
    ],
)
def test_each_seeded_change_to_the_install_is_caught(tmp_path: Path, seed: str) -> None:
    """The must-not-pass side of the test above (EP-29)."""
    rel, old, new = SEEDS[seed]
    install = copy_install(tmp_path)
    seeded(install / rel, old, new)()
    assert install_failures(install, INSTALL_GOLDENS["v022-fresh"], tmp_path / "out") != []
