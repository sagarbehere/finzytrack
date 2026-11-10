"""The mirror's per-table checksums over the corpus (BL-0057, S0096; helpers and rules in `tests/mirror/checksums.py`).

The snapshot itself (`corpora/ledgers/mirror-checksums.json`) is compared with a fresh generation by
`scripts/check_generated.py` (INV-19), so a change to any row of any table of any corpus ledger fails a gate. These
tests hold what makes that comparison mean something: every corpus root is in it and nothing else (claimed equals
found), every ledger file of the corpus is read by some root, every table has rows somewhere, the normalization keeps
the machine out and the content in, and the generator hashes as the engine does.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import tomllib
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from ledger_engine.ledger import fresh_load
from ledger_engine.mirror.core.rows import Row
from tests.ledger import parity
from tests.mirror import checksums
from tests.mirror.checksums import LEDGERS, NOT_READ, SNAPSHOT, Built, Root

pytestmark = pytest.mark.inv("INV-05")

GENERATOR = Path(__file__).resolve().parents[2] / "scripts" / "gen_mirror_checksums.py"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[Root, Built]:
    return checksums.build_all(tmp_path_factory.mktemp("corpus").resolve())


def committed() -> dict[str, dict[str, Any]]:
    return checksums.parse(SNAPSHOT.read_text(encoding="utf-8"))


# ---- the population: every root, every file, every table ----------------------------------------------------------


def test_the_snapshot_holds_every_corpus_root_and_nothing_else(built: dict[Root, Built]) -> None:
    found = sorted(r.case for r in built)
    assert found == checksums.inventory()
    assert sorted(committed()) == found
    assert len(found) == 52  # 12 + 6 parity, 19 write, 12 INV-21, the oracle's, 2 install roots (S0096 counts)


def test_the_inventory_comes_from_the_corpus_tables() -> None:
    """Each corpus table's rows are cases of the inventory, so a row added to a table must reach the snapshot."""
    names = set(checksums.inventory())
    for corpus in ("write", "parity", "inv21"):
        rows = tomllib.loads((LEDGERS / corpus / "cases.toml").read_text(encoding="utf-8"))
        assert {f"{corpus}/{n}" for n in rows} <= names, corpus


def test_the_generated_parity_cases_are_all_in_the_inventory(tmp_path: Path) -> None:
    generated = {f"parity/{c.name}" for c in parity.generated_cases(tmp_path)}
    assert generated == {n for n in checksums.inventory() if n.startswith("parity/")} - {
        f"parity/{n}" for n in parity.corpus_rows()
    }


def test_every_file_of_every_case_folder_is_read_by_some_root(built: dict[Root, Built]) -> None:
    assert checksums.unread_files(built) == []
    files = checksums.corpus_files()
    assert len(files) == 103  # S0096 round 1 count: every file of every case folder, whatever its name
    assert "ledgers/write/includes/years/2023-extra.bean" in files  # a ledger without a `.beancount` name
    # Each file excused as not read is a real file that no load reads (claimed equals found).
    read = checksums.read_files(built, files)
    assert {f for f in NOT_READ if f in files and f not in read} == set(NOT_READ)


def test_a_ledger_file_no_root_reads_is_found(built: dict[Root, Built]) -> None:
    """A file beside a loaded case's root that its load does not include is unread; one in a refused case's folder
    counts as read by the refusal (the load stops at the encrypted file)."""
    planted = ["ledgers/write/comments/forgotten.beancount", "ledgers/write/encrypted-gpg/forgotten.beancount"]
    assert checksums.unread_files(built, [*checksums.corpus_files(), *planted]) == planted[:1]


def test_every_case_has_every_table_and_every_table_has_rows_somewhere(built: dict[Root, Built]) -> None:
    loaded = [b.rows for b in built.values() if b.rows is not None]
    assert all(tuple(rows) == checksums.TABLE_NAMES for rows in loaded)
    assert checksums.empty_tables(built) == []


