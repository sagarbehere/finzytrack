"""The write contract end to end on a test copy: the real engine, a real disk, the sidecar's preview and confirm
(`app.workspaces.writes`; S0117; ADR-0008 steps 1-7, ADR-0014, ADR-0067, ADR-0068 D1; `design/specs/engine-jobs.md`
§2, §6.1, §6.7; BL-0106).

**The producer** (the brief's constraint: no test-only producer reachable in the shipped engine). The engine's
`preview.PRODUCERS` stays empty in the product; here the engine is started by a test-only entry, the sidecar's own
bootstrap (`bootstrap_argv`, the venv's site) running a few lines that put `engine/` on the path, register the test
producer of `engine/tests/ledger/producer.py` (S0116) and then run the engine as `ENGINE_RUN` does. `engine/tests` is
not part of the built package (`[tool.uv.build-backend] module-name = "ledger_engine"`) and is not on the engine's
path under the product's bootstrap; `test_the_shipped_engine_has_no_producer` holds both.

Each must-not-pass case compares the ledger folder's bytes before and after (the brief: "each with the disk
byte-identical afterwards").

BL-0255 (S0118): a rebuild that ends without publishing no longer leaves every preview `WORKSPACE_NOT_READY
{WARMING}` until a reopen: the stale preview queues the rebuild from the generation's stamp, and once it has
published, the preview prepares a plan again. With no usable mirror (removed by the OS) the preview queues nothing and
a reopen builds it (round 1, red team RT-F1).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.platform.adapters.app_folder import AppFolder, prepare
from app.platform.adapters.engine_client import (
    ENGINE_RUN,
    EngineClient,
    EngineCommand,
    bootstrap_argv,
    engine_env,
    venv_site,
)
from app.platform.adapters.engine_lock import engine_lock_path, wait_engine_lock
from app.platform.clock import FrozenClock
from app.platform.engine_jobs.opening import Opened, submit_open
from app.platform.engine_jobs.opening import OpenTarget as OpenJobTarget
from app.platform.engine_jobs.preview import NotPrepared, preview_spec
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.save import NotDone, Saved
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobContext, JobTicket, Work, WorkResult
from app.platform.engine_supervision import RestartPolicy
from app.platform.errors import ChangeNotSavedDetails, ErrorCode, WorkspaceNotReadyDetails, error
from app.platform.plans import PlannedFile, PlanStore, WritePlan, new_plan
from app.workspaces import writes
from app.workspaces.writes import CopyMark, Previewed, WriteServices, WriteTarget, mirror_generation

REPO = Path(__file__).resolve().parents[3]
ENGINE = REPO / "engine"
ENGINE_VENV = ENGINE / ".venv"
DEADLINE = 120.0
KEY = "k1"
MUTATOR = "test.edits"  # `engine/tests/ledger/producer.TEST_MUTATOR`
CLOCK = FrozenClock(datetime(2026, 11, 14, 10, 0, tzinfo=UTC))
BEFORE = b'option "operating_currency" "USD"\n2026-01-01 open Assets:Cash USD\n'
NOTE = '2026-01-02 note Assets:Cash "s0117"\n'

WITH_TEST_PRODUCER = (
    f"import sys; sys.path.insert(0, {str(ENGINE)!r}); "
    "from tests.ledger import producer; from ledger_engine.ledger import preview; "
    "preview.PRODUCERS[producer.TEST_MUTATOR] = producer.produce_json; " + ENGINE_RUN
)


def python() -> Path:
    found = ENGINE_VENV / "bin" / "python"
    if not found.exists():
        pytest.fail("engine/.venv is missing: run `uv sync --locked` in engine/ (the engine tests need it)")
    return found


def recorded_mark(folder: Path) -> CopyMark:
    """The test-copy mark as the copy job will record it when it makes the folder (BL-0267, M3): its real path and its
    identity, read by the engine's own file-system port (the journal's folder identity, S0089), in the engine's
    interpreter (the sidecar never imports the engine)."""
    path = os.path.realpath(folder)
    code = (
        "import json, sys; from ledger_engine.fs.this_os import for_this_os; "
        "m = for_this_os().open_dir(sys.argv[-1]).mark(); "
        "print(json.dumps([m.volume, m.fs_type, m.born]))"
    )
    out = subprocess.run(  # noqa: S603 (fixed argv: the engine's interpreter under the sidecar's bootstrap)
        [*bootstrap_argv(python(), venv_site(ENGINE_VENV), code), path],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        env=engine_env(os.environ),
    )
    volume, fs_type, born = json.loads(out.stdout)
    return CopyMark(path, volume, fs_type, born)


def snapshot(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


class Rig:
    """A test copy (its folder holds the ledger), the app's folders, a scheduler over a real engine that has the test
    producer, the open's published mirror, and the write services as the product wires them (the generation read
    from the published mirror: `mirror_generation`)."""

    def __init__(self, tmp: Path) -> None:
        tmp = tmp.resolve()
        self.folder = tmp / "copy"
        self.folder.mkdir()
        self.main = self.folder / "main.bean"
        self.main.write_bytes(BEFORE)
        app = prepare(tmp / "app")
        assert isinstance(app, AppFolder), app
        self.app: AppFolder = app
        self.cache = tmp / "cache"
        self.cache.mkdir()
        command = EngineCommand(
            argv=bootstrap_argv(python(), venv_site(ENGINE_VENV), WITH_TEST_PRODUCER),
            env=engine_env(os.environ),
            engine_lock=engine_lock_path(app.path),
            app_folder=app,
            app_cache=self.cache,
        )
        self.client = EngineClient(
            command, RestartPolicy(), on_engine_spawned=self._spawned, on_engine_ended=self._ended
        )
        self.jobs = EngineJobs(self.client, wait_engine_lock=lambda: wait_engine_lock(app))
        boot = self.jobs.start()
        assert isinstance(boot, JobTicket)
        assert boot.wait(DEADLINE) is not None
        self.jobs.exclusive_began()
        opening = submit_open(self.jobs, KEY, OpenJobTarget(KEY, str(self.main), "lin1", app.path), clock=CLOCK)
        assert isinstance(opening, JobTicket)
        opened = opening.wait(DEADLINE)
        assert isinstance(opened, Done), opened
        assert isinstance(opened.value, Opened), opened.value
        assert opened.value.mirror_ready, opened.value
        self.jobs.exclusive_ended()
        self.jobs.workspace_opened(KEY)
        self.target = WriteTarget(
            workspace=KEY,
            root=str(self.main),
            lineage="lin1",
            app_local_data=app.path,
            app_cache=self.cache,
            copy=recorded_mark(self.folder),
        )
        self.plans = PlanStore(CLOCK)
        self.services = WriteServices(jobs=self.jobs, plans=self.plans, clock=CLOCK, ids=_Ids(), session="s1")

    def _spawned(self, serial: int) -> None:
        self.jobs.engine_spawned(serial)

    def _ended(self, end: object) -> None:
        self.jobs.engine_ended(end)  # pyright: ignore[reportArgumentType] (the client's own report)

    def close(self) -> None:
        self.jobs.close()
        self.client.close()

    def generation(self) -> int | None:
        return mirror_generation(self.target)

    def preview(self, target: WriteTarget | None = None) -> Previewed:
        request = {"edits": [{"kind": "insert", "text": NOTE, "default_file": str(self.main)}]}
        generation = self.generation()
        assert generation is not None
        ticket = writes.submit_preview(self.services, target or self.target, generation, MUTATOR, request)
        assert isinstance(ticket, JobTicket), ticket
        out = ticket.wait(DEADLINE)
        assert isinstance(out, Done), out
        assert isinstance(out.value, Previewed), out.value
        return out.value

    def confirm(self, p: Previewed, target: WriteTarget | None = None) -> Saved | NotDone | Failed:
        out = writes.submit_confirm(
            self.services, target or self.target, p.plan_id, p.preview_hash, acknowledged=frozenset()
        )
        if isinstance(out, NotDone | Failed):
            return out
        done = out.wait(DEADLINE)
        assert isinstance(done, Done | Failed), done
        if isinstance(done, Failed):
            return done
        assert isinstance(done.value, Saved | NotDone), done.value
        return done.value


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self) -> str:
        self.n += 1
        return f"plan-{self.n}"


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    yield r
    r.close()


def code_of(value: object) -> str:
    assert isinstance(value, NotDone | Failed), value
    return value.error.code


def wait_for(cond: object, seconds: float = DEADLINE) -> bool:
    for _ in range(int(seconds / 0.05)):
        if cond():  # pyright: ignore[reportCallIssue]
            return True
        threading.Event().wait(0.05)
    return False


def test_a_preview_confirmed_on_a_test_copy_is_saved_byte_for_byte_and_rebuilt(rig: Rig) -> None:
    p = rig.preview()
    assert len(p.files) == 1
    planned = p.files[0]
    assert planned.path == str(rig.main)
    assert planned.text.startswith(BEFORE.decode())
    assert NOTE in planned.text
    assert rig.main.read_bytes() == BEFORE  # the preview wrote nothing
    assert rig.confirm(p) == Saved(published=False)
    assert rig.main.read_bytes() == planned.text.encode()  # exactly the plan's text
    assert wait_for(lambda: rig.generation() == 2)  # the background rebuild ran and published (§6.7)


def test_a_file_changed_after_the_preview_is_ledger_externally_changed_and_kept(rig: Rig) -> None:
    p = rig.preview()
    rig.main.write_bytes(BEFORE + b"; another program\n")
    before = snapshot(rig.folder)
    assert code_of(rig.confirm(p)) == ErrorCode.LEDGER_EXTERNALLY_CHANGED
    assert snapshot(rig.folder) == before


def test_a_real_workspace_is_refused_by_the_guards_default(rig: Rig) -> None:
    real = dataclasses.replace(rig.target, copy=None)
    p = rig.preview(real)
    before = snapshot(rig.folder)
    assert code_of(rig.confirm(p, real)) == ErrorCode.UNPROMOTED_MUTATOR_ON_REAL_LEDGER
    assert snapshot(rig.folder) == before


def _planted(rig: Rig, p: Previewed, extra: PlannedFile) -> Previewed:
    """The previewed plan with one more file, put back in the store under its own hash (as if a bug had stored it)."""
    stored = rig.plans.take(KEY, p.plan_id)
    assert stored is not None
    f = dataclasses.asdict(stored)
    f.pop("preview_hash")
    f["files"] = (*stored.files, extra)
    bad = new_plan(**f)
    rig.plans.add(bad)
    return dataclasses.replace(p, preview_hash=bad.preview_hash)


def test_a_plan_with_a_file_outside_the_ledger_set_writes_nothing(rig: Rig) -> None:
    """Rule 5: the save refuses a change that is not in the plan's fingerprint at its `pre` bytes before anything is
    written (`ledger_engine.ledger.save`, `pre_not_the_plans`); only our own preview makes plans, so it is a bug:
    `INTERNAL`, never "saved". The file is inside the test copy, so the promotion guard lets it through to the save."""
    other = rig.folder / "other.bean"
    other.write_bytes(b"; not in the ledger\n")
    p = rig.preview()
    old = hashlib.sha256(other.read_bytes()).hexdigest()
    new = "; written\n"
    p = _planted(rig, p, PlannedFile(str(other), old, hashlib.sha256(new.encode()).hexdigest(), new))
    before = snapshot(rig.folder)
    assert code_of(rig.confirm(p)) == ErrorCode.INTERNAL
    assert snapshot(rig.folder) == before


def test_a_plan_with_an_encrypted_file_writes_nothing(rig: Rig) -> None:
    secret = rig.folder / "secret.bean.gpg"
    secret.write_bytes(b"\x85\x01not really encrypted\n")
    p = rig.preview()
    old = hashlib.sha256(secret.read_bytes()).hexdigest()
    p = _planted(rig, p, PlannedFile(str(secret), old, hashlib.sha256(b"x\n").hexdigest(), "x\n"))
    before = snapshot(rig.folder)
    assert code_of(rig.confirm(p)) == ErrorCode.ENCRYPTED_FILE_NOT_EDITABLE
    assert snapshot(rig.folder) == before


def _hold_the_engine(rig: Rig) -> threading.Event:
    """A job that holds the one engine slot (as a long rebuild or another preview would) until the event is set."""
    release, started = threading.Event(), threading.Event()

    def work(_ctx: JobContext) -> WorkResult:
        started.set()
        release.wait(DEADLINE)
        return WorkResult(None, ran_user_code=False)

    ticket = rig.jobs.submit(preview_spec(KEY), work)
    assert isinstance(ticket, JobTicket), ticket
    assert started.wait(DEADLINE)
    return release


def _queued_confirm(rig: Rig, p: Previewed) -> JobTicket:
    ticket = writes.submit_confirm(rig.services, rig.target, p.plan_id, p.preview_hash, acknowledged=frozenset())
    assert isinstance(ticket, JobTicket), ticket  # the guard passed: every file was inside the copy then
    return ticket


def test_a_link_to_the_real_ledger_swapped_in_after_the_confirm_writes_nothing(rig: Rig, tmp_path: Path) -> None:
    """S0117 round 1, red team RT-F1 (owner, 2026-11-14: "Go with (2), fix it now"): a test copy holds the bytes of its
    original, so a link from the copy's file to the original, made while the confirm waits in the queue, leaves the
    plan's fingerprint as it was. The engine's writer resolves the file after the gate and refuses it: neither the
    real ledger nor the copy changes."""
    real = tmp_path.resolve() / "real"
    real.mkdir()
    (real / "main.bean").write_bytes(BEFORE)
    p = rig.preview()
    release = _hold_the_engine(rig)
    ticket = _queued_confirm(rig, p)
    rig.main.unlink()
    rig.main.symlink_to(real / "main.bean")
    before = (snapshot(rig.folder), snapshot(real))
    release.set()
    done = ticket.wait(DEADLINE)
    assert isinstance(done, Done), done
    assert isinstance(done.value, NotDone), done.value
    assert done.value.error.code == ErrorCode.CHANGE_NOT_SAVED
    details = done.value.error.details
    assert isinstance(details, ChangeNotSavedDetails)
    assert (details.reason, details.unchanged) == ("link_in_copy", False)  # round 3: no link followed
    assert (snapshot(rig.folder), snapshot(real)) == before
    assert rig.main.is_symlink()


def _real_ledger(tmp_path: Path) -> Path:
    """The real ledger the test copy was made from: the same file, the same bytes."""
    real = tmp_path.resolve() / "real"
    real.mkdir()
    (real / "main.bean").write_bytes(BEFORE)
    return real


def _copy_folder_moved(done: object, folder: Path) -> None:
    """Refused: the copy's folder is not the recorded one (a link at it, or another folder at its path); nothing read
    back through it, so never "unchanged"."""
    assert isinstance(done, Done), done
    assert isinstance(done.value, NotDone), done.value
    details = done.value.error.details
    assert isinstance(details, ChangeNotSavedDetails)
    assert (details.reason, details.folder, details.unchanged) == ("copy_folder_moved", str(folder), False)


@pytest.mark.parametrize("to", ["the_moved_copy", "the_real_ledger"])
def test_the_copys_folder_swapped_for_a_link_after_the_confirm_writes_nothing(
    rig: Rig, tmp_path: Path, to: str
) -> None:
    """The copy's folder moved away and a link put at its path, while the confirm waits in the queue: to the moved
    copy itself, or to the real ledger's folder (round 2, V-R2-1 / RT2-F1)."""
    real = _real_ledger(tmp_path)
    p = rig.preview()
    release = _hold_the_engine(rig)
    ticket = _queued_confirm(rig, p)
    moved = tmp_path.resolve() / "moved"
    rig.folder.rename(moved)
    rig.folder.symlink_to(moved if to == "the_moved_copy" else real)
    before = (snapshot(moved), snapshot(real))
    release.set()
    _copy_folder_moved(ticket.wait(DEADLINE), rig.folder)
    assert (snapshot(moved), snapshot(real)) == before


