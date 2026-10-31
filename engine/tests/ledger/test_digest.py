"""The digest R4 compares (`ledger_engine.ledger.digest`; `design/specs/engine-jobs.md` §6.7 "Digests"; BL-0118;
S0066). Written from the spec's lines:

- "the adopted digest is the SHA-256 of a canonical encoding of the load result (entries in Beancount's order, ties
  broken by canonical text; the error keys; the options plugins can read); the parse digest is the same over the
  parser's output. File names inside entries, errors and options are encoded as `path_key`s" "[no false mismatch from
  spelling or hash order]";
- BL-0118: the digest "must leave out `options["input_hash"]`": a `touch`, or the save itself, changes it.

The replica (`overlay.load`) and Beancount's own loader (`beancount_port.reconcile_load`) give one digest on one disk:
that is what lets R4 pass.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core.amount import Amount
from beancount.core.data import Open, Posting, Transaction

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import digest, overlay
from ledger_engine.ledger.policy import DesktopPolicy

pytestmark = [
    pytest.mark.inv("INV-01"),
    pytest.mark.skipif(sys.platform != "darwin", reason="path keys are resolved on the macOS port until BL-0087"),
]

POLICY = DesktopPolicy()
MAIN = b"""option "title" "Digest"
option "operating_currency" "USD"
plugin "beancount.plugins.implicit_prices"
include "inc/*.bean"
2026-01-01 open Assets:Cash
2026-01-01 open Expenses:Food
2026-01-01 open Assets:Broker
2026-01-02 * "Lunch" #food ^trip
  Expenses:Food  5.00 USD
  Assets:Cash
2026-01-03 * "Buy"
  Assets:Broker  2 ABC {10.00 USD}
  Assets:Cash
2026-01-04 balance Assets:Cash  -100 USD
2026-01-05 custom "budget" Expenses:Food "monthly" 100.00 USD
2026-01-06 document Assets:Cash "doc.pdf"
"""
INC = b'2026-01-02 * "Coffee" #a #b #c\n  memo: "x"\n  Expenses:Food  3 USD\n  Assets:Cash\n'


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    root = tmp_path / "L"
    (root / "inc").mkdir(parents=True)
    (root / "main.bean").write_bytes(MAIN)
    (root / "inc" / "a.bean").write_bytes(INC)
    (root / "doc.pdf").write_bytes(b"%PDF")
    return root / "main.bean"


def keys() -> digest.KeyOf:
    return digest.path_keys(for_this_os())


def real(root: Path) -> str:
    entries, errors, options = port.reconcile_load(str(root))
    return digest.of_result(entries, errors, options, keys())


def replica(root: Path) -> str:
    loaded = overlay.load(str(root), {}, POLICY)
    assert isinstance(loaded, overlay.Loaded)
    return digest.of_result(loaded.entries, loaded.errors, loaded.options, keys())


def test_the_replica_and_beancounts_loader_give_one_digest(ledger: Path) -> None:
    entries, errors, _ = port.reconcile_load(str(ledger))
    assert errors, "the fixture has a failing balance assertion: errors are part of the digest"
    assert any(isinstance(e, Transaction) for e in entries)
    assert replica(ledger) == real(ledger)
    assert real(ledger).startswith("sha256:")


def test_a_touch_changes_nothing_though_beancount_s_input_hash_moves(ledger: Path) -> None:
    before = port.reconcile_load(str(ledger))[2]["input_hash"]
    d = real(ledger)
    st = ledger.stat()
    os.utime(ledger, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert port.reconcile_load(str(ledger))[2]["input_hash"] != before  # BL-0118: Beancount's own value moved
    assert real(ledger) == d


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b"5.00 USD", b"5.01 USD"),  # an amount
        (b"5.00 USD", b"5.0 USD"),  # its precision: Beancount keeps it
        (b'memo: "x"', b'memo: "y"'),  # a meta value
        (b"#a #b #c", b"#a #b"),  # a tag
        (b"-100 USD", b"-108 USD"),  # an error gone (the assertion now holds)
        (b'"Digest"', b'"Other"'),  # an option
        (b"{10.00 USD}", b"{11.00 USD}"),  # a cost
    ],
)
def test_a_change_to_the_result_changes_the_digest(ledger: Path, old: bytes, new: bytes) -> None:
    d = real(ledger)
    for f in (ledger, ledger.parent / "inc" / "a.bean"):
        data = f.read_bytes()
        if old in data:
            f.write_bytes(data.replace(old, new, 1))
            break
    else:
        pytest.fail(f"{old!r} is in no fixture file")
    assert real(ledger) != d


def test_two_spellings_of_one_file_give_one_digest(ledger: Path) -> None:
    """§6.7: file names encode as `path_key`s: a case-insensitive volume's two spellings, or a link to the folder."""
    link = ledger.parent.parent / "link"
    link.symlink_to(ledger.parent)
    assert real(link / "main.bean") == real(ledger)
    if os.path.exists(str(ledger.parent / "MAIN.bean")):  # a case-insensitive volume (APFS default)
        assert real(ledger.parent / "MAIN.bean") == real(ledger)


