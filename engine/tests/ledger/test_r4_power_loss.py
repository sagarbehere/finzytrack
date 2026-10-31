"""R4's renames under a crash and a power loss (`ledger_engine.ledger.r4` over the fake file system; spec §6.5, §6.7;
fidelity review 2, M5; S0066).

§6.7: "Every rename of the record (`.closed` → `.done`, `.closed` → `.mismatch` or `.mismatch {could_not_check}`, and
Resume saving's `.mismatch` → `.done`) is followed by a `durable_sync` of the journal directory before the outcome is
acted on or shown, so a power loss cannot turn a pause back into `.closed` that a later R4 might lift without Resume
saving". So, for every op and every effect it makes: an op that answered has its rename durable in every state a power
loss may leave; an op cut at any effect, then any power loss, leaves the record in exactly one state, the one before
or the one after (never lost, never doubled), and a count file only beside a `.closed` or `.mismatch` record.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ledger_engine.ledger import overlay, r4
from ledger_engine.ledger.core import journal
from ledger_engine.ledger.core.journal import FileEntry, Record
from ledger_engine.ledger.core.ledger_set import Content, Fingerprint, encode
from tests.fakes.memfs import Crash, MemFS

ALD = "/a"
KEY = "k1"
JD = f"{ALD}/local/{KEY}/journal"
NAME = "s1-j7"
FP = Fingerprint.of("/L/main.bean", {"/L/main.bean": Content("1" * 64)}, {}, {}, {})
PARSE = "sha256:" + "b" * 64
OTHER = "sha256:" + "0" * 64
RECORD = Record(
    session="s1",
    job="j7",
    workspace_key=KEY,
    lineage="lin1",
    engine_build="build1",
    post_fingerprint=encode(FP).decode("ascii"),
    adopted_digest="sha256:" + "a" * 64,
    parse_digest=PARSE,
    backup_dir=f"{ALD}/local/{KEY}/backups/{NAME}",
    files=(
        FileEntry(
            "/L/main.bean", "/l/main.bean", "2" * 64, "1" * 64, "0-main.bean", journal.temp_name("main.bean", NAME)
        ),
    ),
    created_dirs=(),
)


class NoJob:
    def progress(self, phase: str) -> None:
        pass


def world(state: str = "closed", attempts: int = 0) -> MemFS:
    fs = MemFS()
    fs.outside_mkdirs(JD)
    fs.outside_write(f"{JD}/{NAME}.{state}", journal.encode(RECORD))
    for n in range(1, attempts + 1):
        fs.outside_write(f"{JD}/{journal.attempt_name(NAME, n)}", b"")
    return fs


def ports(fs: MemFS, parse_beancount: str) -> r4.Ports:
    return r4.Ports(
        fs,
        "build1",
        load=lambda _root: OTHER,
        parse_beancount=lambda _root: parse_beancount,
        parse_replica=lambda _root: overlay.Parsed(PARSE, {}),
        observe=lambda fp: fp,
    )


def records(fs: MemFS) -> list[str]:
    return sorted(n for n in fs.names_in(JD) if journal.parse_file_name(n) is not None)


def counts(fs: MemFS) -> int:
    return sum(1 for n in fs.names_in(JD) if ".r4-attempt-" in n)


type Op = Callable[[MemFS], r4.Answer]

OPS: dict[str, tuple[str, int, Op, str, str]] = {
    # name: (state before, attempts before, op, state after, outcome)
    "confirm_mismatch": (
        "closed",
        0,
        lambda fs: r4.confirm(ports(fs, OTHER), ALD, KEY, NAME, NoJob()),
        "mismatch",
        "mismatch",
    ),
    "confirm_nondeterministic": (
        "closed",
        0,
        lambda fs: r4.confirm(ports(fs, PARSE), ALD, KEY, NAME, NoJob()),
        "done",
        "nondeterministic",
    ),
    "attempt_third": ("closed", 2, lambda fs: r4.attempt(fs, ALD, KEY, NAME), "mismatch", "could_not_check"),
    "attempt_first": ("closed", 0, lambda fs: r4.attempt(fs, ALD, KEY, NAME), "closed", "counted"),
    "resume": ("mismatch", 3, lambda fs: r4.resume(fs, ALD, KEY, NAME), "done", "done"),
}


def effects(op: Op, before: str, attempts: int) -> int:
    fs = world(before, attempts)
    op(fs)
    return len(fs.effects)


@pytest.mark.parametrize("name", sorted(OPS))
def test_an_answered_op_is_durable_under_every_power_loss(name: str) -> None:
    before, attempts, op, after, outcome = OPS[name]
    fs = world(before, attempts)
    out = op(fs)
    assert out.outcome == outcome
    for lost in fs.power_loss_states():
        assert records(lost) == [f"{NAME}.{after}"], name
        assert counts(lost) == counts(fs), name


@pytest.mark.parametrize("name", sorted(OPS))
def test_a_cut_op_leaves_one_record_before_or_after_under_every_power_loss(name: str) -> None:
    before, attempts, op, after, _ = OPS[name]
    n = effects(op, before, attempts)
    assert n > 0
    for at in range(n):
        fs = world(before, attempts)
        fs.inject(at, crash=True)
        with pytest.raises(Crash):
            op(fs)
        for lost in fs.power_loss_states():
            assert records(lost) in ([f"{NAME}.{before}"], [f"{NAME}.{after}"]), (name, at)
            assert attempts <= counts(lost) <= attempts + 1, (name, at)


@pytest.mark.parametrize("name", sorted(OPS))
def test_an_io_error_at_any_effect_is_raised_never_answered(name: str) -> None:
    """A failed rename or sync is a failure to run (the sidecar counts it), never an outcome shown before it is
    durable."""
    before, attempts, op, _, _ = OPS[name]
    for at in range(effects(op, before, attempts)):
        fs = world(before, attempts)
        fs.inject(at, crash=False)
        with pytest.raises(OSError, match="injected"):
            op(fs)
