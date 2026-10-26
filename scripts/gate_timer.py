#!/usr/bin/env python3
"""Check times for the T0 gates: a hang detector set from each check's own history, and a time report (S0034).

A time limit on a check is a hang detector, not a budget: the suites grow, so a fixed number is exceeded one day
and then either switches the gate off or blocks it for no reason. So each check's limit comes from its own history
in this clone: once it has passed MIN_SAMPLES times, a run that takes over K times its usual time (the median of its
last N green runs), and at least FLOOR_S seconds, is stopped as hung and fails. A check with less history has no
limit here. Slowness alone never fails a check, and a check that did not finish never passes.

Usage (the pre-push hook's `run` function, and its time report at the end):
  python3 scripts/gate_timer.py run <hook> <label> <command...>   run one check; exit 0 only when it ran and passed
  python3 scripts/gate_timer.py report <hook>                    this hook run's check times against their usual

The history lives in the clone's git directory (`git rev-parse --git-path finzytrack`), never in the tree. Agents
can write it: a forged history can raise a limit (weaker hang detection) or lower it (a false failure), never make a
failing check pass. A deleted or unreadable history means no limit until the check has passed MIN_SAMPLES times.
"""

from __future__ import annotations

import json
import os
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

K = 8  # a check over K times its usual time is treated as hung (S0034 verifier F2: concurrent runs, see the journal)
N = 5  # usual time = the median of the last N green runs
MIN_SAMPLES = 3  # fewer green runs than this: no limit yet
FLOOR_S = 60.0  # a check is never stopped as hung before this many seconds
KEEP = 20  # green durations kept per check
GRACE_S = 5.0  # after SIGTERM, wait this long before SIGKILL
SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


@dataclass(frozen=True)
class Outcome:
    status: str  # "passed" | "failed" | "hung" | "error"
    seconds: float
    output: str = ""
    message: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def state_dir(repo: Path) -> Path | None:
    """This clone's (or worktree's) state folder in its git directory, or None outside a git repository."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-path", "finzytrack"], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode or not r.stdout.strip():
        return None
    path = Path(r.stdout.strip())
    return path if path.is_absolute() else repo / path


def check_key(label: str) -> str:
    """A check's history key: its label with commit IDs blanked, so `pushed commits a..b` keeps one history."""
    return SHA.sub("<sha>", label)


