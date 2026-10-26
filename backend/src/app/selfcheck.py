"""The packaged build's engine self-check (S0038; ADR-0054 decisions 1 and 5, ADR-0056).

    python -I -B -m app.selfcheck

Run by the bundled interpreter, from the packaged `--smoke-test` (`shell/src-tauri/src/smoke.rs`) and at the end
of the sidecar build (`shell/sidecar/build_sidecar.py`). It prints one JSON report of facts, which those two judge:
- the packaged engine (`EngineCommand.for_packaged`, the same command the sidecar uses) started and handshaked
  (`handshake`), answered a ping (`pong`), or the failure (`error`);
- the main side's view: whether this process finds `beancount`, `beanquery` or `ledger_engine` (`main_finds`; each
  must be false), its `sys.path` and its own site-packages (`main_sites`);
- the engine side's view under the same bootstrap: its `sys.path` (`engine_path`) and whether it finds `app`
  (`engine_finds_app`; must be false).
It never raises on a broken engine: a failure is a fact in the report, so the judges can name it.
"""

from __future__ import annotations

import json
import site
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

from app.platform.adapters.engine_client import (
    EngineClient,
    EngineCommand,
    packaged_argv,
    packaged_engine_site,
)
from app.platform.engine import EngineFailure, EngineRefusal
from app.platform.engine_supervision import RestartPolicy

ENGINE_ONLY = ("beancount", "beanquery", "ledger_engine")
PING_TIMEOUT = 15.0
ENGINE_VIEW = (
    "import importlib.util as u, json; print(json.dumps({'path': sys.path, 'app': u.find_spec('app') is not None}))"
)


def _engine_view(python: Path, engine_site: Path) -> tuple[list[str] | None, bool | None, str | None]:
    """(sys.path, finds app, error) of a process started as the packaged engine is."""
    try:
        out = subprocess.run(  # noqa: S603 (the packaged bootstrap with a fixed probe)
            packaged_argv(python, engine_site, ENGINE_VIEW),
            capture_output=True,
            text=True,
            timeout=RestartPolicy().longest_start,
            check=True,
        )
        view = json.loads(out.stdout)
        return [str(p) for p in view["path"]], view["app"] is True, None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as e:
        return None, None, f"engine view: {type(e).__name__}"


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
            answer = client.ping(PING_TIMEOUT)
            if isinstance(answer, EngineFailure):
                error = f"ping: {answer.kind}: {answer.detail}"
            elif isinstance(answer, EngineRefusal):
                error = f"ping: refused: {answer.key}"
            else:
                pong = True
    finally:
        client.close()
    engine_path, engine_finds_app, view_error = _engine_view(python, engine_site)
    return {
        "site": str(engine_site),
        "handshake": handshake,
        "pong": pong,
        "error": error or view_error,
        "main_finds": {name: find_spec(name) is not None for name in ENGINE_ONLY},
        "main_path": list(sys.path),
        "main_sites": site.getsitepackages(),
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