def txn(tags: frozenset[str], meta: dict[str, object]) -> Transaction:
    posting = Posting(
        "Assets:Cash", Amount(Decimal(1), "USD"), None, None, None, {"filename": "/l/x.bean", "lineno": 3}
    )
    return Transaction(meta, datetime.date(2026, 1, 1), "*", None, "n", tags, frozenset(), [posting])


def test_set_and_dict_order_do_not_show() -> None:
    k = digest.path_keys(for_this_os())
    a = txn(frozenset(["a", "b", "c", "d"]), {"filename": "/l/x.bean", "lineno": 1, "x": 1, "y": 2})
    b = txn(frozenset(["d", "c", "b", "a"]), {"y": 2, "x": 1, "lineno": 1, "filename": "/l/x.bean"})
    assert digest.of_result([a], [], {}, k) == digest.of_result([b], [], {}, k)


def test_entries_in_another_order_of_equal_rank_give_one_digest() -> None:
    """Ties broken by canonical text: a plugin that emits two same-day entries in another order changes nothing."""
    k = digest.path_keys(for_this_os())
    meta = {"filename": "/l/x.bean", "lineno": 0}
    one = Open(meta, datetime.date(2026, 1, 1), "Assets:A", [], None)
    two = Open(meta, datetime.date(2026, 1, 1), "Assets:B", [], None)
    assert digest.of_result([one, two], [], {}, k) == digest.of_result([two, one], [], {}, k)
    assert digest.of_result([one], [], {}, k) != digest.of_result([one, two], [], {}, k)


def test_the_errors_are_part_of_the_digest_and_their_order_is_not() -> None:
    k = digest.path_keys(for_this_os())
    one = port.load_error("one")
    two = port.load_error("two")
    assert digest.of_result([], [one], {}, k) != digest.of_result([], [], {}, k)
    assert digest.of_result([], [one], {}, k) != digest.of_result([], [two], {}, k)
    assert digest.of_result([], [one, two], {}, k) == digest.of_result([], [two, one], {}, k)


def test_input_hash_is_left_out_and_other_options_are_not() -> None:
    k = digest.path_keys(for_this_os())
    assert digest.of_result([], [], {"input_hash": "a"}, k) == digest.of_result([], [], {"input_hash": "b"}, k)
    assert digest.of_result([], [], {"title": "a"}, k) != digest.of_result([], [], {"title": "b"}, k)


class Opaque:
    __slots__ = ()


def test_an_object_without_fields_encodes_without_its_address() -> None:
    k = digest.path_keys(for_this_os())
    assert digest.of_result([], [], {"o": Opaque()}, k) == digest.of_result([], [], {"o": Opaque()}, k)


class Node:
    def __init__(self) -> None:
        self.me = self
        self.value = Decimal("1.5")


def test_a_cycle_in_an_option_ends() -> None:
    k = digest.path_keys(for_this_os())
    assert digest.of_result([], [], {"n": Node()}, k) == digest.of_result([], [], {"n": Node()}, k)


def test_a_name_that_cannot_be_resolved_encodes_as_itself() -> None:
    k = digest.path_keys(for_this_os())
    assert k("relative/name") == "n:relative/name"
    assert k("/").startswith("k:")


GEN_DEMO = Path(__file__).resolve().parents[3] / "backend" / "scripts" / "gen_demo.py"


def test_the_replica_and_beancounts_loader_give_one_digest_on_the_54k_demo_ledger(tmp_path: Path) -> None:
    """@regression-guard (S0067: the code was right; the test was missing). BL-0172, BL-0171 (8) (S0066 verifier r1
    F10): the equality R4's `pass` rests on, at the size ADR-0008 names
    (the demo generator's 54k ledger, one file of about 8.7 MB). The generator is the main sidecar's build step; it is
    run as its own program (it writes ledger text; the engine imports nothing of the sidecar, ADR-0054). About 5 s."""
    out = tmp_path / "demo"
    done = subprocess.run(  # noqa: S603 (fixed argv: this interpreter and the repository's own generator)
        [sys.executable, "-I", str(GEN_DEMO), "--size", "54k", "--out", str(out), "--anchor", "2026-10-25"],
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    roots = [p for p in out.iterdir() if p.suffix in (".beancount", ".bean")]
    assert len(roots) == 1, sorted(p.name for p in out.iterdir())
    entries, _, _ = port.reconcile_load(str(roots[0]))
    assert sum(isinstance(e, Transaction) for e in entries) >= 54_000
    assert replica(roots[0]) == real(roots[0])
