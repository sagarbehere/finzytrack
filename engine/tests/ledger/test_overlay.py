"""The overlay load and its LoadPolicy (ADR-0010; ADR-0059; ARCHITECTURE.md §2.2, §2.4 rule 4; S0049).

Guardrail 1 on the D1 write corpus (`corpora/ledgers/write/`, spec design/specs/write-path/write-corpus.md): the
replica with an empty overlay, and with every file's disk bytes as candidates, equals Beancount's own `load_file`;
a candidate equals `load_file` after the same bytes are written. Guardrail 2: the Beancount version and the source
of the mirrored loader functions are pinned, so an upgrade fails here until the replica is re-mirrored. Guardrail 4
and ADR-0059: encrypted files are refused, never decrypted or partly loaded. The policy seam (ADR-0010; WS-ISOLATION
L11-02, L11-06, L11.M-1): every file read, user plugin, `pythonpath` directory and documents root is put to the
policy before any plugin runs, and a refusal refuses the whole load. Each case is loaded from a temp copy (INV-24).
"""

from __future__ import annotations

import ast
import hashlib
import shutil
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest
from beancount import loader
from beancount.utils import encryption

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger.overlay import Loaded, LoadRefused, load
from ledger_engine.ledger.policy import DESKTOP

pytestmark = pytest.mark.inv("INV-02")

CORPUS = Path(__file__).resolve().parents[3] / "corpora" / "ledgers" / "write"
CASES: dict[str, Any] = tomllib.loads((CORPUS / "cases.toml").read_text(encoding="utf-8"))
LOAD_CASES = sorted(k for k, v in CASES.items() if v["expect"] == "load")
REFUSE_CASES = sorted(k for k, v in CASES.items() if v["expect"] == "refuse")

# Guardrail 2: the replica mirrors these functions of Beancount 3.2.0's `beancount/loader.py`. A change to either
# fails this pin; re-mirror `overlay.py` first, then update the hashes (ADR-0004, ADR-0010).
MIRRORED = {
    "_parse_recursive": {"sha256": "5abcf9a2e28b6ad08a03133b20570c8e93f1d09cbcf8bc79c5f4a693a7847d1a"},
    "_load": {"sha256": "ebfa8865cb54078b607113cb2474eac221a57e2d1e8e27400ed8d89cb75dbf6b"},
}


def copy_case(name: str, tmp_path: Path) -> Path:
    dest = tmp_path / name
    shutil.copytree(CORPUS / name, dest)
    return dest / "main.beancount"


def same(
    result: Loaded | LoadRefused,
    expected: tuple[list[Any], list[Any], dict[str, Any]],
    *,
    written_after: bool = False,
) -> None:
    """`written_after`: the files changed between the two loads, so `input_hash` (a stat of the files on disk, which
    only Beancount's pickle cache reads) differs by design and is left out."""
    assert isinstance(result, Loaded), result
    entries, errors, options = expected
    if written_after:
        options = {k: v for k, v in options.items() if k != "input_hash"}
        result = Loaded(result.entries, result.errors, {k: v for k, v in result.options.items() if k != "input_hash"})
    assert result.entries == entries
    assert [(type(e).__name__, e.source, e.message) for e in result.errors] == [
        (type(e).__name__, e.source, e.message) for e in errors
    ]
    assert set(result.options) == set(options)
    for key, value in options.items():
        if key == "dcontext":
            assert str(result.options[key]) == str(value)
        else:
            assert result.options[key] == value, key


def load_file(root: Path) -> tuple[list[Any], list[Any], dict[str, Any]]:
    entries, errors, options = loader.load_file(str(root))
    return list(entries), list(errors), dict(options)


# ---- guardrail 1: equivalence with Beancount's loader ------------------------------------------------------------


def test_the_corpus_has_load_and_refuse_cases() -> None:
    assert len(LOAD_CASES) >= 15
    assert REFUSE_CASES == ["encrypted-asc", "encrypted-gpg"]


