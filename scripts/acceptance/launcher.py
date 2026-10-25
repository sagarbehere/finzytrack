#!/usr/bin/env python3
"""The acceptance-suite harness launcher (S0030; review:27 §1(d); INV-24, BL-0024).

One run per Playwright worker (`frontend/e2e/harness/`), started with the backend venv's Python:

    backend/.venv/bin/python scripts/acceptance/launcher.py [--tz America/Los_Angeles | --tz Asia/Kolkata] [--keep]

It creates a fresh temp root, generates a fresh demo workspace into it (`backend/scripts/gen_demo.py`, anchored at
the frozen local date), starts an OpenAI-compatible LLM stub and the main sidecar (`python -m app.main`) on
OS-assigned loopback ports with the sidecar's folders inside the root and its clock frozen, then prints one line,
`FT_HARNESS_READY {json}`, and waits. When its stdin closes (the worker finished or died) or it gets SIGTERM, it
stops the processes it started and removes the root (`--keep` leaves the root for debugging).

The checks remove whole classes of risk (EP-30) rather than modelling paths:
- Data dirs: no folder is taken from outside. Every folder given to the sidecar was made by this run with `mkdir`
  as a direct child of its own `mkdtemp` root, is not a symlink and still has the identity it was made with; the
  root is never created in, or around, the repo, the live data folder or the legacy worktree (resolved,
  case-insensitively: `copies.refuse_dir`).
- Ports: the run never picks a port. The sidecar gets `--port 0`; only the port in its own child's handshake is
  used, and the owner's port (8001), privileged ports and nonsense are refused.
- Processes: only the `Popen` handles this run created are ever signalled.
Exit status: 0 after a clean stop, 2 when refused or when a step failed.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
sys.path.insert(0, str(REPO / "scripts" / "oracle"))

import copies  # noqa: E402  (the shared copy guards, stdlib only)

OWNER_PORT = 8001  # the owner's running app (.claude/hooks/guards.py OWNER_PORT; a test keeps them equal)
LOOPBACK = "127.0.0.1"
READY = "FT_HARNESS_READY "
ROOT_PREFIX = "ft-acceptance-"
DIR_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
HANDSHAKE = re.compile(r"FT_READY (\{[^\n]*\})\n?")

# Frozen local wall times per zone: each is on a month boundary where the local date differs from the UTC date,
# so any "UTC today" shows (INV-07). West of UTC is the default for every run; Kolkata is the after-midnight case.
PROFILES: dict[str, tuple[int, int, int, int, int]] = {
    "America/Los_Angeles": (2026, 3, 31, 21, 30),  # UTC is already 2026-04-01 04:30
    "Asia/Kolkata": (2026, 4, 1, 0, 30),  # UTC is still 2026-03-31 19:00
}
DEFAULT_TZ = "America/Los_Angeles"
STUB_MODEL = "stub-model"


class Refused(Exception):
    """A check refused, or a step of the run failed; nothing further is started."""


def profile(tz: str) -> tuple[datetime, date]:
    """The frozen instant and its local date for *tz*; a zone with no profile (UTC included) is refused."""
    if tz not in PROFILES:
        raise Refused(f"no frozen-clock profile for time zone {tz!r}; use one of {sorted(PROFILES)}")
    at = datetime(*PROFILES[tz], tzinfo=ZoneInfo(tz))
    return at, at.date()


class TempRoot:
    """This run's own folder: made with `mkdtemp`, never in or around a forbidden folder."""

    def __init__(self, parent: Path | None = None) -> None:
        base = Path(parent) if parent is not None else Path(tempfile.gettempdir())
        problem = copies.refuse_dir(base)
        if problem:
            raise Refused(f"temp folder: {problem}")
        raw = tempfile.mkdtemp(prefix=ROOT_PREFIX, dir=base)
        path = Path(raw).resolve()
        problem = copies.refuse_dir(path)
        if problem:
            os.rmdir(raw)
            raise Refused(f"temp root: {problem}")
        self.path = path
        self._id = self._identity(path)
        self._made: dict[str, tuple[int, int]] = {}

    @staticmethod
    def _identity(p: Path) -> tuple[int, int]:
        st = os.lstat(p)
        return st.st_dev, st.st_ino

    def _check_root(self) -> None:
        try:
            st = os.lstat(self.path)
        except OSError as e:
            raise Refused(f"the temp root {self.path} is gone: {e}") from e
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or (st.st_dev, st.st_ino) != self._id:
            raise Refused(f"the temp root {self.path} was replaced")

    def make_dir(self, name: str) -> Path:
        """A fresh folder *name* directly inside the root; *name* is one plain lower-case word."""
        if not DIR_NAME.fullmatch(name):
            raise Refused(f"{name!r} is not a plain folder name")
        self._check_root()
        p = self.path / name
        try:
            os.mkdir(p, 0o700)
        except FileExistsError as e:
            raise Refused(f"{p} already exists; every folder the harness hands out is fresh") from e
        self._made[str(p)] = self._identity(p)
        return p

    def check_own(self, p: Path) -> Path:
        """*p* itself if this run made it with make_dir and it is unchanged; else Refused."""
        self._check_root()
        made = self._made.get(str(p))
        if made is None:
            raise Refused(f"{p} was not made by this harness run")
        try:
            st = os.lstat(p)
        except OSError as e:
            raise Refused(f"{p}: {e}") from e
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or (st.st_dev, st.st_ino) != made:
            raise Refused(f"{p} was replaced after the harness made it")
        # No path or containment check beyond this: only make_dir records a path (always `root / name`), and the
        # root was checked when created and still has its identity (EP-30; S0030 self-mutation showed such checks
        # could never fire).
        return p

    def remove(self) -> None:
        self._check_root()
        shutil.rmtree(self.path)