def test_a_table_with_no_rows_in_any_case_is_found(built: dict[Root, Built]) -> None:
    emptied = {r: Built(b.entry, {**b.rows, "stored_queries": []}, b.read) if b.rows else b for r, b in built.items()}
    assert checksums.empty_tables(emptied) == ["stored_queries"]


def test_exactly_the_refuse_cases_are_refused_each_with_a_file_beancount_would_decrypt(
    built: dict[Root, Built],
) -> None:
    refused = {r: b.entry for r, b in built.items() if b.rows is None}
    want = {
        f"{corpus}/{name}"
        for corpus in ("write", "parity")
        for name, row in tomllib.loads((LEDGERS / corpus / "cases.toml").read_text(encoding="utf-8")).items()
        if row["expect"] == "refuse"
    }
    assert {r.case for r in refused} == want
    assert len(want) == 6
    for r, entry in refused.items():
        assert entry["refused"] == "encrypted", r.case
        assert parity.beancount_decrypts(r.folder / entry["file"]), r.case
        if r.refused is not None:  # the parity corpus names its refused file
            assert r.folder / entry["file"] == r.refused, r.case


# ---- normalization ------------------------------------------------------------------------------------------------


FOLDER = Path("/w/case-a")


def tables_with(postings: list[Row], options: list[Row] | None = None) -> dict[str, list[Row]]:
    return {"postings": postings, "ledger_options": options or [], "notes": [(1, "2020-01-01", "x")]}


def test_the_case_folder_becomes_a_placeholder_in_every_text_value() -> None:
    made = tables_with(
        [(1, '{"filename": "/w/case-a/main.beancount", "lineno": 3}', "/w/case-a/docs/a.pdf", "/w/other/b.pdf", 7)],
        [("filename", '"/w/case-a/main.beancount"'), ("input_hash", '"an md5 of names and times"')],
    )
    out = checksums.normalized(made, FOLDER)
    assert out["postings"] == [
        [1, '{"filename": "<case>/main.beancount", "lineno": 3}', "<case>/docs/a.pdf", "/w/other/b.pdf", 7]
    ]
    assert out["ledger_options"] == [["filename", '"<case>/main.beancount"'], ["input_hash", '"<input_hash>"']]


def test_the_same_rows_in_another_order_or_folder_give_the_same_checksums() -> None:
    a = checksums.normalized(tables_with([(1, "/w/case-a/x"), (2, "y")]), FOLDER)
    b = checksums.normalized(tables_with([(2, "y"), (1, "/w/case-b/x")]), Path("/w/case-b"))
    assert {k: checksums.digest(v) for k, v in a.items()} == {k: checksums.digest(v) for k, v in b.items()}


BASE: list[Row] = [(1, "/w/case-a/x"), (2, "y"), (3, 3)]


@pytest.mark.parametrize(
    "changed",
    [
        [(1, "/w/case-a/x"), (2, "z"), (3, 3)],  # a value
        [(1, "/w/case-a/x"), (4, "y"), (3, 3)],  # an id
        [(1, "/w/case-a/x"), (3, 3)],  # a row dropped
        [(1, "/w/case-a/x"), (2, "y"), (2, "y"), (3, 3)],  # a row repeated
        [(1, "/w/case-a/x"), (2, None), (3, 3)],  # a NULL
        [(1, "/w/case-a/x"), (2, "y"), (3, "3")],  # text for a number
        [(1, "/w/case-a/y"), (2, "y"), (3, 3)],  # a path inside the case
    ],
)
def test_a_change_to_one_table_changes_its_checksum_and_no_other(changed: list[Row]) -> None:
    base = checksums.normalized(tables_with(BASE), FOLDER)
    other = checksums.normalized(tables_with(changed), FOLDER)
    differ = [k for k in base if checksums.digest(base[k]) != checksums.digest(other[k])]
    assert differ == ["postings"]