def every_case(check: Callable[[str, Path], None], tmp_path_factory: pytest.TempPathFactory) -> None:
    """Each `load` case, each in its own temp copy; a failure names the case (no parametrize, so no case can be
    switched off by an empty list, scripts/check_invariants.py)."""
    failures: list[str] = []
    for case in LOAD_CASES:
        try:
            check(case, tmp_path_factory.mktemp(case))
        except AssertionError as e:
            failures.append(f"{case}: {e}")
    assert failures == []


def empty_overlay(case: str, tmp_path: Path) -> None:
    root = copy_case(case, tmp_path)
    same(load(str(root), {}, DESKTOP), load_file(root))


def disk_bytes_as_candidates(case: str, tmp_path: Path) -> None:
    root = copy_case(case, tmp_path)
    expected = load_file(root)
    files = expected[2]["include"]
    assert files, case
    # Every file as a candidate, except a `.asc` one: a candidate for it is refused (guardrail 4, tested below).
    candidates = {f: Path(f).read_bytes() for f in files if not f.endswith(".asc")}
    same(load(str(root), candidates, DESKTOP), expected)


def test_an_empty_overlay_equals_load_file(tmp_path_factory: pytest.TempPathFactory) -> None:
    every_case(empty_overlay, tmp_path_factory)


def test_the_disk_bytes_as_candidates_equal_load_file(tmp_path_factory: pytest.TempPathFactory) -> None:
    every_case(disk_bytes_as_candidates, tmp_path_factory)


def test_a_candidate_equals_load_file_after_the_same_bytes_are_written(tmp_path: Path) -> None:
    root = copy_case("insert-entry", tmp_path)
    food = root.parent / "food.beancount"
    new = food.read_bytes() + b'2024-01-07 * "Shop" "candidate"\n  Expenses:Food  2.25 USD\n  Assets:Cash  -2.25 USD\n'
    predicted = load(str(root), {str(food): new}, DESKTOP)
    assert isinstance(predicted, Loaded)
    assert any(isinstance(e, port.Transaction) and e.narration == "candidate" for e in predicted.entries)
    food.write_bytes(new)
    same(predicted, load_file(root), written_after=True)


def test_a_candidate_for_a_new_file_named_by_a_plain_include_is_loaded(tmp_path: Path) -> None:
    root = tmp_path / "main.beancount"
    root.write_bytes(b'include "new.beancount"\n')
    new = tmp_path / "new.beancount"
    text = b"2020-01-01 open Assets:Cash USD\n"
    predicted = load(str(root), {str(new): text}, DESKTOP)
    new.write_bytes(text)
    same(predicted, load_file(root), written_after=True)


def test_a_candidate_for_the_root_that_is_not_on_disk_is_loaded(tmp_path: Path) -> None:
    root = tmp_path / "main.beancount"
    text = b"2020-01-01 open Assets:Cash USD\n"
    predicted = load(str(root), {str(root): text}, DESKTOP)
    root.write_bytes(text)
    same(predicted, load_file(root), written_after=True)


# ---- refusals ----------------------------------------------------------------------------------------------------