@dataclass(frozen=True)
class Dirs:
    home: Path
    app_local_data: Path
    app_cache: Path
    workspace: Path

    @classmethod
    def make(cls, root: TempRoot) -> Dirs:
        return cls(root.make_dir("home"), root.make_dir("app-local-data"), root.make_dir("app-cache"),
                   root.make_dir("workspace"))


def check_port(port: int) -> int:
    if type(port) is not int:
        raise Refused(f"{port!r} is not a port number")
    if port == OWNER_PORT:
        raise Refused(f"port {port} is the owner's running app; tests never use it (INV-24)")
    if not 1024 < port <= 65535:
        raise Refused(f"port {port} is not an unprivileged port")
    return port


def parse_handshake(line: str) -> tuple[int, str]:
    """(port, version) from the sidecar's `FT_READY {"port":…,"version":…}` line (ARCHITECTURE.md §5)."""
    m = HANDSHAKE.fullmatch(line)
    if m is None:
        raise Refused(f"not a handshake line: {line[:120]!r}")
    try:
        body = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise Refused(f"handshake is not JSON: {e}") from e
    if not isinstance(body, dict) or set(body) != {"port", "version"}:
        raise Refused(f"handshake must hold exactly port and version: {m.group(1)[:120]}")
    if not isinstance(body["version"], str) or not body["version"]:
        raise Refused("handshake version must be a non-empty string")
    return check_port(body["port"]), body["version"]


class Children:
    """The processes this run started. Only these handles are ever signalled."""

    def __init__(self) -> None:
        self._procs: list[subprocess.Popen[Any]] = []

    def spawn(self, argv: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
        proc = subprocess.Popen(argv, **kwargs)  # noqa: S603 (argv built by this module)
        self._procs.append(proc)
        return proc

    def stop(self, proc: subprocess.Popen[Any], grace: float = 10.0) -> None:
        if not isinstance(proc, subprocess.Popen) or not any(p is proc for p in self._procs):
            raise Refused(f"{proc!r} was not started by this harness run; it is never signalled")
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=grace)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass  # a pump thread may still hold it; the process is gone either way

    def stop_all(self) -> None:
        for proc in reversed(self._procs):
            self.stop(proc)


def backend_python() -> Path:
    return BACKEND / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def require_backend_python() -> None:
    if not backend_python().is_file():
        raise Refused(f"{backend_python()} is missing; run `uv sync --locked` in backend/ first")


def backend_argv(root: TempRoot, dirs: Dirs, frozen: datetime) -> list[str]:
    """The sidecar's command: port 0, and only folders this run made (ARCHITECTURE.md §5 args)."""
    return [str(backend_python()), "-I", "-B", "-m", "app.main",
            "--app-local-data", str(root.check_own(dirs.app_local_data)),
            "--app-cache", str(root.check_own(dirs.app_cache)),
            "--workspace", str(root.check_own(dirs.workspace)),
            "--port", "0", "--frozen-clock", frozen.isoformat()]