def test_a_rename_of_the_case_folder_inside_a_longer_name_is_not_touched() -> None:
    out = checksums.normalized(tables_with([(1, "/w/case-ab/x")]), FOLDER)
    assert out["postings"] == [[1, "/w/case-ab/x"]]


# ---- the listing order (BL-0234) ----------------------------------------------------------------------------------


@contextmanager
def file_system_order(order: Callable[[list[str]], list[str]]) -> Generator[None]:
    """A file system that lists files in `order` (every `glob.glob` answer reordered)."""
    listed = glob.glob

    def reordered(*args: Any, **kwargs: Any) -> list[str]:
        return order(listed(*args, **kwargs))

    setattr(glob, "glob", reordered)  # noqa: B010
    try:
        yield
    finally:
        setattr(glob, "glob", listed)  # noqa: B010


def ascending(names: list[str]) -> list[str]:
    return sorted(names)


def descending(names: list[str]) -> list[str]:
    return sorted(names, reverse=True)


def test_the_snapshot_is_the_same_whatever_order_the_file_system_lists_files_in(tmp_path: Path) -> None:
    snaps: list[dict[str, dict[str, Any]]] = []
    for order in (ascending, descending):
        with file_system_order(order):
            snaps.append(checksums.snapshot(checksums.build_all((tmp_path / order.__name__).resolve())))
    assert snaps[0] == snaps[1]


def test_without_the_sorted_listing_the_order_shows_in_the_rows(tmp_path: Path) -> None:
    """The near miss: the corpus is sensitive to the listing order (`parity/includes` error ids, BL-0234), so the
    test above would fail without `sorted_listing`."""
    snaps: list[dict[str, dict[str, Any]]] = []
    for order in (ascending, descending):
        with file_system_order(order):
            built = checksums.build_all((tmp_path / order.__name__).resolve(), listing=checksums.as_listed)
            snaps.append(checksums.snapshot(built))
    differ = sorted(
        f"{case}.{table}"
        for case, entry in snaps[0].items()
        for table, value in entry.get("tables", {}).items()
        if snaps[1][case]["tables"][table] != value
    )
    assert "parity/includes.ledger_errors" in differ


# ---- the generator ------------------------------------------------------------------------------------------------


def test_the_generator_hashes_as_the_engine_does_whatever_seed_it_is_started_with() -> None:
    """Beancount's `onecommodity`, `close_tree` and `commodity_attr` plugins word their errors and order their
    entries by set order (`parity/plugins-builtin`: seeds 1 and 2 give different `ledger_errors`). The engine runs
    with `PYTHONHASHSEED=0` (`app.platform.adapters.engine_client`; `fresh_load.environment` for a load it starts), so
    the generator does too, even under `-I`: its rows started with seeds 1 and 2 equal the rows built directly in a
    process with `fresh_load.environment()`."""
    repo = Path(__file__).resolve().parents[3]

    def rows(seed: str) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")} | {"PYTHONHASHSEED": seed}
        cmd = [sys.executable, str(GENERATOR), "--rows", "parity/plugins-builtin", "ledger_errors"]
        return subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True, text=True).stdout  # noqa: S603 (this interpreter, the generator)

    # The truth: the same rows built directly in a process with the environment the engine gives a load it starts.
    direct = (
        "import json, sys, tempfile; from pathlib import Path; "
        f"sys.path[:0] = [{str(GENERATOR.parents[1] / 'src')!r}, {str(GENERATOR.parents[1])!r}]; "
        "from tests.mirror import checksums; "
        "rows = dict(checksums.iter_rows(Path(tempfile.mkdtemp()).resolve(), 'parity/plugins-builtin')); "
        "[print(json.dumps(r, ensure_ascii=False)) for r in rows['ledger_errors']]"
    )
    cmd = [sys.executable, "-s", "-P", "-c", direct]
    engine = subprocess.run(cmd, cwd=repo, env=fresh_load.environment(), check=True, capture_output=True, text=True)  # noqa: S603
    assert rows("1") == rows("2") == engine.stdout
    assert "More than one currency" in engine.stdout
