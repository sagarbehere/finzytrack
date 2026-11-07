"""The copy's ops on the protocol (`ledger_engine.protocol.copy`; `design/specs/engine-jobs.md` §4, §5.1, §8.1, §8.2;
S0081), on the real file system (macOS, fresh `tmp_path` folders) with Beancount's own load. Written from the spec's
lines and the owner's decisions of 2026-11-07:

- "Copy every file the ledger loads into the new workspace folder. Rewrite only the include lines and the documents
  option that point outside, so they point to the copies. … a v0.3 ledger's `../documents/<stem>-<hash>` comes in as
  `documents/`" (decision 3);
- "The "loads the same" check then confirms that the copy has the same entries as the source. … Because the source
  ledger may have Beancount errors (accounting advises, ADR-0067), "the same" means the same as the source, not
  error-free" (decision 3);
- "Copy fingerprints each source file before reading it, and again just before its commit point. If anything
  changed, it stops … cleans up its own files and writes no manifest" (decision 2);
- §8.2 step 2: "refused … if any `.open` journal names one"; the legacy-lock try (ADR-0062 decision 2);
- the source is only ever read (owner, decision 1: "The source ledger is only ever read, never written").
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, override

import pytest

from ledger_engine.ledger.core import journal
from ledger_engine.protocol.server import InputEnded
from tests.test_save_ops import Channel as SaveChannel
from tests.test_save_ops import engine, request

pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's port is macOS-only until BL-0087"),
    pytest.mark.inv("INV-22"),
    pytest.mark.inv("INV-26"),
    pytest.mark.inv("INV-32"),
]

WS_JSON = (
    '{"format": 1, "min_reader": 1, "lineage_id": "7e1c3b52-6a0e-4f43-9d2c-8f1b2c3d4e5f",'
    ' "ledger": {"root": "main.beancount"}}\n'
)
MAIN = (
    'option "title" "Books"\n'
    'option "operating_currency" "EUR"\n'
    'option "documents" "../documents/books-1a2b"\n'
    "2026-01-01 open Assets:Cash EUR\n"
    "2026-01-01 open Assets:Bank EUR\n"
    "2026-01-01 open Expenses:Food EUR\n"
    'include "prices/p.beancount"\n'
    'include "../shared/rates.beancount"\n'
    '2026-01-05 * "Lunch" #food\n'
    "  Expenses:Food  12.50 EUR\n"
    "  Assets:Cash\n"
    "2026-01-06 balance Assets:Cash 3 EUR\n"  # a Beancount error the copy must keep (ADR-0067)
)
PRICES = "2026-01-02 price EUR 1.10 USD\n"
RATES = '2026-01-03 price EUR 1.11 USD\ninclude "more.beancount"\n'
MORE = "2026-01-04 price EUR 1.12 USD\n"
PDF = b"%PDF-1.4 statement " * 4000  # about 76 KiB: more than one chunk on the fake, one here


class Channel(SaveChannel):
    """The protocol channel, with `proceed` for a `copy`'s gate."""

    @override
    def read_line(self) -> bytes:
        line = super().read_line()
        return line.replace(b'"op": "save"', b'"op": "copy"')


@pytest.fixture
def ald(tmp_path: Path) -> Path:
    out = tmp_path / "app"
    (out / "locks").mkdir(parents=True)
    return out


def sources(tmp_path: Path) -> Path:
    """A v0.3-shaped ledger: its root in `ledgers/`, an include beside it, one outside it (which includes its own
    neighbour), and its documents in `../documents/<stem>-<hash>`."""
    books = tmp_path / "data" / "ledgers"
    (books / "prices").mkdir(parents=True)
    (books / "main.beancount").write_text(MAIN)
    (books / "prices" / "p.beancount").write_text(PRICES)
    shared = tmp_path / "data" / "shared"
    shared.mkdir()
    (shared / "rates.beancount").write_text(RATES)
    (shared / "more.beancount").write_text(MORE)
    docs = tmp_path / "data" / "documents" / "books-1a2b" / "Assets" / "Bank"
    docs.mkdir(parents=True)
    (docs / "2026-01-07.statement.pdf").write_bytes(PDF)
    return books / "main.beancount"