def child_env(root: TempRoot, dirs: Dirs, tz: str) -> dict[str, str]:
    """A small environment: nothing inherited but PATH, and HOME inside the root, so `~` never reaches the owner's."""
    env = {"PATH": os.environ.get("PATH", os.defpath), "TZ": tz, "HOME": str(root.check_own(dirs.home)),
           "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return env


def generate_demo(kids: Children, root: TempRoot, dirs: Dirs, today: date, env: dict[str, str]) -> None:
    """A fresh demo in the workspace folder. The generator writes into a staging folder (it renames its output into
    place); the files are then copied into the folder this run made, so the sidecar only ever sees that folder."""
    stage = root.make_dir("demo-build")
    out = stage / "out"
    proc = kids.spawn([str(backend_python()), "-I", "-B", str(BACKEND / "scripts/gen_demo.py"), "--size", "demo",
                       "--out", str(out), "--anchor", today.isoformat()],
                      cwd=BACKEND, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.PIPE, text=True)
    try:
        _, err = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired as e:
        kids.stop(proc)
        raise Refused("the demo generator did not finish in 120 s") from e
    if proc.returncode != 0:
        raise Refused(f"the demo generator failed ({proc.returncode}): {err.strip()[:500]}")
    workspace = root.check_own(dirs.workspace)
    for src in sorted(out.rglob("*")):
        st = os.lstat(src)
        dest = workspace / src.relative_to(out)
        if stat.S_ISDIR(st.st_mode):
            dest.mkdir()
        elif stat.S_ISREG(st.st_mode):
            shutil.copyfile(src, dest)
        else:
            raise Refused(f"the demo output holds {src.relative_to(out)}, which is not a plain file or folder")
    shutil.rmtree(stage)


def read_handshake(proc: subprocess.Popen[str], timeout: float) -> tuple[int, str]:
    """The handshake from *proc*'s first stdout line; later output is drained so the child never blocks."""
    lines: queue.Queue[str] = queue.Queue()

    def pump() -> None:
        assert proc.stdout is not None
        first = True
        for line in proc.stdout:
            if first:
                lines.put(line)
                first = False
        if first:
            lines.put("")

    threading.Thread(target=pump, daemon=True).start()
    try:
        line = lines.get(timeout=timeout)
    except queue.Empty as e:
        raise Refused(f"no handshake from the sidecar within {timeout:.0f} s") from e
    if not line:
        raise Refused(f"the sidecar exited before its handshake (code {proc.wait()})")
    port = parse_handshake(line)
    if proc.poll() is not None:
        raise Refused("the sidecar exited right after its handshake")
    return port


def probe(port: int) -> None:
    """The port answers HTTP (any status) while our child runs."""
    conn = http.client.HTTPConnection(LOOPBACK, check_port(port), timeout=10)
    try:
        conn.request("GET", "/")
        conn.getresponse().read()
    except OSError as e:
        raise Refused(f"nothing answers on {LOOPBACK}:{port}: {e}") from e
    finally:
        conn.close()


# --- The LLM stub: OpenAI-compatible, scripted, loopback only (ARCHITECTURE.md §2.3 `Llm` test double). ---

def _valid_rules(body: object) -> list[dict[str, Any]] | None:
    if not isinstance(body, dict) or set(body) != {"rules"} or not isinstance(body["rules"], list):
        return None
    rules: list[dict[str, Any]] = body["rules"]
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("match"), str):
            return None
        kinds = set(rule) - {"match"}
        if kinds == {"reply"} and isinstance(rule["reply"], str):
            continue
        if kinds == {"tool_calls"} and isinstance(rule["tool_calls"], list) and rule["tool_calls"] \
                and all(isinstance(c, dict) for c in rule["tool_calls"]):
            continue
        return None
    return rules


def _last_user_text(messages: object) -> str:
    if not isinstance(messages, list):
        return ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


class _StubServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, created: int) -> None:
        super().__init__((LOOPBACK, 0), _StubHandler)
        self.created = created
        self.lock = threading.Lock()
        self.rules: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.unscripted: list[dict[str, Any]] = []


