"""The engine lock, held by every engine for its life (S0053; `design/specs/engine-jobs.md` §5.1, §5.5; ADR-0064
amendment of 2026-10-28).

Written from the spec: "Every engine, after its handshake and before any ledger work, takes it with a whole-file
`flock`, waiting, and holds it for its whole life." So: after the handshake the engine holds it (no other process can
take it) until it ends; a request after the handshake is not answered while another process holds the lock (an engine
of an earlier session), and is answered once that process lets it go; an engine started without `--engine-lock` (the
packaged self-check, stand-ins) takes none. Waits are on a condition with a deadline (EP-28).
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="flock is POSIX; Windows is BL-0087 (M1b)")

DEADLINE = 15.0


def engine(*extra: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 (this interpreter running the engine under test)
        [sys.executable, "-I", "-m", "ledger_engine", *extra],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def send(proc: subprocess.Popen[bytes], op: str, rid: int) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps({"v": 2, "id": rid, "op": op, "params": {}}).encode() + b"\n")
    proc.stdin.flush()


class Replies:
    """The engine's reply lines, read on a thread so a test can wait for one with a deadline."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._lines: list[dict[str, Any]] = []
        self._got = threading.Condition()
        assert proc.stdout is not None
        out = proc.stdout
        threading.Thread(target=self._pump, args=(out,), daemon=True).start()

    def _pump(self, out: Any) -> None:
        for line in out:
            with self._got:
                self._lines.append(json.loads(line))
                self._got.notify_all()

    def wait(self, n: int, timeout: float = DEADLINE) -> bool:
        with self._got:
            return self._got.wait_for(lambda: len(self._lines) >= n, timeout)

    def __getitem__(self, i: int) -> dict[str, Any]:
        return self._lines[i]


def folder_id(lock: Path) -> str:
    """`--app-folder-id` for the app folder `lock` lives in (`<app folder>/locks/engine.lock`; S0102)."""
    st = os.stat(lock.parent.parent)
    return f"{st.st_dev}:{st.st_ino}"


