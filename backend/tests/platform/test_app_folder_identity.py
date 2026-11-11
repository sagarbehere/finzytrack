"""The app folder's identity from start, on the sidecar's side (S0102, BL-0244; owner, 2026-11-11: "BL-0244 slice in
M1a (Recommended)"; `design/specs/engine-jobs.md` §5.1, §6.1 step 8, §11).

Written from the brief: the start records the app folder's `(st_dev, st_ino)`; the sidecar's lock wait, the bytecode
fill (and its compiler child) and the fill's discard write in the app folder only while it is that object; a folder
removed or replaced while the app runs gets nothing of ours, and every later job ends with a true next step
(`ENGINE_UNAVAILABLE {app_folder_missing}`: quit and reopen).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.platform.adapters.app_folder import AppFolder
from app.platform.adapters.engine_client import (
    BYTECODE_COMPLETE,
    EngineBytecode,
    EngineCommand,
    _Filler,  # pyright: ignore[reportPrivateUsage]
    bootstrap_argv,
    bytecode_build_dir,
)
from app.platform.adapters.engine_group import PosixGroupOps
from app.platform.adapters.engine_lock import engine_lock_path, wait_engine_lock
from app.platform.engine import AppFolderChangedError
from app.platform.engine_jobs.queue import (
    Boot,
    EngineLockFailed,
    NoEngine,
    QueuePolicy,
    QueueState,
    transition,
)
from app.platform.engine_jobs.text import unavailable_text
from app.platform.errors import EngineUnavailableDetails
from tests.platform.test_engine_client import ENGINE_VENV
from tests.platform.test_engine_queue import W1, run, submit, switch_job

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the app folder's identity: macOS until BL-0087")


def replace_folder(path: Path) -> None:
    """Another program removes the folder and puts an empty one in its place (S0100 round 2 red team `test_g`)."""
    shutil.rmtree(path)
    path.mkdir()


@pytest.fixture
def app(tmp_path: Path) -> AppFolder:
    from app.platform.adapters import app_folder

    made = app_folder.prepare(tmp_path / "app")
    assert isinstance(made, AppFolder), made
    return made


# -- the start ---------------------------------------------------------------------------------------------------------


def test_S0102_the_start_records_the_folders_identity(tmp_path: Path) -> None:
    from app.platform.adapters import app_folder

    made = app_folder.prepare(tmp_path / "app")
    assert isinstance(made, AppFolder), made
    st = os.stat(tmp_path / "app")
    assert (made.path, made.dev, made.ino) == (tmp_path / "app", st.st_dev, st.st_ino)


def test_S0102_the_engine_is_started_with_the_identity(app: AppFolder) -> None:
    command = EngineCommand(argv=("python",), env=None, engine_lock=engine_lock_path(app.path), app_folder=app)
    argv = command.spawn_argv(7)
    assert argv[argv.index("--app-folder-id") + 1] == f"{app.dev}:{app.ino}"


def test_S0102_an_engine_lock_without_the_identity_is_refused(tmp_path: Path) -> None:
    """Must not pass: an engine started to hold the lock with no identity would take it unchecked."""
    with pytest.raises(ValueError, match="app folder"):
        EngineCommand(argv=("python",), env=None, engine_lock=engine_lock_path(tmp_path))


# -- the sidecar's lock wait -------------------------------------------------------------------------------------------


def test_S0102_the_wait_in_the_folder_of_the_start_makes_its_lock(app: AppFolder) -> None:
    wait_engine_lock(app)
    assert engine_lock_path(app.path).is_file()


@pytest.mark.parametrize("change", ["removed", "replaced"])
def test_S0102_the_wait_refuses_a_removed_or_replaced_folder_and_makes_nothing(app: AppFolder, change: str) -> None:
    if change == "removed":
        shutil.rmtree(app.path)
    else:
        replace_folder(app.path)
    with pytest.raises(AppFolderChangedError):
        wait_engine_lock(app)
    assert (os.listdir(app.path) if app.path.exists() else None) == ([] if change == "replaced" else None)


# -- the queue and the scheduler: every later job says to quit and reopen ------------------------------------------


def test_S0102_a_failed_wait_for_the_app_folder_ends_the_waiting_jobs_with_their_own_reason() -> None:
    state, _ = run(QueueState(open_workspace=W1), Boot())
    state, (recover,) = submit(state, switch_job("recover_all"))
    step = transition(QueuePolicy(), state, EngineLockFailed(app_folder=True))
    assert step.effects == (NoEngine(recover, "app_folder"),)


def test_S0102_the_app_folder_reason_says_to_quit_and_reopen() -> None:
    text = unavailable_text(EngineUnavailableDetails(reason="app_folder_missing"))
    assert "Quit Finzytrack and open it again" in text


# -- the bytecode fill -------------------------------------------------------------------------------------------------


def fill_command(app: AppFolder, code: str | None = None) -> EngineBytecode:
    directory = bytecode_build_dir(app.path, Path(sys.executable), Path(sys.prefix), "9.9.9")
    command = EngineCommand.for_venv(ENGINE_VENV, test_ops=True, bytecode_dir=directory, app_folder=app)
    assert command.bytecode is not None
    if code is None:
        return command.bytecode
    python = Path(command.bytecode.fill_argv[0])
    site = Path(command.bytecode.fill_argv[-1])
    return EngineBytecode(directory, bootstrap_argv(python, site, code, ("-s", "-S", "-P")), app)


def run_fill(bytecode: EngineBytecode) -> list[dict[str, object]]:
    seen: list[dict[str, object]] = []

    def log(event: object, /, **fields: object) -> None:
        del event
        seen.append(fields)

    _Filler(bytecode, None, PosixGroupOps(), log)._run()  # pyright: ignore[reportPrivateUsage]
    return seen


def test_S0102_the_fill_marks_a_complete_build_in_the_folder_of_the_start(app: AppFolder) -> None:
    """Must pass: the fill in the folder of the start compiles there and marks it complete."""
    bytecode = fill_command(app)
    assert run_fill(bytecode) == []
    assert (bytecode.directory / BYTECODE_COMPLETE).is_file()
    assert any(bytecode.directory.rglob("*.pyc"))


def test_S0102_the_fill_in_a_replaced_folder_writes_nothing(app: AppFolder) -> None:
    replace_folder(app.path)
    seen = run_fill(fill_command(app))
    assert os.listdir(app.path) == []
    assert [f["outcome"] for f in seen] == ["fill_failed"]


def test_S0102_r2_the_fills_child_never_makes_the_app_folder_again(
    app: AppFolder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S0100 round 2 verifier F1 (`r2/test_r2_fill.py`): the user removes the app folder (and its parent) as the
    fill's compiler child starts; the child's `os.makedirs` under an absolute `pycache_prefix` made it again (0755,
    unsynced) and the fill marked it complete."""
    from app.platform.adapters import engine_client as ec

    real = subprocess.Popen

    def popen(*a: object, **k: object) -> object:
        shutil.rmtree(app.path.parent)
        return real(*a, **k)  # type: ignore[call-overload]

    monkeypatch.setattr(ec.subprocess, "Popen", popen)
    run_fill(fill_command(app))
    assert not app.path.exists(), "the fill's child made the app folder again"


