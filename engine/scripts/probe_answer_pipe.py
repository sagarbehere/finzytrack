"""Probe the Windows primitives the copy check's answer reader is built on (S0146, BL-0087; temporary: the slice's
landing commit removes it and its `ci.yml` step). Prints one JSON line per question; decides nothing.

Questions (the fresh-load child's answer, `ledger_engine.ledger.fresh_load`):
- **anon_peek_behind_read**: with one thread blocked in a read of an anonymous pipe (what `subprocess.PIPE` makes),
  does `PeekNamedPipe` on the same handle from another thread answer, or wait behind the read? (If it waits, "a
  blocking reader thread plus a peek once the child ended" cannot work.)
- **overlapped_***: the parent's end of a named pipe opened overlapped, the child's end its standard output; the
  parent waits for "a read finished" or "the child ended" (`WaitForMultipleObjects`); once the child ended, the read
  is cancelled, its result taken, and what `PeekNamedPipe` says is there is read. Cases: an answer line; a child that
  writes part of a line, starts a process that holds the pipe, and ends; a child that starts such a process and dies
  without writing; an answer larger than the pipe's buffer; a child that ends with nothing written and no holder.
- **symlink**: whether the runner's user may make a symbolic link (the copy tests that need one skip on error 1314).
  Each prints what was read, which wait woke, and the time it took (a hang shows as the watchdog's dump).

Usage (from `engine/`, Windows only): python scripts/probe_answer_pipe.py
"""

from __future__ import annotations

import faulthandler
import json
import os
import subprocess
import sys
import threading
import time

if sys.platform != "win32":
    raise SystemExit(1)  # Windows only

import _winapi
import msvcrt
import secrets

ERROR_IO_PENDING = 997
ERROR_OPERATION_ABORTED = 995
ERROR_BROKEN_PIPE = 109
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008


def say(**kw: object) -> None:
    print(json.dumps(kw), flush=True)  # noqa: T201 (a probe prints)


HOLDER = "import threading; threading.Event().wait()"


def child_code(write: bytes, holder: bool, die: bool) -> str:
    lines = ["import os, subprocess, sys"]
    if len(write) > 1000:  # a command line holds 32767 characters at most (run 3: error 206)
        lines.append(f"os.write(1, {write[:1]!r} * {len(write)})")
    elif write:
        lines.append(f"os.write(1, {write!r})")
    if holder:
        lines.append(
            f"h = subprocess.Popen([sys.executable, '-c', {HOLDER!r}], stdin=subprocess.DEVNULL)\n"
            "open(sys.argv[1], 'w').write(str(h.pid))"
        )
    lines.append("os._exit(9)" if die else "os._exit(0)")
    return "\n".join(lines)


def anon_peek_behind_read() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    if child.stdout is None or child.stdin is None:
        return
    fd = child.stdout.fileno()
    handle = msvcrt.get_osfhandle(fd)
    reader = threading.Thread(target=lambda: os.read(fd, 1), daemon=True)
    reader.start()
    threading.Event().wait(0.5)  # the reader is blocked in its read by then (a probe, not a test)
    answer: list[object] = []
    peeker = threading.Thread(target=lambda: answer.append(_winapi.PeekNamedPipe(handle, 0)), daemon=True)
    t0 = time.monotonic()
    peeker.start()
    peeker.join(5)
    say(q="anon_peek_behind_read", peek_answered=not peeker.is_alive(), answer=str(answer), secs=time.monotonic() - t0)
    child.stdin.close()
    child.wait()
    reader.join(5)
    peeker.join(5)
    say(q="anon_peek_behind_read_after_child_end", peek_answered=not peeker.is_alive(), answer=str(answer))


def overlapped_pipe() -> tuple[int, int]:
    name = rf"\\.\pipe\ft-probe-{os.getpid()}-{secrets.token_hex(8)}"
    server = _winapi.CreateNamedPipe(
        name,
        _winapi.PIPE_ACCESS_INBOUND | _winapi.FILE_FLAG_OVERLAPPED | _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE,
        _winapi.PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,  # byte type and read mode are 0
        1,
        0,
        1 << 16,
        _winapi.NMPWAIT_WAIT_FOREVER,
        _winapi.NULL,
    )
    client = _winapi.CreateFile(name, _winapi.GENERIC_WRITE, 0, _winapi.NULL, _winapi.OPEN_EXISTING, 0, _winapi.NULL)
    ov = _winapi.ConnectNamedPipe(server, overlapped=True)
    ov.GetOverlappedResult(True)
    return server, client


