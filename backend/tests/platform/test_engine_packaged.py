"""The engine in the packaged layout (ADR-0054 decisions 1 and 5, ADR-0056; S0038 brief).

The bundle ships one interpreter with two separate site-packages: the interpreter's own holds the main sidecar, and
`sidecar/engine/site-packages` holds the engine's locked closure. The packaged command runs that interpreter with
`-S`, so its own site-packages (the main sidecar's) never reach the engine, and adds only the engine's site.

Here the backend's interpreter stands for the bundled one and the engine venv's site-packages for the engine site:
the backend's interpreter has `app` and no Beancount, the engine site has Beancount and no `app`, exactly the two
sides of the bundle. The packaged build itself is proven by the packaged `--smoke-test` (`shell/src-tauri/src/
smoke.rs`), on three OSes only in the owner's manual CI run (ADR-0061).
"""

from __future__ import annotations

import json
import os
import site
import subprocess
import sys
from pathlib import Path

import pytest

from app.platform.adapters.engine_client import (
    CREATE_NO_WINDOW,
    PATH_SETUP,
    EngineClient,
    EngineCommand,
    bootstrap_argv,
    creation_flags,
    packaged_engine_site,
)
from app.platform.engine import EngineFailure, EngineInfo, Pong

REPO = Path(__file__).resolve().parents[3]
ENGINE_VENV = REPO / "engine" / ".venv"
DEADLINE = 20.0


def engine_site() -> Path:
    """The engine venv's site-packages: the dev stand-in for the bundle's engine site."""
    found = [Path(p) for p in (ENGINE_VENV / "lib").glob("python3.*/site-packages")] + [
        ENGINE_VENV / "Lib" / "site-packages"
    ]
    found = [p for p in found if p.is_dir()]
    if len(found) != 1:
        pytest.fail("engine/.venv is missing: run `uv sync --locked` in engine/ (the engine tests need it)")
    return found[0]


def engine_view(site_dir: Path) -> dict[str, object]:
    """What a module sees under the packaged bootstrap: its sys.path and whether `app` and Beancount are found."""
    code = (
        "import importlib.util as u, json; "
        "print(json.dumps({'path': sys.path, 'app': u.find_spec('app') is not None, "
        "'beancount': u.find_spec('beancount') is not None, "
        "'ledger_engine': u.find_spec('ledger_engine') is not None}))"
    )
    out = subprocess.run(  # noqa: S603 (fixed argv: this interpreter under the packaged bootstrap)
        bootstrap_argv(Path(sys.executable), site_dir, code),
        capture_output=True,
        text=True,
        timeout=DEADLINE,
        check=True,
    )
    view: dict[str, object] = json.loads(out.stdout)
    return view


def test_the_packaged_command_is_one_interpreter_without_its_own_site_and_with_the_engine_site() -> None:
    python, site_dir = Path("/pkg/sidecar/python/bin/python3.13"), Path("/pkg/sidecar/engine/site-packages")
    cmd = EngineCommand.for_packaged(
        python, site_dir, environ={"PATH": "/bin", "FT_SHELL_TOKEN": "t", "PYTHONPATH": "x"}
    )
    # engine-jobs §5.1 (S0057): `-s -S -B -P`, never `-I` (it would ignore PYTHONHASHSEED)
    assert cmd.argv[:6] == (str(python), "-s", "-S", "-B", "-P", "-c")
    assert cmd.argv[6].startswith(PATH_SETUP)
    assert "ledger_engine" in cmd.argv[6]
    assert cmd.argv[7:] == (str(site_dir),)
    assert cmd.env == {"PATH": "/bin", "PYTHONHASHSEED": "0"}
    assert EngineCommand.for_packaged(python, site_dir, test_ops=True).argv[-1] == "--test-ops"