def load_json(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_json(path: Path | None, data: dict) -> None:
    """Write atomically. A failure to save loses only timing history, so it is ignored."""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


def greens(history: dict, key: str) -> list[float]:
    values = history.get(key)
    if not isinstance(values, list):
        return []
    return [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0]


def usual(history: dict, key: str) -> float | None:
    """The median of the last N green durations, or None with fewer than MIN_SAMPLES of them."""
    values = greens(history, key)[-N:]
    return statistics.median(values) if len(values) >= MIN_SAMPLES else None


def limit(history: dict, key: str) -> float | None:
    """The hang limit in seconds, or None while the check has too little history."""
    u = usual(history, key)
    return None if u is None else max(K * u, FLOOR_S)


def record(history: dict, key: str, seconds: float) -> dict:
    return {**history, key: (greens(history, key) + [round(seconds, 3)])[-KEEP:]}


def stop_group(proc: subprocess.Popen) -> None:
    """Stop the check and everything it started in its process group: SIGTERM, then SIGKILL."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=GRACE_S)
            return
        except subprocess.TimeoutExpired:
            continue


def run_check(cmd: list[str], cwd: Path, timeout: float | None, capture: bool) -> Outcome:
    """Run one check in its own process group, stopped after `timeout` seconds. A check that could not start,
    exited non-zero, died from a signal or was stopped did not pass."""
    out = tempfile.TemporaryFile() if capture else None  # a file, not a pipe: a leftover grandchild cannot block
    start = time.monotonic()
    try:
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT if capture else None, start_new_session=True)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return Outcome("error", time.monotonic() - start, message=f"could not start: {exc}")
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_group(proc)
            return Outcome("hung", time.monotonic() - start, _read(out))
        except BaseException:
            stop_group(proc)  # interrupted (Ctrl-C, SIGTERM): never leave the check running
            raise
        return Outcome("passed" if rc == 0 else "failed", time.monotonic() - start, _read(out))
    finally:
        if out is not None:
            out.close()


def _read(out) -> str:
    if out is None:
        return ""
    try:
        out.seek(0)
        return out.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def timed(repo: Path, hook: str, label: str, cmd: list[str], capture: bool, deadline: float | None = None,
          log: bool = False) -> Outcome:
    """Run a check under its hang limit and record its time when it passed. `deadline` (a time.monotonic() value)
    caps the limit, for a caller with its own backstop. `log` keeps this hook run's times for `report`."""
    folder = state_dir(repo)
    history_file = folder / f"times-{hook}.json" if folder else None
    history = load_json(history_file)
    key = check_key(label)
    u, lim = usual(history, key), limit(history, key)
    remaining = None if deadline is None else max(deadline - time.monotonic(), 0.0)
    by_backstop = remaining is not None and (lim is None or remaining < lim)
    outcome = run_check(cmd, repo, remaining if by_backstop else lim, capture)
    if outcome.status == "hung":
        if by_backstop:
            message = f"{label}: did not finish before the backstop; hung?"
        else:
            message = (f"{label}: took over {K}x its usual {u:.1f} s (stopped after {lim:.0f} s); hung? If it is only "
                       f"slower now, delete {history_file} and run again")
        outcome = Outcome("hung", outcome.seconds, outcome.output, message)
    if outcome.passed:
        save_json(history_file, record(load_json(history_file), key, outcome.seconds))
    if log and folder is not None:
        log_run(folder / f"last-run-{hook}.json", label, u, outcome)
    return outcome


def run_id() -> str:
    """This hook run: the hook's shell, the parent of every `run` it makes (passed on to the checks it starts)."""
    return os.environ.get("FT_GATE_RUN") or str(os.getppid())


def log_run(path: Path, label: str, usual_s: float | None, outcome: Outcome) -> None:
    """Append one check to this hook run's log; a log from another run (or over an hour old) is replaced."""
    log, now = load_json(path), time.time()
    at = log.get("at")
    fresh = isinstance(at, (int, float)) and now - at < 3600
    if log.get("run") != run_id() or not isinstance(log.get("checks"), list) or not fresh:
        log = {"run": run_id(), "checks": []}
    log["at"] = now
    log["checks"].append({"label": label, "seconds": round(outcome.seconds, 3), "usual": usual_s,
                          "status": outcome.status})
    save_json(path, log)


def report(repo: Path, hook: str) -> str:
    """This hook run's check times, each against its usual time before the run, and the total."""
    folder = state_dir(repo)
    log = load_json(folder / f"last-run-{hook}.json" if folder else None)
    checks = log.get("checks") if log.get("run") == run_id() else None
    if not isinstance(checks, list) or not checks:
        return f"{hook}: no check times recorded for this run"
    lines = [f"{hook}: check times (usual = the median of the last {N} green runs in this clone)"]
    total = total_usual = 0.0
    any_usual = False
    for c in (c for c in checks if isinstance(c, dict)):
        seconds, u = float(c.get("seconds") or 0), c.get("usual")
        total += seconds
        if isinstance(u, (int, float)):
            total_usual += float(u)
            any_usual = True
        note = f"usual {u:.1f} s" if isinstance(u, (int, float)) else "no usual yet"
        status = "" if c.get("status") == "passed" else f"  {str(c.get('status')).upper()}"
        lines.append(f"  {seconds:7.1f} s  ({note}){status}  {c.get('label', '')}")
    note = f"usual {total_usual:.1f} s, over the checks with a usual time" if any_usual else "no usual yet"
    lines.append(f"  {total:7.1f} s  ({note})  total")
    return "\n".join(lines)


def _interrupted(signum: int, _frame) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


def main(argv: list[str]) -> int:
    repo = Path.cwd()
    if len(argv) >= 4 and argv[0] == "run":
        hook, label, cmd = argv[1], argv[2], argv[3:]
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, _interrupted)  # stop the check's process group on the way out
        try:
            os.environ["FT_GATE_RUN"] = run_id()  # the report, started through `run`, sees this hook run's ID
            outcome = timed(repo, hook, label, cmd, capture=False, log=True)
        except Exception as exc:  # noqa: BLE001 (a check that did not run is a failure, never a pass)
            print(f"{hook}: {label}: the check timer failed: {exc!r}", file=sys.stderr)
            return 1
        if outcome.message:
            print(f"{hook}: {outcome.message}", file=sys.stderr)
        return 0 if outcome.passed else 1
    if len(argv) == 2 and argv[0] == "report":
        try:
            print(report(repo, argv[1]), file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 (the report is information; it never fails the hook)
            print(f"{argv[1]}: no check time report: {exc!r}", file=sys.stderr)
        return 0
    print("usage: gate_timer.py run <hook> <label> <command...> | report <hook>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
