"""The write core's sequence model: a Hypothesis `RuleBasedStateMachine` (ARCHITECTURE.md §8.2; S0121).

What it adds to the per-effect sweeps (`test_writer.py`, `test_restore.py`, `test_publish_crash.py`) and the TLA+
replays (`test_save_conformance.py`, `test_recover_conformance.py`, `test_publish_conformance.py`): **sequences** of
the engine's real jobs over one in-memory disk (`tests/fakes/memfs.py`), checked at the level of the ledger's
**entries** as Beancount parses them, against a reference model the test keeps itself.

**The jobs** (the product's own code, with the ports of `memledger.py`):
- the preview (`ledger.preview.run`, with the contract's prepare and the test producer `producer.produce_json`) of a
  one-transaction insert, replace or delete, or one account token, on the published generation;
- the save of the last prepared plan (`ledger.save.run`, with the commit point and the history trim);
- the rebuild with its publish (`ledger.rebuild.run`, from the hint the sidecar would pass) and the open
  (`ledger.opening.run`);
- recover-all in the sidecar's order (`recover_all.list_journals`, then `restore.recover_journal` for each listed
  `.open` record that can be read, as `test_recover_conformance.py` plays it), and Keep (`recover_all.keep`) on a
  journal it left blocking.
Between them: outside edits (a whole transaction added, removed or rewritten, or only a comment; durable, or left
unsynced as a weaker editor does; `edit_unfinished` on the file an unfinished save writes), a crash or an I/O error at
the k-th step of a job (a step: an effect of the fake, or a call of the load, read or observe port; the preview makes no
effect, so its steps are its port calls; `crash_during_save` is the save's case as a rule of its own), a power loss
(one durable state of `MemFS.power_loss_states`, then a restart), a plain restart, the cache emptied (`purge_mirror`),
and a read of the mirror (`mirror.publish.read_published`). `write` is an insert previewed and confirmed at once.

**The reference** (`Ref`): per ledger file the bytes expected; the multiset of the whole ledger's entry keys
(`entry_keys.entry_key`, location-free), moved only by the intents the engine called saved (worked out by this test
from the request, apart from the producer's plan) and by the outside edits (worked out from the edit); the unfinished
save, if any, with its pre and post bytes; the saves that landed and their pre bytes; every generation the jobs
published and the stamp each carries; the journals recover-all left blocking.

**Invariants after every step** (`check_all`; each has a self-test below that a hand-broken disk or reference fails):
1. `inv_files`: every ledger file holds bytes the reference allows: its expected bytes, or the post bytes of an
   unfinished save (never partial);
2. `inv_entries`: the ledger's entry multiset, parsed from the disk, is the reference's (the pre or the post one of an
   unfinished save, by what the disk holds): the saved intents and the outside edits applied, nothing else changed;
3. `inv_saved_kept`: the bytes of every save the engine called saved are still there, unless a later outside edit or
   save replaced them;
4. `inv_unchanged`: a refusal before the commit point (the save's gate, the preview) and every preview, rebuild and
   open leave every ledger file byte-identical (checked in the rule, against the bytes before the job);
5. `inv_mirror`: the mirror's `current.db` always resolves to a complete generation one of the jobs published, with
   that job's stamp (the last one published, unless a power loss came since); and in the rules, the freshness the
   preview and the save's gate acted on agrees with `fresh_now` (the stamp's ledger set has the stamp's state on the
   disk now), and a `loaded` rebuild or open stamps exactly the files the disk holds, never beside an `.open` journal
   (nor does a preview prepare beside one: engine-jobs §7); and no job that met no injected fault answers `moved`
   (nothing acts while a job runs here, so the disk did not move: `_never_moved`);
6. `inv_history`: every `.open` record, and each of the newest 16 saves that landed (`KEEP`: engine-jobs §6.5's number
   of `.closed` records), has its record and a backup holding each replaced file's pre bytes;
7. `inv_recovered`: right after recover-all, no `.open` journal is left that it did not report blocking; and at every
   step no `.open` journal is left but the unfinished save's or a blocking one; no job but recover-all runs after a
   restart before recover-all ended (INV-32: the rules' preconditions).

**Run sizes:** the bounded profile (`BOUNDED`, derandomized, no example database) runs at every push; the long
profile (`FT_WRITE_MODEL=long`) is for T2 and the owner's long runs (random, Hypothesis's database in the ignored
`.hypothesis/`). The bounded run asserts at its end that every rule fired, and that a crash during each job kind and a
power loss happened (`test_the_sequence_model_holds_and_reaches_every_rule`).

**What the bounded run does not catch** (seeded mutants, S0121 journal; each killed by the per-effect suites):
- recovery skipping its flush of a file found at pre (`restore-skips-the-flush`): it needs an unsynced outside edit, a
  save cut before its first swap and then recovery, which the bounded run rarely draws (the long profile may);
  `test_recover_conformance.py`'s weak world and `test_restore.py` kill it;
- a rollback that fails too: with one fault per job, `Unfinished` and `NotSaved(open_journal=True)` cannot happen (both
  need the rollback to fail after the save's own fault), so their branch in `do_confirm` is kept for the long profile's
  sake but dead here; `test_writer.py`'s faults inside the rollback cover them.
The history's limit is reached by a sequence of its own (`test_the_history_keeps_the_newest_16_over_18_saves`), not by
the bounded run (at most a few saves per example).

**Fidelity limit** of the fake load: `memledger.py`'s docstring (plain includes only, no plugins of the ledger's own,
no documents, no encrypted or special files, the rebuild's real load replaced by the overlay load; each covered on the
real disk by the tests it names). The ledger's set of files is fixed: no edit adds or removes an `include`.
"""

from __future__ import annotations

import contextlib
import datetime
import errno
import hashlib
import itertools
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import pytest
from hypothesis import HealthCheck, settings, stateful
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule

from ledger_engine.ledger import opening as op
from ledger_engine.ledger import preview as pv
from ledger_engine.ledger import publishing
from ledger_engine.ledger import rebuild as rb
from ledger_engine.ledger import recover_all as ra
from ledger_engine.ledger import restore as rs
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.beancount_port import Directive, Posting, Transaction, parse_string
from ledger_engine.ledger.core import entry_keys, journal
from ledger_engine.ledger.core.journal import Record, State
from ledger_engine.ledger.core.ledger_set import Absent, Content, Fingerprint, decode
from ledger_engine.ledger.writer import Change, NotSaved, Unfinished
from ledger_engine.mirror import publish as pb
from tests.fakes.memfs import Crash, MemFS, tears
from tests.ledger import memledger, producer
from tests.mirror.fake_store import FakeStore
from tests.windows_pending import BL_0282

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22"), pytest.mark.inv("INV-32")]

# -- the world ---------------------------------------------------------------------------------------------------------

ROOT: Final = "/L/main.bean"
OPENS: Final = "/L/open.bean"
TX: Final = "/L/tx.bean"
FILES: Final = (ROOT, OPENS, TX)
ALD: Final = "/w"
KEY: Final = "k1"
LINEAGE: Final = "lin1"
CACHE: Final = "/cache"
BUILD: Final = "b"
SESSION: Final = "s1"
JD: Final = f"{ALD}/local/{KEY}/journal"
MIRROR: Final = f"{CACHE}/workspaces/{KEY}/mirror"
TODAY: Final = datetime.date(2026, 11, 15)
NOW_NS: Final = 10**18  # after every time of the fake's logical clock: no generation is in its grace period
WHERE: Final = pv.Where(CACHE, KEY, ALD, LINEAGE, BUILD)
TARGET: Final = publishing.Target(CACHE, KEY, ALD, LINEAGE, 0, TODAY, "2026-11-15T12:00:00Z", NOW_NS, BUILD)
PRODUCERS: Final = {producer.TEST_MUTATOR: producer.produce_json}

