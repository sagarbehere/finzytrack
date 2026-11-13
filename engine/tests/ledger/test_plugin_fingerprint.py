"""The fingerprint covers the plugin code a load imports (BL-0124; `design/specs/engine-jobs.md` §1 "Fingerprint";
S0099 brief).

Two requirements, as for the ledger's own files (S0051): a change that makes the next load run other code changes the
fingerprint (else a save lands on a preview made with other plugin code, or the mirror stays stale: INV-01, INV-05,
INV-22), and unchanged code keeps it (else every open rebuilds and every save is refused). What the next load's
imports read is the import system's path search: for each module name, the folders it searches in order and the file
it finds there, then that file's bytes. So the changes below are a plugin file's bytes, a submodule's, a module a
plugin imports while it runs, a module that was not found and appears, a file that appears earlier on the path and
shadows the one found, and a namespace package that becomes a regular one. The observation asks the same questions
without importing anything (the save runs no user code, ADR-0068 D1).

Each test names its plugin modules uniquely and removes them from the process's module cache afterwards, as a fresh
engine would start without them (spec §5.1: an engine that ran user code is ended after its job).
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import ledger_engine
from ledger_engine.ledger import create, overlay
from ledger_engine.ledger.core.ledger_set import Content, Fingerprint, Module
from ledger_engine.ledger.fingerprint import observe
from ledger_engine.ledger.policy import DESKTOP

pytestmark = pytest.mark.inv("INV-22")

HEAD = 'option "insert_pythonpath" "TRUE"\n'
BODY = "2026-01-01 open Assets:Cash\n"
NOOP = '__plugins__ = ["run"]\n\n\ndef run(entries, options_map):\n    return entries, []\n'

type Names = Callable[[str], str]


@pytest.fixture
def names() -> Iterator[Names]:
    """Unique module names for the test's plugins; each is dropped from the module cache after the test."""
    made: list[str] = []

    def name(stem: str) -> str:
        n = f"ft_s0099_{stem}_{uuid.uuid4().hex[:8]}"
        made.append(n)
        return n

    yield name
    forget(*made)


def forget(*names: str) -> None:
    """Drop modules from the process's module cache, as a fresh engine starts without them."""
    for n in list(sys.modules):
        if any(n == m or n.startswith(m + ".") for m in names):
            del sys.modules[n]