@pytest.fixture
def no_decryption(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> str:
        pytest.fail("the load tried to decrypt a file")

    monkeypatch.setattr(encryption, "read_encrypted_file", refuse)


@pytest.mark.usefixtures("no_decryption")
@pytest.mark.parametrize(
    ("case", "secret"), [("encrypted-gpg", "secret.beancount.gpg"), ("encrypted-asc", "secret.beancount.asc")]
)
def test_an_encrypted_include_refuses_the_whole_load(case: str, secret: str, tmp_path: Path) -> None:
    root = copy_case(case, tmp_path)
    assert load(str(root), {}, DESKTOP) == LoadRefused("encrypted", str(root.parent / secret))


@pytest.mark.usefixtures("no_decryption")
def test_an_encrypted_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "ledger.beancount.gpg"
    root.write_bytes(b"\x85\x01\x0c\x03not really gpg")
    assert load(str(root), {}, DESKTOP) == LoadRefused("encrypted", str(root))


@pytest.mark.parametrize("name", ["notes.beancount.asc", "notes.beancount.gpg", "notes.beancount.GPG"])
def test_a_candidate_for_a_file_beancount_may_decrypt_is_refused(name: str, tmp_path: Path) -> None:
    # Guardrail 4: writes to encrypted files are refused. After the write, Beancount would judge the file by its
    # name and new bytes; any `.gpg` or `.asc` candidate is refused, the plain `.asc` of asc-plain included.
    root = copy_case("asc-plain", tmp_path)
    target = root.parent / name
    root.write_bytes(root.read_bytes() + f'include "{name}"\n'.encode())
    assert load(str(root), {str(target): b"2020-01-01 open Assets:Cash USD\n"}, DESKTOP) == LoadRefused(
        "encrypted", str(target)
    )


def test_a_candidate_the_load_never_reaches_is_refused(tmp_path: Path) -> None:
    root = copy_case("comments", tmp_path)
    stray = root.parent / "stray.beancount"
    assert load(str(root), {str(stray): b"2020-01-01 open Assets:Cash USD\n"}, DESKTOP) == LoadRefused(
        "not-reached", str(stray)
    )


def test_a_new_file_reached_only_by_a_glob_is_not_reached(tmp_path: Path) -> None:
    # The replica globs the disk as Beancount does; a candidate for a file that does not exist yet is not found by
    # a glob, so the load refuses rather than validate without it (BL-0117).
    root = copy_case("includes", tmp_path)
    new = root.parent / "years" / "2025.beancount"
    assert load(str(root), {str(new): b"2025-01-01 open Assets:New USD\n"}, DESKTOP) == LoadRefused(
        "not-reached", str(new)
    )


def test_paths_must_be_absolute(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        load("main.beancount", {}, DESKTOP)
    with pytest.raises(ValueError, match="absolute"):
        load(str(tmp_path / "main.beancount"), {"main.beancount": b""}, DESKTOP)


# ---- the policy seam ---------------------------------------------------------------------------------------------


@dataclass
class Recording:
    """A test LoadPolicy: records every question and refuses the paths and names it is given."""

    refuse: set[str] = field(default_factory=lambda: set[str]())
    asked: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())

    def _answer(self, point: str, subject: str) -> bool:
        self.asked.append((point, subject))
        return subject not in self.refuse

    def may_read(self, path: str) -> bool:
        return self._answer("read", path)

    def may_run_plugin(self, name: str) -> bool:
        return self._answer("plugin", name)

    def may_add_pythonpath(self, path: str) -> bool:
        return self._answer("pythonpath", path)

    def may_scan_documents(self, path: str) -> bool:
        return self._answer("documents", path)


def marker_ledger(tmp_path: Path) -> tuple[Path, str, str, Path]:
    """A ledger with two local plugins: the first writes a marker file when it runs, the second does nothing."""
    first, second = f"marker_{uuid.uuid4().hex}", f"quiet_{uuid.uuid4().hex}"
    marker = tmp_path / "plugin-ran"
    (tmp_path / f"{first}.py").write_text(
        "from pathlib import Path\n__plugins__ = ('run',)\n"
        f"def run(entries, options_map):\n    Path({str(marker)!r}).touch()\n    return entries, []\n",
        encoding="utf-8",
    )
    (tmp_path / f"{second}.py").write_text(
        "__plugins__ = ('run',)\ndef run(entries, options_map):\n    return entries, []\n", encoding="utf-8"
    )
    root = tmp_path / "main.beancount"
    root.write_text(
        f'option "insert_pythonpath" "TRUE"\nplugin "{first}"\nplugin "{second}"\n2020-01-01 open Assets:Cash\n',
        encoding="utf-8",
    )
    return root, first, second, marker


def test_the_desktop_policy_allows_every_point() -> None:
    assert DESKTOP.may_read("/any/file.beancount")
    assert DESKTOP.may_run_plugin("any.plugin")
    assert DESKTOP.may_add_pythonpath("/any")
    assert DESKTOP.may_scan_documents("/any/docs")


def test_every_point_is_put_to_the_policy(tmp_path: Path) -> None:
    root, first, second, marker = marker_ledger(tmp_path)
    policy = Recording()
    assert isinstance(load(str(root), {}, policy), Loaded)
    assert marker.exists()
    assert policy.asked == [
        ("read", str(root)),
        ("plugin", first),
        ("plugin", second),
        ("pythonpath", str(tmp_path)),
    ]


def test_every_file_read_and_every_documents_root_is_put_to_the_policy(tmp_path: Path) -> None:
    root = copy_case("includes", tmp_path)
    policy = Recording()
    loaded = load(str(root), {}, policy)
    assert isinstance(loaded, Loaded)
    reads = [s for p, s in policy.asked if p == "read"]
    assert reads[0] == str(root)
    assert sorted(reads) == sorted(loaded.options["include"])
    assert len(reads) == 5  # main, accounts, the two files the glob matches and the one 2023 includes
    docs_root = copy_case("documents", tmp_path)
    policy = Recording()
    assert isinstance(load(str(docs_root), {}, policy), Loaded)
    assert ("documents", str(docs_root.parent / "docs")) in policy.asked


@pytest.mark.parametrize("point", ["plugin-first", "plugin-second", "pythonpath"])
def test_a_refused_plugin_or_pythonpath_refuses_the_load_before_any_plugin_runs(point: str, tmp_path: Path) -> None:
    root, first, second, marker = marker_ledger(tmp_path)
    subject = {"plugin-first": first, "plugin-second": second, "pythonpath": str(tmp_path)}[point]
    reason = "pythonpath-refused" if point == "pythonpath" else "plugin-refused"
    assert load(str(root), {}, Recording(refuse={subject})) == LoadRefused(reason, subject)
    assert not marker.exists()


def test_a_refused_documents_root_refuses_the_load(tmp_path: Path) -> None:
    root = copy_case("documents", tmp_path)
    docs = str(root.parent / "docs")
    assert load(str(root), {}, Recording(refuse={docs})) == LoadRefused("documents-refused", docs)


def test_a_refused_include_refuses_the_load(tmp_path: Path) -> None:
    root = copy_case("includes", tmp_path)
    nested = str(root.parent / "years" / "2024.beancount")
    assert load(str(root), {}, Recording(refuse={nested})) == LoadRefused("read-refused", nested)


def test_a_refused_root_refuses_the_load(tmp_path: Path) -> None:
    root = copy_case("comments", tmp_path)
    policy = Recording(refuse={str(root)})
    assert load(str(root), {}, policy) == LoadRefused("read-refused", str(root))
    assert policy.asked == [("read", str(root))]


def test_a_refused_candidate_path_refuses_the_load(tmp_path: Path) -> None:
    root = copy_case("insert-entry", tmp_path)
    food = str(root.parent / "food.beancount")
    result = load(str(root), {food: b""}, Recording(refuse={food}))
    assert result == LoadRefused("read-refused", food)


# ---- guardrail 2: the replica is pinned to Beancount 3.2.0 -------------------------------------------------------


def test_the_mirrored_loader_functions_are_unchanged() -> None:
    assert port.BEANCOUNT_VERSION == "3.2.0"
    spec = find_spec("beancount.loader")
    assert spec is not None
    assert spec.origin is not None
    source = Path(spec.origin).read_text(encoding="utf-8")
    functions = {
        n.name: ast.get_source_segment(source, n) or ""
        for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef)
    }
    assert {name: {"sha256": hashlib.sha256(functions[name].encode()).hexdigest()} for name in MIRRORED} == MIRRORED