INITIAL: Final[Mapping[str, bytes]] = {
    ROOT: b'option "operating_currency" "EUR"\ninclude "open.bean"\ninclude "tx.bean"\n\n'
    b'2026-01-05 * "Salary"\n  Assets:Bank  1000.00 EUR\n  Income:Salary\n',
    OPENS: b"2026-01-01 open Assets:Bank\n2026-01-01 open Assets:Cash\n2026-01-01 open Expenses:Food\n"
    b"2026-01-01 open Expenses:Rent\n2026-01-01 open Income:Salary\n",
    TX: b'2026-02-01 * "Shop"\n  Expenses:Food  12.50 EUR\n  Assets:Bank\n\n'
    b'2026-02-03 * "Landlord"\n  Expenses:Rent  700.00 EUR\n  Assets:Bank\n',
}
SWAP: Final = {
    "Expenses:Food": "Expenses:Rent",
    "Expenses:Rent": "Expenses:Food",
    "Assets:Bank": "Assets:Cash",
    "Assets:Cash": "Assets:Bank",
    "Income:Salary": "Assets:Cash",
}
"""A token edit's new account for each account the ledger uses (all of them are open)."""

SPARE: Final = '2026-05-05 * "Cafe"\n  Expenses:Food  3.20 EUR\n  Assets:Cash\n'
"""The insert a rule makes when it has no transaction to edit, and the crashing preview's."""

Job = Literal["preview", "confirm", "rebuild", "open", "recover"]
JOBS: Final[tuple[Job, ...]] = ("preview", "confirm", "rebuild", "open", "recover")

LENGTH: Final[Mapping[Job, int]] = {"preview": 8, "confirm": 36, "rebuild": 22, "open": 16, "recover": 12}
"""At least as many steps as each job takes here, so a crash point drawn up to it can land on any of them (measured,
and held by `test_each_job_s_steps_fit_its_crash_range`): a preview 7, a save 33, a rebuild that loads 21, an open 2
with a fresh mirror and 15 when it loads, recover-all 0 with no journal folder and about 12 with an `.open` journal."""

INJECTED: Final = "injected by the model"
KEEP: Final = 16
"""The history kept of each kind (engine-jobs §6.5), the spec's number, not the product's constant."""

TALLY: Counter[str] = Counter()
"""What the runs reached: each rule, each job's outcomes, each crash that fired (the teardown's assertion)."""


@st.composite
def txn_text(draw: st.DrawFn) -> str:
    month = draw(st.integers(2, 10))
    day = draw(st.integers(1, 28))
    payee = draw(st.sampled_from(["Shop", "Cafe", "Landlord", "Market"]))
    cents = draw(st.integers(1, 99_999))
    expense = draw(st.sampled_from(["Expenses:Food", "Expenses:Rent"]))
    asset = draw(st.sampled_from(["Assets:Bank", "Assets:Cash"]))
    return f'2026-{month:02d}-{day:02d} * "{payee}"\n  {expense}  {cents // 100}.{cents % 100:02d} EUR\n  {asset}\n'


# -- reading the disk --------------------------------------------------------------------------------------------------


def parsed(data: bytes | None, path: str) -> list[Directive]:
    return [] if data is None else list(parse_string(data, path)[0])


def keys_of(files: Mapping[str, bytes | None]) -> Counter[str]:
    """The multiset of the entries' keys Beancount's parser finds in these bytes (location-free)."""
    return Counter(entry_keys.entry_key(e) for p, data in files.items() for e in parsed(data, p))


def disk(fs: MemFS) -> dict[str, bytes | None]:
    return {p: fs.file(p) for p in FILES}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transactions(fs: MemFS) -> list[tuple[str, Transaction]]:
    return [(p, e) for p in FILES for e in parsed(fs.file(p), p) if isinstance(e, Transaction)]


def fresh_now(fs: MemFS, stamp: str) -> bool:
    """The oracle's own freshness (publish.md §7): the stamp's ledger set holds on the disk now (`holds`)."""
    return holds(fs, decode(stamp.encode("ascii")))


def holds(fs: MemFS, fp: Fingerprint) -> bool:
    """Every file `fp` names is in the state it names, and every plain include matches as it did (written apart from
    `fingerprint.observe` and `memledger.observe`)."""
    for p, state in fp.files:
        data = fs.file(p)
        now = Absent() if data is None else Content(sha(data))
        if now != state:
            return False
    return all(((g,) if fs.file(g) is not None else ()) == m for g, m in fp.globs)


def current_stamp(fs: MemFS) -> tuple[str, str] | None:
    cur = pb.read_published(fs, CACHE, KEY, FakeStore())
    return None if cur is None else (cur.gen.name, cur.meta.fingerprint)


def open_journals(fs: MemFS) -> set[str]:
    if fs.resolve(JD).rest:
        return set()
    out: set[str] = set()
    for n in fs.names_in(JD):
        p = journal.parse_file_name(n)
        if p is not None and p[1] is State.OPEN:
            out.add(p[0])
    return out


def record_at(fs: MemFS, name: str, state: State) -> Record | None:
    data = None if fs.resolve(JD).rest else fs.file(f"{JD}/{journal.file_name(name, state)}")
    if data is None:
        return None
    rec = journal.decode(data)
    return rec if isinstance(rec, Record) else None


def record_state(fs: MemFS, name: str) -> State | None:
    if fs.resolve(JD).rest:
        return None
    states = [p[1] for n in fs.names_in(JD) if (p := journal.parse_file_name(n)) is not None and p[0] == name]
    assert len(states) <= 1, f"{name} in several states: {states}"
    return states[0] if states else None


# -- the reference -----------------------------------------------------------------------------------------------------


@dataclass
class Pending:
    """A save the engine did not call saved whose journal may still name it: its files' pre and post bytes, and the
    ledger's keys at post. `post_allowed`: the files may be at post (an unfinished save; never after a `NotSaved`,
    which promises every file at pre). `displaced`: files an outside edit changed since (their bytes are the editor's,
    which recovery must keep)."""

    name: str
    files: dict[str, tuple[bytes, bytes]]
    keys_post: Counter[str]
    post_allowed: bool = True
    displaced: set[str] = field(default_factory=set[str])


@dataclass
class Plan:
    prepared: Any  # contract.Prepared
    delta_in: Counter[str]
    delta_out: Counter[str]


@dataclass
class Ref:
    files: dict[str, bytes]
    keys: Counter[str]
    pending: Pending | None = None
    saved_post: dict[str, bytes] = field(default_factory=dict[str, bytes])
    landed: list[tuple[str, dict[str, str]]] = field(default_factory=list[tuple[str, dict[str, str]]])
    published: dict[str, str] = field(default_factory=dict[str, str])  # generation name -> its stamp
    current: str | None = None
    lost_power: bool = False  # since the last publish: `current.db` may name an earlier generation
    hint: Fingerprint | None = None
    plan: Plan | None = None
    needs_recovery: bool = False
    blocking: set[str] = field(default_factory=set[str])
    unsynced: dict[str, bytes] = field(default_factory=dict[str, bytes])  # path -> its bytes a power loss may keep

    def apply(self, path: str, data: bytes, plus: Counter[str], minus: Counter[str]) -> None:
        """`path` now holds `data`, its entries changed by `plus` and `minus` (an outside edit): in the ledger's keys,
        and in an unfinished save's post keys too, since the edit holds whichever of its states the disk is in."""
        self.files[path] = data
        self.keys = +(self.keys + plus - minus)
        pend = self.pending
        if pend is not None:
            pend.keys_post = +(pend.keys_post + plus - minus)


# -- the invariants (pure: each takes the disk and the reference; the self-tests break one or the other) ---------------


