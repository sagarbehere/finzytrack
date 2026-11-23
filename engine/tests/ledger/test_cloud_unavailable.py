"""A ledger file only in the cloud that Windows cannot download (S0149, BL-0283; owner, 2026-11-21, change D: "a
cloud-only file that cannot be downloaded (offline) is refused with a named reason"; 2026-11-22: every Cloud Files
provider alike). The reason is `cloud_unavailable`, told only for `fs.port.CloudUnavailable`: the Windows adapter's
own reads raise it for the cloud filter's download errors (`port.CLOUD_DOWNLOAD_ERRORS`), and `fingerprint.chunks`
after a read through the C runtime failed and `ReadFile` named one of them (`this_os.cloud_download_failed`). The
runner shows both on the real cloud filter (`tests/fs/test_windows_cloud.py`); here every site that reads a ledger
file is driven with the error on every OS:

- the writer (over the fake in its Windows mode): a cloud-only file at the backup's read is "not saved"
  `cloud_unavailable`, naming it, and "unchanged" only after the re-hash;
- the save's gate (real disk): its observation of a cloud-only file the plan read is `cloud_unavailable` naming it, a
  file it only reads (an `include`) too; any other difference beside it stays `ledger_changed`;
- the open and the rebuild (the protocol, real disk): refused `cloud_unavailable`, naming the file, never
  `engine.internal`;
- Copy: refused `cloud_unavailable`, naming the file, before anything is written.

Must not pass: a plain `OSError` of any errno (`EINVAL`, which the C runtime gives for many reads; `EIO`) is never
`cloud_unavailable`.
"""

from __future__ import annotations

import dataclasses
import errno
import json
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path
from typing import Any, cast, override

import pytest

from ledger_engine.fs import this_os
from ledger_engine.fs.port import CLOUD_DOWNLOAD_ERRORS, CloudUnavailable
from ledger_engine.ledger import fingerprint
from ledger_engine.ledger import save as sv
from ledger_engine.ledger.copy import run as copy_run
from ledger_engine.ledger.writer import Landed, NotSaved, Reason, Step, durable_commit
from tests.fresh_engine import fresh_engine
from tests.ledger import test_copy as tcopy
from tests.ledger.test_save import FakeJob, Ledger, plan_for, ports_for
from tests.ledger.test_save import led as led  # the fixture, shared
from tests.ledger.test_writer import check_told
from tests.ledger.worlds import World, journals, world
from tests.test_save_ops import Channel, engine, request

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]

MAIN = "/w/L/main.bean"


def _cloud(p: str) -> OSError:
    return CloudUnavailable(p, "ReadFile")


def offline(monkeypatch: pytest.MonkeyPatch, path: str, err: Callable[[str], OSError] | None = None) -> None:
    """`fingerprint.chunks` of `path` raises `err(path)` (default: `CloudUnavailable`, as on Windows offline)."""
    real = fingerprint.chunks

    def chunks(p: str, size: int = 1 << 20) -> Iterator[bytes]:
        if p == path:
            raise (err or _cloud)(p)
        return real(p, size)

    monkeypatch.setattr(fingerprint, "chunks", chunks)


def _einval(p: str) -> OSError:
    return OSError(errno.EINVAL, "Invalid argument", p)


def _eio(p: str) -> OSError:
    return OSError(errno.EIO, "I/O error", p)


PLAIN_ERRORS: dict[str, Callable[[str], OSError]] = {"EINVAL": _einval, "EIO": _eio}


# -- the writer, over the fake ------------------------------------------------------------------------------------


def _windows() -> World:
    w = world(volume="no_swap")
    w.fs.windows = True
    return w


def test_a_cloud_only_file_at_the_backups_read_is_not_saved_cloud_unavailable_and_named() -> None:
    """The writer reads each replaced file for its backup before any write: a cloud-only file Windows cannot download
    stops it there, "not saved", `cloud_unavailable`, naming the file, no journal. The re-hash cannot read that file,
    so "unchanged" is not said (§11's rule); the disk agrees with what is told in every power-loss state."""
    w = _windows()
    w.fs.outside_cloud_offline(MAIN)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.step, out.reason, out.path, out.open_journal) == (Step.BACKUP, Reason.CLOUD_UNAVAILABLE, MAIN, False)
    assert out.unchanged is False, "the re-hash could not read the file: never 'unchanged'"
    assert journals(w.fs) == {}
    check_told(w.fs, w, out)


