#!/usr/bin/env python3
"""Start the legacy v0.3.0 app (tag legacy-final) on a COPY of a Finzytrack data root (INV-24).

The differential-oracle harness (`harness.py`) and characterization tests use this to run the old app beside the
new one. It refuses the owner's live data folder (or $FT_LIVE_DATA), the legacy worktree itself and this repo
(`copies.refuse_dir`), refuses port 8001 (the owner's daily v0.3) and any port already in use, and never writes
bytecode into the read-only legacy worktree.

The owner's own daily v0.3 is NOT started by this script: it is the legacy worktree's run-v0.3.sh.

Usage:
    python3 scripts/oracle/launch_legacy.py --root /tmp/ft-oracle/root --port 18950 [--static-dir DIR]
    python3 scripts/oracle/launch_legacy.py --root ... --port ... --smoke   # start, probe /health, stop

Legacy launcher facts (review:B-1.9): v0.3 desktop mode resolves config/, data/ and logs/ against the
working directory, so the process runs with cwd = --root; it seeds config/ on first start when missing;
it serves a built frontend only with --static-dir; its CLI is `python -m app.main` (click options
--server-port, --static-dir, --config, --mode). /health answers 503 until setup is complete and the ledger
is readable.
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

from copies import legacy_root, refuse_dir

OWNER_PORT = 8001
# Nothing listens on the discard port: proxied requests fail at once instead of reaching the internet.
DEAD_PROXY = "http://127.0.0.1:9"
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def offline_env(env: dict[str, str]) -> dict[str, str]:
    """*env* with outbound HTTP sent to a dead proxy (httpx and requests honour these), loopback exempt. A legacy
    process on a copy must not fetch prices or call an AI endpoint on the owner's behalf."""
    out = dict(env)
    for name in PROXY_VARS:
        out[name] = out[name.lower()] = DEAD_PROXY
    out["NO_PROXY"] = out["no_proxy"] = "127.0.0.1,localhost"
    return out


def refuse_root(root: Path) -> str | None:
    problem = refuse_dir(root)
    return f"--root {problem}" if problem else None


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
            time.sleep(0.1)
        except (OSError, ValueError):
            time.sleep(0.1)
    return None


class LegacyApp:
    """The legacy app running on a copied data root; a context manager that always stops the process."""

    def __init__(self, root: Path, port: int, static_dir: Path | None = None, *, offline: bool = True) -> None:
        for problem in (refuse_root(root), refuse_port(port)):
            if problem:
                raise ValueError(f"launch_legacy: refused: {problem}")
        self.python = legacy_root() / "venv/bin/python"
        if not self.python.exists():
            raise FileNotFoundError(f"launch_legacy: no legacy venv at {self.python}")
        self.root, self.port, self.static_dir, self.offline = root, port, static_dir, offline
        self.proc: subprocess.Popen[bytes] | None = None
        self.health: dict | None = None

    def start(self) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        legacy = legacy_root()
        env = {**os.environ, "PYTHONPATH": str(legacy / "backend"), "PYTHONDONTWRITEBYTECODE": "1",
               "FINZYTRACK_MODE": "desktop"}
        if self.offline:
            env = offline_env(env)
        cmd = [str(self.python), "-m", "app.main", "--server-port", str(self.port)]
        if self.static_dir:
            cmd += ["--static-dir", str(self.static_dir.resolve())]
        with (self.root / "launch_legacy.log").open("ab") as log:
            self.proc = subprocess.Popen(cmd, cwd=self.root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True)
        self.health = wait_health(self.port, self.proc)
        if self.health is None:
            self.stop()
            raise RuntimeError(f"launch_legacy: the legacy app did not become healthy; see {self.root / 'launch_legacy.log'}")
        return self.health

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def __enter__(self) -> LegacyApp:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, type=Path, help="a COPY of a data root (config/, data/)")
    ap.add_argument("--port", required=True, type=int)
    ap.add_argument("--static-dir", type=Path)
    ap.add_argument("--smoke", action="store_true", help="start, check /health, stop")
    args = ap.parse_args(argv)
    try:
        app = LegacyApp(args.root, args.port, args.static_dir)
        health = app.start()
    except (ValueError, FileNotFoundError) as err:
        print(err, file=sys.stderr)
        return 2
    except RuntimeError as err:
        print(err, file=sys.stderr)
        return 1
    print(json.dumps({"pid": app.proc.pid if app.proc else None, "port": args.port, "root": str(args.root), "health": health}))
    if args.smoke:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