SLOW_FILL = (
    "import time, compileall, os, sys; time.sleep(1.0); "
    "[compileall.compile_dir(p, quiet=2, workers={workers}) for p in list(sys.path) if os.path.isdir(p)]"
)


@pytest.mark.parametrize("workers", [0, 1])
@pytest.mark.parametrize("change", ["removed", "replaced"])
def test_S0102_rt2_the_app_folder_removed_while_the_child_runs_gets_nothing_and_no_mark(
    app: AppFolder, workers: int, change: str
) -> None:
    """S0100 round 2 red team (`rt2/fill.py`): the app folder moved away (or replaced by an empty one) while the
    compiler child runs: it is never made again, nothing is written in its replacement, and nothing is marked complete
    (in the moved folder either: it is not the app folder at its path)."""
    bytecode = fill_command(app, SLOW_FILL.format(workers=workers))
    gone = threading.Event()

    def remove() -> None:
        while not bytecode.directory.is_dir():
            threading.Event().wait(0.01)
        os.rename(app.path, app.path.with_name("away"))  # at once, whatever the child is writing in it
        if change == "replaced":
            app.path.mkdir()
        gone.set()

    remover = threading.Thread(target=remove, daemon=True)
    remover.start()
    run_fill(bytecode)
    remover.join()
    assert gone.is_set()
    assert (os.listdir(app.path) if app.path.exists() else None) == ([] if change == "replaced" else None)
    assert not list(app.path.with_name("away").rglob(BYTECODE_COMPLETE)), "a build marked complete in a moved folder"


