#!/usr/bin/env python3
"""Start the legacy v0.3.0 app (tag legacy-final) on a COPY of a Finzytrack data root (INV-24).

The differential-oracle harness and characterization tests use this to run the old app beside the new one.
It refuses the owner's live data folder (default ~/FinzytrackData, or $FT_LIVE_DATA), the legacy worktree
itself and this repo, refuses port 8001 (the owner's daily v0.3) and any port already in use, and never
writes bytecode into the read-only legacy worktree.

The owner's own daily v0.3 is NOT started by this script: it is the legacy worktree's run-v0.3.sh.

Usage:
    python3 scripts/oracle/launch_legacy.py --root /tmp/ft-oracle/root --port 18950 [--static-dir DIR]
    python3 scripts/oracle/launch_legacy.py --root ... --port ... --smoke   # start, probe /health, stop

Legacy launcher facts (review:B-1.9): v0.3 desktop mode resolves config/, data/ and logs/ against the
working directory, so the process runs with cwd = --root; it seeds config/ on first start when missing;
it serves a built frontend only with --static-dir; its CLI is `python -m app.main` (click options
--server-port, --static-dir, --config, --mode).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OWNER_PORT = 8001


def legacy_root() -> Path:
    return Path(os.environ.get("FT_LEGACY_ROOT", REPO.parent / "finzytrack-legacy")).expanduser().resolve()


def live_data_root() -> Path:
    return Path(os.environ.get("FT_LIVE_DATA", Path.home() / "FinzytrackData")).expanduser().resolve()


def refuse_root(root: Path) -> str | None:
    r = root.resolve()
    for forbidden, why in ((live_data_root(), "the owner's live data folder"),
                           (legacy_root(), "the legacy worktree"), (REPO.resolve(), "this repo")):
        if r == forbidden or forbidden in r.parents or r in forbidden.parents:
            return f"--root {root} is (or contains, or is inside) {why}; use a copy in a temp dir"
    return None


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def refuse_port(port: int) -> str | None:
    if port == OWNER_PORT:
        return "port 8001 belongs to the owner's daily v0.3; pick another"
    if not 1024 < port < 65536:
        return "pick an unprivileged port"
    if not port_free(port):
        return f"port {port} is already in use"
    return None


def wait_health(port: int, proc: subprocess.Popen[bytes], timeout: float = 60.0) -> dict | None:
    """The /health body once the server answers; 503 (up, not ready) is returned with "http": 503."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return None
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as err:  # 503 = up but not ready (e.g. setup not complete): report it
            if err.code == 503:
                return {"http": 503, **json.loads(err.read() or b"{}")}
            time.sleep(0.3)
        except (OSError, ValueError):
            time.sleep(0.3)
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, type=Path, help="a COPY of a data root (config/, data/)")
    ap.add_argument("--port", required=True, type=int)
    ap.add_argument("--static-dir", type=Path)
    ap.add_argument("--smoke", action="store_true", help="start, check /health, stop")
    args = ap.parse_args(argv)

    for problem in (refuse_root(args.root), refuse_port(args.port)):
        if problem:
            print(f"launch_legacy: refused: {problem}", file=sys.stderr)
            return 2
    legacy = legacy_root()
    python = legacy / "venv/bin/python"
    if not python.exists():
        print(f"launch_legacy: no legacy venv at {python}", file=sys.stderr)
        return 2
    args.root.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(legacy / "backend"), "PYTHONDONTWRITEBYTECODE": "1",
           "FINZYTRACK_MODE": "desktop"}
    cmd = [str(python), "-m", "app.main", "--server-port", str(args.port)]
    if args.static_dir:
        cmd += ["--static-dir", str(args.static_dir.resolve())]
    log = (args.root / "launch_legacy.log").open("ab")
    proc = subprocess.Popen(cmd, cwd=args.root, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    health = wait_health(args.port, proc)
    if health is None:
        print(f"launch_legacy: the legacy app did not become healthy; see {args.root / 'launch_legacy.log'}", file=sys.stderr)
        proc.terminate()
        return 1
    print(json.dumps({"pid": proc.pid, "port": args.port, "root": str(args.root), "health": health}))
    if args.smoke:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
