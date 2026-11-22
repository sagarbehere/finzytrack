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
from typing import Any

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


@pytest.mark.parametrize("at", range(len(clean_effects())))
def test_a_cloud_error_at_every_effect_is_told_only_when_not_saved_and_names_a_file_of_the_save(at: int) -> None:
    """The error injected at every effect of the save: never "saved"; the outcome agrees with the disk in every
    power-loss state (`check_told`); a "not saved" outcome is `cloud_unavailable` only while the save worked on one of
    its files (the file named), else `io_error` (its own record, its backups folder: no ledger file to name)."""
    w = _windows()
    w.fs.inject(at, crash=False, cloud=True)
    out = durable_commit(w.fs, w.commit)
    assert not isinstance(out, Landed), out
    check_told(w.fs, w, out)
    if isinstance(out, NotSaved) and out.reason is Reason.CLOUD_UNAVAILABLE:
        assert out.path in {t.path for t in w.targets}, out
    effect = clean_effects()[at]
    if isinstance(out, NotSaved) and (".part" in effect or "journal" in effect):
        assert out.reason is not Reason.CLOUD_UNAVAILABLE, (effect, out)


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
    offline(monkeypatch, str(led.inc))
    out = sv.run(plan, ports_for(led), FakeJob())
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.path) == (Reason.CLOUD_UNAVAILABLE, str(led.inc))


def test_a_cloud_only_file_beside_another_change_stays_ledger_changed(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any other difference beside it (an outside edit of `inc.bean`) is the ledger changing: `ledger_changed`."""
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


def _rebuild(led: Ledger) -> dict[str, Any]:
    plan = plan_for(led)
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


def test_the_rebuild_of_a_cloud_only_file_is_refused_cloud_unavailable_named(
    led: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rebuild's first observation finds it (Beancount's own `open` would give a bare error)."""
    led.main.write_bytes(led.main.read_bytes() + b"; changed\n")  # not fresh: the rebuild loads
    offline(monkeypatch, str(led.main))
    reply = _rebuild(led)
    assert reply["ok"], reply
    r = reply["result"]
    assert (r["outcome"], r["refusal"], r["refused"]) == ("refused", "cloud_unavailable", str(led.main)), r


@pytest.mark.parametrize("kind", sorted(PLAIN_ERRORS))
def test_must_not_pass_a_plain_read_error_at_the_rebuild_is_never_cloud_unavailable(
    led: Ledger, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """`EINVAL` is refused before the load as a special file (`_refuse_special`), `EIO` reaches the load: neither is
    `cloud_unavailable`."""
    led.main.write_bytes(led.main.read_bytes() + b"; changed\n")
    offline(monkeypatch, str(led.main), PLAIN_ERRORS[kind])
    reply = _rebuild(led)
    assert not (reply["ok"] and reply["result"]["refusal"] == "cloud_unavailable"), reply


@pytest.mark.parametrize("dehydrated", [True, False])
def test_beancounts_own_read_failing_is_cloud_unavailable_only_when_the_file_is_not_downloadable(
    led: Ledger, monkeypatch: pytest.MonkeyPatch, dehydrated: bool
) -> None:
    """The file made cloud-only after the rebuild's first look and Beancount's own `open` failing on it with a bare
    `OSError` (the C runtime keeps no code): the rebuild looks again and refuses `cloud_unavailable` naming it. Must
    not pass: the same failure on a file that reads (a plain I/O error) stays the engine's failure, never this text."""
    from ledger_engine.ledger import beancount_port

    led.main.write_bytes(led.main.read_bytes() + b"; changed\n")

    def failing(root: str, parsed: list[str]) -> Any:
        if dehydrated:
            offline(monkeypatch, str(led.main))
        raise OSError(errno.EINVAL, "Invalid argument")

    monkeypatch.setattr(beancount_port, "real_load", failing)
    reply = _rebuild(led)
    if dehydrated:
        r = reply["result"]
        assert (r["outcome"], r["refusal"], r["refused"]) == ("refused", "cloud_unavailable", str(led.main)), reply
    else:
        assert not reply["ok"], reply
        assert reply["error"]["key"] == "engine.internal"


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