def params(target: Path, source: Path, ald: Path, locks: list[str] | None = None) -> dict[str, Any]:
    return {
        "job": "j8",
        "target": str(target),
        "source": str(source),
        "manifest": [{"path": "workspace.json", "text": WS_JSON}],
        "app_local_data": str(ald),
        "legacy_locks": locks or [],
    }


def copy(ald: Path, p: dict[str, Any], *, proceed: bool = True) -> tuple[dict[str, Any], list[Any]]:
    channel = Channel(1, proceed=proceed)
    reply = json.loads(engine(ald).handle(request("copy", p), channel))
    return reply, [m.get("phase") or m.get("event") for m in channel.sent]


def snapshot(folder: Path) -> dict[str, tuple[str, int]]:
    """Every entry under `folder`: a file's SHA-256, a folder's marker, and its modification time."""
    out: dict[str, tuple[str, int]] = {}
    for p in sorted(folder.rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(folder))] = (
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir",
            st.st_mtime_ns,
        )
    return out


def test_a_copy_brings_in_outside_files_and_documents_and_loads_the_same(ald: Path, tmp_path: Path) -> None:
    source = sources(tmp_path)
    before = snapshot(tmp_path / "data")
    target = tmp_path / "Books copy"
    reply, events = copy(ald, params(target, source, ald))
    assert reply["ok"], reply
    r = reply["result"]
    assert (r["outcome"], r["reason"], r["left"]) == ("created", None, []), r
    assert events == ["saving", "checking_change", "gate"]
    assert reply["decision"]["table"] == "engine.copy"
    main = (target / "main.beancount").read_text()
    assert main == MAIN.replace("../documents/books-1a2b", "documents").replace(
        "../shared/rates.beancount", "outside/rates.beancount"
    ), "only the lines that point outside are rewritten"
    assert (target / "prices" / "p.beancount").read_text() == PRICES
    assert (target / "outside" / "rates.beancount").read_text() == RATES, "its own neighbour include still resolves"
    assert (target / "outside" / "more.beancount").read_text() == MORE
    assert (target / "documents" / "Assets" / "Bank" / "2026-01-07.statement.pdf").read_bytes() == PDF
    assert (target / ".finzytrack" / "workspace.json").read_text() == WS_JSON
    assert snapshot(tmp_path / "data") == before, "the source is only ever read"


def test_an_absolute_include_into_the_folder_is_made_relative(ald: Path, tmp_path: Path) -> None:
    books = tmp_path / "src"
    books.mkdir()
    (books / "p.beancount").write_text(PRICES)
    (books / "main.beancount").write_text(f'2026-01-01 open Assets:Cash EUR\ninclude "{books}/p.beancount"\n')
    target = tmp_path / "copy"
    reply, _ = copy(ald, params(target, books / "main.beancount", ald))
    assert reply["result"]["outcome"] == "created", reply
    assert (target / "main.beancount").read_text() == '2026-01-01 open Assets:Cash EUR\ninclude "p.beancount"\n'


def test_without_proceed_no_manifest_is_written(ald: Path, tmp_path: Path) -> None:
    source = sources(tmp_path)
    target = tmp_path / "Books copy"
    with pytest.raises(InputEnded):
        copy(ald, params(target, source, ald), proceed=False)
    assert (target / "prices" / "p.beancount").read_text() == PRICES
    assert not (target / ".finzytrack").exists()


PLUG = (
    "__plugins__ = ['run']\nfrom beancount.core import data\n"
    "def run(entries, options):\n"
    "    return entries + [data.Commodity(data.new_metadata('<p>', 0), entries[0].date, 'PLUGGED')], []\n"
)


