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
  (`engine_finds_app`; must be false). The probe runs with the engine's environment (`engine_env`: no `PYTHON*` or
  `FT_*` variable of the parent): the AppImage's launcher sets `PYTHONHOME` and `PYTHONPATH` to its mount, and a probe
  that inherited them could not start its interpreter (S0153, BL-0263). When that probe fails, `error` is
  `engine view: <exception name>` followed, on the same line, by a JSON object of what it saw
  (`view_failure_detail`: the exit code, the end of its stderr and stdout, its argv and working folder, and the
  variables of its environment that steer Python, the loader or an AppImage, a value whose name looks secret hidden),
  with the home folder written `~`.
It never raises on a broken engine: a failure is a fact in the report, so the judges can name it.
"""

from __future__ import annotations

import json
import os
import re
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
    engine_env,
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
# A listed variable whose name holds one of these (any case) shows only its name (S0153 round 1, V1-4).
VIEW_SECRET_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD")
VIEW_HIDDEN = "<hidden>"
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


def _is_root(path: str) -> bool:
    return path.strip("/\\") == "" or (len(path.rstrip("/\\")) == 2 and path[1] == ":")


def mask_home(
    text: str,
    home: str | None,
    *,
    windows: bool = os.name == "nt",
    real: Callable[[str], str] = os.path.realpath,
) -> str:
    """`text` with each spelling of `home` written `~`: as given and as `real` resolves it (a home reached through a
    link), and on Windows in any case and with either slash. Unchanged when there is no home to mask (none, empty, or
    a root). Limits (S0153 round 1, V1-1): a Windows 8.3 short name of the home and macOS's firmlinked spelling of it
    (under the system's data volume) are not masked; a home whose name is the start of a longer folder name is masked
    there too (a home `al` inside a path to `al2`), which hides more, never less; a home with a non-ASCII letter in
    output the probe wrote in another encoding than UTF-8 (Windows' ANSI code page on a pipe) is decoded with a
    replacement letter first, so it is not masked (S0153 round 2, V2-1)."""
    if not home or _is_root(home):
        return text
    spellings = {s for s in (home, real(home)) if s and not _is_root(s)}
    if windows:
        spellings = {v for s in spellings for v in (s.replace("/", "\\"), s.replace("\\", "/"))}
    pattern = "|".join(re.escape(s) for s in sorted(spellings, key=len, reverse=True))
    return re.sub(pattern, "~", text, flags=re.IGNORECASE if windows else 0)


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
    characters of its stdout and stderr, its argv, its working folder, and the variables of `environ` (the
    environment the probe ran under) that steer Python, the loader or an AppImage; a listed variable whose name holds
    a `VIEW_SECRET_WORDS` word shows `VIEW_HIDDEN`. `home` is written `~`."""

    def m(text: str) -> str:
        return mask_home(text, home)

    def shown(name: str, value: str) -> str:
        return VIEW_HIDDEN if any(w in name.upper() for w in VIEW_SECRET_WORDS) else m(value)

    env = {k: shown(k, v) for k, v in sorted(environ.items()) if k.upper().startswith(VIEW_ENV_PREFIXES)}
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
    """A process's captured output as text: bytes are decoded here, any that are not UTF-8 replaced, so output that
    is not UTF-8 never hides the exit code or the output itself (S0153 round 1, V1-2); Windows' `\r\n` line ends read
    as `\n`, so the report reads the same on every OS (the M1b exit run)."""
    if output is None:
        return ""
    text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
    return text.replace("\r\n", "\n")


def engine_view(python: Path, engine_site: Path) -> tuple[list[str] | None, bool | None, str | None]:
    """(sys.path, finds app, error) of a process started as the packaged engine is: its flags, bootstrap and
    environment (`engine_env`, one rule with the engine's start); the error carries `view_failure_detail`."""
    argv = bootstrap_argv(python, engine_site, ENGINE_VIEW)
    env = engine_env(os.environ)
    exit_code: int | None = None
    stdout = stderr = ""
    try:
        out = subprocess.run(  # noqa: S603 (the packaged bootstrap with a fixed probe)
            argv, capture_output=True, env=env, timeout=VIEW_HANG_SECONDS, check=False
        )
        exit_code, stdout, stderr = out.returncode, _text(out.stdout), _text(out.stderr)
        out.check_returncode()
        view = json.loads(stdout, parse_float=Decimal)
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
    detail = view_failure_detail(argv, exit_code, stdout, stderr, env, cwd, home_folder())
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
