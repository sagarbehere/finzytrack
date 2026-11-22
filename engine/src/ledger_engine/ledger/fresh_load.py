"""Start the copy's "loads the same" load in a fresh process (`ledger_engine.ledger.copy_check`;
`design/specs/engine-jobs.md` §5.1, §8.2; S0081 r2, the owner's choice of 2026-11-07, "Fresh process (Recommended)").

The child is a helper of the copy job, not a second engine (ADR-0064: one engine, one job at a time): it is started
by the job's engine while the job runs, and the job waits for it before it answers. It is started the way the sidecar
starts an engine (§5.1): the same interpreter with `-s -S -B -P` (and the engine's `pycache_prefix`), never `-I`, its
environment the engine's without `FT_*` and `PYTHON*` variables plus `PYTHONHASHSEED=0`, the engine's `sys.path`
given on its standard input (the bootstrap's own), no descriptor inherited (`close_fds`: not the engine lock, not the
protocol channel). It stays in the engine's process group (no new session; on Windows a member of the engine's Job
Object, as every process a member starts is: the job allows no breakaway and the child asks for none, S0129, S0146),
so the drain that ends the engine ends it too (§5.2), and it holds no lock and writes nothing (its load reads the copy).

The answer's pipe and its reader have one form per OS (S0146, BL-0087) under one contract (`check`): on POSIX an OS
pipe read with `selectors`, woken by a thread that waits for the child's end; on Windows, whose `select` takes sockets
only, a named pipe whose reading end is overlapped, waited on together with the child's process handle.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import secrets
import selectors
import subprocess
import sys
import threading
from decimal import Decimal
from typing import Final, cast

from ledger_engine.ledger.copy_check import CheckRequest, CheckResult

BOOT: Final = (
    "import sys, json\n"
    "raw = json.loads(sys.stdin.buffer.readline())\n"
    "sys.path[:] = raw['path']\n"
    "from ledger_engine.ledger.copy_check import main\n"
    "main(raw)\n"
)


def command() -> list[str]:
    """The child's command line: the engine's interpreter and flags (§5.1)."""
    flags = ["-s", "-S", "-B", "-P"]
    prefix = sys.pycache_prefix
    if prefix:
        flags += ["-X", f"pycache_prefix={prefix}"]
    return [sys.executable, *flags, "-c", BOOT]


def environment() -> dict[str, str]:
    """The child's environment: the engine's, minus `FT_*` and `PYTHON*`, plus `PYTHONHASHSEED=0` (§5.1)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FT_", "PYTHON"))}
    env["PYTHONHASHSEED"] = "0"
    return env


def check(request: CheckRequest) -> CheckResult:
    """The copy's load in a fresh process, and its answer; `failed` when the child did not answer.

    The parent reads one answer line or stops reading when the child has ended, whichever comes first, never waiting
    for the end of the pipe: a process a plugin forked can hold the pipe open after the child has answered, or died
    without an answer (BL-0203; S0082 verifier V1). The child ends itself once it has answered (`copy_check.main`);
    one that never answers and never ends is ended with the engine's group (§5.2)."""
    line = json.dumps({"path": list(sys.path), "request": request.encode()}).encode("utf-8") + b"\n"
    if sys.platform == "win32":
        answer, code = _windows_check(line)
    else:
        with subprocess.Popen(  # noqa: S603 (our own interpreter and code, no shell)
            command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=environment(),
            close_fds=True,
        ) as child:
            assert child.stdin is not None  # noqa: S101 (PIPE)
            assert child.stdout is not None  # noqa: S101 (PIPE)
            try:
                child.stdin.write(line)
                child.stdin.close()
            except BrokenPipeError:  # a child that ended before it read: its exit status tells
                with contextlib.suppress(OSError):
                    child.stdin.close()
            answer = _answer_line(child)
            code = child.wait()
    if code != 0:
        return CheckResult("failed", subject=f"exit {code}")
    try:
        return CheckResult.decode(cast("dict[str, object]", json.loads(answer, parse_float=Decimal)))
    except (ValueError, KeyError, TypeError):
        return CheckResult("failed", subject="answer")


def _answer_line(child: subprocess.Popen[bytes]) -> bytes:
    """The child's answer: its standard output up to the first newline, or what it wrote before it ended. A thread
    waits for the child's end and wakes the reader through a pipe of its own; once the child has ended, only what is
    already in the answer's pipe is read (everything the child wrote is there by then), so a process a plugin forked
    that holds the pipe open never holds the job. No time limit: each wait ends on data, the pipe's end or the
    child's end."""
    assert child.stdout is not None  # noqa: S101 (PIPE)
    out = child.stdout.fileno()
    wake_r, wake_w = os.pipe()

    def waiter() -> None:
        child.wait()
        os.write(wake_w, b"x")

    thread = threading.Thread(target=waiter, name="copy-check-wait", daemon=True)
    thread.start()
    got = bytearray()
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(out, selectors.EVENT_READ)
            sel.register(wake_r, selectors.EVENT_READ)
            ended = False
            while b"\n" not in got:
                ready = {key.fd for key, _ in sel.select(0 if ended else None)}
                if out in ready:
                    chunk = os.read(out, 1 << 16)
                    if not chunk:
                        break  # the pipe's end
                    got += chunk
                elif ended:
                    break  # the child has ended and nothing more is in the pipe
                if wake_r in ready:
                    ended = True
                    sel.unregister(wake_r)
    finally:
        thread.join()
        os.close(wake_r)
        os.close(wake_w)
    return bytes(got).partition(b"\n")[0]


