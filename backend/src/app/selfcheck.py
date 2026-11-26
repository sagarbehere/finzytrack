"""The packaged build's engine self-check (S0038; ADR-0054 decisions 1 and 5, ADR-0056).

    python -I -B -m app.selfcheck

Run by the bundled interpreter, from the packaged `--smoke-test` (`shell/src-tauri/src/smoke.rs`) and at the end
of the sidecar build (`shell/sidecar/build_sidecar.py`). It prints one JSON report of facts, which those two judge:
- the packaged engine (`EngineCommand.for_packaged`, the same command the sidecar uses) started and handshaked
  (`handshake`), answered a ping (`pong`), or the failure (`error`);
- the main side's view: whether this process finds `beancount`, `beanquery` or `ledger_engine` (`main_finds`; each
  must be false), its `sys.path` and its own site-packages folders (`main_sites`: `site.getsitepackages()` without
  the interpreter's prefix, which Windows lists there too although only the standard library lies in it; S0114);
- the engine side's view under the same bootstrap: its `sys.path` (`engine_path`) and whether it finds `app`
  (`engine_finds_app`; must be false). When that probe fails, `error` is `engine view: <exception name>` followed, on
  the same line, by a JSON object of what it saw (`view_failure_detail`: the exit code, the end of its stderr and
  stdout, its argv and working folder, and the variables that steer Python, the loader or an AppImage), with the home
  folder written `~` (S0153, BL-0263: the AppImage's probe failed and only the name was seen).
It never raises on a broken engine: a failure is a fact in the report, so the judges can name it.
"""

from __future__ import annotations

import json
import os
import site
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from importlib.util import find_spec
from pathlib import Path

from app.platform.adapters.engine_client import (
    EngineClient,
    EngineCommand,
    bootstrap_argv,
    packaged_engine_site,
)
from app.platform.engine import EngineFailure, EngineRefusal

ENGINE_ONLY = ("beancount", "beanquery", "ledger_engine")
# The probe below is a build-time check, not the engine: its hang detector stays (the engine has no time limit since
# S0057; a hung packaged engine is stopped by the smoke test's own runner).
VIEW_HANG_SECONDS = 30.0
# A failed view probe's report (S0153): the end of its output, and the variables that steer the interpreter, the loader
# or an AppImage's launcher.
VIEW_TAIL = 4000
VIEW_ENV_PREFIXES = ("PYTHON", "FT_", "LD_", "DYLD_", "APPIMAGE", "APPDIR", "ARGV0", "OWD")
ENGINE_VIEW = (
    "import importlib.util as u, json; print(json.dumps({'path': sys.path, 'app': u.find_spec('app') is not None}))"
)


def _same_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def main_site_packages(sites: list[str], prefixes: list[str], same: Callable[[str], str] = _same_path) -> list[str]:
    """The main side's real site-packages folders: `sites` without the interpreter's prefixes, compared as `same`
    spells a path. Windows' `site.getsitepackages()` is `[prefix, prefix\\Lib\\site-packages]`, and the engine's
    standard library (`python313.zip`, `DLLs`, `Lib`, the prefix) lies inside the prefix, so the judges' "the engine's
    path holds the main side's site-packages" refused every Windows build (S0112 verifier F1; owner, 2026-11-13: "Fix
    the check now (Recommended)", S0114). Only a prefix itself is dropped; a folder inside it stays."""
    own = {same(p) for p in prefixes}
    return [s for s in sites if same(s) not in own]


def home_folder() -> str | None:
    """This user's home folder, or None when it cannot be told."""
    home = os.path.expanduser("~")
    return None if home == "~" else home


def mask_home(text: str, home: str | None) -> str:
    """`text` with `home` written `~`; unchanged when there is no home to mask (none, empty, or a root)."""
    if not home or home.strip("/\\") == "":
        return text
    return text.replace(home, "~")