@pytest.mark.parametrize("shape", ["module", "package", "namespace"])
def test_a_ledgers_own_plugin_refuses_the_copy_whatever_its_shape(ald: Path, tmp_path: Path, shape: str) -> None:
    """BL-0202, r1 F1: a ledger's own plugin is not copied, so the copy could never load the same; a namespace
    package (no `__init__.py`, no `__file__`) included. Its modules are forgotten after the source's load."""
    books = tmp_path / "src"
    books.mkdir()
    name = f"ft_s0081_{shape}_{os.getpid()}"
    if shape == "module":
        (books / f"{name}.py").write_text(PLUG)
        plugin = name
    else:
        (books / name).mkdir()
        (books / name / "p.py").write_text(PLUG)
        if shape == "package":
            (books / name / "__init__.py").write_text("")
        plugin = f"{name}.p"
    (books / "main.beancount").write_text(
        f'option "insert_pythonpath" "True"\nplugin "{plugin}"\n2026-01-01 open Assets:Cash EUR\n'
    )
    target = tmp_path / "copy"
    reply, events = copy(ald, params(target, books / "main.beancount", ald))
    r = reply["result"]
    assert (r["outcome"], r["reason"], r["left"]) == ("not_created", "plugin_modules", []), r
    assert r["path"].startswith(str(books)), r
    assert "gate" not in events
    assert not target.exists(), "a refusal of step 2 makes nothing"
    assert not any(m.startswith(name) for m in sys.modules)


def test_a_documents_root_at_the_ledgers_folder_is_copied_whole_and_its_names_kept(ald: Path, tmp_path: Path) -> None:
    """r1 F2: the folder's own `outside/statement.pdf` keeps its name; the outside ledger file takes another."""
    books = tmp_path / "data" / "books"
    (books / "outside").mkdir(parents=True)
    (tmp_path / "data" / "shared").mkdir()
    doc = b"%PDF the user's statement"
    (books / "outside" / "statement.pdf").write_bytes(doc)
    (tmp_path / "data" / "shared" / "statement.pdf").write_text("2026-01-02 price EUR 1.10 USD\n")
    (books / "main.beancount").write_text(
        'option "documents" "."\n2026-01-01 open Assets:Cash EUR\ninclude "../shared/statement.pdf"\n'
    )
    target = tmp_path / "copy"
    reply, _ = copy(ald, params(target, books / "main.beancount", ald))
    assert reply["result"]["outcome"] == "created", reply
    assert (target / "outside" / "statement.pdf").read_bytes() == doc
    assert (target / "outside-2" / "statement.pdf").read_text() == "2026-01-02 price EUR 1.10 USD\n"


@pytest.mark.parametrize("where", ["beside", "elsewhere"])
def test_v03_document_directives_are_copied_in_and_point_at_the_copies(ald: Path, tmp_path: Path, where: str) -> None:
    """r1 F3 (owner's decision 3: the copy is self-contained): a v0.3 ledger-relative `document` path, one into a
    sibling folder and an absolute one are copied in, their lines rewritten; the copy then loads the same."""
    books = tmp_path / "data" / "ledgers"
    books.mkdir(parents=True)
    docs = tmp_path / "data" / "documents" / "books-1a2b" / "Assets" / "Bank"
    docs.mkdir(parents=True)
    (docs / "statement.pdf").write_bytes(b"%PDF x")
    (tmp_path / "data" / "receipts").mkdir()
    (tmp_path / "data" / "receipts" / "r.pdf").write_bytes(b"%PDF receipt")
    (books / "a.pdf").write_bytes(b"%PDF a")
    (books / "main.beancount").write_text(
        'option "documents" "../documents/books-1a2b"\n2026-01-01 open Assets:Bank EUR\n'
        '2026-01-07 document Assets:Bank "../documents/books-1a2b/Assets/Bank/statement.pdf"\n'
        '2026-01-08 document Assets:Bank "../receipts/r.pdf"\n'
        f'2026-01-09 document Assets:Bank "{books}/a.pdf"\n'
    )
    target = tmp_path / ("data" if where == "beside" else "other") / "Books copy"
    target.parent.mkdir(exist_ok=True)
    reply, _ = copy(ald, params(target, books / "main.beancount", ald))
    assert reply["result"]["outcome"] == "created", reply
    assert (target / "main.beancount").read_text().splitlines()[2:] == [
        '2026-01-07 document Assets:Bank "documents/Assets/Bank/statement.pdf"',
        '2026-01-08 document Assets:Bank "outside/r.pdf"',
        '2026-01-09 document Assets:Bank "a.pdf"',
    ]
    assert (target / "outside" / "r.pdf").read_bytes() == b"%PDF receipt"
    assert (target / "a.pdf").read_bytes() == b"%PDF a"


