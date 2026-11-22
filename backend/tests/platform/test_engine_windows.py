"""The sidecar's engine supervision on Windows (S0129, BL-0281; `design/specs/engine-jobs.md` §5.1, §5.2, §5.5, §9.3;
ADR-0064, ADR-0061 and its amendment of 2026-11-14). Runs on the Windows runner (`backend-windows` in `ci.yml`).

Written from the brief and the spec:
- "Each engine leads its own process group (POSIX; a Job Object nested in the shell's, with kill-on-close, on
  Windows). Our end signals the whole group until it has no live member, so a plugin's child processes go too and
  none outlives its engine's drain."
- "On Windows the Job Object's kill-on-close does it when the sidecar's handle closes."
- "CPU of a job ... Windows the Job Object total."
- "A process that left the job (`CREATE_BREAKAWAY_FROM_JOB`, where allowed) is out of reach": the engine's job allows
  none, so a member that asks for it is refused (the agent's decision, S0129's journal).
- "Cancel and retire end the engine through its job."
- The engine lock waited for with `LockFileEx` as the engine takes it, the advice on the timer meanwhile (§5.1).
- The app folder's identity: the volume serial and the file ID on NTFS, never the creation time, and the same as the
  engine's (`port.folder_id`).

Every process a test starts is watched through a handle held from the moment its pid is known
(`tests/platform/windows_procs.py`), so a pid cannot pass to another program while a test looks at it. A leader
script waits for a line on its stdin before it starts any child: the engine likewise starts nothing before the
sidecar's first request, which comes after the engine is in its job (the agent's decision, S0129's journal).
"""

from __future__ import annotations

import contextlib
import faulthandler
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Generator, Iterator
from pathlib import Path

import pytest

from tests.windows_pending import WINDOWS_ONLY

if sys.platform != "win32":
    raise pytest.skip.Exception(WINDOWS_ONLY, allow_module_level=True)

from app.platform.adapters import app_folder
from app.platform.adapters.app_folder import AppFolder, AppFolderRefusal
from app.platform.adapters.engine_client import EngineClient, EngineCommand
from app.platform.adapters.engine_group import EngineGroup
from app.platform.adapters.engine_lock import engine_lock_path, wait_engine_lock
from app.platform.adapters.windows_job import EngineJob
from app.platform.engine import AppFolderChangedError, EngineEnd, EngineFailure
from app.platform.engine_jobs.scheduler import EngineJobs
from tests.platform.test_engine_scheduler import DELAY, Rig, outcome
from tests.platform.windows_procs import Watched, lock_free, open_exclusively

REPO = Path(__file__).resolve().parents[3]
ENGINE_VENV = REPO / "engine" / ".venv"
ENGINE_PYTHON = ENGINE_VENV / "Scripts" / "python.exe"
DEADLINE = 20.0
RUNS = 20