if sys.platform == "win32":  # `_winapi` and `msvcrt` imported where used: tests import the engine as on win32
    _PIPE_REJECT_REMOTE_CLIENTS: Final = 0x00000008  # not in `_winapi`; byte type and byte read mode are 0
    _CHUNK: Final = 1 << 16

    def _windows_check(line: bytes) -> tuple[bytes, int]:
        """`check`'s child on Windows: its standard output the client end of a named pipe whose reading end is
        overlapped (`_answer_pipe`), so the reader can wait for data and for the child's end at once."""
        import _winapi
        import msvcrt

        server, client = _answer_pipe()
        try:
            fd = msvcrt.open_osfhandle(client, 0)  # Popen makes its own inheritable copy of the handle
        except BaseException:
            _winapi.CloseHandle(client)
            _winapi.CloseHandle(server)
            raise
        try:
            with subprocess.Popen(  # noqa: S603 (our own interpreter and code, no shell)
                command(),
                stdin=subprocess.PIPE,
                stdout=fd,
                env=environment(),
                close_fds=True,
            ) as child:
                os.close(fd)  # the child's copy is its own: the pipe ends when the child and what it started close it
                fd = -1
                assert child.stdin is not None  # noqa: S101 (PIPE)
                try:
                    child.stdin.write(line)
                    child.stdin.close()
                except OSError as e:  # a child that ended before it read: its exit status tells
                    # Windows answers a write to a pipe its reader closed with EINVAL or EPIPE (as `subprocess` notes)
                    if not isinstance(e, BrokenPipeError) and e.errno != errno.EINVAL:
                        raise
                    with contextlib.suppress(OSError):
                        child.stdin.close()
                process = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, child.pid)  # unwaited: the pid is the child's
                try:
                    answer = _windows_answer_line(server, process)
                finally:
                    _winapi.CloseHandle(process)
                code = child.wait()
        finally:
            if fd != -1:
                os.close(fd)
            _winapi.CloseHandle(server)
        return answer, code

    def _answer_pipe() -> tuple[int, int]:
        """A one-way named pipe: the reading end overlapped (the parent's), the writing end not (the child's standard
        output). One instance, local clients only, a random name, and the parent's own end connected before anything
        else can be: no other process can take the pipe."""
        import _winapi

        name = rf"\\.\pipe\finzytrack-copy-check-{os.getpid()}-{secrets.token_hex(16)}"
        server = _winapi.CreateNamedPipe(
            name,
            _winapi.PIPE_ACCESS_INBOUND | _winapi.FILE_FLAG_OVERLAPPED | _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE,
            _winapi.PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS,
            1,
            0,
            _CHUNK,
            _winapi.NMPWAIT_WAIT_FOREVER,
            _winapi.NULL,
        )
        try:
            client = _winapi.CreateFile(
                name, _winapi.GENERIC_WRITE, 0, _winapi.NULL, _winapi.OPEN_EXISTING, 0, _winapi.NULL
            )
            try:
                _winapi.ConnectNamedPipe(server, overlapped=True).GetOverlappedResult(True)  # connected already
            except BaseException:
                _winapi.CloseHandle(client)
                raise
        except BaseException:
            _winapi.CloseHandle(server)
            raise
        return server, client

    def _windows_answer_line(server: int, process: int) -> bytes:
        """`_answer_line` on Windows, with the same contract: up to the first newline, or what the child wrote before
        it ended. Each read is overlapped and waited for together with the child's process handle. Once the child has
        ended, a read that waits is cancelled at once and its result taken (bytes that came before the cancel are
        kept), so only what is already in the pipe is read and a process the child started that holds the pipe open
        never holds the job. No time limit: each wait ends on data, the pipe's end or the child's end."""
        import _winapi

        got = bytearray()
        while b"\n" not in got:
            try:
                ov, err = _winapi.ReadFile(server, _CHUNK, overlapped=True)
            except BrokenPipeError:
                break  # the pipe's end: every writer closed it and it is empty
            if err == _winapi.ERROR_IO_PENDING:
                woke = _winapi.WaitForMultipleObjects([ov.event, process], False, _winapi.INFINITE)
                if woke != _winapi.WAIT_OBJECT_0:  # the child has ended (data, when both are set, wakes index 0)
                    ov.cancel()
            try:
                _, err = ov.GetOverlappedResult(True)  # after a cancel: its data, or ERROR_OPERATION_ABORTED
            except BrokenPipeError:
                break  # the pipe's end while the read waited (`_winapi` raises it for ERROR_BROKEN_PIPE)
            got += ov.getbuffer() or b""
            if err == _winapi.ERROR_OPERATION_ABORTED:
                break  # cancelled: the child has ended and nothing more is in the pipe
        return bytes(got).partition(b"\n")[0]