def test_the_engine_site_sits_beside_the_interpreter_in_the_sidecar_folder() -> None:
    prefix = Path("/Applications/Finzytrack.app/Contents/Resources/sidecar/python")
    assert packaged_engine_site(prefix) == prefix.parent / "engine" / "site-packages"


def test_a_real_engine_answers_through_the_packaged_bootstrap() -> None:
    client = EngineClient(EngineCommand.for_packaged(Path(sys.executable), engine_site()))
    try:
        info = client.start()
        assert isinstance(info, EngineInfo), info
        assert info.beancount_version == "3.2.0"
        assert client.ping() == Pong()
    finally:
        client.close()


def test_the_engine_never_sees_the_interpreters_own_site_packages() -> None:
    """ADR-0054 licence separation, engine side: the main sidecar's packages are not on the engine's path."""
    view = engine_view(engine_site())
    own = {str(Path(p).resolve()) for p in site.getsitepackages()}
    path = view["path"]
    assert isinstance(path, list)
    assert not own & {str(Path(str(p)).resolve()) for p in path}, path  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    assert view["app"] is False
    assert view["beancount"] is True
    assert view["ledger_engine"] is True


def test_the_main_side_never_sees_the_engine_site() -> None:
    """ADR-0054 licence separation, main side: this interpreter, run as the bundle runs it, finds no engine code."""
    code = (
        "import importlib.util as u; print([n for n in ('beancount', 'beanquery', 'ledger_engine') if u.find_spec(n)])"
    )
    out = subprocess.run(  # noqa: S603 (fixed argv: this interpreter)
        [sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=DEADLINE, check=True
    )
    assert out.stdout.strip() == "[]"


def test_a_missing_engine_site_fails_the_start_instead_of_finding_the_engine_elsewhere(tmp_path: Path) -> None:
    client = EngineClient(EngineCommand.for_packaged(Path(sys.executable), tmp_path / "missing"))
    try:
        out = client.start()
        assert isinstance(out, EngineFailure)
        assert out.kind == "start_failed"
    finally:
        client.close()


def test_the_engine_stderr_reaches_the_callback() -> None:
    lines: list[str] = []
    client = EngineClient(
        EngineCommand.for_packaged(Path(sys.executable), engine_site(), test_ops=True), on_stderr=lines.append
    )
    try:
        assert isinstance(client.start(), EngineInfo)
        client.call("test.noise", {})
        client.call("test.fail", {})
    finally:
        client.close()
    assert "noise via print" in lines
    assert any("boom (test.fail)" in line for line in lines), lines


def test_a_failing_stderr_callback_does_not_stop_the_engine_or_the_tail() -> None:
    def broken(_: str) -> None:
        raise OSError

    client = EngineClient(
        EngineCommand.for_packaged(Path(sys.executable), engine_site(), test_ops=True), on_stderr=broken
    )
    try:
        assert isinstance(client.start(), EngineInfo)
        client.call("test.noise", {})
        assert client.ping() == Pong()
        client.call("test.noise", {})
        assert client.stderr_tail().count("noise via print") == 2
    finally:
        client.close()


def test_on_windows_the_engine_gets_no_console_window() -> None:
    assert creation_flags("nt") == CREATE_NO_WINDOW == 0x08000000
    assert creation_flags("posix") == 0


@pytest.mark.skipif(os.name != "posix", reason="the venv layout of this check is POSIX")
def test_the_venv_command_is_the_packaged_bootstrap_with_the_venvs_site() -> None:
    """engine-jobs §5.1 (S0057): "the development form uses the same bootstrap with the virtual environment's
    site-packages, since `-S` keeps a venv's packages off the path"."""
    cmd = EngineCommand.for_venv(ENGINE_VENV, environ={})
    packaged = EngineCommand.for_packaged(ENGINE_VENV / "bin" / "python", engine_site(), environ={})
    assert cmd.argv == packaged.argv
    assert cmd.argv[1:5] == ("-s", "-S", "-B", "-P")