@pytest.fixture(autouse=True)
def hang_guard() -> Iterator[None]:
    """The test's own hang detector, not the product's (S0057 retro): the client has no time limits."""
    faulthandler.dump_traceback_later(15 * DEADLINE, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


def wait_until(cond: Callable[[], bool], timeout: float = DEADLINE) -> bool:
    end = time.monotonic() + timeout
    tick = threading.Event()
    while time.monotonic() < end:
        if cond():
            return True
        tick.wait(0.02)
    return cond()


def engine_command(**kw: object) -> EngineCommand:
    if not ENGINE_PYTHON.exists():
        pytest.skip("no engine environment: run `uv sync --locked` in engine/")
    return EngineCommand.for_venv(ENGINE_VENV, test_ops=True, **kw)  # pyright: ignore[reportArgumentType]


# -- the Job Object on its own -------------------------------------------------------------------------------------

HOLDER = r"""
import msvcrt, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT | os.O_BINARY)
msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
print("held", flush=True)
time.sleep(int(sys.argv[2]) if len(sys.argv) > 2 else 3600)
"""
"""A plugin's child holding a file open with a lock on it (the engine lock's kind of hold)."""

LEADER = r"""
import subprocess, sys, time
sys.stdin.readline()  # nothing starts before the sidecar's first line: the engine is in its job by then
child = subprocess.Popen([sys.executable, "-I", "-c", sys.argv[1], *sys.argv[2:]], stdout=subprocess.PIPE, text=True)
print(child.pid, child.stdout.readline().strip(), flush=True)
time.sleep(3600)
"""
"""An engine whose plugin started `argv[1]` as its child; prints the child's pid and its first line."""


@contextlib.contextmanager
def in_a_job(
    script: str, *args: str, python: str = sys.executable
) -> Generator[tuple[subprocess.Popen[str], EngineJob, Watched]]:
    """A leader started as the client starts an engine, put in its own job, then told to go."""
    proc = subprocess.Popen(  # noqa: S603 (this interpreter, or the engine's, with a fixed script)
        [python, "-I", "-c", script, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    leader = Watched(proc.pid)
    job = EngineJob.around(proc)
    try:
        assert proc.stdin is not None
        proc.stdin.write("go\n")
        proc.stdin.flush()
        yield proc, job, leader
    finally:
        with contextlib.suppress(OSError):
            job.ops.kill_group(proc.pid)
        job.close()
        leader.close()
        if proc.returncode is None:
            proc.wait(DEADLINE)
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                pipe.close()


def child_of(proc: subprocess.Popen[str]) -> tuple[Watched, str]:
    assert proc.stdout is not None
    pid, first = proc.stdout.readline().split(maxsplit=1)
    return Watched(int(pid)), first.strip()


def test_an_engine_and_the_children_it_starts_are_members_of_its_job(tmp_path: Path) -> None:
    with in_a_job(LEADER, HOLDER, str(tmp_path / "f.lock")) as (proc, job, _):
        child, first = child_of(proc)
        try:
            assert first == "held"
            members = job.member_ids()
            assert members is not None
            assert {proc.pid, child.pid} <= set(members)
        finally:
            child.close()


def test_the_drain_ends_a_plugins_child_and_every_handle_it_held_is_closed_at_the_reap(tmp_path: Path) -> None:
    """§5.2 ("none outlives its engine's drain") and "no live member" on Windows: the leader crashes alone (Task
    Manager's end), the drain ends the rest; at the reap the child is gone and the file it held open with a lock on it
    can be opened with no sharing at all, at once. Repeated: an exiting process leaves the job's list before its
    handles are closed, so a drain that read only the list would reap too early in some runs. The child's lock is
    free soon after, not at once: Windows frees a dead process's locks after it ends (§5.2, "Limit (lock release on
    Windows)"; BL-0291, S0137)."""
    for run in range(RUNS):
        held = tmp_path / f"held-{run}.lock"
        with in_a_job(LEADER, HOLDER, str(held)) as (proc, job, leader):
            child, _ = child_of(proc)
            try:
                assert not lock_free(held)
                leader.end()
                reaped = EngineGroup(job.ops, proc.pid).drain()
                proc.returncode = reaped.returncode
                opened, gone = open_exclusively(held), child.gone()
                assert (opened, gone) == (True, True), f"run {run}: the child outlived the drain"
                assert wait_until(lambda held=held: lock_free(held)), (
                    f"run {run}: the dead child's lock was never freed"
                )
            finally:
                child.close()


COPY_CHECK_ENGINE = r"""
import sys
sys.stdin.readline()  # nothing starts before the sidecar's first line: the engine is in its job by then
from ledger_engine.ledger import fresh_load
from ledger_engine.ledger.copy_check import CheckRequest
from ledger_engine.ledger.core import copy_layout as cl
folder = sys.argv[1]
print(fresh_load.check(CheckRequest(folder + "\\main.beancount", folder, cl.Layout(folder, ()), ())), flush=True)
"""
"""An engine running the copy's "loads the same" check (`fresh_load.check`) on the ledger in `argv[1]`."""

STUCK_PLUGIN = """
import os, threading
__plugins__ = ["run"]
def run(entries, options, config):
    with open(config + ".part", "w") as f:
        f.write(str(os.getpid()))
    os.replace(config + ".part", config)
    threading.Event().wait()
"""
"""A plugin that names its process, then never returns."""


def test_S0146_the_copy_checks_child_is_a_member_of_the_engines_job_and_ends_with_its_drain(tmp_path: Path) -> None:
    """@regression-guard (Windows' own rule; at the base the check's reader failed, but its child was started the same
    way). BL-0087 (fresh_load's docstring: the child "stays in the engine's process group"): on Windows the copy check's
    child (`fresh_load.check`, started with `subprocess.Popen`, no breakaway asked) is a member of the engine's Job
    Object, so the drain that ends the engine ends it too, never a process left running the user's plugin."""
    if not ENGINE_PYTHON.exists():
        pytest.skip("no engine environment: run `uv sync --locked` in engine/")
    books = tmp_path / "books"
    books.mkdir()
    pid_file = tmp_path / "child.pid"
    (books / "pl_s0146.py").write_text(STUCK_PLUGIN)
    named = str(pid_file).replace("\\", "\\\\")  # Beancount reads a backslash in a string as an escape
    (books / "main.beancount").write_text(
        f'option "insert_pythonpath" "True"\nplugin "pl_s0146" "{named}"\n2026-01-01 open Assets:Cash EUR\n'
    )
    with in_a_job(COPY_CHECK_ENGINE, str(books), python=str(ENGINE_PYTHON)) as (proc, job, leader):
        ran = wait_until(lambda: pid_file.exists() or proc.poll() is not None, 5 * DEADLINE)
        assert ran, "the copy check's child never ran the plugin"
        assert proc.poll() is None, f"the engine ended first: {proc.returncode}"
        child = Watched(int(pid_file.read_text()))
        try:
            members = job.member_ids()
            assert members is not None
            assert {proc.pid, child.pid} <= set(members), "the child is in the engine's job"
            leader.end()  # the engine crashes (the drain starts once its leader has exited, §5.2)
            reaped = EngineGroup(job.ops, proc.pid).drain()
            proc.returncode = reaped.returncode
            assert child.gone(), "the child outlived the engine's drain"
        finally:
            child.close()


SPAWNER = r"""
import subprocess, sys, time
print("spawning", flush=True)
workers = []
while True:  # a plugin helper that starts workers in a loop; each holds the same file open
    workers.append(subprocess.Popen([sys.executable, "-I", "-c", sys.argv[1], sys.argv[2], "30"]))
    time.sleep(0.005)
"""

WORKER = r"""
import os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT | os.O_BINARY)
time.sleep(int(sys.argv[2]))
"""


def test_a_member_starting_processes_in_a_loop_does_not_outlive_the_drain(tmp_path: Path) -> None:
    """BL-0190's Windows form: members started while the job is ended are ended too; at the reap no process of the
    job is left and no worker holds the shared file open."""
    for run in range(5):
        shared = tmp_path / f"shared-{run}"
        shared.touch()
        with in_a_job(LEADER, SPAWNER, WORKER, str(shared)) as (proc, job, leader):
            spawner, first = child_of(proc)
            try:
                assert first == "spawning"
                assert wait_until(lambda: len(job.member_ids() or ()) > 4), "the spawner did not start workers"
                leader.end()
                proc.returncode = EngineGroup(job.ops, proc.pid).drain().returncode
                assert open_exclusively(shared), f"run {run}: a worker still holds the file after the drain"
                assert spawner.gone()
            finally:
                spawner.close()


BREAKAWAY = r"""
import subprocess, sys, time
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
try:
    p = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                         creationflags=CREATE_BREAKAWAY_FROM_JOB)
except OSError as e:
    print("refused", getattr(e, "winerror", None), flush=True)
else:
    print("created", p.pid, flush=True)
time.sleep(3600)
"""


def test_a_member_that_asks_to_leave_the_engines_job_stays_in_the_drains_reach() -> None:
    """The engine's job allows no breakaway (S0129's journal): a plugin's child started with
    `CREATE_BREAKAWAY_FROM_JOB` is either refused or, where the shell's job above allows breakaway, leaves only the
    jobs that allow it and stays in the engine's, so the drain ends it like any member."""
    with in_a_job(LEADER, BREAKAWAY) as (proc, job, leader):
        member, first = child_of(proc)
        member.close()
        word, value = first.split()
        if word == "refused":
            return
        asked = Watched(int(value))
        try:
            listed = job.member_ids() or []
            leader.end()
            proc.returncode = EngineGroup(job.ops, proc.pid).drain().returncode
            where = f"listed in the job before the drain: {asked.pid in listed}"
            assert asked.gone(), f"a child that asked to leave the job outlived the drain ({where})"
            assert asked.pid in listed
        finally:
            asked.end()
            asked.close()


BURNER = r"""
import time
while time.process_time() < 0.5:
    pass
print("burnt", flush=True)
"""

BURNING_LEADER = r"""
import subprocess, sys
sys.stdin.readline()
subprocess.run([sys.executable, "-I", "-c", sys.argv[1]], check=True)
"""


def test_the_cpu_of_a_job_is_the_job_objects_total_its_childrens_included() -> None:
    """§5.2: "CPU of a job ... Windows the Job Object total": a child's half second of CPU is counted."""
    with in_a_job(BURNING_LEADER, BURNER) as (proc, job, _):
        reaped = EngineGroup(job.ops, proc.pid).drain()
        proc.returncode = reaped.returncode
        assert reaped.returncode == 0
        assert reaped.cpu_ns is not None
        assert reaped.cpu_ns >= 500_000_000, reaped


# -- the real engine through the client ----------------------------------------------------------------------------


class Ends:
    def __init__(self, probe: Callable[[], object]) -> None:
        self.reports: list[EngineEnd] = []
        self.seen: list[object] = []
        self._probe = probe
        self._got = threading.Condition()

    def __call__(self, end: EngineEnd) -> None:
        with self._got:
            self.seen.append(self._probe())
            self.reports.append(end)
            self._got.notify_all()

    def wait(self, n: int) -> bool:
        with self._got:
            return self._got.wait_for(lambda: len(self.reports) >= n, DEADLINE)


@pytest.mark.parametrize("how", ["cancel", "retire", "crash of the engine alone", "close"])
def test_a_plugins_child_holding_a_lock_is_ended_with_the_engine(tmp_path: Path, how: str) -> None:
    """§5.2 on Windows, on the real engine: Cancel, a retire, an engine that died by itself and the close each end
    the engine through its job; the end is reported (for the close: `close()` returns) only once the job has no live
    member, so the child is gone then and the next engine never starts beside it. The lock the child held is free
    soon after, within the test's wait, not at that moment: Windows frees a dead process's locks after it ends, "the
    time it takes ... depends upon available system resources" (Microsoft's LockFileEx documentation; §5.2, "Limit
    (lock release on Windows)"; BL-0291, S0137: the close case saw the lock still held at its return 4 times)."""
    lock = tmp_path / "ledger.lock"
    watched: list[Watched] = []
    ends = Ends(lambda: bool(watched) and watched[0].gone())
    c = EngineClient(engine_command(), on_engine_ended=ends)
    try:
        out = c.call("test.child", {"lock": str(lock)})
        assert isinstance(out, dict), out
        watched.append(Watched(int(out["pid"])))  # pyright: ignore[reportArgumentType]
        pid = c.pid
        assert pid is not None
        assert not lock_free(lock)
        if how == "cancel":
            c.end("cancel")
        elif how == "retire":
            c.end("retire")
        elif how == "close":
            c.close()
            assert watched[0].gone(), "the close returned before the drain"
        else:
            Watched(pid).end()
        if how != "close":
            assert ends.wait(1)
            assert ends.seen == [True], "the end was reported while the job still held the child"
            assert ends.reports[0].pid == pid
        assert wait_until(lambda: lock_free(lock)), "the dead child's lock was never freed"
    finally:
        c.close()
        for w in watched:
            w.end()
            w.close()


def test_cancel_during_a_request_ends_the_engine_through_its_job() -> None:
    """@regression-guard (S0129: it passes on the base, which ended the engine alone with `TerminateProcess`; it must
    keep passing now that Cancel ends the engine's job)."""
    c = EngineClient(engine_command())
    try:
        c.start()
        pid = c.pid
        assert pid is not None
        engine = Watched(pid)
        out: list[object] = []
        t = threading.Thread(target=lambda: out.append(c.call("test.stall", {})), daemon=True)
        t.start()
        assert wait_until(lambda: "stalling" in c.stderr_tail())
        c.end("cancel")
        t.join(DEADLINE)
        assert out == [EngineFailure("cancelled", "the engine was ended by us (cancel)")]
        assert engine.gone(DEADLINE)
        engine.close()
    finally:
        c.close()


PARENT = Path(__file__).with_name("windows_sidecar_stand_in.py")


def test_a_sidecar_ended_hard_takes_its_engine_and_the_engines_child_with_it(tmp_path: Path) -> None:
    """§5.2: "on Windows the Job Object's kill-on-close does it when the sidecar's handle closes". A stand-in
    sidecar (the real client) starts an engine whose plugin child sleeps and never reads the engine's pipes; the
    sidecar is ended hard (no `finally`, no close). The child cannot notice by itself: only the job ends it."""
    if not ENGINE_PYTHON.exists():
        pytest.skip("no engine environment")
    sidecar = subprocess.Popen(  # noqa: S603 (this interpreter running a fixed test helper)
        [sys.executable, str(PARENT), str(ENGINE_VENV), str(tmp_path / "child.lock")],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert sidecar.stdout is not None
    line = sidecar.stdout.readline()
    engine_pid, child_pid = (int(x) for x in line.split())
    engine, child = Watched(engine_pid), Watched(child_pid)
    try:
        assert not child.gone()
        sidecar.kill()  # TerminateProcess: the sidecar's handles close, its job handle among them
        sidecar.wait(DEADLINE)
        assert engine.gone(DEADLINE), "the engine outlived its sidecar"
        assert child.gone(DEADLINE), "the plugin's child outlived the sidecar"
    finally:
        for w in (engine, child):
            w.end()
            w.close()
        sidecar.stdout.close()


# -- the engine lock (§5.1) ----------------------------------------------------------------------------------------

ENGINE_HOLDER = r"""
import sys
from ledger_engine.protocol.app_folder import parse_identity
from ledger_engine.protocol.server import hold_engine_lock
hold_engine_lock(sys.argv[1], parse_identity(sys.argv[2]))
print("held", flush=True)
sys.stdin.read()
"""
"""An engine of an earlier session, as far as the lock goes: the engine's own `hold_engine_lock`."""


@pytest.fixture
def folder(tmp_path: Path) -> AppFolder:
    made = app_folder.prepare(tmp_path / "app")
    assert isinstance(made, AppFolder), made
    return made


@contextlib.contextmanager
def engine_holding(folder: AppFolder) -> Generator[subprocess.Popen[str]]:
    if not ENGINE_PYTHON.exists():
        pytest.skip("no engine environment")
    proc = subprocess.Popen(  # noqa: S603 (the engine's interpreter running a fixed script)
        [str(ENGINE_PYTHON), "-I", "-c", ENGINE_HOLDER, str(engine_lock_path(folder.path)), folder.id_text],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    try:
        assert proc.stdout.readline() == "held\n", "the engine refused the sidecar's app-folder identity"
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(DEADLINE)
        proc.stdout.close()
        if proc.stdin is not None:
            with contextlib.suppress(OSError):
                proc.stdin.close()


def test_the_wait_lasts_while_an_earlier_sessions_engine_holds_the_lock(folder: AppFolder) -> None:
    with engine_holding(folder) as holder:
        done = threading.Event()
        threading.Thread(target=lambda: (wait_engine_lock(folder), done.set()), daemon=True).start()
        assert not done.wait(0.5), "the sidecar went on while an engine of an earlier session held the engine lock"
        assert holder.stdin is not None
        holder.stdin.close()  # the earlier engine ends
        assert done.wait(DEADLINE), "the wait did not end once the holder had ended"
    assert lock_free(engine_lock_path(folder.path)), "the sidecar must release the lock at once"


def test_with_no_lock_file_the_wait_returns_at_once_and_makes_nothing(folder: AppFolder) -> None:
    wait_engine_lock(folder)
    assert list(folder.path.iterdir()) == []


def test_the_wait_refuses_an_app_folder_made_again_at_the_same_path(folder: AppFolder) -> None:
    """S0102 on Windows: the identity is the file ID, so a folder removed and made again at once (which tunnelling
    gives its predecessor's creation time) is another folder."""
    folder.path.rmdir()
    folder.path.mkdir()
    with pytest.raises(AppFolderChangedError):
        wait_engine_lock(folder)
    assert list(folder.path.iterdir()) == []


def test_while_the_lock_is_waited_for_the_advice_shows_after_the_display_delay(folder: AppFolder) -> None:
    """§5.1: "While the sidecar waits, jobs stay queued ... with the advice of §5.2", with the real wait."""
    with engine_holding(folder) as holder:
        rig = Rig()
        rig.jobs = EngineJobs(
            rig.engine,
            wait_engine_lock=lambda: wait_engine_lock(folder),
            display_delay=DELAY,
            monotonic=rig.clock,
            log=rig.log,
        )
        rig.engine.spawned = rig.jobs.engine_spawned
        rig.engine.ended = rig.jobs.engine_ended
        try:
            boot = rig.jobs.start()
            assert boot is not None
            assert not rig.jobs.status().queue.advice
            assert rig.advice_after_the_delay()
            assert rig.engine.starts == 0, "an engine started while the engine lock was held"
            assert holder.stdin is not None
            holder.stdin.close()
            outcome(boot)  # pyright: ignore[reportArgumentType]
            assert rig.engine.starts == 1
        finally:
            rig.close()


# -- the app folder's identity -------------------------------------------------------------------------------------

ENGINE_IDENTITY = r"""
import sys
from ledger_engine.fs.port import folder_id
from ledger_engine.fs.windows import WindowsFS
with WindowsFS().open_dir(sys.argv[1]) as d:
    print(":".join(str(n) for n in folder_id(d)))
"""


def test_the_app_folders_identity_is_the_engines_the_volume_serial_and_file_id(folder: AppFolder) -> None:
    """BL-0281 (S0128 verifier Q-C): `--app-folder-id` on NTFS is the 64-bit volume serial, the 128-bit file ID and,
    as `born`, the file ID again (never the creation time), computed as the engine computes it."""
    if not ENGINE_PYTHON.exists():
        pytest.skip("no engine environment")
    engines = subprocess.run(  # noqa: S603 (the engine's interpreter running a fixed script)
        [str(ENGINE_PYTHON), "-I", "-c", ENGINE_IDENTITY, str(folder.path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert folder.id_text == engines
    assert folder.born == folder.ino, "on NTFS `born` is the file ID"


def test_a_folder_made_again_at_once_is_not_the_folder_of_the_start(folder: AppFolder) -> None:
    assert folder.same_now()
    folder.path.rmdir()
    folder.path.mkdir()
    assert not folder.same_now()
    assert AppFolder.of(folder.path).identity != folder.identity


def test_a_file_where_the_app_folder_belongs_is_refused_at_start(tmp_path: Path) -> None:
    (tmp_path / "app").write_bytes(b"")
    refused = app_folder.prepare(tmp_path / "app")
    assert isinstance(refused, AppFolderRefusal)


def test_the_engine_takes_its_lock_with_the_sidecars_identity(folder: AppFolder) -> None:
    lock = engine_lock_path(folder.path)
    c = EngineClient(engine_command(engine_lock=lock, app_folder=folder))
    try:
        c.start()
        assert wait_until(lambda: lock.exists() and not lock_free(lock)), "the engine did not take the engine lock"
    finally:
        c.close()
    # Free soon after the engine's end, not at once (§5.2, "Limit (lock release on Windows)"; BL-0291, S0137).
    assert wait_until(lambda: lock_free(lock)), "the engine's lock outlived it"


def test_a_new_engine_starts_only_after_the_previous_engines_job_is_drained(tmp_path: Path) -> None:
    """The stop rule's invariant on Windows (S0129 round 1, V3; §5.1: "A new engine is spawned ... only after the
    previous one is reaped and its process group has no live member"): at the moment the next engine is spawned,
    the retired engine's plugin child is gone. The lock it held is free soon after, not necessarily at that moment
    (§5.2, "Limit (lock release on Windows)"; BL-0291, S0137)."""
    lock = tmp_path / "ledger.lock"
    watched: list[Watched] = []
    at_spawn: list[tuple[int, bool]] = []

    def spawned(serial: int) -> None:
        if watched:
            at_spawn.append((serial, watched[0].gone()))

    c = EngineClient(engine_command(), on_engine_spawned=spawned)
    try:
        out = c.call("test.child", {"lock": str(lock)})
        assert isinstance(out, dict), out
        watched.append(Watched(int(out["pid"])))  # pyright: ignore[reportArgumentType]
        c.end("retire")
        assert c.ping() is not None  # a fresh engine
        assert at_spawn, "no second engine was spawned"
        assert all(gone for _, gone in at_spawn), f"a new engine started beside the old job: {at_spawn}"
        assert wait_until(lambda: lock_free(lock)), "the dead child's lock was never freed"
    finally:
        c.close()
        for w in watched:
            w.end()
            w.close()


def volumes() -> list[Path]:
    """The scratch drives `ci.yml` formats on the runner (FAT32, exFAT, ReFS), as the engine's tests use them."""
    import os

    return [Path(v) for v in os.environ.get("FT_TEST_VOLUMES", "").split(";") if v]


@pytest.mark.parametrize("kind", ["fat32", "exfat", "refs"])
def test_the_app_folders_identity_is_the_engines_on_each_volume_kind(kind: str) -> None:
    """V3 (S0129 round 1): the sidecar's identity (`windows_files`) and the engine's (`port.folder_id`) agree on
    FAT32, exFAT and ReFS too: the file ID as `born` on ReFS, the creation time on FAT32 and exFAT."""
    if not ENGINE_PYTHON.exists():
        pytest.skip("no engine environment")
    import uuid

    drives = [v for v in volumes() if _fs_of(v) == kind]
    if not drives:
        pytest.skip(f"no {kind} scratch drive (FT_TEST_VOLUMES)")
    made = app_folder.prepare(drives[0] / f"ft-app-{uuid.uuid4().hex}")
    assert isinstance(made, AppFolder), made
    engines = subprocess.run(  # noqa: S603 (the engine's interpreter running a fixed script)
        [str(ENGINE_PYTHON), "-I", "-c", ENGINE_IDENTITY, str(made.path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert made.id_text == engines
    assert made.born is not None
    if kind == "refs":
        assert made.born == made.ino, "on ReFS `born` is the file ID"
    else:
        assert made.born != made.ino, "on FAT `born` is the creation time"


def _fs_of(drive: Path) -> str:
    import ctypes

    name = ctypes.create_unicode_buffer(261)
    if not ctypes.windll.kernel32.GetVolumeInformationW(str(drive), None, 0, None, None, None, name, 261):
        return ""
    return name.value.lower()