def no_early_look(_plan: WritePlan, _target: WriteTarget) -> None:
    """The sidecar's early guard switched off, to show the engine's own check."""


@pytest.mark.parametrize("when", ["before_the_preview", "between_the_preview_and_the_confirm"])
def test_the_copys_folder_replaced_by_a_link_to_the_real_ledger_writes_nothing(
    rig: Rig, tmp_path: Path, when: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S0117 round 2 (verifier V-R2-1, red team RT2-F1; owner, 2026-11-14: "3"): the sidecar never resolves the folder
    and the engine checks the recorded path and identity, so a link to the real ledger's folder at the copy's path,
    made at any time, is refused when the save writes; the real ledger byte-identical."""
    real = _real_ledger(tmp_path)

    def swap() -> None:
        rig.folder.rename(tmp_path.resolve() / "moved")
        rig.folder.symlink_to(real)

    if when == "before_the_preview":
        swap()
    p = rig.preview()
    if when != "before_the_preview":
        swap()
    before = snapshot(real)
    assert code_of(rig.confirm(p)) == ErrorCode.UNPROMOTED_MUTATOR_ON_REAL_LEDGER  # the early look sees it
    monkeypatch.setattr(writes, "promotion_refusal", no_early_look)  # without it, the engine refuses
    _copy_folder_moved(Done(rig.confirm(p)), rig.folder)
    assert snapshot(real) == before


def test_the_copys_folder_replaced_after_the_guard_writes_nothing(rig: Rig, tmp_path: Path) -> None:
    """Between the early guard and the save (V-R2-2's window): the value the guard read is the one sent; the engine
    checks it when it writes."""
    real = _real_ledger(tmp_path)
    p = rig.preview()
    release = _hold_the_engine(rig)
    ticket = _queued_confirm(rig, p)  # the guard passed, `confine` built from the same mark
    rig.folder.rename(tmp_path.resolve() / "moved")
    real.rename(rig.folder)  # no link at all: the real ledger's own folder moved to the copy's path
    before = snapshot(rig.folder)
    release.set()
    _copy_folder_moved(ticket.wait(DEADLINE), rig.folder)
    assert snapshot(rig.folder) == before


def test_a_folder_made_again_at_the_copys_path_writes_nothing(rig: Rig, tmp_path: Path) -> None:
    """The right path, another folder: deleted and made again (another creation time)."""
    p = rig.preview()
    release = _hold_the_engine(rig)
    ticket = _queued_confirm(rig, p)
    shutil.rmtree(rig.folder)
    rig.folder.mkdir()
    rig.main.write_bytes(BEFORE)
    before = snapshot(rig.folder)
    release.set()
    _copy_folder_moved(ticket.wait(DEADLINE), rig.folder)
    assert snapshot(rig.folder) == before


def test_a_second_confirm_of_a_saved_plan_is_already_confirmed(rig: Rig) -> None:
    """S0117 round 1, red team RT-F2: sequential (a retry after a lost answer) and queued together (a double click)."""
    p = rig.preview()
    assert rig.confirm(p) == Saved(published=False)
    assert code_of(rig.confirm(p)) == ErrorCode.PLAN_ALREADY_CONFIRMED
    assert wait_for(lambda: rig.generation() == 2)
    q = rig.preview()
    release = _hold_the_engine(rig)
    first, second = _queued_confirm(rig, q), _queued_confirm(rig, q)
    release.set()
    one, two = first.wait(DEADLINE), second.wait(DEADLINE)
    assert isinstance(one, Done)
    assert one.value == Saved(published=False)
    assert isinstance(two, Done)
    assert code_of(two.value) == ErrorCode.PLAN_ALREADY_CONFIRMED


# -- BL-0255 (S0118): "wait a moment and try again" comes true -------------------------------------------------------

REBUILT = 30.0  # a rebuild of this one-file ledger takes well under a second; on the base none is ever queued


def _warming(rig: Rig, generation: int) -> None:
    request = {"edits": [{"kind": "insert", "text": NOTE, "default_file": str(rig.main)}]}
    ticket = writes.submit_preview(rig.services, rig.target, generation, MUTATOR, request)
    assert isinstance(ticket, JobTicket), ticket
    out = ticket.wait(DEADLINE)
    assert isinstance(out, Done), out
    assert isinstance(out.value, NotPrepared), out.value
    assert (out.value.error.code, out.value.error.details) == (
        ErrorCode.WORKSPACE_NOT_READY,
        WorkspaceNotReadyDetails(phase="WARMING"),
    )


def test_after_a_rebuild_that_did_not_publish_the_next_preview_queues_one(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The landed save's rebuild never runs (its submit refused: a full queue, one of BL-0255's causes), so the
    published generation no longer describes the disk. The preview answers WARMING (`not_fresh`) and queues the
    rebuild with the generation's stamp as its hint; once it has published, the preview prepares a plan."""
    submit, refused = rig.jobs.submit, list[JobSpec]()

    def refuse_the_first_rebuild(spec: JobSpec, work: Work) -> JobTicket | Failed:
        if spec.kind == "rebuild" and not refused:
            refused.append(spec)
            return Failed(error(ErrorCode.ENGINE_QUEUE_FULL))
        return submit(spec, work)

    monkeypatch.setattr(rig.jobs, "submit", refuse_the_first_rebuild)
    assert rig.confirm(rig.preview()) == Saved(published=False)
    assert len(refused) == 1
    assert rig.generation() == 1  # nothing published since the save
    _warming(rig, 1)
    assert wait_for(lambda: rig.generation() == 2, REBUILT), "the stale preview queued no rebuild"
    p = rig.preview()
    assert p.files[0].text.count(NOTE) == 2  # planned on the saved ledger


def _rebuild_queued(rig: Rig) -> bool:
    q = rig.jobs.status().queue
    return any(j.kind == "rebuild" for j in q.queued) or (q.running is not None and q.running.job.kind == "rebuild")


def test_after_the_os_removed_the_mirror_previews_stay_warming_until_a_reopen(rig: Rig) -> None:
    """S0118 round 1 (red team RT-F1; owner, 2026-11-14: "Narrow the scope (Recommended)"): with no published mirror
    there is no stamp to rebuild from (the root alone would drop the ledger's include patterns), so the preview queues
    nothing and stays WARMING; a reopen builds the mirror again (engine-jobs §15)."""
    assert rig.confirm(rig.preview()) == Saved(published=False)
    assert wait_for(lambda: rig.generation() == 2)
    shutil.rmtree(rig.cache / "workspaces" / KEY / "mirror")
    _warming(rig, 2)
    assert not _rebuild_queued(rig)  # the job would have queued it before its answer
    assert rig.generation() is None
    rig.jobs.exclusive_began()  # the reopen
    opening = submit_open(rig.jobs, KEY, OpenJobTarget(KEY, str(rig.main), "lin1", rig.app.path), clock=CLOCK)
    assert isinstance(opening, JobTicket)
    opened = opening.wait(DEADLINE)
    rig.jobs.exclusive_ended()
    assert isinstance(opened, Done), opened
    assert isinstance(opened.value, Opened), opened.value
    assert rig.preview().files[0].text.count(NOTE) == 2


PRODUCER_HOME = "src/ledger_engine/ledger/preview.py"
PRODUCER_HOME_LINES = (
    "PRODUCERS: Final[dict[str, Producer]] = {}",
    "    producers: Mapping[str, Producer] = PRODUCERS,",
)


def producer_writers(sources: dict[str, str]) -> list[str]:
    """S0117 round 1 (verifier V3): every place of the engine's source that names `PRODUCERS`, except its definition
    and the default of `preview.run` (so a write in any spelling, `setdefault`, `|=`, `update`, an item, or an alias,
    is found by the name it must use); a name built at run time is the full import's to catch."""
    out: list[str] = []
    for name, text in sources.items():
        for n, line in enumerate(text.splitlines(), 1):
            prose = line.replace("`PRODUCERS`", "") if name == PRODUCER_HOME else line  # its own docstring
            if "PRODUCERS" in prose and not (name == PRODUCER_HOME and line in PRODUCER_HOME_LINES):
                out.append(f"{name}:{n}")
    return out


@pytest.mark.parametrize(
    "line",
    [
        'preview.PRODUCERS["x"] = f',
        'preview.PRODUCERS.setdefault("x", f)',
        "preview.PRODUCERS |= {'x': f}",
        "preview.PRODUCERS.update(x=f)",
        "P = preview.PRODUCERS",
        "from ledger_engine.ledger.preview import PRODUCERS as P",
    ],
)
def test_the_producer_scan_finds_every_spelling_of_a_write(line: str) -> None:
    """@regression-guard (it tests this file's own scan, which passes on any base): verifier V3's shapes."""
    assert producer_writers({"src/ledger_engine/x.py": f"{line}\n"}) == ["src/ledger_engine/x.py:1"]
    assert producer_writers({PRODUCER_HOME: f"{line}\n"}) == [f"{PRODUCER_HOME}:1"]


def test_the_shipped_engine_has_no_producer(tmp_path: Path) -> None:
    """Under the product's bootstrap (`EngineCommand.for_venv` / `for_packaged`: `ENGINE_RUN` after the path setup
    with the engine's site only), `preview.PRODUCERS` is empty and the engine's tests cannot be imported; no module
    of the engine's source registers a producer."""
    code = (
        "import importlib.util as u; from ledger_engine.ledger import preview; "
        "print(len(preview.PRODUCERS), u.find_spec('tests') is None)"
    )
    out = subprocess.run(  # noqa: S603 (fixed argv: the engine's interpreter under the sidecar's bootstrap)
        bootstrap_argv(python(), venv_site(ENGINE_VENV), code),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        cwd=tmp_path,
        env=engine_env(os.environ),
    )
    assert out.stdout.split() == ["0", "True"], out
    product = EngineCommand.for_venv(ENGINE_VENV).argv
    assert "producer" not in " ".join(product)
    sources = {
        str(p.relative_to(ENGINE)): p.read_text(encoding="utf-8") for p in sorted((ENGINE / "src").rglob("*.py"))
    }
    assert PRODUCER_HOME in sources
    assert producer_writers(sources) == []
    every_module = (  # every module of the engine imported, then the registry read (verifier V3)
        "import importlib, pkgutil, ledger_engine; "
        "[importlib.import_module(m.name) for m in pkgutil.walk_packages(ledger_engine.__path__, 'ledger_engine.') "
        "if m.name != 'ledger_engine.__main__']; "
        "from ledger_engine.ledger import preview; print(len(preview.PRODUCERS))"
    )
    out = subprocess.run(  # noqa: S603 (fixed argv: the engine's interpreter under the sidecar's bootstrap)
        bootstrap_argv(python(), venv_site(ENGINE_VENV), every_module),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
        cwd=tmp_path,
        env=engine_env(os.environ),
    )
    assert out.stdout.split() == ["0"], out