@pytest.mark.parametrize("line", ['include "../shared/missing.beancount"', 'include "../shared/*.beancount"'])
def test_a_missing_include_keeps_its_error_and_the_ledger_copies(ald: Path, tmp_path: Path, line: str) -> None:
    """r1 F4: Beancount's error quotes the include as written; the rewritten text maps back, so the same error is the
    same (ADR-0067: the copy keeps the source's errors)."""
    books = tmp_path / "data" / "books"
    books.mkdir(parents=True)
    (tmp_path / "data" / "shared").mkdir()
    (books / "main.beancount").write_text(line + "\n2026-01-01 open Assets:Cash EUR\n")
    (tmp_path / "out").mkdir()
    reply, _ = copy(ald, params(tmp_path / "out" / "copy", books / "main.beancount", ald))
    assert reply["result"]["outcome"] == "created", reply


def test_a_missing_include_inside_the_folder_keeps_its_error(ald: Path, tmp_path: Path) -> None:
    """@regression-guard (S0081 r1 F4): it held on 55800b54; a missing include that is not rewritten keeps its error."""
    books = tmp_path / "books"
    books.mkdir()
    (books / "main.beancount").write_text('include "gone.beancount"\n2026-01-01 open Assets:Cash EUR\n')
    reply, _ = copy(ald, params(tmp_path / "copy", books / "main.beancount", ald))
    assert reply["result"]["outcome"] == "created", reply


def test_an_indented_include_beancount_reads_is_rewritten(ald: Path, tmp_path: Path) -> None:
    """r1 verifier F3: the decision is Beancount's parse, never the shape alone."""
    parent = tmp_path / "Ledgers"
    books = parent / "books"
    books.mkdir(parents=True)
    (parent / "p.beancount").write_text("2026-01-02 price EUR 1.10 USD\n")
    (books / "main.beancount").write_text('2026-01-01 open Assets:Cash EUR\n  include "../p.beancount"\n')
    target = tmp_path / "copy"
    reply, _ = copy(ald, params(target, books / "main.beancount", ald))
    assert reply["result"]["outcome"] == "created", reply
    assert (target / "main.beancount").read_text().endswith('  include "outside/p.beancount"\n')


def test_a_workspace_with_documents_dot_is_copied_without_its_own_finzytrack(ald: Path, tmp_path: Path) -> None:
    """r1 verifier F5: the source's `.finzytrack/` is not a document of the copy (the copy writes its own)."""
    books = tmp_path / "src"
    (books / ".finzytrack").mkdir(parents=True)
    (books / ".finzytrack" / "workspace.json").write_text('{"theirs": true}\n')
    (books / "main.beancount").write_text('option "documents" "."\n2026-01-01 open Assets:Cash EUR\n')
    target = tmp_path / "copy"
    reply, _ = copy(ald, params(target, books / "main.beancount", ald))
    assert reply["result"]["outcome"] == "created", reply
    assert (target / ".finzytrack" / "workspace.json").read_text() == WS_JSON