def held_by_another(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    path = tmp_path / "locks" / "engine.lock"
    path.parent.mkdir()
    return path


@pytest.fixture
def procs() -> Iterator[list[subprocess.Popen[bytes]]]:
    started: list[subprocess.Popen[bytes]] = []
    yield started
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait(DEADLINE)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


def test_after_its_handshake_the_engine_holds_the_lock_until_it_ends(
    lock: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    proc = engine("--engine-lock", str(lock), "--app-folder-id", folder_id(lock))
    procs.append(proc)
    replies = Replies(proc)
    send(proc, "handshake", 1)
    send(proc, "ping", 2)
    assert replies.wait(2)
    assert replies[1]["ok"] is True
    assert held_by_another(lock), "the engine answered a request after its handshake without holding the lock"
    assert proc.stdin is not None
    proc.stdin.close()  # the engine ends at the end of its input
    proc.wait(DEADLINE)
    assert not held_by_another(lock)


def test_a_request_after_the_handshake_waits_while_an_earlier_engine_holds_the_lock(
    lock: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)  # an engine of an earlier session (this test process holds it)
    try:
        proc = engine("--engine-lock", str(lock), "--app-folder-id", folder_id(lock))
        procs.append(proc)
        replies = Replies(proc)
        send(proc, "handshake", 1)
        assert replies.wait(1), "the handshake is answered: the lock is taken after it"
        send(proc, "ping", 2)
        assert not replies.wait(2, timeout=0.5), "a request was answered while another process held the engine lock"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert replies.wait(2), "the request was not answered once the lock was free"
    assert held_by_another(lock)


def test_an_engine_started_without_the_option_takes_no_lock(lock: Path, procs: list[subprocess.Popen[bytes]]) -> None:
    """@regression-guard: the packaged self-check and stand-in engines run without the lock (it passes on the base,
    where no engine takes one; it keeps the lock tied to the option)."""
    proc = engine()
    procs.append(proc)
    replies = Replies(proc)
    send(proc, "handshake", 1)
    send(proc, "ping", 2)
    assert replies.wait(2)
    assert not held_by_another(lock)


def test_the_lock_file_is_never_deleted(lock: Path, procs: list[subprocess.Popen[bytes]]) -> None:
    proc = engine("--engine-lock", str(lock), "--app-folder-id", folder_id(lock))
    procs.append(proc)
    replies = Replies(proc)
    send(proc, "handshake", 1)
    send(proc, "ping", 2)
    assert replies.wait(2)
    assert proc.stdin is not None
    proc.stdin.close()
    proc.wait(DEADLINE)
    assert lock.is_file()


def test_an_engine_whose_lock_folder_is_missing_creates_it_and_holds_the_lock(
    tmp_path: Path, procs: list[subprocess.Popen[bytes]]
) -> None:
    """§5.1: every engine holds the lock for its life; nothing in the spec makes the engine depend on the sidecar
    having made `locks/` first (S0053 verifier F7: an engine spawned before the sidecar's wait, as piece 2's lazy
    spawn could be, answered its handshake and then died)."""
    lock = tmp_path / "locks" / "engine.lock"
    proc = engine("--engine-lock", str(lock), "--app-folder-id", folder_id(lock))
    procs.append(proc)
    replies = Replies(proc)
    send(proc, "handshake", 1)
    send(proc, "ping", 2)
    assert replies.wait(2), "the engine did not answer after its handshake"
    assert replies[1]["ok"] is True
    assert held_by_another(lock)


def test_S0100_the_engine_never_makes_the_app_folder_above_its_locks_folder(tmp_path: Path) -> None:
    """S0100 (BL-0163 (a); owner, 2026-11-10: "Sidecar makes it once (Recommended)"): the app's folder is the
    sidecar's, made and its parent synced at start; the engine makes only `locks/` inside it, never a missing app
    folder in its parent."""
    from ledger_engine.protocol.server import AppFolderChangedError, hold_engine_lock

    app = tmp_path / "app"
    with pytest.raises(AppFolderChangedError):  # S0102: a missing app folder is the start's folder gone
        hold_engine_lock(str(app / "locks" / "engine.lock"), (0, 0))
    assert not app.exists()


# -- S0102 (BL-0244; owner, 2026-11-11: "BL-0244 slice in M1a (Recommended)"): the app folder's identity from start --


def identity(p: Path) -> tuple[int, int]:
    st = os.stat(p)
    return (st.st_dev, st.st_ino)


def replace_folder(ald: Path) -> None:
    """Another program removes the app folder and puts an empty one in its place (S0100 round 2 red team `test_g`)."""
    shutil.rmtree(ald)
    ald.mkdir()


def test_S0102_the_lock_is_taken_in_the_folder_with_its_identity(tmp_path: Path) -> None:
    """@regression-guard companion: the folder as it was at start: `locks/` made in it and the lock held."""
    from ledger_engine.protocol.server import hold_engine_lock

    app = tmp_path / "app"
    app.mkdir()
    hold_engine_lock(str(app / "locks" / "engine.lock"), identity(app))
    assert (app / "locks" / "engine.lock").is_file()


@pytest.mark.parametrize("change", ["removed", "replaced"])
def test_S0102_the_lock_refuses_a_removed_or_replaced_app_folder_and_makes_nothing(tmp_path: Path, change: str) -> None:
    from ledger_engine.protocol.server import AppFolderChangedError, hold_engine_lock

    app = tmp_path / "app"
    app.mkdir()
    start = identity(app)
    if change == "removed":
        app.rmdir()
    else:
        replace_folder(app)
    with pytest.raises(AppFolderChangedError):
        hold_engine_lock(str(app / "locks" / "engine.lock"), start)
    assert (sorted(os.listdir(app)) if app.exists() else None) == ([] if change == "replaced" else None)


def test_S0102_an_engine_whose_app_folder_was_replaced_ends_after_its_handshake_taking_no_lock(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    dev, ino = identity(app)
    replace_folder(app)
    lock = app / "locks" / "engine.lock"
    proc = subprocess.run(  # noqa: S603 (this interpreter running the engine under test)
        [sys.executable, "-I", "-m", "ledger_engine", "--engine-lock", str(lock), "--app-folder-id", f"{dev}:{ino}"],
        input=b'{"v":2,"id":1,"op":"handshake","params":{}}\n{"v":2,"id":2,"op":"ping","params":{}}\n',
        capture_output=True,
        timeout=DEADLINE,
        check=False,
    )
    from ledger_engine.protocol.server import EXIT_APP_FOLDER

    assert proc.returncode == EXIT_APP_FOLDER, proc
    assert len(proc.stdout.splitlines()) == 1, "the engine answered a request after its lock was refused"
    assert b"app folder" in proc.stderr
    assert os.listdir(app) == [], "the engine wrote in a folder that is not the app folder of its start"


def test_S0102_an_engine_lock_needs_the_app_folder_identity(tmp_path: Path) -> None:
    """Must not pass: `--engine-lock` without `--app-folder-id` is a usage error, never a lock taken unchecked."""
    proc = subprocess.run(  # noqa: S603 (this interpreter running the engine under test)
        [sys.executable, "-I", "-m", "ledger_engine", "--engine-lock", str(tmp_path / "locks" / "engine.lock")],
        input=b"",
        capture_output=True,
        timeout=DEADLINE,
        check=False,
    )
    assert proc.returncode == 2, proc
    assert not (tmp_path / "locks").exists()


def test_S0102_the_engines_arguments_refuse_a_lock_without_the_identity(tmp_path: Path) -> None:
    """The same rule in this process (the spawned engine above runs the installed code): the parser refuses before
    anything is read or taken."""
    from ledger_engine.protocol.server import main

    with pytest.raises(SystemExit) as refused:
        main(["--engine-lock", str(tmp_path / "locks" / "engine.lock")])
    assert refused.value.code == 2
    assert not (tmp_path / "locks").exists()
