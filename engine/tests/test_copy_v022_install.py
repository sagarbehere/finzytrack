"""A v0.2.2 install's ledgers through Copy keep their INV-21 goldens and their bytes (S0095, BL-0021; ADR-0062: Copy is
the only way a legacy ledger comes into a v0.4 workspace; ADR-0026, INV-21).

The install fixture (`corpora/workspaces/legacy/v022-fresh/`, made by v0.2.2's own seed code) is copied to a temp
folder, and each ledger root it holds is brought in with the `copy` op, naming the install's `data/.write.lock` as its
legacy lock, as Copy will for an install whose config names those files (BL-0026). Then:
- the copy's mirror gives every posting the TransactionId, content hash and metadata JSON v0.2.2's code gave the
  source (`corpora/workspaces/legacy/goldens.json`), file names mapped from the copy's folder back to the
  root's folder in the install (Copy keeps a file under the root's folder at its relative path);
- every ledger file in the copy is byte for byte its source: none of these ledgers names anything outside its folder,
  so Copy has nothing to rewrite, and no key is removed, renamed or changed (INV-21 (b), (c));
- the install is byte-identical afterwards: its empty `.write.lock` (v0.2.2's wizard leaves one) is tried, held and
  released, never written; with the lock file removed, none is created (INV-26).
The same goldens opened in place, and the seeded changes they catch, are `tests/mirror/test_inv21.py`.
"""

from __future__ import annotations

import posixpath
import sys
from pathlib import Path

import pytest

from tests.mirror.support import build_loaded, load_root
from tests.mirror.test_inv21 import INSTALL_GOLDENS, copy_install, first_difference, golden_postings, snapshot
from tests.mirror.test_inv21 import mirror_postings as copy_postings
from tests.test_copy_ops import copy, params

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's port is macOS-only until BL-0087"),
    pytest.mark.inv("INV-21"),
    pytest.mark.inv("INV-26"),
]

GOLDEN = INSTALL_GOLDENS["v022-fresh"]


@pytest.fixture
def ald(tmp_path: Path) -> Path:
    """The app-local data folder the engine's ops get (as in `tests/test_copy_ops.py`)."""
    out = tmp_path / "app"
    (out / "locks").mkdir(parents=True)
    return out


def manifest(root_name: str) -> list[dict[str, str]]:
    text = (
        '{"format": 1, "min_reader": 1, "lineage_id": "4b7d2e10-3c5a-4f6b-8e9d-0a1b2c3d4e5f",'
        f' "ledger": {{"root": "{root_name}"}}}}\n'
    )
    return [{"path": "workspace.json", "text": text}]


def to_install(rows: list[tuple[object, ...]], base: str) -> list[tuple[object, ...]]:
    """The copy's rows with each `filename` (already relative to the copy's folder) put back under the root's folder
    in the install (`base`), the goldens' form."""

    def back(text: object) -> object:
        if not isinstance(text, str):
            return text
        return text.replace('"filename": "', f'"filename": "{base}/', 1)

    return [(tid, digest, back(meta), back(pmeta)) for tid, digest, meta, pmeta in rows]


@pytest.mark.parametrize("lock_file", ["as the wizard left it", "removed"])
@pytest.mark.parametrize("rel", sorted(GOLDEN["roots"]))
def test_a_copy_of_each_v022_root_keeps_its_goldens_and_its_bytes(
    ald: Path, tmp_path: Path, rel: str, lock_file: str
) -> None:
    install = copy_install(tmp_path)
    lock = install / "data" / ".write.lock"
    assert lock.read_bytes() == b"", "the fixture holds v0.2.2's empty lock file (S0095 verifier F1)"
    if lock_file == "removed":
        lock.unlink()
    before = snapshot(install)
    base = posixpath.dirname(rel)
    target = tmp_path / "Books"
    p = params(target, install / rel, ald, [str(lock)])
    p["manifest"] = manifest(posixpath.basename(rel))
    reply, _ = copy(ald, p)
    assert reply["ok"], reply
    assert (reply["result"]["outcome"], reply["result"]["reason"]) == ("created", None), reply

    copied = {
        q.relative_to(target).as_posix(): q.read_bytes()
        for q in sorted(target.rglob("*"))
        if q.is_file() and ".finzytrack" not in q.relative_to(target).parts
    }
    source = {q.relative_to(install / base).as_posix(): q.read_bytes() for q in (install / base).rglob("*.beancount")}
    parsed = {posixpath.relpath(f, str(install / base)) for f in load_root(install / rel).options["include"]}
    assert set(copied) == parsed, "the copy holds exactly the ledger files the root loads"
    assert all(copied[name] == source[name] for name in copied), "every copied ledger file is its source's bytes"

    loaded = load_root(target / posixpath.basename(rel))
    assert loaded.errors == []
    con = build_loaded(loaded, tmp_path / "copy.db")
    have = to_install(copy_postings(con, str(target)), base)
    assert first_difference(rel, have, golden_postings(GOLDEN["roots"][rel])) == []

    assert snapshot(install) == before, "Copy only reads the install"
    assert lock.exists() == (lock_file != "removed"), "INV-26: the legacy lock is never created"