def allowed(ref: Ref, path: str) -> set[bytes]:
    out = {ref.files[path]}
    pend = ref.pending
    if pend is not None and path in pend.files and path not in pend.displaced and pend.post_allowed:
        out.add(pend.files[path][1])
    return out


def inv_files(fs: MemFS, ref: Ref) -> None:
    for p in FILES:
        now = fs.file(p)
        assert now is not None, f"{p} is gone"
        assert now in allowed(ref, p), f"{p} holds bytes the reference does not allow: {now!r}"


def at_post(fs: MemFS, ref: Ref) -> bool:
    pend = ref.pending
    if pend is None or not pend.post_allowed:
        return False
    live = [p for p in pend.files if p not in pend.displaced]
    return bool(live) and all(fs.file(p) == pend.files[p][1] != ref.files[p] for p in live)


def inv_entries(fs: MemFS, ref: Ref) -> None:
    want = ref.pending.keys_post if at_post(fs, ref) and ref.pending is not None else ref.keys
    got = keys_of(disk(fs))
    assert got == want, f"the ledger's entries differ from the reference: extra {got - want}, missing {want - got}"


def inv_saved_kept(fs: MemFS, ref: Ref) -> None:
    pend = ref.pending
    for p, data in ref.saved_post.items():
        if pend is not None and pend.post_allowed and p in pend.files and fs.file(p) == pend.files[p][1]:
            continue  # a later save, unfinished, replaced it
        assert fs.file(p) == data, f"a change the engine called saved is lost from {p}"


def inv_unchanged(before: Mapping[str, bytes | None], fs: MemFS, why: str) -> None:
    for p in FILES:
        assert fs.file(p) == before[p], f"{why} changed {p}"


def inv_mirror(fs: MemFS, ref: Ref) -> None:
    link = None if fs.resolve(MIRROR).rest else f"{MIRROR}/{pb.CURRENT}"
    if link is None or pb.CURRENT not in fs.names_in(MIRROR):
        assert ref.current is None or ref.lost_power, "the published mirror is gone"
        return
    cur = current_stamp(fs)
    assert cur is not None, "current.db does not resolve to a complete generation"
    name, stamp = cur
    assert ref.published.get(name) == stamp, f"current.db names {name}, which no job published with that stamp"
    if not ref.lost_power:
        assert name == ref.current, f"current.db names {name}, the last publish was {ref.current}"


def check_fresh_answer(fs: MemFS, said_fresh: bool, what: str) -> None:
    """Rule 5's agreement: `said_fresh` is what the job acted on; the oracle's reading of the disk must say the same."""
    cur = current_stamp(fs)
    truth = cur is not None and fresh_now(fs, cur[1])
    assert said_fresh == truth, (
        f"{what} acted on a mirror {'fresh' if said_fresh else 'not fresh'}; the disk says {truth}"
    )


def inv_history(fs: MemFS, ref: Ref) -> None:
    def backed(rec: Record) -> None:
        for f in rec.files:
            if f.backup is not None:
                data = fs.file(f"{rec.backup_dir}/{f.backup}")
                assert data is not None, f"{rec.name}: the backup of {f.canonical_path} is gone"
                assert sha(data) == f.pre, f"{rec.name}: the backup of {f.canonical_path} holds other bytes"

    for name in open_journals(fs):
        rec = record_at(fs, name, State.OPEN)
        if rec is not None:
            backed(rec)
    for name, pres in ref.landed[-KEEP:]:
        rec = record_at(fs, name, State.CLOSED)
        assert rec is not None, f"the history lost the record of {name}, one of the newest {KEEP}"
        backed(rec)
        assert {f.canonical_path: f.pre for f in rec.files} == pres, f"{name}: its record names other pre bytes"


def inv_recovered(fs: MemFS, ref: Ref, *, just_recovered: bool) -> None:
    """`ref.blocking` holds the journals recover-all reported blocking, and "" when a journal folder could not be
    listed, which blocks every open whatever it holds."""
    if "" in ref.blocking:
        return
    left = open_journals(fs) - ref.blocking
    if not just_recovered and ref.pending is not None:
        left.discard(ref.pending.name)
    if just_recovered or not ref.needs_recovery:
        assert not left, f"an .open journal left that recover-all did not report blocking: {sorted(left)}"


# -- the machine -------------------------------------------------------------------------------------------------------


@dataclass
class _Job:
    gated: bool = False
    landed: bool = False

    def progress(self, phase: str) -> None:
        del phase

    def gate(self) -> None:
        self.gated = True

    def committed(self) -> None:
        self.landed = True