def view_failure_detail(
    argv: Sequence[str],
    exit_code: int | None,
    stdout: str,
    stderr: str,
    environ: Mapping[str, str],
    cwd: str,
    home: str | None,
) -> str:
    """One line of JSON about a failed view probe: its exit code (None: it did not exit), the last `VIEW_TAIL`
    characters of its stdout and stderr, its argv, its working folder, and the variables of `environ` that steer
    Python, the loader or an AppImage (the probe sets none of its own: it inherits them). `home` is written `~`."""

    def m(text: str) -> str:
        return mask_home(text, home)

    env = {k: m(v) for k, v in sorted(environ.items()) if k.upper().startswith(VIEW_ENV_PREFIXES)}
    return json.dumps(
        {
            "exit": exit_code,
            "stderr": m(stderr[-VIEW_TAIL:]),
            "stdout": m(stdout[-VIEW_TAIL:]),
            "argv": [m(a) for a in argv],
            "cwd": m(cwd),
            "env": env,
        }
    )


def _text(output: str | bytes | None) -> str:
    """A process's captured output as text (`TimeoutExpired` may hold bytes even when text was asked for)."""
    if output is None:
        return ""
    return output.decode(errors="replace") if isinstance(output, bytes) else output


def engine_view(python: Path, engine_site: Path) -> tuple[list[str] | None, bool | None, str | None]:
    """(sys.path, finds app, error) of a process started as the packaged engine is; the error carries
    `view_failure_detail`."""
    argv = bootstrap_argv(python, engine_site, ENGINE_VIEW)
    exit_code: int | None = None
    stdout = stderr = ""
    try:
        out = subprocess.run(  # noqa: S603 (the packaged bootstrap with a fixed probe)
            argv, capture_output=True, text=True, timeout=VIEW_HANG_SECONDS, check=False
        )
        exit_code, stdout, stderr = out.returncode, out.stdout, out.stderr
        out.check_returncode()
        view = json.loads(out.stdout, parse_float=Decimal)
        return [str(p) for p in view["path"]], view["app"] is True, None
    except subprocess.TimeoutExpired as e:
        stdout, stderr = _text(e.stdout), _text(e.stderr)
        name = type(e).__name__
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as e:
        name = type(e).__name__
    try:
        cwd = os.getcwd()
    except OSError as e:  # a working folder removed under it
        cwd = f"<{type(e).__name__}>"
    detail = view_failure_detail(argv, exit_code, stdout, stderr, os.environ, cwd, home_folder())
    return None, None, f"engine view: {name} {detail}"


def report(python: Path, engine_site: Path) -> dict[str, object]:
    """The facts about the engine at `engine_site` run by `python`, and about this process (the main side)."""
    handshake: dict[str, object] | None = None
    pong = False
    error: str | None = None
    client = EngineClient(EngineCommand.for_packaged(python, engine_site))
    try:
        info = client.start()
        if isinstance(info, EngineFailure):
            error = f"{info.kind}: {info.detail}"
        else:
            handshake = {
                "protocol": info.protocol,
                "engine_version": info.engine_version,
                "beancount_version": info.beancount_version,
            }
            answer = client.ping()
            if isinstance(answer, EngineFailure):
                error = f"ping: {answer.kind}: {answer.detail}"
            elif isinstance(answer, EngineRefusal):
                error = f"ping: refused: {answer.key}"
            else:
                pong = True
    finally:
        client.close()
    engine_path, engine_finds_app, view_error = engine_view(python, engine_site)
    return {
        "site": str(engine_site),
        "handshake": handshake,
        "pong": pong,
        "error": error or view_error,
        "main_finds": {name: find_spec(name) is not None for name in ENGINE_ONLY},
        "main_path": list(sys.path),
        "main_sites": main_site_packages(site.getsitepackages(), [sys.prefix, sys.exec_prefix]),
        "engine_finds_app": engine_finds_app,
        "engine_path": engine_path,
    }


def report_packaged() -> dict[str, object]:
    """The report for the packaged layout around this interpreter."""
    return report(Path(sys.executable), packaged_engine_site(Path(sys.prefix)))


def main() -> int:
    sys.stdout.write(json.dumps(report_packaged()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