@cache
def clean_effects() -> tuple[str, ...]:
    w = _windows()
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    return tuple(w.fs.effects)


@pytest.mark.parametrize("path", ["/w/L/inc/a.bean", MAIN])
def test_each_replaced_file_the_writer_reads_is_told_cloud_unavailable_named_with_nothing_written(path: str) -> None:
    """The spec's sites (§11 row): the writer's read of a replaced file for its backup. Each of the save's replaced
    files, cloud-only and not downloadable, stops the save there, naming that file; no journal, no ledger byte
    written, the outcome as the disk says in every power-loss state."""
    w = _windows()
    w.fs.outside_cloud_offline(path)
    before = {t.path: w.fs.file(t.path) for t in w.targets}
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.path, out.step) == (Reason.CLOUD_UNAVAILABLE, path, Step.BACKUP), out
    assert journals(w.fs) == {}
    assert {t.path: w.fs.file(t.path) for t in w.targets} == before
    check_told(w.fs, w, out)


@pytest.mark.parametrize("at", range(len(clean_effects())))
def test_must_not_pass_no_effect_of_the_save_is_ever_told_cloud_unavailable(at: int) -> None:
    """Round 1 (V2, RT-2): a write, a rename, a flush, a folder's sync or the backups folder's own effects never come
    back "could not download it": an I/O error injected at each one is never `cloud_unavailable`."""
    w = _windows()
    w.fs.inject(at, crash=False, err=errno.EIO)
    out = durable_commit(w.fs, w.commit)
    assert getattr(out, "reason", None) is not Reason.CLOUD_UNAVAILABLE, (clean_effects()[at], out)


def test_must_not_pass_the_fake_in_its_posix_mode_never_raises_it() -> None:
    """The error is Windows'; the fake raises it only in its Windows mode, as the adapter alone does."""
    w = world(volume="no_swap")
    w.fs.outside_cloud_offline(MAIN)
    out = durable_commit(w.fs, w.commit)
    assert not isinstance(out, NotSaved), out


# -- the save's gate (real disk) ------------------------------------------------------------------------------------


def test_the_gate_meeting_a_cloud_only_file_is_not_saved_cloud_unavailable_named(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 4 observes every ledger file: one Windows cannot download is "not saved" `cloud_unavailable`, naming it,
    before the gate (nothing written); the re-hash reads it through the port (here it reads: "unchanged")."""
    plan = plan_for(led)
    before = led.tree()
    offline(monkeypatch, str(led.main))
    job = FakeJob()
    out = sv.run(plan, ports_for(led), job)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.path, out.unchanged) == (Reason.CLOUD_UNAVAILABLE, str(led.main), True)
    assert job.events == []
    assert led.tree() == before


def test_an_include_the_save_only_reads_is_cloud_unavailable_too(led: Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file of the ledger the save does not write (`inc.bean`) that cannot be downloaded: the ledger did not change,
    Windows could not read it, so `cloud_unavailable` names it (not `ledger_changed`)."""
    plan = plan_for(led)
    assert str(led.inc) not in {c.path for c in plan.changes}, "inc.bean is only read by the save"
    offline(monkeypatch, str(led.inc))
    out = sv.run(plan, ports_for(led), FakeJob())
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.path) == (Reason.CLOUD_UNAVAILABLE, str(led.inc))
    assert out.unchanged is False, "round 1 (V1, RT-1): nobody hashed inc.bean again: never 'unchanged'"