def test_a_fifo_in_the_documents_folder_is_left_out_and_never_hangs_the_copy(ald: Path, tmp_path: Path) -> None:
    """r1 F5: an entry of a documents folder that is not a regular file is left out (a FIFO is never opened)."""
    books = tmp_path / "src"
    (books / "docs").mkdir(parents=True)
    os.mkfifo(books / "docs" / "pipe")
    (books / "docs" / "note.txt").write_text("kept\n")
    (books / "main.beancount").write_text('option "documents" "docs"\n2026-01-01 open Assets:Cash EUR\n')
    target = tmp_path / "copy"
    result: list[dict[str, Any]] = []
    t = threading.Thread(target=lambda: result.append(copy(ald, params(target, books / "main.beancount", ald))[0]))
    t.daemon = True
    t.start()
    t.join(30)
    if t.is_alive():
        fd = os.open(books / "docs" / "pipe", os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)
        t.join(10)
        pytest.fail("the copy hung on a FIFO")
    assert result[0]["result"]["outcome"] == "created", result
    assert sorted(p.name for p in (target / "docs").iterdir()) == ["note.txt"]


def test_a_new_folder_inside_a_documents_root_named_through_a_link_is_refused_before_anything(
    ald: Path, tmp_path: Path
) -> None:
    """r1 F6: the documents root is compared as the volume names it (links resolved), before anything is written."""
    real = tmp_path / "real"
    (real / "docs").mkdir(parents=True)
    (tmp_path / "link").symlink_to(real)
    (real / "main.beancount").write_text('option "documents" "docs"\n2026-01-01 open Assets:Cash EUR\n')
    reply, events = copy(ald, params(real / "docs" / "copy", tmp_path / "link" / "main.beancount", ald))
    r = reply["result"]
    assert (r["outcome"], r["reason"], r["detail"]) == ("not_created", "cannot_place", "inside_documents"), r
    assert list((real / "docs").iterdir()) == [], "nothing was written into the documents folder"
    assert "checking_change" not in events


def test_a_missing_source_is_refused_and_nothing_stays(ald: Path, tmp_path: Path) -> None:
    target = tmp_path / "copy"
    reply, _ = copy(ald, params(target, tmp_path / "nothing.beancount", ald))
    r = reply["result"]
    assert (r["outcome"], r["reason"], r["left"]) == ("not_created", "source_missing", []), r
    assert not target.exists()


def test_an_encrypted_ledger_file_is_refused(ald: Path, tmp_path: Path) -> None:
    books = tmp_path / "src"
    books.mkdir()
    (books / "secret.beancount.gpg").write_bytes(b"\x85\x01\x0c")
    (books / "main.beancount").write_text('include "secret.beancount.gpg"\n')
    reply, _ = copy(ald, params(tmp_path / "copy", books / "main.beancount", ald))
    assert (reply["result"]["reason"], reply["result"]["path"]) == ("encrypted", str(books / "secret.beancount.gpg"))


def write_open_journal(ald: Path, file: Path) -> None:
    rec = journal.Record(
        session="s1",
        job="j1",
        workspace_key="k1",
        lineage="0d4c1f1e-0b8a-4c2e-9a39-5b1f0f6f2a10",
        engine_build="dev",
        backup_dir=str(ald / "local" / "k1" / "backups" / journal.record_name("s1", "j1")),
        files=(
            journal.FileEntry(
                canonical_path=str(file),
                path_key=str(file).lower(),
                pre="0" * 64,
                post="1" * 64,
                backup=journal.backup_name(0, file.name),
                temp=journal.temp_name(file.name, "s1-j1"),
            ),
        ),
        created_dirs=(),
    )
    jd = ald / "local" / "k1" / "journal"
    jd.mkdir(parents=True)
    (jd / journal.file_name(journal.record_name("s1", "j1"), journal.State.OPEN)).write_bytes(journal.encode(rec))