def read_until_line_or_end(server: int, process: int) -> tuple[bytes, list[str]]:
    """The product's algorithm (`fresh_load._windows_answer_line`), logging each step; then what a peek finds."""
    got = bytearray()
    log: list[str] = []
    while b"\n" not in got:
        try:
            ov, err = _winapi.ReadFile(server, 1 << 16, overlapped=True)
        except BrokenPipeError:
            log.append("read: broken pipe at once")
            break
        log.append(f"read err={err}")
        if err == ERROR_IO_PENDING:
            woke = _winapi.WaitForMultipleObjects([ov.event, process], False, _winapi.INFINITE)
            log.append(f"woke {woke}")
            if woke != _winapi.WAIT_OBJECT_0:
                ov.cancel()
        try:
            n, err = ov.GetOverlappedResult(True)
        except BrokenPipeError:
            log.append("result: broken pipe (raised)")
            break
        got += ov.getbuffer() or b""
        log.append(f"result n={n} err={err}")
        if err == ERROR_OPERATION_ABORTED:
            break
    try:
        log.append(f"peek at the end: {_winapi.PeekNamedPipe(server, 0)}")
    except OSError as e:
        log.append(f"peek at the end: {e!r}")
    return bytes(got), log


def overlapped_case(name: str, write: bytes, *, holder: bool, die: bool) -> None:
    server, client = overlapped_pipe()
    fd = msvcrt.open_osfhandle(client, 0)
    pid_file = os.path.join(os.environ.get("RUNNER_TEMP", "."), f"holder-{secrets.token_hex(4)}.pid")
    child = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", child_code(write, holder, die), pid_file], stdout=fd, close_fds=True
    )
    os.close(fd)
    process = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, child.pid)
    t0 = time.monotonic()
    got, log = read_until_line_or_end(server, process)
    secs = time.monotonic() - t0
    code = child.wait()
    say(q=f"overlapped_{name}", read=len(got), head=got[:40].decode("latin-1"), exit=code, log=log, secs=secs)
    _winapi.CloseHandle(process)
    _winapi.CloseHandle(server)
    if holder and os.path.exists(pid_file):
        with open(pid_file) as f:
            holder_pid = int(f.read())
        h = _winapi.OpenProcess(_winapi.PROCESS_ALL_ACCESS, False, holder_pid)
        _winapi.TerminateProcess(h, 1)
        _winapi.CloseHandle(h)
        os.remove(pid_file)


def symlink() -> None:
    base = os.environ.get("RUNNER_TEMP", ".")
    at = os.path.join(base, f"ft-link-{secrets.token_hex(4)}")
    try:
        os.symlink(base, at, target_is_directory=True)
        say(q="symlink", made=True)
        os.rmdir(at)
    except OSError as e:
        say(q="symlink", made=False, winerror=getattr(e, "winerror", None))


def main() -> None:
    faulthandler.dump_traceback_later(120, exit=True)
    symlink()
    try:
        anon_peek_behind_read()
    except Exception as e:  # noqa: BLE001 (a probe reports)
        say(q="anon_peek_behind_read", error=repr(e))
    cases = [
        ("answer", b'{"outcome": "checked"}\n', False, False),
        ("answer_then_holder", b'{"outcome": "checked"}\n', True, False),
        ("part_then_holder_then_end", b'{"outcome": "che', True, False),
        ("holder_then_death", b"", True, True),
        ("large_no_newline", b"x" * 300_000, False, False),
        ("nothing", b"", False, False),
    ]
    for name, write, holder, die in cases:
        try:
            overlapped_case(name, write, holder=holder, die=die)
        except Exception as e:  # noqa: BLE001 (a probe reports)
            say(q=f"overlapped_{name}", error=repr(e))


if __name__ == "__main__":
    main()