def test_V1b_an_include_edited_elsewhere_then_made_cloud_only_is_never_told_unchanged(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 1 (V1b): the include changed after the plan (an edit synced from another PC), then the sync app freed its
    space, then offline: the gate cannot read it, so it cannot tell it changed; "unchanged" is never said, and the
    reason stays the true cause (it cannot be downloaded). The text says Finzytrack could not check."""
    plan = plan_for(led)
    led.inc.write_bytes(led.inc.read_bytes() + b"; edited on the other PC\n")
    offline(monkeypatch, str(led.inc))
    out = sv.run(plan, ports_for(led), FakeJob())
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.unchanged) == (Reason.CLOUD_UNAVAILABLE, False), out


def test_must_not_pass_a_file_the_plan_saw_absent_and_now_cloud_only_is_the_ledger_changing(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 1 (the verifier's M4): a file the save creates (the plan saw it absent) that now stands, cloud-only: a
    file appeared, so the ledger changed (`ledger_changed`), never `cloud_unavailable`, which is only for a file whose
    bytes the plan read."""
    from ledger_engine.ledger.core.ledger_set import Absent
    from ledger_engine.ledger.handles import sha256
    from ledger_engine.ledger.writer import Change

    late = led.root / "late.bean"
    text = led.main.read_bytes()
    plan = plan_for(
        led,
        (
            Change(str(led.main), sha256(text), text + b'include "late.bean"\n'),
            Change(str(late), None, b"; the save makes it\n"),
        ),
    )
    assert dict(plan.fingerprint.files).get(str(late)) == Absent(), plan.fingerprint.files
    late.write_bytes(b"; arrived\n")
    offline(monkeypatch, str(late))
    out = sv.run(plan, ports_for(led), FakeJob())
    assert isinstance(out, sv.Refused), out
    assert out.reason == "ledger_changed"


def test_a_generation_whose_own_set_cannot_be_downloaded_is_mirror_stale(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The save's check of the published generation (owner, 2026-11-23: "Remove the three early cloud checks"): the
    plan's files read, and the generation's own set observed with a file offline is `mirror_stale`, as any file that
    look cannot read; its set may be another ledger's (round 2, V2-1, R1), so none of it is named. Nothing written."""
    plan = plan_for(led)
    real = sv.Ports(ports_for(led).fs).observe
    calls: list[int] = []

    def observe(fp: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:  # the generation's own set, after the gate's look
            offline(monkeypatch, str(led.inc))
        return real(fp)

    before = led.tree()
    out = sv.run(plan, dataclasses.replace(ports_for(led), observe=observe), FakeJob())
    assert isinstance(out, sv.Refused), out
    assert out.reason == "mirror_stale"
    assert led.tree() == before


def test_a_cloud_only_file_beside_another_change_stays_ledger_changed(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any other difference beside it (an outside edit of `inc.bean`) is the ledger changing: `ledger_changed`
    (restored in round 3, V3-3: round 2 removed it with the early checks, though it tests the gate on the plan's own
    files)."""
    plan = plan_for(led)
    led.inc.write_bytes(led.inc.read_bytes() + b"; edited outside\n")
    offline(monkeypatch, str(led.main))
    out = sv.run(plan, ports_for(led), FakeJob())
    assert isinstance(out, sv.Refused), out
    assert out.reason == "ledger_changed"


@pytest.mark.parametrize("kind", sorted(PLAIN_ERRORS))
def test_must_not_pass_a_plain_read_error_at_the_gate_is_never_cloud_unavailable(
    led: Ledger, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    plan = plan_for(led)
    offline(monkeypatch, str(led.main), PLAIN_ERRORS[kind])
    out = sv.run(plan, ports_for(led), FakeJob())
    assert isinstance(out, NotSaved), out
    assert out.reason == Reason.IO_ERROR


# -- the open and the rebuild (the protocol, real disk) -----------------------------------------------------------


def _open(led: Ledger) -> dict[str, Any]:
    params = {
        "root": str(led.main),
        "app_local_data": str(led.ald),
        "workspace_key": "k2",
        "lineage": "lin1",
        "floor": 0,
        "today": "2026-11-08",
        "built_at": "2026-11-08T10:00:00+05:30",
    }
    with fresh_engine():
        return json.loads(engine(led.ald, led.cache).handle(request("open", params, 2), Channel(2, proceed=False)))


def test_the_open_of_a_cloud_only_file_is_refused_cloud_unavailable_named(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    offline(monkeypatch, str(led.inc))
    reply = _open(led)
    assert reply["ok"], reply
    r = reply["result"]
    assert (r["outcome"], r["refusal"], r["refused"], r["publish"]) == (
        "refused",
        "cloud_unavailable",
        str(led.inc),
        None,
    ), r


@pytest.mark.parametrize("kind", sorted(PLAIN_ERRORS))
def test_must_not_pass_a_plain_read_error_at_the_open_is_never_cloud_unavailable(
    led: Ledger, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    offline(monkeypatch, str(led.inc), PLAIN_ERRORS[kind])
    reply = _open(led)
    assert not reply["ok"], reply
    assert reply["error"]["key"] == "engine.internal"


def _rebuild(led: Ledger, plan: sv.SavePlan | None = None) -> dict[str, Any]:
    plan = plan or plan_for(led)
    params = {
        "root": str(led.main),
        "fingerprint": fingerprint_text(plan),
        "app_local_data": str(led.ald),
        "workspace_key": plan.workspace_key,
        "lineage": plan.lineage,
        "floor": 0,
        "today": "2026-11-08",
        "built_at": "2026-11-08T10:00:00+05:30",
    }
    with fresh_engine():
        return json.loads(engine(led.ald, led.cache).handle(request("rebuild", params, 2), Channel(2, proceed=False)))


def fingerprint_text(plan: sv.SavePlan) -> str:
    from ledger_engine.ledger.core.ledger_set import encode

    return encode(plan.fingerprint).decode("ascii")


def _offline_in_beancounts_parser(monkeypatch: pytest.MonkeyPatch, path: str, *, at: str) -> None:
    """What the cloud filter does offline to Beancount's own read (S0144 Fact 6; round 3's R9 model): `at="read"`, its
    parser's `open()` of `path` succeeds and the first read raises a bare `EINVAL` (Beancount's C parser then raises
    its own `SystemError`); `at="open"`, the `open()` itself raises the bare `EINVAL`."""
    import builtins
    import io

    real_open: Callable[..., Any] = builtins.open

    class Offline(io.RawIOBase):
        @override
        def readable(self) -> bool:
            return True

        @override
        def readinto(self, buffer: Any) -> int:
            raise OSError(errno.EINVAL, "Invalid argument")

    def fake_open(file: object, *args: object, **kwargs: object) -> object:
        if str(file) == path:
            if at == "open":
                raise OSError(errno.EINVAL, "Invalid argument")
            return io.BufferedReader(Offline())
        return real_open(file, *args, **kwargs)

    # Beancount's `parse_file` opens the file by the builtin name `open` in its parser module
    monkeypatch.setattr("beancount.parser.parser.open", fake_open, raising=False)


@pytest.mark.parametrize("at", ["read", "open"])
def test_offline_beancounts_own_failed_read_of_a_file_it_opened_is_cloud_unavailable_named(
    led: Ledger, monkeypatch: pytest.MonkeyPatch, at: str
) -> None:
    """Round 3 (V3-1, R9; owner, 2026-11-23: "Option 2."): offline, Beancount's own load of a cloud-only ledger file
    fails at its read (its C parser's `SystemError`, which `load_failure` calls Beancount's own failure) or at its
    open (an `OSError`): the rebuild asks the files this load opened and refuses `cloud_unavailable` naming that one,
    never `beancount_failed` (a false cause) nor the engine's failure."""
    led.main.write_bytes(led.main.read_bytes() + b"; changed\n")  # not fresh: the rebuild loads
    offline(monkeypatch, str(led.main))
    _offline_in_beancounts_parser(monkeypatch, str(led.main), at=at)
    reply = _rebuild(led)
    assert reply["ok"], reply
    r = reply["result"]
    assert (r["outcome"], r["refusal"], r["refused"]) == ("refused", "cloud_unavailable", str(led.main)), r


@pytest.mark.parametrize("kind", sorted(PLAIN_ERRORS))
def test_must_not_pass_a_plain_read_error_at_the_rebuild_is_never_cloud_unavailable(
    led: Ledger, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """A file that fails Beancount's read for another cause (its own look also gives a plain error) is never
    `cloud_unavailable`: `EINVAL` is refused before the load as a special file (`_refuse_special`), `EIO` reaches the
    load and Beancount's read of it fails as Beancount's own failure."""
    led.main.write_bytes(led.main.read_bytes() + b"; changed\n")
    offline(monkeypatch, str(led.main), PLAIN_ERRORS[kind])
    _offline_in_beancounts_parser(monkeypatch, str(led.main), at="read")
    reply = _rebuild(led)
    assert not (reply["ok"] and reply["result"]["refusal"] == "cloud_unavailable"), reply


def test_the_file_whose_read_failed_is_named_the_last_one_the_load_opened(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 3 (option 2's rule): when several files this load opened are cloud-only at the look (main.bean read and
    then freed again by the sync app, inc.bean's read failing), the one named is the last one opened, whose read is
    the one that failed (Beancount reads each file it opens before it opens the next)."""
    led.main.write_bytes(led.main.read_bytes() + b"; changed\n")
    hint = plan_for(led)
    real = fingerprint.chunks

    def both(p: str, size: int = 1 << 20) -> Iterator[bytes]:
        if p in (str(led.main), str(led.inc)):
            raise CloudUnavailable(p, "ReadFile")
        return real(p, size)

    monkeypatch.setattr(fingerprint, "chunks", both)
    _offline_in_beancounts_parser(monkeypatch, str(led.inc), at="read")
    r = _rebuild(led, hint)["result"]
    assert (r["outcome"], r["refusal"], r["refused"]) == ("refused", "cloud_unavailable", str(led.inc)), r


def test_a_readable_last_opened_file_is_never_named_an_earlier_cloud_only_one_is(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 4 (F2): the file named is a cloud-only one among those this load opened, never simply the last opened.
    Here Beancount's read of inc.bean (opened last) fails while inc.bean itself reads, and main.bean (opened first,
    already read) was freed by the sync app before the look: main.bean is named, never the readable inc.bean (the
    cause is best effort, IMP-0078; §6.3's stated limit of the rule)."""
    led.main.write_bytes(led.main.read_bytes() + b"; changed\n")
    hint = plan_for(led)
    offline(monkeypatch, str(led.main))
    _offline_in_beancounts_parser(monkeypatch, str(led.inc), at="read")
    r = _rebuild(led, hint)["result"]
    assert (r["outcome"], r["refusal"], r["refused"]) == ("refused", "cloud_unavailable", str(led.main)), r


def test_R7_must_not_pass_a_plain_oserror_never_names_a_dropped_cloud_only_file(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 3 (V3-2, R7): the hint names inc.bean, which the ledger no longer includes and which is cloud-only
    offline; the load fails on a folder named as an include (a plain `OSError`). inc.bean is never named: only the
    files this load opened are asked."""
    hint = plan_for(led)
    (led.root / "new.bean").mkdir()
    led.main.write_bytes(led.main.read_bytes().replace(b'include "inc.bean"\n', b'include "new.bean"\n'))
    offline(monkeypatch, str(led.inc))
    reply = _rebuild(led, hint)
    r: dict[str, Any] = reply.get("result") or {}
    assert not (r.get("refusal") == "cloud_unavailable" and r.get("refused") == str(led.inc)), reply


def test_R8_a_new_include_that_cannot_be_read_is_named_never_a_dropped_one(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 3 (V3-2, R8): offline both the dropped inc.bean and the newly included new.bean are cloud-only, and
    Beancount's read of new.bean fails: the refusal names new.bean (the file this load could not read), never
    inc.bean."""
    hint = plan_for(led)
    new = led.root / "new.bean"
    new.write_bytes(b"2020-01-01 open Assets:New\n")
    led.main.write_bytes(led.main.read_bytes().replace(b'include "inc.bean"\n', b'include "new.bean"\n'))
    offline(monkeypatch, str(led.inc))
    offline(monkeypatch, str(new))
    _offline_in_beancounts_parser(monkeypatch, str(new), at="read")
    r = _rebuild(led, hint)["result"]
    assert (r["outcome"], r["refusal"], r["refused"]) == ("refused", "cloud_unavailable", str(new)), r


def test_the_rebuild_tells_a_cloud_only_file_at_its_warnings_read(led: Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 1 (V5, M5): online for the first look and the load, offline for the warnings' read of the files the load
    parsed: refused `cloud_unavailable` naming the file (a file this load read, so this ledger's)."""
    from ledger_engine.ledger import beancount_port

    led.main.write_bytes(led.main.read_bytes() + b"; changed\n")
    real = beancount_port.real_load

    def load(root: str, parsed: list[str]) -> Any:
        out = real(root, parsed)
        offline(monkeypatch, str(led.main))
        return out

    monkeypatch.setattr(beancount_port, "real_load", load)
    r = _rebuild(led)["result"]
    assert (r["outcome"], r["refusal"], r["refused"]) == ("refused", "cloud_unavailable", str(led.main)), r


def test_no_file_of_the_hint_is_named_before_the_load_runs(led: Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 2 (V2-1, R2; owner, 2026-11-23: "Remove the three early cloud checks"): a file of the hint the ledger no
    longer includes, cloud-only and offline, is never named: the rebuild loads the ledger as it is, which reads it no
    more, and loads."""
    hint = plan_for(led)  # the hint names inc.bean, as a generation of the ledger before the edit would
    led.main.write_bytes(led.main.read_bytes().replace(b'include "inc.bean"\n', b""))
    offline(monkeypatch, str(led.inc))
    r = _rebuild(led, hint)["result"]
    assert r["refusal"] is None, r
    assert r["outcome"] in ("loaded", "moved"), r


# -- Copy ------------------------------------------------------------------------------------------------------------


def test_a_copy_whose_source_cannot_be_downloaded_is_refused_cloud_unavailable_before_any_write() -> None:
    w = tcopy.new_world()

    def load(root: str) -> Any:
        raise CloudUnavailable(tcopy.PRICES, "ReadFile")

    job = tcopy.Job()
    nc = tcopy.not_copied(copy_run(tcopy.PLAN, dataclasses.replace(w.ports(), load=load), job), "cloud_unavailable")
    assert nc.path == tcopy.PRICES
    assert job.gated == 0
    assert not tcopy.exists(w.fs, tcopy.T)


def test_a_document_that_cannot_be_downloaded_mid_copy_stops_cloud_unavailable_and_its_part_is_removed() -> None:
    """A document the copy streams (its bytes are never parsed, so the load did not read it): its read failing with
    `CloudUnavailable` stops the copy `cloud_unavailable`, naming it; what the copy made is removed."""
    w = tcopy.new_world()
    original = w.source

    def failing(path: str) -> Iterator[bytes]:
        it = original(path)
        if path == tcopy.PDF:
            yield next(it)
            raise CloudUnavailable(path, "ReadFile")
        yield from it

    w.source = failing
    nc = tcopy.not_copied(tcopy.go(w)[0], "cloud_unavailable")
    assert (nc.path, nc.left) == (tcopy.PDF, ())
    assert not tcopy.exists(w.fs, tcopy.T)


@pytest.mark.parametrize("which", ["ledger", "document"])
def test_a_source_that_goes_cloud_only_before_the_recheck_is_cloud_unavailable_not_changed(
    which: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 1 (RT-4): the re-check just before the commit point finds a ledger file, or a copied document, only in
    the cloud and not downloadable: not copied, `cloud_unavailable` naming it (it did not change; Windows could not
    read it). The copy's world is `test_copy`'s fake, so its documents roots take POSIX's rules on Windows too
    (`test_copy.posix_documents_roots`, that module's autouse fixture; the 2026-11-22 run 13 failed without it:
    "loads the same" compared Windows' reading of the fake's `/...` roots and stopped at `load_differs`)."""
    from ledger_engine.ledger.fingerprint import CLOUD_UNAVAILABLE

    tcopy.posix_documents_roots(monkeypatch)
    w = tcopy.new_world()
    real_observe, real_state = w.observe, w.file_state
    late: list[bool] = []
    w.on_copy_load = lambda: late.append(True)
    target = tcopy.PRICES if which == "ledger" else tcopy.PDF

    def observe(ls: Any) -> Any:
        fp = real_observe(ls)
        if not late or which != "ledger":
            return fp
        files = tuple((p, CLOUD_UNAVAILABLE if p == target else s) for p, s in fp.files)
        return dataclasses.replace(fp, files=files)

    def file_state(p: str) -> Any:
        return CLOUD_UNAVAILABLE if late and p == target else real_state(p)

    ports = dataclasses.replace(w.ports(), observe=observe, file_state=file_state)
    nc = tcopy.not_copied(copy_run(tcopy.PLAN, ports, tcopy.Job()), "cloud_unavailable")
    assert nc.path == target
    assert not tcopy.exists(w.fs, tcopy.T), "round 2 (V2-3): what the copy made is removed"


def test_the_copy_checks_child_refuses_when_the_pin_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 1 (V5): the copy check's child pins the mode as the engine's start does, and a refused pin fails the
    check (Copy then refuses `load_failed`), never a load in another mode."""
    from ledger_engine.ledger import copy_check

    monkeypatch.setattr(this_os, "pin_placeholder_mode", lambda: False)
    loads: list[str] = []

    def load(*args: object, **kwargs: object) -> None:
        loads.append("loaded")

    monkeypatch.setattr(copy_check.overlay, "load", load)
    with pytest.raises(this_os.UnsupportedPlatform):
        copy_check._checked(cast("Any", None))  # pyright: ignore[reportPrivateUsage] (the pin comes first)
    assert loads == []


def test_must_not_pass_a_plain_read_error_at_copys_load_is_source_unreadable() -> None:
    w = tcopy.new_world()

    def load(root: str) -> Any:
        raise OSError(errno.EIO, "I/O error", tcopy.PRICES)

    tcopy.not_copied(copy_run(tcopy.PLAN, dataclasses.replace(w.ports(), load=load), tcopy.Job()), "source_unreadable")


# -- the pieces ------------------------------------------------------------------------------------------------------


class _Failing:
    """A file object whose read fails as the C runtime's does (a bare `OSError`)."""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def __enter__(self) -> _Failing:
        return self

    def __exit__(self, *_: object) -> None:
        import os

        os.close(self.fd)

    def read(self, size: int) -> bytes:
        raise OSError(errno.EINVAL, "Invalid argument")


@pytest.mark.parametrize("cloud", [True, False])
def test_chunks_tells_a_cloud_download_failure_only_when_readfile_names_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cloud: bool
) -> None:
    """`fingerprint.chunks`: a read failing through the C runtime is `CloudUnavailable` naming the file exactly when
    `this_os.cloud_download_failed` (Windows' `ReadFile` again) says so; else the C runtime's error as it was."""
    p = tmp_path / "main.bean"
    p.write_bytes(b"x")

    def fdopen(fd: int, mode: str) -> _Failing:
        del mode
        return _Failing(fd)

    def failed(fd: int) -> bool:
        del fd
        return cloud

    monkeypatch.setattr(fingerprint.os, "fdopen", fdopen)
    monkeypatch.setattr(this_os, "cloud_download_failed", failed)
    with pytest.raises(OSError, match=r".") as got:
        list(fingerprint.chunks(str(p)))
    assert isinstance(got.value, CloudUnavailable) is cloud
    if cloud:
        assert got.value.filename == str(p)
    else:
        assert got.value.errno == errno.EINVAL


def test_file_state_of_a_cloud_only_file_is_its_own_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "main.bean"
    p.write_bytes(b"x")
    offline(monkeypatch, str(p))
    assert fingerprint.file_state(str(p)) == fingerprint.CLOUD_UNAVAILABLE
    offline(monkeypatch, str(p), PLAIN_ERRORS["EINVAL"])
    assert fingerprint.file_state(str(p)) != fingerprint.CLOUD_UNAVAILABLE


def test_off_windows_nothing_is_a_cloud_download_failure_and_the_pin_has_nothing_to_do() -> None:
    import sys

    if sys.platform == "win32":
        pytest.skip("the runner's own: tests/fs/test_windows_cloud.py")
    assert this_os.cloud_download_failed(0) is False
    assert this_os.pin_placeholder_mode() is True
    assert this_os.pin_refused() is None


def test_the_download_errors_are_the_cloud_filters_and_never_a_common_code() -> None:
    """The set holds only codes of the cloud filter's block (`winerror.h` 358-427; the runner checks each one's text),
    never a code every read can give (access denied 5, sharing 32, the invalid parameter 87)."""
    assert 362 in CLOUD_DOWNLOAD_ERRORS
    assert all(358 <= c <= 427 for c in CLOUD_DOWNLOAD_ERRORS)
    assert not CLOUD_DOWNLOAD_ERRORS & {5, 32, 87, 1, 2, 3}