def test_an_open_journal_naming_a_source_refuses_the_copy(ald: Path, tmp_path: Path) -> None:
    source = sources(tmp_path)
    write_open_journal(ald, tmp_path / "data" / "shared" / "rates.beancount")
    target = tmp_path / "copy"
    reply, events = copy(ald, params(target, source, ald))
    r = reply["result"]
    assert (r["outcome"], r["reason"]) == ("not_created", "recovery_blocked"), r
    assert r["path"] == str(tmp_path / "data" / "shared" / "rates.beancount")
    assert "gate" not in events
    assert not target.exists()


@pytest.fixture
def held_lock(tmp_path: Path) -> Iterator[Path]:
    """The old app's `.write.lock`, held by another open file description the way portalocker holds it on POSIX."""
    lock = tmp_path / "legacy" / "data" / ".write.lock"
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"")
    fd = os.open(lock, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        yield lock
    finally:
        os.close(fd)


def test_a_held_legacy_lock_refuses_the_copy_and_creates_nothing_there(
    ald: Path, tmp_path: Path, held_lock: Path
) -> None:
    source = sources(tmp_path)
    before = snapshot(held_lock.parent)
    reply, _ = copy(ald, params(tmp_path / "copy", source, ald, [str(held_lock)]))
    r = reply["result"]
    assert (r["outcome"], r["reason"], r["path"]) == ("not_created", "legacy_saving", str(held_lock)), r
    assert snapshot(held_lock.parent) == before


def test_an_absent_legacy_lock_is_never_created(ald: Path, tmp_path: Path) -> None:
    source = sources(tmp_path)
    legacy = tmp_path / "legacy" / "data"
    legacy.mkdir(parents=True)
    reply, _ = copy(ald, params(tmp_path / "copy", source, ald, [str(legacy / ".write.lock")]))
    assert reply["result"]["outcome"] == "created", reply
    assert list(legacy.iterdir()) == [], "INV-26: nothing created under a legacy root"


def test_the_outcome_names_every_entry_after_a_copy_cut_before_its_manifest(ald: Path, tmp_path: Path) -> None:
    source = sources(tmp_path)
    target = tmp_path / "Books copy"
    with pytest.raises(InputEnded):
        copy(ald, params(target, source, ald), proceed=False)
    reply = json.loads(
        engine(ald).handle(request("copy_outcome", params(target, source, ald)), Channel(1, proceed=True))
    )
    assert reply["ok"], reply
    assert reply["result"]["manifest"] == "absent"
    assert reply["result"]["left"] == ["documents", "main.beancount", "outside", "prices"]


@pytest.mark.parametrize("op", ["copy", "copy_outcome"])
def test_no_copy_op_runs_in_an_engine_without_the_engine_lock(tmp_path: Path, op: str) -> None:
    source = sources(tmp_path)
    reply = json.loads(
        engine(None).handle(request(op, params(tmp_path / "c", source, tmp_path)), Channel(1, proceed=True))
    )
    assert not reply["ok"]
    assert reply["error"]["key"] == "engine.no_engine_lock"


def test_an_empty_documents_folder_comes_in_empty(ald: Path, tmp_path: Path) -> None:
    """r1: Beancount reports a documents root that does not exist, so the copy makes it even with nothing in it (a
    fresh v0.3 ledger's documents folder is empty); without it the copy would never load the same."""
    books = tmp_path / "data" / "ledgers"
    books.mkdir(parents=True)
    (tmp_path / "data" / "documents" / "books-1a2b").mkdir(parents=True)
    (books / "main.beancount").write_text(
        'option "documents" "../documents/books-1a2b"\n2026-01-01 open Assets:Cash EUR\n'
    )
    target = tmp_path / "copy"
    reply, _ = copy(ald, params(target, books / "main.beancount", ald))
    assert reply["result"]["outcome"] == "created", reply
    assert (target / "documents").is_dir()
    assert list((target / "documents").iterdir()) == []