def ledger(folder: Path, *plugins: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    root = folder / "main.beancount"
    root.write_text(HEAD + "".join(f'plugin "{p}"\n' for p in plugins) + BODY, encoding="utf-8")
    return root


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def load(root: Path) -> overlay.Loaded:
    out = overlay.load(str(root), {}, DESKTOP)
    assert isinstance(out, overlay.Loaded), out
    return out


def fp_of(root: Path) -> Fingerprint:
    return load(root).fingerprint


def answers(fp: Fingerprint) -> dict[str, Module | None]:
    """Each module name's answer (the tests use one search path per name)."""
    return {name: answer for (name, _path), answer in fp.modules}


def unchanged(fp: Fingerprint) -> bool:
    return observe(fp.ledger_set) == fp


# ---- what changes it --------------------------------------------------------------------------------------------


def test_a_plugin_source_edit_changes_the_fingerprint(tmp_path: Path, names: Names) -> None:
    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    src = write(tmp_path / "L" / f"{plug}.py", NOOP)
    fp = fp_of(root)
    assert unchanged(fp)
    found = answers(fp)[plug]
    assert found is not None
    assert found.origin == str(src)
    write(src, NOOP.replace("return entries, []", "return entries, [] "))
    assert not unchanged(fp)


def test_an_edit_that_keeps_the_size_and_the_mtime_changes_it(tmp_path: Path, names: Names) -> None:
    """A stat cannot see it. The fingerprint hashes the source, and a load compiles a module from its source alone,
    never from cached bytecode (`beancount_port._SourceOnly`), so the next load runs the new code; that it does is
    `test_equal_fingerprints_mean_the_same_code_ran`."""
    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    src = write(tmp_path / "L" / f"{plug}.py", NOOP + "X = 1\n")
    fp = fp_of(root)
    st = src.stat()
    src.write_text(NOOP + "X = 2\n", encoding="utf-8")
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert src.stat().st_size == st.st_size
    assert not unchanged(fp)


def test_a_submodule_of_a_plugin_package_edited_changes_it(tmp_path: Path, names: Names) -> None:
    pkg = names("pkg")
    root = ledger(tmp_path / "L", f"{pkg}.plugin")
    write(tmp_path / "L" / pkg / "__init__.py", "")
    write(tmp_path / "L" / pkg / "plugin.py", f"from {pkg} import helper\n" + NOOP)
    helper = write(tmp_path / "L" / pkg / "helper.py", "VALUE = 1\n")
    fp = fp_of(root)
    assert {f"{pkg}", f"{pkg}.plugin", f"{pkg}.helper"} <= set(answers(fp))
    assert unchanged(fp)
    write(helper, "VALUE = 2\n")
    assert not unchanged(fp)


def test_a_module_a_plugin_imports_while_it_runs_is_covered(tmp_path: Path, names: Names) -> None:
    plug, late = names("plug"), names("late")
    root = ledger(tmp_path / "L", plug)
    write(
        tmp_path / "L" / f"{plug}.py",
        f'__plugins__ = ["run"]\n\n\ndef run(entries, options_map):\n    import {late}\n\n    return entries, []\n',
    )
    late_src = write(tmp_path / "L" / f"{late}.py", "A = 1\n")
    fp = fp_of(root)
    assert late in answers(fp)
    write(late_src, "A = 2\n")
    assert not unchanged(fp)


@pytest.mark.parametrize("shape", ["module", "package"])
def test_a_plugin_not_found_that_appears_changes_it(tmp_path: Path, names: Names, shape: str) -> None:
    """The load's error "Error importing" goes away in the next load: the same question as an edit (BL-0124)."""
    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    loaded = load(root)
    assert any("Error importing" in e.message for e in loaded.errors)
    fp = loaded.fingerprint
    assert plug in answers(fp)
    assert answers(fp)[plug] is None
    assert unchanged(fp)
    if shape == "module":
        write(tmp_path / "L" / f"{plug}.py", NOOP)
    else:
        write(tmp_path / "L" / plug / "__init__.py", NOOP)
    assert not unchanged(fp)


def test_a_plugin_appearing_in_a_folder_whose_time_is_put_back_changes_it(tmp_path: Path, names: Names) -> None:
    """The path search keeps each folder's listing until the folder's modification time changes; on a volume with
    coarse times (FAT, some network shares) a new file may not change it. The observation lists afresh."""
    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    folder = tmp_path / "L"
    fp = fp_of(root)
    assert unchanged(fp)  # the listing is now cached, without the plugin
    st = folder.stat()
    write(folder / f"{plug}.py", NOOP)
    os.utime(folder, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert not unchanged(fp)


def test_a_found_plugin_that_goes_changes_it(tmp_path: Path, names: Names) -> None:
    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    src = write(tmp_path / "L" / f"{plug}.py", NOOP)
    fp = fp_of(root)
    src.unlink()
    assert not unchanged(fp)


def test_a_file_shadowing_a_standard_library_module_changes_it(tmp_path: Path, names: Names) -> None:
    """The `pythonpath` folder comes first on `sys.path` while the plugins run, so a `colorsys.py` beside the ledger
    replaces the standard library's for a plugin that imports it: its lookup is recorded wherever it was found."""
    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    write(tmp_path / "L" / f"{plug}.py", "import colorsys\n" + NOOP)
    forget("colorsys")  # a fresh engine has not imported it
    fp = fp_of(root)
    found = answers(fp)["colorsys"]
    assert found is not None
    assert found.origin is not None
    assert not found.origin.startswith(str(tmp_path))
    assert unchanged(fp)
    write(tmp_path / "L" / "colorsys.py", "")
    assert not unchanged(fp)
    forget("colorsys")
    os.remove(tmp_path / "L" / "colorsys.py")


def test_a_shadowing_file_in_an_earlier_pythonpath_folder_changes_it(tmp_path: Path, names: Names) -> None:
    """Two `insert_pythonpath` folders (Beancount sorts them): a plugin found in the second is replaced by a file of
    the same name that appears in the first."""
    plug = names("plug")
    root = ledger(tmp_path / "b", plug)
    root.write_text(HEAD + 'include "../a/a.beancount"\n' + f'plugin "{plug}"\n' + BODY, encoding="utf-8")
    write(tmp_path / "a" / "a.beancount", HEAD)
    write(tmp_path / "b" / f"{plug}.py", NOOP)
    fp = fp_of(root)
    ((_, path),) = [k for k, _ in fp.modules if k[0] == plug]
    assert path[:2] == (str(tmp_path / "a"), str(tmp_path / "b"))
    assert unchanged(fp)
    write(tmp_path / "a" / f"{plug}.py", NOOP)
    assert not unchanged(fp)


def test_a_namespace_package_that_becomes_a_regular_one_changes_it(tmp_path: Path, names: Names) -> None:
    ns = names("ns")
    root = ledger(tmp_path / "L", f"{ns}.plugin")
    write(tmp_path / "L" / ns / "plugin.py", NOOP)
    fp = fp_of(root)
    package = answers(fp)[ns]
    assert package is not None
    assert package.origin is None
    assert package.locations == (str(tmp_path / "L" / ns),)
    assert unchanged(fp)
    write(tmp_path / "L" / ns / "__init__.py", "")
    assert not unchanged(fp)


def test_a_module_in_a_zip_archive_a_plugin_puts_on_the_path_is_covered(tmp_path: Path, names: Names) -> None:
    """The loader reads a zipped module from its archive, so the archive's bytes are what is hashed."""
    import zipfile

    plug, zipped = names("plug"), names("zipped")
    root = ledger(tmp_path / "L", plug)
    archive = tmp_path / "L" / "lib.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(f"{zipped}.py", "A = 1\n")
    write(
        tmp_path / "L" / f"{plug}.py",
        f"import sys\nsys.path.insert(0, {str(archive)!r})\nimport {zipped}\n" + NOOP,
    )
    try:
        fp = fp_of(root)
    finally:
        while str(archive) in sys.path:
            sys.path.remove(str(archive))
    found = answers(fp)[zipped]
    assert found is not None
    assert found.state == Content(__import__("hashlib").sha256(archive.read_bytes()).hexdigest())
    assert unchanged(fp)
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(f"{zipped}.py", "A = 2\n")
    assert not unchanged(fp)


def test_a_plugin_edited_between_its_lookup_and_its_read_changes_it(
    tmp_path: Path, names: Names, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record hashes the file when the path search finds it, before the loader reads it: an edit in between
    makes the next observation differ (the safe side), never a record of bytes the load did not run."""
    import importlib.machinery

    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    src = write(tmp_path / "L" / f"{plug}.py", NOOP)
    real = importlib.machinery.SourceFileLoader.get_data

    def get_data(self: importlib.machinery.SourceFileLoader, path: str) -> bytes:
        if path == str(src):
            src.write_text(NOOP + "EDITED = True\n", encoding="utf-8")
        return real(self, path)

    monkeypatch.setattr(importlib.machinery.SourceFileLoader, "get_data", get_data)
    fp = fp_of(root)
    monkeypatch.undo()
    assert getattr(sys.modules[plug], "EDITED", False)  # the load ran the edited bytes
    assert not unchanged(fp)


def test_a_plugin_not_found_and_then_found_in_one_load_keeps_the_first_answer(tmp_path: Path, names: Names) -> None:
    """Listed twice, with a plugin between that writes it: the load holds the import error and the plugin's run, which
    the next load would not give (no error), so the first answer stays and the next observation differs."""
    late, maker = names("late"), names("maker")
    root = ledger(tmp_path / "L", late, maker, late)
    target = tmp_path / "L" / f"{late}.py"
    write(
        tmp_path / "L" / f"{maker}.py",
        f'import pathlib\n__plugins__ = ["run"]\n\n\ndef run(entries, options_map):\n'
        f"    pathlib.Path({str(target)!r}).write_text({NOOP!r})\n    return entries, []\n",
    )
    loaded = load(root)
    assert any("Error importing" in e.message for e in loaded.errors)
    assert late in sys.modules  # the second import found it
    assert answers(loaded.fingerprint)[late] is None
    assert not unchanged(loaded.fingerprint)


def test_the_load_lists_each_folder_afresh(tmp_path: Path, names: Names) -> None:
    """A listing the process kept from before the load (an observation of the same folder) never answers the load's
    import: a plugin that appeared with the folder's time put back is found and named."""
    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    folder = tmp_path / "L"
    fp = fp_of(root)
    assert unchanged(fp)  # the folder's listing is now cached, without the plugin
    st = folder.stat()
    write(folder / f"{plug}.py", NOOP)
    os.utime(folder, ns=(st.st_atime_ns, st.st_mtime_ns))
    again = fp_of(root)
    found = answers(again)[plug]
    assert found is not None
    assert found.origin == str(folder / f"{plug}.py")
    assert unchanged(again)


REPORTS = """__plugins__ = ["run"]
import collections
E = collections.namedtuple("E", "source message entry")


def run(entries, options_map):
    return entries, [E({"filename": "<plugin>", "lineno": 0}, "CODE=TAG", None)]
"""


def ran(loaded: overlay.Loaded) -> list[str]:
    return [e.message for e in loaded.errors if e.message.startswith("CODE=")]


@pytest.mark.parametrize("mode", ["unchecked", "same_stat"])
def test_a_load_never_runs_cached_bytecode(tmp_path: Path, names: Names, mode: str) -> None:
    """S0099 r1 (verifier F1): a `__pycache__` file beside a plugin (`bean-check` leaves one; it may be compiled from
    other code) is never what runs: the load compiles the source, the file the fingerprint hashes, and writes no
    bytecode. `unchecked`: a pyc Python would trust without looking at the source; `same_stat`: a timestamp pyc
    whose source was edited keeping its size and mtime."""
    import py_compile

    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    src = write(tmp_path / "L" / f"{plug}.py", REPORTS.replace("TAG", "AAAA"))
    if mode == "unchecked":
        write(src, REPORTS.replace("TAG", "BBBB"))
        py_compile.compile(str(src), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        write(src, REPORTS.replace("TAG", "AAAA"))
    else:
        py_compile.compile(str(src))  # a timestamp pyc of AAAA
        st = src.stat()
        write(src, REPORTS.replace("TAG", "BBBB"))
        os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
    want = "CODE=AAAA" if mode == "unchecked" else "CODE=BBBB"
    cache = tmp_path / "L" / "__pycache__"
    cached = {p.name: p.read_bytes() for p in cache.iterdir()}
    loaded = load(root)
    assert ran(loaded) == [want]
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == cached  # nothing written either


CONTRACT = """
import json, sys
from ledger_engine.ledger import overlay
from ledger_engine.ledger.core.ledger_set import encode
from ledger_engine.ledger.policy import DESKTOP
out = overlay.load(sys.argv[1], {}, DESKTOP)
codes = [e.message for e in out.errors if e.message.startswith("CODE=")]
print(json.dumps({"fp": encode(out.fingerprint).decode(), "ran": codes}))
"""


def child_env() -> dict[str, str]:
    """The child imports the same `ledger_engine` as this test (the sources under test, also from a copy)."""
    return {**os.environ, "PYTHONPATH": str(Path(ledger_engine.__file__).resolve().parents[1])}


def fresh_load(root: Path) -> tuple[str, list[str]]:
    """One load in a fresh process, with the engine's `-s -B` (each job has a fresh engine, spec §5.1)."""
    out = subprocess.run(  # noqa: S603 (this interpreter, a literal script, a test path)
        [sys.executable, "-s", "-B", "-c", CONTRACT, str(root)],
        capture_output=True,
        text=True,
        check=True,
        cwd=root.parent,
        env=child_env(),
    )
    got = json.loads(out.stdout)
    return got["fp"], got["ran"]


@pytest.mark.parametrize("change", ["edit", "same_size_and_mtime", "unchecked_pyc", "stale_timestamp_pyc", "none"])
def test_equal_fingerprints_mean_the_same_code_ran(tmp_path: Path, change: str) -> None:
    """The contract end to end (S0099 r1, verifier T1): two loads in fresh processes, a change between them; equal
    fingerprints must mean the same plugin code ran. A pyc changed beside an unchanged source runs nothing new."""
    import py_compile

    name = f"ft_s0099_contract_{change}"
    root = ledger(tmp_path / "L", name)
    src = write(tmp_path / "L" / f"{name}.py", REPORTS.replace("TAG", "AAAA"))
    if change == "stale_timestamp_pyc":
        py_compile.compile(str(src))
    fp_a, ran_a = fresh_load(root)
    st = src.stat()
    if change == "edit":
        write(src, REPORTS.replace("TAG", "BBBBB"))
    elif change in ("same_size_and_mtime", "stale_timestamp_pyc"):
        write(src, REPORTS.replace("TAG", "BBBB"))
        os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
    elif change == "unchecked_pyc":
        write(src, REPORTS.replace("TAG", "BBBB"))
        py_compile.compile(str(src), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        write(src, REPORTS.replace("TAG", "AAAA"))
        os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
    fp_b, ran_b = fresh_load(root)
    source = src.read_text(encoding="utf-8")
    assert ran_a == ["CODE=AAAA"]
    assert ran_b == re.findall(r"CODE=\w+", source)  # the source on disk is what ran
    assert fp_a != fp_b or ran_a == ran_b  # equal fingerprints, same code
    assert (fp_a == fp_b) == (change in ("unchecked_pyc", "none"))


DISCOVERS = """import importlib, pkgutil
__plugins__ = ["run"]


def run(entries, options_map):
    errors = []
    for info in pkgutil.iter_modules(__path__):
        errors += importlib.import_module(f"{__name__}.{info.name}").check(entries)
    return entries, errors
"""


def test_a_module_added_to_a_package_that_finds_its_own_modules_changes_it(tmp_path: Path, names: Names) -> None:
    """S0099 r1 (red team F1): the package lists its folder and imports what it finds, so a new module runs in the
    next load; each package folder's importable names are in the fingerprint."""
    pkg = names("rules")
    root = ledger(tmp_path / "L", pkg)
    write(tmp_path / "L" / pkg / "__init__.py", DISCOVERS)
    write(tmp_path / "L" / pkg / "r1.py", "def check(entries):\n    return []\n")
    fp = fp_of(root)
    found = answers(fp)[pkg]
    assert found is not None
    assert found.listings == (("__init__.py", "r1.py"),)
    assert unchanged(fp)
    write(tmp_path / "L" / pkg / "r2.py", "def check(entries):\n    return []\n")
    assert not unchanged(fp)


@pytest.mark.parametrize(
    "new",
    ["L/notes.txt", "L/unrelated.py", "L/sub/x.py", "L/PKG/README.md", "L/PKG/__pycache__/x.cpython-313.pyc"],
)
def test_a_file_no_import_can_reach_keeps_it(tmp_path: Path, names: Names, new: str) -> None:
    """Must not change (S0099 r1): only a package's own folders are listed, by importable names; the ledger's folder
    (a `pythonpath` folder) is not, so an unrelated file beside the ledger never refuses a save."""
    pkg = names("rules")
    root = ledger(tmp_path / "L", pkg)
    write(tmp_path / "L" / pkg / "__init__.py", DISCOVERS)
    fp = fp_of(root)
    write(tmp_path / new.replace("PKG", pkg), "")
    assert unchanged(fp)


def test_a_subpackage_added_to_a_package_that_finds_its_own_modules_changes_it(tmp_path: Path, names: Names) -> None:
    """S0099 r2 (verifier T2): `pkgutil.iter_modules(__path__)` yields subpackages too."""
    pkg = names("rules")
    root = ledger(tmp_path / "L", pkg)
    write(tmp_path / "L" / pkg / "__init__.py", DISCOVERS)
    fp = fp_of(root)
    write(tmp_path / "L" / pkg / "r3" / "__init__.py", "def check(entries):\n    return []\n")
    assert not unchanged(fp)


@pytest.mark.parametrize("new", [".#__init__.py", "._x.py", ".pytest_cache/", ".git/", "data/", "data/x.csv"])
def test_names_no_import_can_reach_inside_a_package_keep_it(tmp_path: Path, names: Names, new: str) -> None:
    """Must not change (S0099 r2, verifier F3): a name that is no identifier (an editor's lock file, a macOS `._`
    file, a tool's folder) and a data folder that is no package are not what `pkgutil.iter_modules` yields."""
    pkg = names("rules")
    root = ledger(tmp_path / "L", pkg)
    write(tmp_path / "L" / pkg / "__init__.py", DISCOVERS)
    if new.startswith("data/x"):
        (tmp_path / "L" / pkg / "data").mkdir()
    fp = fp_of(root)
    target = tmp_path / "L" / pkg / new.rstrip("/")
    if new.endswith("/"):
        target.mkdir()
    else:
        target.write_bytes(b"")
    assert unchanged(fp)


@pytest.mark.parametrize("when", ["import", "run"])
def test_a_plugin_reads_its_package_data_as_under_beancount(tmp_path: Path, names: Names, when: str) -> None:
    """S0099 r2 (verifier F1; owner, 2026-11-10, "Narrow the refusal"): only bytecode reads are refused, so a package
    reads its own data files (`pkgutil.get_data`, `__loader__.get_data`) when imported and while it runs, as under
    bean-check."""
    pkg = names("rates")
    root = ledger(tmp_path / "L", pkg)
    read = 'pkgutil.get_data(__name__, "rates.csv") + __loader__.get_data(__file__.replace("__init__.py", "rates.csv"))'
    body = (
        f"import pkgutil\nRATES = {read}\n" + REPORTS.replace("TAG", "IMPORT")
        if when == "import"
        else "import pkgutil\n"
        + REPORTS.replace("TAG", "RUN").replace(
            "    return entries,", f"    assert {read} == b'USD,1\\nUSD,1\\n'\n    return entries,"
        )
    )
    write(tmp_path / "L" / pkg / "__init__.py", body)
    write(tmp_path / "L" / pkg / "rates.csv", "USD,1\n")
    out = load(root)
    assert ran(out) == [f"CODE={when.upper()}"], out.errors


def test_a_plugin_that_replaces_the_finder_list_leaves_no_recorder_behind(tmp_path: Path, names: Names) -> None:
    """S0099 r1 (red team F7): the path search is put back in whatever list `sys.meta_path` names after the load."""
    import importlib.machinery

    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    write(tmp_path / "L" / f"{plug}.py", "import sys\nsys.meta_path = list(sys.meta_path)\n" + NOOP)
    saved = sys.meta_path
    try:
        load(root)
        assert importlib.machinery.PathFinder in sys.meta_path
        assert all(type(f).__name__ != "_Recorder" for f in sys.meta_path)
    finally:
        sys.meta_path = saved


# ---- what keeps it ----------------------------------------------------------------------------------------------


def test_unchanged_plugin_code_observes_unchanged_after_a_touch(tmp_path: Path, names: Names) -> None:
    plug, pkg = names("plug"), names("pkg")
    root = ledger(tmp_path / "L", plug, f"{pkg}.sub", names("missing"))
    src = write(tmp_path / "L" / f"{plug}.py", "import json\n" + NOOP)
    write(tmp_path / "L" / pkg / "__init__.py", "")
    write(tmp_path / "L" / pkg / "sub.py", NOOP)
    fp = fp_of(root)
    assert observe(fp.ledger_set) == fp
    os.utime(src, ns=(1, 1))  # a time is no input of the next load
    assert observe(fp.ledger_set) == fp


def test_a_ledger_with_no_plugin_of_its_own_records_only_beancounts_steps(tmp_path: Path) -> None:
    root = tmp_path / "main.beancount"
    root.write_text(BODY, encoding="utf-8")
    fp = fp_of(root)
    assert all(name.startswith("beancount.") for (name, _), _ in fp.modules), fp.modules
    assert unchanged(fp)


def test_the_observation_imports_nothing_and_runs_no_code(tmp_path: Path, names: Names) -> None:
    """The save's gate observes (ADR-0068 D1: it runs no user code): a plugin whose import leaves a mark is asked
    about, not imported."""
    plug, late = names("plug"), names("late")
    root = ledger(tmp_path / "L", plug)
    mark = tmp_path / "imported"
    body = f"import pathlib\npathlib.Path({str(mark)!r}).touch()\n"
    write(tmp_path / "L" / f"{plug}.py", body + f"import {late}\n" + NOOP)
    write(tmp_path / "L" / f"{late}.py", body)
    fp = fp_of(root)
    assert mark.exists()
    mark.unlink()
    forget(plug, late)
    before, listing = set(sys.modules), sorted((tmp_path / "L").rglob("*"))
    assert unchanged(fp)
    assert set(sys.modules) == before
    assert not mark.exists()
    assert sorted((tmp_path / "L").rglob("*")) == listing  # no bytecode written either


# ---- the record is the load's own ----------------------------------------------------------------------------------


def test_the_record_names_the_file_the_import_used(tmp_path: Path, names: Names) -> None:
    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    src = write(tmp_path / "L" / f"{plug}.py", NOOP)
    found = answers(fp_of(root))[plug]
    assert found is not None
    assert found.origin == sys.modules[plug].__file__ == str(src)
    assert found.state == Content(__import__("hashlib").sha256(src.read_bytes()).hexdigest())


def test_a_plugin_that_raises_at_import_is_in_the_refusals_read(tmp_path: Path, names: Names) -> None:
    """A plugin failing at import refuses the load (S0098); what the load read names it, so a fix is a moved disk."""
    plug = names("plug")
    root = ledger(tmp_path / "L", plug)
    src = write(tmp_path / "L" / f"{plug}.py", "raise RuntimeError('broken')\n")
    out = overlay.load(str(root), {}, DESKTOP)
    assert isinstance(out, overlay.LoadRefused), out
    assert out.read is not None
    assert plug in answers(out.read)
    assert observe(out.read.ledger_set) == out.read
    write(src, NOOP)
    assert observe(out.read.ledger_set) != out.read


# The real load is the one the rebuild's ports hold by default, read from the dataclass: `rebuild.Ports()` would also
# make the file-system port, which refuses off macOS (S0113 round 2: the Linux CI run of 2026-11-13); the load needs
# none.
SCRIPT = """
import dataclasses, sys
from ledger_engine.ledger import imports, overlay, rebuild
from ledger_engine.ledger.core.ledger_set import encode, Fingerprint
from ledger_engine.ledger.policy import DESKTOP
root = sys.argv[2]
if sys.argv[1] == "overlay":
    modules = dict(overlay.load(root, {}, DESKTOP).fingerprint.modules)
else:
    modules = {}
    with imports.recording(modules):
        real_load = next(f.default for f in dataclasses.fields(rebuild.Ports) if f.name == "load")
        real_load(root, [])
print(encode(Fingerprint.of(root, {}, {}, {}, {}, modules)).decode())
"""


def test_the_overlay_load_and_the_real_load_record_the_same_imports(tmp_path: Path) -> None:
    """Each in a fresh process, as each job runs in a fresh engine: the open's stamp and the rebuild's agree, so a
    rebuild after a save needs no extra try for its imports."""
    root = ledger(tmp_path / "L", "ft_s0099_same", "ft_s0099_absent")
    write(tmp_path / "L" / "ft_s0099_same.py", "import colorsys\n" + NOOP)

    def run(kind: str) -> str:
        out = subprocess.run(  # noqa: S603 (this interpreter, a literal script, a test path)
            [sys.executable, "-s", "-B", "-c", SCRIPT, kind, str(root)],
            capture_output=True,
            text=True,
            check=True,
            cwd=tmp_path,
            env=child_env(),
        )
        return out.stdout

    assert run("overlay") == run("real")
    assert "ft_s0099_same" in run("overlay")


ENGINE_SRC = Path(__file__).resolve().parents[2] / "src" / "ledger_engine"


def test_every_load_that_runs_plugins_records_its_imports() -> None:
    """A load whose record misses its imports would stamp a generation, or give a plan, that a plugin edit cannot
    move. The loads that run plugins are the port's `real_load` and `run_transformations` (the overlay's), found by
    behaviour: every engine module that calls one of them (S0099). Each whose result carries a fingerprint wraps it in
    `imports.recording`; the create's load check keeps none (its count and parsed files only)."""
    callers: set[str] = set()
    for p in sorted(ENGINE_SRC.rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        if "port.real_load(" in text or "port.run_transformations(" in text:
            callers.add(str(p.relative_to(ENGINE_SRC)))
    # a new caller fails here until it records its imports and is added
    assert callers == {"ledger/create.py", "ledger/overlay.py", "ledger/rebuild.py"}
    for rel in ("ledger/overlay.py", "ledger/rebuild.py"):
        assert "imports.recording(" in (ENGINE_SRC / rel).read_text(encoding="utf-8"), rel
    assert {f.name for f in dataclasses.fields(create.LoadResult)} == {"errors", "parsed"}  # no fingerprint