class _StubHandler(BaseHTTPRequestHandler):
    server: _StubServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (the base class's name)
        pass

    def _send(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str, kind: str) -> None:
        self._send(status, {"error": {"message": message, "type": kind}})

    def _body(self) -> object:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"null")
        except json.JSONDecodeError:
            return None

    def do_GET(self) -> None:  # noqa: N802 (http.server's name)
        s = self.server
        if self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": STUB_MODEL, "object": "model", "created": s.created,
                                                         "owned_by": "finzytrack-harness"}]})
        elif self.path == "/__harness/calls":
            with s.lock:
                self._send(200, {"calls": list(s.calls), "unscripted": list(s.unscripted)})
        else:
            self._error(404, f"{self.path} is not served by the LLM stub", "not_found")

    def do_PUT(self) -> None:  # noqa: N802
        s = self.server
        if self.path != "/__harness/script":
            self._error(404, f"{self.path} is not served by the LLM stub", "not_found")
            return
        rules = _valid_rules(self._body())
        if rules is None:
            self._error(400, 'a script is {"rules": [{"match": str, "reply": str} | {"match": str, '
                             '"tool_calls": [ ... ]}]}', "harness_bad_script")
            return
        with s.lock:
            s.rules, s.calls, s.unscripted = rules, [], []
        self._send(200, {"rules": len(rules)})

    def do_POST(self) -> None:  # noqa: N802
        s = self.server
        if self.path != "/v1/chat/completions":
            self._error(404, f"{self.path} is not served by the LLM stub", "not_found")
            return
        body = self._body()
        req: dict[str, Any] = body if isinstance(body, dict) else {}
        text = _last_user_text(req.get("messages"))
        call = {"model": req.get("model"), "user": text}
        with s.lock:
            s.calls.append(call)
            rule = next((r for r in s.rules if r["match"] in text), None)
            if rule is None:
                s.unscripted.append(call)
        if req.get("stream"):
            self._error(400, "streaming replies are not scripted yet (BL-0072)", "harness_unsupported")
            return
        if rule is None:
            self._error(500, f"no scripted reply for {text[:200]!r}", "harness_unscripted")
            return
        if "tool_calls" in rule:
            message: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": rule["tool_calls"]}
            finish = "tool_calls"
        else:
            message, finish = {"role": "assistant", "content": rule["reply"]}, "stop"
        self._send(200, {"id": f"stub-{len(s.calls)}", "object": "chat.completion", "created": s.created,
                         "model": req.get("model") or STUB_MODEL,
                         "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                         "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})


class LlmStub:
    """An OpenAI-compatible chat server with scripted replies. A call with no matching rule fails (500) and is
    recorded, so a test that reaches the model unscripted fails loudly. Timestamps come from the frozen clock."""

    def __init__(self, frozen: datetime) -> None:
        self._server = _StubServer(int(frozen.timestamp()))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def host(self) -> str:
        return str(self._server.server_address[0])

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join(timeout=10)
        self._server.server_close()


def _stop_on_signal(signum: int, frame: FrameType | None) -> None:
    raise SystemExit(0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tz", default=DEFAULT_TZ, help=f"one of {sorted(PROFILES)}")
    ap.add_argument("--keep", action="store_true", help="leave the temp root in place (debugging)")
    args = ap.parse_args(argv)
    kids, stub, root = Children(), None, None
    for sig in (signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])):
        signal.signal(sig, _stop_on_signal)
    try:
        frozen, today = profile(args.tz)
        require_backend_python()
        root = TempRoot()
        dirs = Dirs.make(root)
        env = child_env(root, dirs, args.tz)
        generate_demo(kids, root, dirs, today, env)
        stub = LlmStub(frozen)
        stub.start()
        check_port(stub.port)
        with (root.path / "sidecar.log").open("wb") as log:
            proc = kids.spawn(backend_argv(root, dirs, frozen), cwd=root.path, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=log, text=True)
        port, version = read_handshake(proc, timeout=30)
        probe(port)
        print(READY + json.dumps({
            "backend": f"http://{LOOPBACK}:{port}", "llm": f"http://{LOOPBACK}:{stub.port}/v1",
            "root": str(root.path), "workspace": str(dirs.workspace), "tz": args.tz,
            "frozenAt": frozen.isoformat(), "today": today.isoformat(), "version": version,
        }), flush=True)
        sys.stdin.read()  # until the worker closes our stdin, or dies
        return 0
    except Refused as e:
        print(f"harness: refused: {e}", file=sys.stderr)
        return 2
    finally:
        kids.stop_all()
        if stub is not None:
            stub.stop()
        if root is not None:
            if args.keep:
                print(f"harness: kept {root.path}", file=sys.stderr)
            else:
                root.remove()


if __name__ == "__main__":
    sys.exit(main())
