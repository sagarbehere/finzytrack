"""Start the copy's "loads the same" load in a fresh process (`ledger_engine.ledger.copy_check`;
`design/specs/engine-jobs.md` §5.1, §8.2; S0081 r2, the owner's choice of 2026-11-07, "Fresh process (Recommended)").

The child is a helper of the copy job, not a second engine (ADR-0064: one engine, one job at a time): it is started
by the job's engine while the job runs, and the job waits for it before it answers. It is started the way the sidecar
starts an engine (§5.1): the same interpreter with `-s -S -B -P` (and the engine's `pycache_prefix`), never `-I`, its
environment the engine's without `FT_*` and `PYTHON*` variables plus `PYTHONHASHSEED=0`, the engine's `sys.path`
given on its standard input (the bootstrap's own), no descriptor inherited (`close_fds`: not the engine lock, not the
protocol channel). It stays in the engine's process group (no new session), so the drain that ends the engine ends it
too (§5.2), and it holds no lock and writes nothing (its load reads the copy).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
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

    The parent reads one answer line, then waits for the child's exit, never for the end of the pipe: a process a
    plugin forked can hold the pipe open after the child has answered and ended (BL-0203). The child ends itself
    once it has answered (`copy_check.main`); one that never answers is ended with the engine's group (§5.2)."""
    line = json.dumps({"path": list(sys.path), "request": request.encode()}).encode("utf-8") + b"\n"
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
        answer = child.stdout.readline()
        child.stdout.close()
        code = child.wait()
    if code != 0:
        return CheckResult("failed", subject=f"exit {code}")
    try:
        return CheckResult.decode(cast("dict[str, object]", json.loads(answer)))
    except (ValueError, KeyError, TypeError):
        return CheckResult("failed", subject="answer")
