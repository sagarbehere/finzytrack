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
    """The copy's load in a fresh process, and its answer; `failed` when the child did not answer."""
    line = json.dumps({"path": list(sys.path), "request": request.encode()}).encode("utf-8") + b"\n"
    done = subprocess.run(  # noqa: S603 (our own interpreter and code, no shell)
        command(),
        input=line,
        stdout=subprocess.PIPE,
        env=environment(),
        close_fds=True,
        check=False,
    )
    if done.returncode != 0:
        return CheckResult("failed", subject=f"exit {done.returncode}")
    try:
        return CheckResult.decode(cast("dict[str, object]", json.loads(done.stdout)))
    except (ValueError, KeyError, TypeError):
        return CheckResult("failed", subject="answer")
