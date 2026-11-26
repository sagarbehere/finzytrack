"""The parity comparison as counts (`parity.differences`, S0158) and Beancount's load without its pickle cache
(`parity.beancount_load`), which the local tier's D2 runner (`engine/scripts/d2_local.py`) uses.

`differences` must be empty exactly when `same` passes: each planted divergence is counted under its kind and fails
`same` too. `beancount_load` gives `load_file`'s result, writes nothing beside the ledger (no `.picklecache`, none
read, none deleted) and never decrypts.
"""

from __future__ import annotations

import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from ledger_engine.ledger import overlay
from ledger_engine.ledger.overlay import Loaded, LoadRefused
from ledger_engine.ledger.policy import DESKTOP
from tests.ledger import parity

pytestmark = pytest.mark.inv("INV-03")


def copy(case: str, tmp_path: Path) -> Path:
    shutil.copytree(parity.CORPUS / case, tmp_path / case, ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path / case / "main.beancount"


def pair(tmp_path: Path) -> tuple[Loaded, parity.Result]:
    root = copy("documents", tmp_path)  # entries and one error
    result = overlay.load(str(root), {}, DESKTOP)
    assert isinstance(result, Loaded)
    return result, parity.beancount_load(root)


def test_equal_loads_have_no_difference(tmp_path: Path) -> None:
    result, expected = pair(tmp_path)
    assert len(expected[0]) > 10
    assert parity.differences(result, expected) == Counter()
    parity.same(result, expected)


PLANTS: dict[str, tuple[Callable[[Loaded], Loaded | LoadRefused], dict[str, int]]] = {
    "an entry dropped": (lambda r: replace(r, entries=r.entries[:-1]), {"entries": 1}),
    "two entries swapped": (lambda r: replace(r, entries=[r.entries[1], r.entries[0], *r.entries[2:]]), {"entries": 2}),
    "an entry changed": (
        lambda r: replace(r, entries=[r.entries[0]._replace(meta={}), *r.entries[1:]]),
        {"entries": 1},
    ),
    "an error added": (lambda r: replace(r, errors=[*r.errors, r.errors[0]]), {"errors": 1}),
    "an error dropped": (lambda r: replace(r, errors=r.errors[1:]), {"errors": 1}),
    "an option changed": (lambda r: replace(r, options={**r.options, "title": "other"}), {"options": 1}),
    "an option missing": (
        lambda r: replace(r, options={k: v for k, v in r.options.items() if k != "title"}),
        {"options": 1},
    ),
    "an option added": (lambda r: replace(r, options={**r.options, "extra": 1}), {"options": 1}),
    "the display context changed": (
        lambda r: replace(r, options={**r.options, "dcontext": "another display context"}),
        {"options": 1},
    ),
    "the input hash changed": (lambda r: replace(r, options={**r.options, "input_hash": "0"}), {"options": 1}),
    "the load refused": (lambda r: LoadRefused("read-refused", "x"), {"refused": 1}),
}


def test_each_planted_difference_is_counted_and_fails_same(tmp_path: Path) -> None:
    result, expected = pair(tmp_path)
    assert result.errors, "the case must report an error for the error plants"
    failures: list[str] = []
    for name, (plant, counts) in PLANTS.items():
        planted = plant(result)
        got = parity.differences(planted, expected)
        if got != Counter(counts):
            failures.append(f"{name}: {dict(got)} != {counts}")
        try:
            parity.same(planted, expected)
        except AssertionError:
            continue
        failures.append(f"{name}: same passed")
    assert failures == []


def test_several_kinds_are_counted_together(tmp_path: Path) -> None:
    result, expected = pair(tmp_path)
    planted = replace(result, entries=result.entries[1:], options={**result.options, "title": "other"})
    # Every entry moves up one place: each position whose neighbour differs counts, and the missing last one.
    moved = sum(1 for a, b in zip(expected[0][1:], expected[0], strict=False) if a != b)
    assert parity.differences(planted, expected) == Counter(entries=moved + 1, options=1)


def test_the_input_hash_is_left_out_only_when_the_files_were_written(tmp_path: Path) -> None:
    result, expected = pair(tmp_path)
    planted = replace(result, options={**result.options, "input_hash": "0"})
    assert parity.differences(planted, expected) == Counter(options=1)
    assert parity.differences(planted, expected, written_after=True) == Counter()
    parity.same(planted, expected, written_after=True)


def test_beancount_load_is_load_file_without_the_pickle_cache(tmp_path: Path) -> None:
    """The same copy, at the same path, loaded both ways (copied afresh in between: `load_file` may leave a cache)."""
    for case in ("fava-features", "plugins-builtin", "includes", "plugins-local"):
        root = copy(case, tmp_path)
        with parity.fresh_plugins(root.parent):
            entries, errors, options = parity.load_file(root)
        shutil.rmtree(root.parent)
        root = copy(case, tmp_path)
        with parity.fresh_plugins(root.parent):
            ours_entries, ours_errors, ours_options = parity.beancount_load(root)
        shutil.rmtree(root.parent)
        assert ours_entries == entries, case
        assert [(type(e).__name__, e.source, e.message) for e in ours_errors] == [
            (type(e).__name__, e.source, e.message) for e in errors
        ], case
        del options["input_hash"], ours_options["input_hash"]  # a stat of the files, which were copied again
        assert {k: str(v) for k, v in ours_options.items()} == {k: str(v) for k, v in options.items()}, case


def test_beancount_load_reads_writes_and_deletes_no_pickle_cache(tmp_path: Path) -> None:
    root = copy("fava-features", tmp_path)
    cache = root.parent / f".{root.name}.picklecache"
    cache.write_bytes(b"not a pickle: Beancount's cache step would read it, and remove it when switched off")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    entries, _, _ = parity.beancount_load(root)
    assert entries
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_dir()) == ["fava-features"]


def test_beancount_load_never_decrypts(tmp_path: Path) -> None:
    shutil.copytree(parity.CORPUS / "encrypted-root-gpg", tmp_path / "c")
    with pytest.raises(parity.DecryptRefused):
        parity.beancount_load(tmp_path / "c" / "main.beancount.gpg")
