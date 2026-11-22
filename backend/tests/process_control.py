"""The process tests skip where the OS refuses what they need, and only there (BL-0309, S0147).

A test that starts a child the test itself must end (a stand-in engine, a sleeper, the sidecar, the real engine
through an `EngineClient` it holds), or that runs `ps` or `os.killpg`, is marked `needs_process_control` (per module:
`pytestmark = pytest.mark.needs_process_control`). Inside the agents' OS sandbox (macOS Seatbelt) running `ps` is
refused, so such a test fails before its own cleanup and leaves its waiting children alive (S0146: about 4,100).

Once per session, and only when a marked test is collected, `probe` asks the OS by behaviour, never by an
environment variable: it starts a child in its own process group (`start_new_session`) that ends by itself after
`CHILD_LIFE` seconds, lists it with `ps`, ends its group with `os.killpg` and waits for it. Any refusal, a listing
without the child, or a child the group kill did not end is "refused"; the child ends by itself whatever is refused,
and the probe always waits for it. A refusal skips every marked test with the answer named, or, with
`FT_REQUIRE_PROCESS_TESTS` set to any non-empty value (the pre-push hook, `ci.yml`'s backend jobs), stops the run
with that answer: where these tests must run, they never skip by this rule.

Stated limits: on Windows nothing is probed (no process group or `ps` to try, and no sandbox in our setup): the answer
is "allowed". The probe runs `ps` as found on `PATH`; a test that runs `/bin/ps` relies on it being the same.
`scripts/check_invariants.py`'s static scan of a conftest does not follow `apply` (its stated limits); no enforcement
file is marked, and the pre-push hook's run proof fails a skipped enforcement test whatever skipped it.
"""

from __future__ import annotations

import contextlib
import functools
import os
import signal
import subprocess
import sys
from collections.abc import Iterable, Mapping
from typing import Protocol

import pytest

from app.platform.adapters.windows_pending import NotOnWindowsError

MARK = "needs_process_control"
REQUIRE = "FT_REQUIRE_PROCESS_TESTS"
CHILD_LIFE = 3.0  # seconds the probe's child lives if nothing ends it
WAIT = CHILD_LIFE + 5.0


class Child(Protocol):
    @property
    def pid(self) -> int: ...

    def wait(self, timeout: float | None = None) -> int: ...


class ProbeOps(Protocol):
    def spawn(self) -> Child: ...

    def listed(self, pid: int) -> bool: ...

    def kill_group(self, pid: int) -> None: ...

    def kill(self, pid: int) -> None: ...


class RealOps:
    """The OS operations the process tests need, each tried once on a child that ends by itself."""

    def spawn(self) -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603 (fixed argv: this interpreter and a constant)
            [sys.executable, "-I", "-S", "-c", f"import time; time.sleep({CHILD_LIFE})"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def listed(self, pid: int) -> bool:
        out = subprocess.run(  # noqa: S603 (fixed argv: `ps` on PATH, as the process tests run it)
            ["ps", "-o", "pid=", "-p", str(pid)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=WAIT,
            check=False,
        )
        return str(pid) in out.stdout.split()

    def kill_group(self, pid: int) -> None:
        if sys.platform == "win32":  # `probe` asks nothing there
            raise NotOnWindowsError
        os.killpg(pid, signal.SIGKILL)

    def kill(self, pid: int) -> None:
        if sys.platform == "win32":
            raise NotOnWindowsError
        os.kill(pid, signal.SIGKILL)


def probe(ops: ProbeOps | None = None) -> str | None:
    """None if the OS allows what the process tests need, else what it refused (the first refusal)."""
    if sys.platform == "win32":
        return None
    ops = RealOps() if ops is None else ops
    try:
        child = ops.spawn()
    except OSError as exc:
        return f"starting a child in its own process group: {exc}"
    refused: str | None = None
    try:
        if not ops.listed(child.pid):
            refused = "`ps` did not list the probe's child"
    except (OSError, subprocess.SubprocessError) as exc:
        refused = f"running `ps`: {exc}"
    try:
        ops.kill_group(child.pid)
    except OSError as exc:
        refused = refused or f"`os.killpg`: {exc}"
        with contextlib.suppress(OSError):
            ops.kill(child.pid)
    try:
        code = child.wait(WAIT)  # the child ends by itself within CHILD_LIFE if no kill reached it
    except subprocess.TimeoutExpired:
        return refused or "the probe's child did not end"
    if refused is None and code != -signal.SIGKILL:
        refused = f"`os.killpg` did not end the probe's child (exit {code})"
    return refused


@functools.cache
def answer() -> str | None:
    """The probe's answer, asked once per session."""
    return probe()


def apply(items: Iterable[pytest.Item], environ: Mapping[str, str]) -> None:
    """Skip the marked tests if the OS refuses what they need; with `REQUIRE` set, stop the run instead."""
    marked = [item for item in items if item.get_closest_marker(MARK) is not None]
    if not marked:
        return
    refused = answer()
    if refused is None:
        return
    if environ.get(REQUIRE):
        pytest.exit(
            f"{REQUIRE} is set, but the process tests cannot run here: the OS refused {refused} (BL-0309)",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
    reason = f"the OS refused {refused}; this test needs process groups and `ps` (BL-0309)"
    for item in marked:
        item.add_marker(pytest.mark.skip(reason=reason))


def summary() -> str | None:
    """The line the run prints once the probe was asked: its answer."""
    if answer.cache_info().currsize == 0:
        return None
    refused = answer()
    return "process tests: allowed" if refused is None else f"process tests: refused ({refused})"