class WriteSequence(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.fs = MemFS()
        self.ref = Ref(dict(INITIAL), keys_of(INITIAL))
        self.saves = 0
        self.tags = itertools.count(1)
        self.notes = itertools.count(1)
        self.steps = 0
        self.armed: tuple[int, bool] | None = None
        self.fired = False

    # -- the fault -----------------------------------------------------------------------------------------------------

    def _tick(self, *_: object) -> None:
        if self.armed is None:
            return
        k, io = self.armed
        if self.steps == k:
            self.armed = None
            self.fired = True
            if io:
                raise OSError(errno.ENOSPC, INJECTED)
            raise Crash(INJECTED)
        self.steps += 1

    @contextlib.contextmanager
    def _faults(self, fault: tuple[int, bool] | None) -> Generator[None]:
        self.steps, self.armed, self.fired = 0, fault, False
        self.fs.on_effect = self._tick
        try:
            yield
        finally:
            self.fs.on_effect = None
            self.armed = None

    def _hook(self, what: str) -> None:
        self._tick(what)

    # -- the ports -----------------------------------------------------------------------------------------------------

    def _observe(self, ls: Any) -> Fingerprint:
        self._hook("observe")
        return memledger.observe(self.fs, ls)

    def _rebuild_ports(self) -> rb.Ports:
        def load(root: str, parsed_: list[str]) -> Any:
            self._hook("load")
            return memledger.real_load(self.fs, root, parsed_)

        return rb.Ports(
            load=load,
            warnings=lambda e, o, p: list(memledger.warnings(self.fs, e, o, p)),
            observe=self._observe,
            fs=self.fs,
            store=FakeStore(),
            tag=lambda: f"{next(self.tags):016x}",
        )

    def _open_ports(self) -> op.Ports:
        def load(root: str) -> Any:
            self._hook("load")
            from ledger_engine.ledger.policy import DESKTOP

            return memledger.load(self.fs, root, {}, DESKTOP)

        return op.Ports(
            load=load, observe=self._observe, fs=self.fs, store=FakeStore(), tag=lambda: f"{next(self.tags):016x}"
        )

    # -- set-up --------------------------------------------------------------------------------------------------------

    @initialize()
    def boot(self) -> None:
        for d in ("/L", ALD, CACHE):
            self.fs.outside_mkdirs(d)
        for p, data in INITIAL.items():
            self.fs.outside_write(p, data)
        self.do_recover(None)
        self.do_open(None)
        assert self.ref.current is not None, "the first open published no generation"

    # -- the jobs ------------------------------------------------------------------------------------------------------

    def _published(self, out: pb.Published | pb.NotPublished | None, by: str) -> None:
        if not isinstance(out, pb.Published):
            TALLY[f"{by}:not_published:{getattr(out, 'why', None)}"] += 1
            return
        cur = current_stamp(self.fs)
        assert cur is not None, f"{by} published {out.gen.name}; current.db resolves to no generation"
        assert cur[0] == out.gen.name, f"{by} published {out.gen.name}; current.db names {cur[0]}"
        stamp = cur[1]
        assert not open_journals(self.fs), f"{by} published beside an .open journal (engine-jobs §7)"
        assert fresh_now(self.fs, stamp), f"{by} published a generation that is not fresh on the disk"
        fp = decode(stamp.encode("ascii"))
        want = {p: Content(sha(self.ref.files[p])) for p in FILES}
        assert dict(fp.files) == want, f"{by}'s stamp names {fp.files}, the disk holds {want}"
        self.ref.published[out.gen.name] = stamp
        self.ref.current = out.gen.name
        self.ref.lost_power = False

    def _never_moved(self, what: str, outcome: str, published: object) -> None:
        """No outside program acts while a job runs here, so a job that met no injected fault and answers `moved`
        (or whose publish answers it) tells a false outcome: the disk did not move, and a sidecar would try again for
        ever (three tries, then `LEDGER_CHANGING`) with saves refused meanwhile."""
        if self.fired:
            return
        assert outcome != "moved", f"{what} answered moved on a disk that did not move"
        why = getattr(published, "why", None)
        assert why != "moved", f"{what}'s publish answered moved on a disk that did not move"

    def do_preview(self, kind: str, pick: int, text: str, fault: tuple[int, bool] | None) -> None:
        fs = self.fs
        before = fs.snapshot()
        cur = current_stamp(fs)
        fresh = cur is not None and fresh_now(fs, cur[1])
        gen = pb.read_published(fs, CACHE, KEY, FakeStore())
        number = 0 if gen is None else gen.gen.number
        txns = transactions(fs)
        plus: Counter[str] = Counter()
        minus: Counter[str] = Counter()
        if kind == "insert" or not txns:
            text = text or SPARE
            edit: dict[str, object] = {"kind": "insert", "text": text, "default_file": TX}
            key = entry_keys.key_of_text(text, TX)
            assert key is not None
            plus[key] += 1
            kind = "insert"
        else:
            path, entry = txns[pick % len(txns)]
            where = {"path": path, "line": entry.meta["lineno"], "date": entry.date.isoformat(), "keyword": "txn"}
            minus[entry_keys.entry_key(entry)] += 1
            if kind == "replace":
                edit = {"kind": "replace", "text": text, **where}
                key = entry_keys.key_of_text(text, path)
                assert key is not None
                plus[key] += 1
            elif kind == "delete":
                edit = {"kind": "delete", **where}
            else:
                posting: Posting = entry.postings[pick % len(entry.postings)]
                new = SWAP[posting.account]
                renamed = entry._replace(
                    postings=[p._replace(account=new) if p is posting else p for p in entry.postings]
                )
                plus[entry_keys.entry_key(renamed)] += 1
                assert posting.meta is not None
                edit = {"kind": "token", "lineno": posting.meta["lineno"], "old": posting.account, "new": new, **where}
        job = _Job()
        try:
            with self._faults(fault):
                ports = memledger.preview_ports(fs, FakeStore(), self._hook)
                ans = pv.run(ports, ROOT, number, producer.TEST_MUTATOR, {"edits": [edit]}, WHERE, job, PRODUCERS)
        except Crash:
            TALLY["crash:preview"] += 1
            assert fs.snapshot() == before, "a preview that died wrote something"
            return
        except OSError:
            TALLY["io:preview"] += 1
            assert fs.snapshot() == before, "a preview that failed wrote something"
            return
        if self.fired:
            TALLY["io:preview"] += 1
        assert fs.snapshot() == before, f"a preview ({ans.outcome}) wrote something"
        TALLY[f"preview:{kind}:{ans.outcome}:{ans.stale or ''}"] += 1
        self._never_moved("the preview", ans.outcome, None)
        if ans.outcome == "paused":
            assert open_journals(fs), "paused with no .open journal"
            return
        if ans.outcome == "stale":
            if ans.stale == "not_fresh" and not self.fired:
                check_fresh_answer(fs, False, "the preview")
                if ans.hint is not None:
                    self.ref.hint = ans.hint
            return
        if not self.fired:
            check_fresh_answer(fs, fresh, "the preview")
            assert fresh, "the preview planned on a generation that is not fresh"
        if ans.outcome == "prepared":
            assert not open_journals(fs), "a preview prepared a plan beside an .open journal (engine-jobs §7)"
            prep = ans.prepared
            assert prep is not None
            posts = {p: fs.file(p) for p in FILES} | {f.path: f.text.encode() for f in prep.files}
            want = +(self.ref.keys + plus - minus)
            got = keys_of(posts)
            assert got == want, f"the prepared plan is not the intent: extra {got - want}, missing {want - got}"
            self.ref.plan = Plan(prep, plus, minus)

    def do_confirm(self, fault: tuple[int, bool] | None) -> None:
        plan = self.ref.plan
        assert plan is not None
        self.ref.plan = None  # a plan is confirmed once (the sidecar's plan store hands it out once)
        fs = self.fs
        prep = plan.prepared
        self.saves += 1
        job_id = f"j{self.saves}"
        name = journal.record_name(SESSION, job_id)
        changes = tuple(Change(f.path, f.pre, f.text.encode()) for f in prep.files)
        identity = fs.resolve(ALD).identity
        assert identity is not None
        sp = sv.SavePlan(SESSION, job_id, KEY, LINEAGE, BUILD, ROOT, changes, prep.fingerprint, CACHE, ALD, identity)
        before = disk(fs)
        cur = current_stamp(fs)
        mirror_fresh = cur is not None and fresh_now(fs, cur[1])
        plan_fresh = holds(fs, prep.fingerprint)
        had_open = bool(open_journals(fs))
        pres = {c.path: before[c.path] or b"" for c in changes}
        posts = {c.path: c.post for c in changes}
        job = _Job()
        out: Any = None
        try:
            with self._faults(fault):
                out = sv.run(sp, sv.Ports(fs, lambda fp: self._observe(fp.ledger_set), FakeStore()), job)
        except (Crash, OSError) as e:
            TALLY[f"{'crash' if isinstance(e, Crash) else 'io'}:confirm"] += 1
            facts = sv.outcome(fs, ALD, KEY, SESSION, job_id, changes)
            TALLY[f"confirm:died:{facts.record}"] += 1
            if facts.record == "closed":
                self._landed(name, pres, posts, plan, sp.fingerprint)
            elif facts.record in ("open", "kept"):
                self._pending(name, pres, posts, plan)
            else:
                at_pre = all(sha(fs.file(c.path) or b"") == c.pre for c in changes)
                assert facts.unchanged == at_pre, (
                    f"§6.6 read unchanged={facts.unchanged}; every target at pre: {at_pre}"
                )
                inv_unchanged(before, fs, "a save that died before its journal")
            if job.gated:
                self._gate_was_right(plan_fresh=plan_fresh, mirror_fresh=mirror_fresh, had_open=had_open)
            return
        if self.fired:
            TALLY["io:confirm"] += 1
        TALLY[f"confirm:{type(out).__name__}:{getattr(out, 'reason', '')}"] += 1
        if isinstance(out, sv.Saved):
            assert job.gated
            assert job.landed
            assert plan_fresh, "saved over a disk that moved since the plan"
            assert mirror_fresh, "saved while the mirror was not fresh"
            assert not had_open, "saved while an .open journal was there"
            self._landed(name, pres, posts, plan, sp.fingerprint)
            return
        if isinstance(out, sv.Refused):
            at_pre = all(sha(fs.file(c.path) or b"") == c.pre for c in changes)
            assert out.unchanged == at_pre, f"a refusal said unchanged={out.unchanged}; every target at pre: {at_pre}"
            inv_unchanged(before, fs, f"a save refused {out.reason}")
            assert not job.gated, "a refusal after the commit point"
            if out.reason == "ledger_changed":
                assert not plan_fresh, "refused ledger_changed on a disk at the plan's bytes"
            elif out.reason == "mirror_stale" and not self.fired:
                check_fresh_answer(fs, False, "the save's gate")
            elif out.reason == "unfinished_save":
                assert had_open
            return
        if job.gated:
            self._gate_was_right(plan_fresh=plan_fresh, mirror_fresh=mirror_fresh, had_open=had_open)
        if isinstance(out, NotSaved):
            inv_unchanged(before, fs, "a save not saved")
            # `open_journal` and `Unfinished` below need the rollback to fail too: never with one fault per job (the
            # module's limits); kept for a profile that injects more
            if out.open_journal:
                self._pending(name, pres, posts, plan, post_allowed=False)
            return
        assert isinstance(out, Unfinished), out
        self._pending(name, pres, posts, plan)

    def _landed(self, name: str, pres: dict[str, bytes], posts: dict[str, bytes], plan: Plan, fp: Fingerprint) -> None:
        for p, data in posts.items():
            self.ref.apply(p, data, Counter(), Counter())
            self.ref.saved_post[p] = data
            self.ref.unsynced.pop(p, None)
        self.ref.keys = +(self.ref.keys + plan.delta_in - plan.delta_out)
        self.ref.landed.append((name, {p: sha(d) for p, d in pres.items()}))
        self.ref.hint = fp

    def _pending(
        self, name: str, pres: dict[str, bytes], posts: dict[str, bytes], plan: Plan, *, post_allowed: bool = True
    ) -> None:
        keys_post = +(self.ref.keys + plan.delta_in - plan.delta_out)
        self.ref.pending = Pending(name, {p: (pres[p], posts[p]) for p in posts}, keys_post, post_allowed)

    def _settle(self, how: str) -> None:
        """The unfinished save once its journal is no longer `.open`: its files as the record's state says."""
        pend = self.ref.pending
        assert pend is not None
        state = record_state(self.fs, pend.name)
        if state is State.OPEN:
            return
        TALLY[f"settled:{how}:{state}"] += 1
        live = [p for p in pend.files if p not in pend.displaced]
        if state in (State.CLOSED, State.RESOLVED, State.KEPT):
            # what each promises is durable: a closed save's files and a resolved one's pre bytes (`restore.Resolved`:
            # "durably holds its pre bytes", the flush of S0101), and what Keep kept ("now" is what a power loss keeps)
            for p in pend.files:
                now = self.fs.file(p)
                durable = now is None or self.fs.synced_file(p) == now
                assert durable, f"{pend.name} {state}, but {p}'s data is not durable"
                self.ref.unsynced.pop(p, None)
        if state is State.CLOSED:
            assert pend.post_allowed
            for p in live:
                assert self.fs.file(p) == pend.files[p][1], f"{pend.name} .closed, {p} not at post"
                self.ref.files[p] = pend.files[p][1]
                self.ref.saved_post.pop(p, None)  # a later save replaced it
            self.ref.keys = pend.keys_post
        elif state is State.KEPT:
            if live and at_post(self.fs, self.ref):
                for p in live:
                    self.ref.files[p] = pend.files[p][1]
                    self.ref.saved_post.pop(p, None)  # the user kept a later save's bytes
                self.ref.keys = pend.keys_post
        else:  # resolved, or (a `.part` never replaced anything) gone
            for p in live:
                assert self.fs.file(p) == pend.files[p][0], f"{pend.name} {state}, {p} not at pre"
        self.ref.pending = None

    def do_rebuild(self, fault: tuple[int, bool] | None) -> None:
        hint = self.ref.hint
        assert hint is not None
        before = disk(self.fs)
        try:
            with self._faults(fault):
                ans = rb.run(self._rebuild_ports(), hint, _Job(), TARGET)
        except (Crash, OSError) as e:
            TALLY[f"{'crash' if isinstance(e, Crash) else 'io'}:rebuild"] += 1
            inv_unchanged(before, self.fs, "a rebuild that died")
            return
        if self.fired:
            TALLY["io:rebuild"] += 1
        inv_unchanged(before, self.fs, "a rebuild")
        TALLY[f"rebuild:{ans.outcome}"] += 1
        self._never_moved("the rebuild", ans.outcome, ans.published)
        if ans.outcome == "loaded":
            self._published(ans.published, "the rebuild")
        elif ans.outcome == "moved":
            self.ref.hint = ans.fingerprint
        elif ans.outcome == "paused":
            assert open_journals(self.fs)
        elif ans.outcome == "fresh":
            check_fresh_answer(self.fs, True, "the rebuild")
        else:
            pytest.fail(f"a rebuild refused a generated ledger: {ans}")

    def do_open(self, fault: tuple[int, bool] | None) -> None:
        before = disk(self.fs)
        try:
            with self._faults(fault):
                ans = op.run(self._open_ports(), ROOT, _Job(), TARGET)
        except (Crash, OSError) as e:
            TALLY[f"{'crash' if isinstance(e, Crash) else 'io'}:open"] += 1
            inv_unchanged(before, self.fs, "an open that died")
            return
        if self.fired:
            TALLY["io:open"] += 1
        inv_unchanged(before, self.fs, "an open")
        TALLY[f"open:{ans.outcome}"] += 1
        self._never_moved("the open", ans.outcome, ans.published)
        if ans.outcome == "loaded":
            self._published(ans.published, "the open")
        elif ans.outcome == "paused":
            assert open_journals(self.fs)
        elif ans.outcome == "fresh":
            check_fresh_answer(self.fs, True, "the open")
        elif ans.outcome == "refused":
            pytest.fail(f"an open refused a generated ledger: {ans}")

    def do_recover(self, fault: tuple[int, bool] | None) -> None:
        """Recover-all as the sidecar orders it: the listing, then a try of each listed `.open` record that can be read;
        one that cannot be read, a try that stops or fails, and a folder that cannot be listed block."""
        self.ref.blocking = set()
        blocking: set[str] = set()
        try:
            with self._faults(fault):
                listing = ra.list_journals(self.fs, ALD)
                if listing.unlisted:  # a folder that cannot be listed blocks every open (it may hold any record)
                    blocking.add("")
                for j in listing.journals:
                    if j.record is None:
                        blocking.add(j.name)
                        continue
                    out = rs.recover_journal(self.fs, j.journal_dir, j.name)
                    TALLY[f"recover:{type(out).__name__}"] += 1
                    if not isinstance(out, rs.Resolved):
                        blocking.add(j.name)
        except (Crash, OSError) as e:
            TALLY[f"{'crash' if isinstance(e, Crash) else 'io'}:recover"] += 1
            self.ref.needs_recovery = True
            return
        if self.fired:
            TALLY["io:recover"] += 1
        self.ref.blocking = blocking
        self.ref.needs_recovery = False
        if self.ref.pending is not None:
            self._settle("recover")
        inv_recovered(self.fs, self.ref, just_recovered=True)

    # -- the rules -----------------------------------------------------------------------------------------------------

    @staticmethod
    def _gate_was_right(*, plan_fresh: bool, mirror_fresh: bool, had_open: bool) -> None:
        """A save that passed its gate (its commit point was asked for) did so on a disk at the plan's fingerprint, a
        fresh mirror and no `.open` journal."""
        assert plan_fresh, "the save passed its gate on a disk that moved since the plan"
        assert mirror_fresh, "the save passed its gate with a mirror that is not fresh"
        assert not had_open, "the save passed its gate beside an .open journal"

    def _ready(self) -> bool:
        return not self.ref.needs_recovery

    @precondition(lambda self: self._ready())
    @rule(text=txn_text())
    def preview_insert(self, text: str) -> None:
        TALLY["rule:preview_insert"] += 1
        self.do_preview("insert", 0, text, None)

    @precondition(lambda self: self._ready())
    @rule(pick=st.integers(0, 1000), text=txn_text())
    def preview_replace(self, pick: int, text: str) -> None:
        TALLY["rule:preview_replace"] += 1
        self.do_preview("replace", pick, text, None)

    @precondition(lambda self: self._ready())
    @rule(pick=st.integers(0, 1000))
    def preview_delete(self, pick: int) -> None:
        TALLY["rule:preview_delete"] += 1
        self.do_preview("delete", pick, "", None)

    @precondition(lambda self: self._ready())
    @rule(pick=st.integers(0, 1000))
    def preview_token(self, pick: int) -> None:
        TALLY["rule:preview_token"] += 1
        self.do_preview("token", pick, "", None)

    @precondition(lambda self: self._ready())
    @rule(text=txn_text(), edit_between=st.booleans(), path=st.sampled_from(FILES))
    def write(self, text: str, edit_between: bool, path: str) -> None:
        """The commonest flow: an insert previewed and confirmed at once; with `edit_between`, an outside program saves
        `path` (a comment) between the preview and the confirm, which the save's gate must meet."""
        TALLY["rule:write"] += 1
        self.do_preview("insert", 0, text, None)
        if self.ref.plan is None:
            return
        if edit_between:
            TALLY["write:edit_between"] += 1
            self.do_outside_edit(path, "comment", 0, text, durable=True)
        self.do_confirm(None)

    @precondition(lambda self: self._ready() and self.ref.plan is not None)
    @rule()
    def confirm(self) -> None:
        TALLY["rule:confirm"] += 1
        self.do_confirm(None)

    @precondition(lambda self: self._ready() and self.ref.hint is not None)
    @rule()
    def rebuild(self) -> None:
        TALLY["rule:rebuild"] += 1
        self.do_rebuild(None)

    @precondition(lambda self: self._ready())
    @rule()
    def open(self) -> None:
        TALLY["rule:open"] += 1
        self.do_open(None)

    @rule(
        path=st.sampled_from(FILES),
        kind=st.sampled_from(["add", "remove", "rewrite", "comment"]),
        pick=st.integers(0, 1000),
        text=txn_text(),
        durable=st.booleans(),
    )
    def outside_edit(self, path: str, kind: str, pick: int, text: str, durable: bool) -> None:
        """An outside program saves a ledger file between jobs."""
        TALLY["rule:outside_edit"] += 1
        # The root is edited durably only: a power loss may tear an unsynced file, and a torn root may lose an
        # `include`, which would change the ledger's set of files (the model keeps it fixed).
        self.do_outside_edit(path, kind, pick, text, durable=durable or path == ROOT)

    def do_outside_edit(self, path: str, kind: str, pick: int, text: str, *, durable: bool) -> None:
        fs, ref = self.fs, self.ref
        data = fs.file(path)
        assert data is not None
        lines = data.decode().splitlines(keepends=True)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        txns = [e for e in parsed(data, path) if isinstance(e, Transaction)]
        starts = sorted(int(e.meta["lineno"]) for e in parsed(data, path))
        plus: Counter[str] = Counter()
        minus: Counter[str] = Counter()
        if kind in ("remove", "rewrite") and txns:
            entry = txns[pick % len(txns)]
            first = int(entry.meta["lineno"]) - 1
            stop = next((s - 1 for s in starts if s - 1 > first), len(lines))
            minus[entry_keys.entry_key(entry)] += 1
            new_lines: list[str] = []
            if kind == "rewrite":
                new_lines = [text, "\n"]
                key = entry_keys.key_of_text(text, path)
                assert key is not None
                plus[key] += 1
            lines[first:stop] = new_lines
        elif kind == "comment":
            lines.append(f"; note {next(self.notes)}\n")
        else:
            kind = "add"
            lines += ["\n", text]
            key = entry_keys.key_of_text(text, path)
            assert key is not None
            plus[key] += 1
        new = "".join(lines).encode()
        TALLY[f"outside:{kind}:{'durable' if durable else 'unsynced'}"] += 1
        pend = ref.pending
        if pend is not None and path in pend.files and path not in pend.displaced:
            if data == pend.files[path][1] != ref.files[path]:  # the editor edited the unfinished save's post bytes
                ref.keys = Counter(pend.keys_post)
            pend.displaced.add(path)
        synced = fs.synced_file(path)
        if durable:
            fs.outside_write(path, new)
            ref.unsynced.pop(path, None)
        else:
            fs.outside_write_unsynced(path, new)
            ref.unsynced.setdefault(path, synced if synced is not None else b"")
        ref.apply(path, new, plus, minus)
        ref.saved_post.pop(path, None)

    @precondition(lambda self: self.ref.pending is not None)
    @rule(
        kind=st.sampled_from(["add", "remove", "rewrite", "comment"]),
        pick=st.integers(0, 1000),
        text=txn_text(),
        durable=st.booleans(),
    )
    def edit_unfinished(self, kind: str, pick: int, text: str, durable: bool) -> None:
        """The user edits the file an unfinished save writes while writes are paused (recovery then meets an outside
        change)."""
        TALLY["rule:edit_unfinished"] += 1
        pend = self.ref.pending
        assert pend is not None
        path = sorted(pend.files)[0]
        self.do_outside_edit(path, kind, pick, text, durable=durable or path == ROOT)

    @precondition(lambda self: self._ready())
    @rule(at=st.floats(0, 1), io=st.booleans(), then_edit=st.booleans(), text=txn_text(), durable=st.booleans())
    def crash_during_save(self, at: float, io: bool, then_edit: bool, text: str, durable: bool) -> None:
        """`crash_during("confirm", ...)` as a rule of its own (the save is the job whose crashes matter most); with
        `then_edit`, when the save is left unfinished, the user then edits the file it writes (`edit_unfinished`)."""
        TALLY["rule:crash_during_save"] += 1
        self.crash_during("confirm", at, io)
        if then_edit and self.ref.pending is not None:
            self.edit_unfinished("rewrite", 0, text, durable=durable)

    @precondition(lambda self: self._job_ready_any())
    @rule(job=st.sampled_from(JOBS), at=st.floats(0, 1), io=st.booleans())
    def crash_during(self, job: Job, at: float, io: bool) -> None:
        """The job's step `k` from now fails: an engine death (`Crash`) or an I/O error (`ENOSPC`), with `k` drawn over
        the job's usual length (`LENGTH`). A save with no plan in hand first previews an insert (no fault)."""
        if job == "confirm" and self._ready() and self.ref.plan is None:
            self.do_preview("insert", 0, SPARE, None)
        if not self._job_ready(job):
            return
        TALLY["rule:crash_during"] += 1
        TALLY[f"crash_during:{job}"] += 1
        fault = (round(at * LENGTH[job]), io)
        if job == "preview":
            self.do_preview("insert", 0, SPARE, fault)
        elif job == "confirm":
            self.do_confirm(fault)
        elif job == "rebuild":
            self.do_rebuild(fault)
        elif job == "open":
            self.do_open(fault)
        else:
            self.do_recover(fault)

    def _job_ready(self, job: Job) -> bool:
        if job == "recover":
            return True
        if not self._ready():
            return False
        return (job != "confirm" or self.ref.plan is not None) and (job != "rebuild" or self.ref.hint is not None)

    def _job_ready_any(self) -> bool:
        return any(self._job_ready(j) for j in JOBS)

    @rule(pick=st.integers(0, 63))
    def power_loss(self, pick: int) -> None:
        TALLY["rule:power_loss"] += 1
        old, ref = self.fs, self.ref
        chosen: MemFS | None = None
        for i, state in enumerate(old.power_loss_states()):
            chosen = state
            if i >= pick:
                break
        assert chosen is not None
        for p in FILES:
            now = chosen.file(p)
            ok = allowed(ref, p)
            if p in ref.unsynced:
                ok = ok | {ref.unsynced[p], *tears(ref.files[p])}
            assert now is not None, f"a power loss removed {p}"
            assert now in ok, f"a power loss left {p} at bytes no state allows: {now!r}"
            if now not in allowed(ref, p):  # the editor's unsynced save lost: the reference takes what the disk kept
                TALLY["power_loss:editor_lost"] += 1
                if ref.pending is not None and p in ref.pending.files:
                    ref.pending.displaced.add(p)
                ref.apply(p, now, keys_of({p: now}), keys_of({p: ref.files[p]}))
                ref.saved_post.pop(p, None)
        ref.unsynced = {}
        self.fs = chosen
        ref.lost_power = True
        self._restart()

    def _restart(self) -> None:
        self.ref.plan = None
        self.ref.needs_recovery = True

    @rule()
    def restart(self) -> None:
        TALLY["rule:restart"] += 1
        self._restart()

    @rule(then_keep=st.booleans())
    def restart_and_recover(self, then_keep: bool) -> None:
        """A restart, then recover-all; with `then_keep`, the user then presses Keep on the blocked screen."""
        TALLY["rule:restart_and_recover"] += 1
        self._restart()
        self.do_recover(None)
        if then_keep:
            self.keep()

    @precondition(lambda self: self.ref.needs_recovery)
    @rule(then_keep=st.booleans())
    def recover(self, then_keep: bool) -> None:
        TALLY["rule:recover"] += 1
        self.do_recover(None)
        if then_keep:
            self.keep()

    @precondition(lambda self: self._ready() and bool(self.ref.blocking - {""}))
    @rule()
    def keep(self) -> None:
        """The blocked screen's "Keep the files as they are now", on the unfinished save's journal."""
        TALLY["rule:keep"] += 1
        pend = self.ref.pending
        if self.ref.needs_recovery or pend is None or pend.name not in self.ref.blocking:
            return
        out = ra.keep(self.fs, JD, pend.name)
        TALLY[f"keep:{type(out).__name__}"] += 1
        if isinstance(out, ra.Done):
            self.ref.blocking.discard(pend.name)
            self._settle("keep")

    @precondition(lambda self: self._ready() and not open_journals(self.fs))
    @rule(at=st.floats(0, 1), text=txn_text())
    def confirm_beside_unfinished(self, at: float, text: str) -> None:
        """Two plans held (the sidecar's plan store holds up to 4): plan A, then plan B, whose save is cut; when the cut
        leaves B's journal `.open` with every file still at pre, A still matches the disk and the mirror, and only the
        gate's `.open` check refuses it (`unfinished_save`)."""
        TALLY["rule:confirm_beside_unfinished"] += 1
        self.do_preview("insert", 0, text, None)
        held = self.ref.plan
        if held is None:
            return
        self.do_preview("insert", 0, SPARE, None)
        if self.ref.plan is None or self.ref.plan is held:
            return
        self.do_confirm((round(at * LENGTH["confirm"]), False))
        if self.ref.pending is None or at_post(self.fs, self.ref):
            return
        TALLY["confirm_beside_unfinished:reached"] += 1
        self.ref.plan = held
        self.do_confirm(None)

    @precondition(lambda self: self._ready())
    @rule(text=txn_text(), put_back=st.booleans(), path=st.sampled_from(FILES))
    def confirm_on_a_newer_generation(self, text: str, put_back: bool, path: str) -> None:
        """A plan whose generation was replaced: preview, an outside rewrite of a transaction in `path`, an open (it
        publishes a generation of the rewritten disk), then confirm. Without `put_back` the mirror is fresh and the plan
        is not: only the gate's fingerprint check refuses (`ledger_changed`). With `put_back` the file is first put
        back to its bytes: the plan matches the disk, the current generation does not, and only the gate's freshness
        check refuses (`mirror_stale`; verifier S0121 r1 F3)."""
        TALLY["rule:confirm_on_a_newer_generation"] += 1
        self.do_preview("insert", 0, SPARE, None)
        if self.ref.plan is None:
            return
        old = self.fs.file(path)
        assert old is not None
        self.do_outside_edit(path, "rewrite", 0, text, durable=True)
        self.do_open(None)
        if put_back:
            self.do_outside_put(path, old)
        self.do_confirm(None)

    def do_outside_put(self, path: str, data: bytes) -> None:
        """An outside program writes `data` to `path`, durably (its entries as Beancount parses them)."""
        before = self.fs.file(path)
        assert before is not None
        pend = self.ref.pending
        if pend is not None and path in pend.files:
            pend.displaced.add(path)
        self.fs.outside_write(path, data)
        self.ref.unsynced.pop(path, None)
        self.ref.apply(path, data, keys_of({path: data}), keys_of({path: before}))
        self.ref.saved_post.pop(path, None)

    @rule()
    def purge_mirror(self) -> None:
        """The OS or the user empties the app's cache folder (publish.md: the cache may be purged): the mirror is gone
        until an open publishes one, and no save may pass its gate meanwhile."""
        TALLY["rule:purge_mirror"] += 1
        if not self.fs.resolve(MIRROR).rest:
            for n in self.fs.names_in(MIRROR):
                self.fs.outside_remove(f"{MIRROR}/{n}")
        self.ref.current = None

    @rule()
    def read_mirror(self) -> None:
        TALLY["rule:read_mirror"] += 1
        cur = pb.read_published(self.fs, CACHE, KEY, FakeStore())
        TALLY[f"read_mirror:{'fresh' if cur and fresh_now(self.fs, cur.meta.fingerprint) else 'not_fresh'}"] += 1

    # -- after every step ----------------------------------------------------------------------------------------------

    @invariant()
    def check_all(self) -> None:
        inv_files(self.fs, self.ref)
        inv_entries(self.fs, self.ref)
        inv_saved_kept(self.fs, self.ref)
        inv_mirror(self.fs, self.ref)
        inv_history(self.fs, self.ref)
        inv_recovered(self.fs, self.ref, just_recovered=False)


# -- the profiles ------------------------------------------------------------------------------------------------------

BOUNDED: Final = settings(
    max_examples=300,
    stateful_step_count=50,
    derandomize=True,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
LONG: Final = settings(
    max_examples=2000,
    stateful_step_count=80,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


def profile() -> settings:
    return LONG if os.environ.get("FT_WRITE_MODEL") == "long" else BOUNDED


REACHED: Final = (
    *(f"rule:preview_{k}" for k in ("insert", "replace", "delete", "token")),
    *(f"rule:{r}" for r in ("confirm", "rebuild", "open", "outside_edit", "crash_during", "power_loss")),
    *(f"rule:{r}" for r in ("restart", "restart_and_recover", "recover", "keep", "purge_mirror", "write")),
    *(f"rule:{r}" for r in ("edit_unfinished", "crash_during_save")),
    *(f"rule:{r}" for r in ("confirm_beside_unfinished", "confirm_on_a_newer_generation")),
    "confirm_beside_unfinished:reached",
    "confirm:Refused:unfinished_save",
    "confirm:Refused:ledger_changed",
    "confirm:Refused:mirror_stale",
    "rule:read_mirror",
    "keep:Done",
    "crash:preview",
    "crash:confirm",
    "crash:rebuild",
    "crash:open",
    "crash:recover",
)


def test_the_sequence_model_holds_and_reaches_every_rule() -> None:
    if sys.platform == "win32":
        raise pytest.skip.Exception(BL_0282)
    TALLY.clear()
    started = time.perf_counter()
    stateful.run_state_machine_as_test(WriteSequence, settings=profile())  # pyright: ignore[reportUnknownMemberType]
    took = time.perf_counter() - started
    missing = [r for r in REACHED if TALLY[r] == 0]
    assert not missing, f"not reached over the run: {missing}\n{sorted(TALLY.items())}"
    print(f"\nwrite sequence model: {took:.1f} s; {sorted(TALLY.items())}")  # noqa: T201 (the run's record, with -s)


# -- the invariants' self-tests: a hand-broken disk or reference fails each --------------------------------------------


def booted() -> WriteSequence:
    m = WriteSequence()
    m.boot()
    m.check_all()
    return m


def saved_once() -> WriteSequence:
    m = booted()
    m.do_preview("insert", 0, '2026-06-01 * "Market"\n  Expenses:Food  4.00 EUR\n  Assets:Bank\n', None)
    assert m.ref.plan is not None
    m.do_confirm(None)
    assert m.ref.landed, TALLY
    m.check_all()
    return m


def test_inv_files_fails_on_a_torn_file() -> None:
    m = booted()
    m.fs.outside_write(TX, INITIAL[TX][:20])
    with pytest.raises(AssertionError, match="does not allow"):
        inv_files(m.fs, m.ref)


def test_inv_entries_fails_on_an_entry_the_reference_lacks() -> None:
    m = booted()
    m.ref.keys[entry_keys.entry_key(parsed(INITIAL[TX], TX)[0])] += 1
    with pytest.raises(AssertionError, match="entries differ"):
        inv_entries(m.fs, m.ref)


def test_inv_entries_fails_on_a_changed_untargeted_entry_with_bytes_the_reference_allows() -> None:
    m = booted()
    changed = INITIAL[TX].replace(b"12.50", b"12.51")
    m.fs.outside_write(TX, changed)
    m.ref.files[TX] = changed  # the bytes agree; the entries must not
    with pytest.raises(AssertionError, match="entries differ"):
        inv_entries(m.fs, m.ref)


def test_inv_saved_kept_fails_when_a_saved_change_is_gone() -> None:
    if sys.platform == "win32":
        raise pytest.skip.Exception(BL_0282)
    m = saved_once()
    m.fs.outside_write(TX, INITIAL[TX])
    with pytest.raises(AssertionError, match="called saved is lost"):
        inv_saved_kept(m.fs, m.ref)


def test_inv_unchanged_fails_on_a_changed_ledger_file() -> None:
    m = booted()
    before = disk(m.fs)
    m.fs.outside_write(OPENS, INITIAL[OPENS] + b"; x\n")
    with pytest.raises(AssertionError, match=r"changed /L/open\.bean"):
        inv_unchanged(before, m.fs, "a refusal")


def test_inv_mirror_fails_on_a_torn_generation_and_on_one_no_job_published() -> None:
    m = booted()
    name = m.ref.current
    assert name is not None
    m.fs.outside_write(f"{MIRROR}/{name}", b"torn")
    with pytest.raises(AssertionError, match="complete generation"):
        inv_mirror(m.fs, m.ref)
    m = booted()
    m.ref.published = {}
    with pytest.raises(AssertionError, match="no job published"):
        inv_mirror(m.fs, m.ref)


def test_the_freshness_agreement_fails_on_a_wrong_answer() -> None:
    m = booted()
    with pytest.raises(AssertionError, match="the disk says True"):
        check_fresh_answer(m.fs, False, "a gate")
    m.fs.outside_write(TX, INITIAL[TX] + b"; x\n")
    with pytest.raises(AssertionError, match="the disk says False"):
        check_fresh_answer(m.fs, True, "a gate")


def test_inv_history_fails_on_a_lost_backup_and_a_lost_record() -> None:
    if sys.platform == "win32":
        raise pytest.skip.Exception(BL_0282)
    m = saved_once()
    name = m.ref.landed[-1][0]
    rec = record_at(m.fs, name, State.CLOSED)
    assert rec is not None
    assert rec.files[0].backup is not None
    m.fs.outside_remove(f"{rec.backup_dir}/{rec.files[0].backup}")
    with pytest.raises(AssertionError, match="backup of"):
        inv_history(m.fs, m.ref)
    m = saved_once()
    m.fs.outside_remove(f"{JD}/{journal.file_name(m.ref.landed[-1][0], State.CLOSED)}")
    with pytest.raises(AssertionError, match="lost the record"):
        inv_history(m.fs, m.ref)


def test_inv_recovered_fails_on_an_open_journal_recovery_did_not_report() -> None:
    if sys.platform == "win32":
        raise pytest.skip.Exception(BL_0282)
    m = saved_once()
    name = m.ref.landed[-1][0]
    data = m.fs.file(f"{JD}/{journal.file_name(name, State.CLOSED)}")
    assert data is not None
    m.fs.outside_write(f"{JD}/{journal.file_name('s9-j9', State.OPEN)}", data)
    with pytest.raises(AssertionError, match="did not report blocking"):
        inv_recovered(m.fs, m.ref, just_recovered=True)


def test_a_crash_after_the_commit_point_leaves_a_save_the_model_tracks_until_recovery() -> None:
    """The model's own handling of an unfinished save (not an invariant's self-test): the pending save allows its
    post bytes until recover-all resolves it to its pre bytes."""
    m = booted()
    m.do_preview("insert", 0, '2026-06-01 * "Market"\n  Expenses:Food  4.00 EUR\n  Assets:Bank\n', None)
    m.do_confirm((30, False))
    m.check_all()
    m.restart_and_recover(then_keep=False)
    m.check_all()
    assert m.ref.pending is None


def test_inv_recovered_fails_step_by_step_on_an_open_journal_that_is_neither_unfinished_nor_blocking() -> None:
    m = saved_once()
    name = m.ref.landed[-1][0]
    data = m.fs.file(f"{JD}/{journal.file_name(name, State.CLOSED)}")
    assert data is not None
    m.fs.outside_write(f"{JD}/{journal.file_name('s9-j9', State.OPEN)}", data)
    assert not m.ref.needs_recovery
    with pytest.raises(AssertionError, match="did not report blocking"):
        inv_recovered(m.fs, m.ref, just_recovered=False)


def test_the_history_keeps_the_newest_16_over_18_saves() -> None:
    """The history's limit, which the bounded run's examples never reach: 18 saves in a row, each with its rebuild,
    every invariant checked after each (`inv_history` holds the newest 16 `.closed` records and their backups)."""
    if sys.platform == "win32":
        raise pytest.skip.Exception(BL_0282)
    m = booted()
    for i in range(KEEP + 2):
        m.write(f'2026-06-{i + 1:02d} * "Market"\n  Expenses:Food  {i + 1}.00 EUR\n  Assets:Bank\n', False, TX)
        m.check_all()
        m.rebuild()
        m.check_all()
    assert len(m.ref.landed) == KEEP + 2


def _steps(run: Callable[[WriteSequence, tuple[int, bool]], None], setup: Callable[[WriteSequence], None]) -> int:
    """How many steps one job takes: the first crash point that does not fire."""
    for k in range(200):
        m = booted()
        setup(m)
        run(m, (k, False))
        if not m.fired:
            return k
    pytest.fail("a job of more than 200 steps")


def _saved(m: WriteSequence) -> None:
    m.write(SPARE, False, TX)


def _open_journal(m: WriteSequence) -> None:
    m.do_preview("insert", 0, SPARE, None)
    m.do_confirm((25, False))  # cut after its journal is `.open`
    assert m.ref.pending is not None


def test_each_job_s_steps_fit_its_crash_range() -> None:
    """`LENGTH` covers every step of each job, so a crash point can land on any of them (verifier S0121 r1 F4)."""
    if sys.platform == "win32":
        raise pytest.skip.Exception(BL_0282)
    jobs: dict[Job, int] = {
        "preview": _steps(lambda m, f: m.do_preview("insert", 0, SPARE, f), lambda m: None),
        "confirm": _steps(lambda m, f: m.do_confirm(f), lambda m: m.do_preview("insert", 0, SPARE, None)),
        "rebuild": _steps(lambda m, f: m.do_rebuild(f), _saved),
        "open": _steps(lambda m, f: m.do_open(f), lambda m: m.purge_mirror()),
        "recover": _steps(lambda m, f: m.do_recover(f), _open_journal),
    }
    for job, n in jobs.items():
        assert n <= LENGTH[job], f"{job} takes {n} steps; LENGTH draws up to {LENGTH[job]}"
