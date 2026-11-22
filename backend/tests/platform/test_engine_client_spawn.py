"""The engine's spawn on every OS (S0129 round 1, verifier V3: these ran on macOS and Linux only, inside
`test_engine_client.py`'s whole-module Windows skip; moved here unchanged so the Windows runner runs them too):
the flags and environment of `design/specs/engine-jobs.md` §5.1, two engines hashing alike, and a start whose
process group (on Windows its Job Object) cannot be made, which ends the engine still alone (`start_failed`).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.platform.adapters.engine_client import EngineClient, EngineCommand
from app.platform.engine import EngineFailure

# BL-0309: its children need a group kill or `ps` (tests/process_control.py).
pytestmark = pytest.mark.needs_process_control

REPO = Path(__file__).resolve().parents[3]
ENGINE_VENV = REPO / "engine" / ".venv"


def engine_command(*, test_ops: bool = True) -> EngineCommand:
    if not (ENGINE_VENV / "bin" / "python").exists() and not (ENGINE_VENV / "Scripts" / "python.exe").exists():
        pytest.fail("engine/.venv is missing: run `uv sync --locked` in engine/ (the engine tests need it)")
    return EngineCommand.for_venv(ENGINE_VENV, test_ops=test_ops)


def venv_site() -> Path:
    (site,) = (
        ENGINE_VENV.glob("lib/python*/site-packages") if os.name == "posix" else [ENGINE_VENV / "Lib/site-packages"]
    )
    return site


def interpreter(c: EngineClient) -> dict[str, Any]:
    out = c.call("test.interpreter", {})
    assert isinstance(out, dict), out
    return dict(out)


def test_the_engine_environment_drops_the_shell_token_and_python_overrides() -> None:
    environ = {
        "FT_SHELL_TOKEN": "secret",
        "FT_OTHER": "x",
        "PYTHONPATH": "/x",
        "PYTHONHOME": "/y",
        "ft_lower": "x",  # Windows names are case-insensitive (S0035 independent verifier F8, M4)
        "Python_Mixed": "x",
        "HOME": "/var/empty",
        "PATH": "/bin",
        "TZ": "Asia/Kolkata",
        "LANG": "C.UTF-8",
    }
    command = EngineCommand.for_venv(ENGINE_VENV, environ=environ)
    assert command.env == {
        "HOME": "/var/empty",
        "PATH": "/bin",
        "TZ": "Asia/Kolkata",
        "LANG": "C.UTF-8",
        "PYTHONHASHSEED": "0",  # S0057: the one PYTHON* variable, set by us (engine-jobs §5.1)
    }


def test_the_development_command_is_s_S_B_P_with_the_venvs_site_and_never_I() -> None:
    """§5.1: "flags `-s -S -B -P ...`, never `-I`"; "the development form runs the same bootstrap with the virtual
    environment's site-packages" (it needs them, since `-S` keeps a venv's packages off the path)."""
    command = EngineCommand.for_venv(ENGINE_VENV, environ={})
    python = Path(command.argv[0])
    assert python.parent.parent == ENGINE_VENV
    assert command.argv[1:5] == ("-s", "-S", "-B", "-P")
    assert command.argv[5] == "-c"
    assert command.argv[7] == str(venv_site())
    assert "-I" not in command.argv
    assert "-E" not in command.argv
    assert EngineCommand.for_venv(ENGINE_VENV, environ={}, test_ops=True).argv[-1] == "--test-ops"


def test_the_engine_environment_sets_the_hash_seed() -> None:
    """§5.1: "Environment: the parent's, minus `FT_*` and every `PYTHON*` variable, plus `PYTHONHASHSEED=0`"."""
    command = EngineCommand.for_venv(ENGINE_VENV, environ={"PYTHONHASHSEED": "random", "HOME": "/h"})
    assert command.env == {"HOME": "/h", "PYTHONHASHSEED": "0"}


def test_two_engines_hash_a_string_alike_and_run_with_the_specs_flags() -> None:
    """§5.1: a load of the same bytes gives the same result in every engine (string hashing decides set order):
    "two runs under `-s -S -B -P` [with PYTHONHASHSEED=0] the same"; `-B`: no `__pycache__` written into a user's
    folder (INV-30); `-I` would ignore the seed."""
    seen: list[dict[str, Any]] = []
    for _ in range(2):
        c = EngineClient(engine_command())
        try:
            seen.append(interpreter(c))
        finally:
            c.close()
    assert seen[0]["hash_abc"] == seen[1]["hash_abc"]
    for flags in seen:
        assert flags["no_site"] == 1
        assert flags["no_user_site"] == 1
        assert flags["safe_path"] is True
        assert flags["dont_write_bytecode"] is True
        assert flags["isolated"] == 0
        assert flags["ignore_environment"] == 0
        assert flags["hash_seed"] == "0"


def test_S0149_the_engine_started_as_the_sidecar_starts_it_runs_in_the_pinned_placeholder_mode() -> None:
    """S0149 (BL-0283; owner, 2026-11-22: "A"; ADR-0070): the engine started exactly as the sidecar starts it
    (`for_venv`: `-s -S -B -P`, the bootstrap, on Windows in its Job Object) pins `disguise_placeholder` (1) at its
    start, so the cloud filter shows it OneDrive's placeholders as plain files: the 2026-11-22 run 11 showed it got 0
    (`application_default`) without the pin. Elsewhere the mode is None."""
    c = EngineClient(engine_command())
    try:
        out = c.call("test.placeholder_mode", {})
    finally:
        c.close()
    assert isinstance(out, dict), out
    assert out["mode"] == (1 if os.name == "nt" else None), out


def test_a_start_whose_group_cannot_be_made_ends_the_engine_alone_and_fails_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S0129 round 1 (V3): on Windows a job Windows refuses (`EngineJob.around` raises `OSError`) fails the start:
    the engine, spawned but in no job and having started nothing, is ended and waited for, and the start is
    `start_failed`, never an engine left running outside a job. Simulated on every OS at the client's one seam.
    @regression-guard (the path was built in S0129's first commit, so it passes on the round's base 18f8a43e)."""
    c = EngineClient(engine_command())
    spawned: list[subprocess.Popen[bytes]] = []

    def refuse(proc: subprocess.Popen[bytes]) -> object:
        spawned.append(proc)
        raise OSError(5, "AssignProcessToJobObject: Access is denied.")

    monkeypatch.setattr(c, "_group_of", refuse)
    try:
        out = c.start()
        assert isinstance(out, EngineFailure), out
        assert out.kind == "start_failed"
        assert "job" in out.detail
        (proc,) = spawned
        assert proc.poll() is not None, "the engine outside a job was left running"
    finally:
        c.close()