def test_S0102_a_discard_never_removes_from_a_folder_that_is_not_the_one_of_the_start(app: AppFolder) -> None:
    """The table discards a build an engine failed to start with; in a folder another program put in place of the
    app folder, what stands at the build's path is not ours."""
    bytecode = fill_command(app)
    replace_folder(app.path)
    bytecode.directory.mkdir(parents=True)
    (bytecode.directory / "theirs").write_bytes(b"x")
    bytecode.discard()
    assert (bytecode.directory / "theirs").read_bytes() == b"x"


# -- the texts -------------------------------------------------------------------------------------------------------


def test_S0102_a_save_in_a_replaced_folder_says_missing_or_replaced() -> None:
    from app.platform.engine_jobs.save_text import not_saved_text
    from app.platform.errors import ChangeNotSavedDetails

    text = not_saved_text(ChangeNotSavedDetails("app_folder_missing", None, "/app", None, True, None))
    assert "/app is missing or was replaced" in text
    assert "Quit Finzytrack and open it again" in text


def test_S0102_recovery_in_a_replaced_folder_says_to_quit_and_reopen() -> None:
    """The recovery ops refused `engine.app_folder_missing`: Retry would meet the same folder, so the screen says to
    quit and reopen (no Retry)."""
    from app.platform.engine_jobs.recovery import Blocking, Journal, NotRecovered
    from app.platform.engine_jobs.recovery_text import blocked_screen, not_recovered_screen

    screen = not_recovered_screen(NotRecovered(None, None, "engine.app_folder_missing"))
    assert "Quit Finzytrack and open it again" in screen.message
    assert "retry" not in screen.actions
    j = Journal("/app/local/k1/journal", "s1-j7", True, "k1", "lin1", ())
    blocked = blocked_screen(Blocking(j, "failed", "engine.app_folder_missing"), "Books")
    assert "Quit Finzytrack and open it again" in blocked.message
    assert "Press Retry" not in blocked.message


def test_S0102_a_build_moved_into_another_app_folder_is_never_marked(app: AppFolder) -> None:
    """The mark needs both: the build path names the folder the fill made, and the app folder at its path is the one
    of the start. Another program makes a new app folder and moves the fill's build folder into it while the child
    runs: the build path names our folder again, in a folder that is not the app folder of the start."""
    bytecode = fill_command(app, SLOW_FILL.format(workers=1))
    moved = threading.Event()

    def move() -> None:
        while not bytecode.directory.is_dir():
            threading.Event().wait(0.01)
        away = app.path.with_name("away")
        os.rename(app.path, away)
        (app.path / "engine" / "bytecode").mkdir(parents=True)
        os.rename(away / "engine" / "bytecode" / bytecode.directory.name, bytecode.directory)
        moved.set()

    mover = threading.Thread(target=move, daemon=True)
    mover.start()
    seen = run_fill(bytecode)
    mover.join()
    assert moved.is_set()
    assert not (bytecode.directory / BYTECODE_COMPLETE).exists(), "marked complete in a folder not of the start"
    assert [f["outcome"] for f in seen] == ["fill_failed"]
