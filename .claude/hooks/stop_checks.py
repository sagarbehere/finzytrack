#!/usr/bin/env python3
"""Stop hook: before an agent session ends, run the fast repo checks so it never stops on a red tree.

Runs the check-script meta-tests (in parallel, `scripts/run_meta_tests.py`), the invariant and capability checks,
and the privacy scan over files changed in the working tree, and fails when this clone's git hooks are not
installed (S0031, the M0 gate red team L2: without `core.hooksPath = .githooks` no commit or push is checked). Exit
2 sends the failures back to the agent once; when the hook has already asked for a fix (stop_hook_active), it lets
the session stop and only prints the problems.

S0034: it does not re-prove what is proven. After a green run it stores a key of the whole working tree (tracked
and untracked files that are not ignored, hashed through a temporary index; the real index is never touched) and
the Python version in the clone's git directory; the same key next time is green at once (the git-hooks check
still runs). Each check runs under the hang detector of `scripts/gate_timer.py` (a limit from its own history, not
a budget) and under BACKSTOP_S for the whole hook, below the harness's Stop timeout in .claude/settings.json (a hook
the harness kills fails open). The hook never crashes: a check that did not finish, or any error here, is red. The
key file is agent-writable, so this is a convenience gate; the pre-push hook is the authoritative one, with no cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECKS = [
    ("check-script meta-tests", ["python3", "scripts/run_meta_tests.py"]),
    ("invariants", ["python3", "scripts/check_invariants.py"]),
    ("capability map", ["python3", "scripts/check_capabilities.py"]),
]
BACKSTOP_S = 1500  # the whole hook; below the Stop timeout in .claude/settings.json (1800 s)
GIT_TIMEOUT_S = 120  # one git plumbing call; capped further by the time left before the backstop
GREEN_FILE = "stop-green"  # in gate_timer.state_dir(): the key of the last tree every check passed on


def load_timer():
    """scripts/gate_timer.py of the repository this hook file is in (not REPO, which the meta-tests point at a
    scratch repository)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    try:
        import gate_timer  # noqa: PLC0415 (from the repo's scripts/, found at run time)
    finally:
        sys.path.pop(0)
    return gate_timer


def changed_files() -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=all"], cwd=REPO,
                         capture_output=True, text=True, timeout=GIT_TIMEOUT_S).stdout
    files = []
    for entry in out.split("\0"):
        if len(entry) > 3 and entry[0] != "D" and entry[1] != "D":
            files.append(entry[3:])
    return [f for f in files if (REPO / f).is_file()]


def hooks_path_problem(repo: Path) -> str | None:
    """None when the clone runs .githooks; otherwise the failure to report."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "config", "--get", "core.hooksPath"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not read core.hooksPath ({exc})"
    if out == ".githooks":
        return None
    return (f"core.hooksPath is {out!r}, not '.githooks': no commit or push in this clone is checked. Ask the owner "
            "to run `git config core.hooksPath .githooks` (agents may not change it)")


def tree_key(repo: Path, deadline: float | None = None) -> str | None:
    """A key of the working tree's content, which of its files are tracked, and the Python version, or None when it
    cannot be computed (or not before `deadline`, a time.monotonic() value).

    A temporary index starts from HEAD (no assume-unchanged or skip-worktree flags) and `git add -A` puts every
    tracked and untracked, not ignored, file in it; `git write-tree` names the result. The real index is only read,
    for the list of tracked paths (`git ls-files`): a check that reads tracked files only, such as `git grep`, sees
    a file go from tracked to untracked (S0034 verifier F3)."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        env.pop("GIT_DIR", None)
        env.pop("GIT_WORK_TREE", None)

        def git(*args: str, real_index: bool = False) -> subprocess.CompletedProcess[str]:
            timeout = GIT_TIMEOUT_S if deadline is None else min(GIT_TIMEOUT_S, deadline - time.monotonic())
            if timeout <= 0:
                raise subprocess.TimeoutExpired(["git", *args], 0)
            return subprocess.run(["git", "-C", str(repo), *args], env=None if real_index else env,
                                  capture_output=True, text=True, timeout=timeout)

        try:
            if git("rev-parse", "--verify", "--quiet", "HEAD").returncode == 0 and git("read-tree", "HEAD").returncode:
                return None
            if git("add", "-A").returncode:
                return None
            tree = git("write-tree")
            tracked = git("ls-files", "-z", real_index=True)
        except (OSError, subprocess.SubprocessError):
            return None
    if tree.returncode or not tree.stdout.strip() or tracked.returncode:
        return None
    paths = hashlib.sha256(tracked.stdout.encode()).hexdigest()
    return hashlib.sha256(f"tree {tree.stdout.strip()}\ntracked {paths}\npython {sys.version}\n".encode()).hexdigest()


def run_checks(timer, deadline: float) -> list[str]:
    failures = []
    # The timer must fail a failing check; if it passes one, every check below would read as passed.
    canary = timer.run_check([sys.executable, "-c", "raise SystemExit(1)"], REPO, 60, capture=True)  # never passes
    if canary.passed:
        return ["the check timer (scripts/gate_timer.py) passed a failing check; no check result can be trusted"]
    checks = list(CHECKS)
    files = changed_files()
    if files:
        checks.append(("privacy scan of changed files", ["python3", "scripts/privacy_scan.py", "--quiet", *files]))
    for label, cmd in checks:
        outcome = timer.timed(REPO, "stop", label, cmd, capture=True, deadline=deadline)
        if not outcome.passed:
            head = outcome.message or f"{label}: {outcome.status}"
            failures.append(f"$ {' '.join(cmd[:4])}{' ...' if len(cmd) > 4 else ''}\n{head}\n{outcome.output[-2000:]}")
    return failures


def check(event: dict) -> list[str]:
    deadline = time.monotonic() + BACKSTOP_S
    failures = []
    problem = hooks_path_problem(REPO)
    if problem:
        failures.append(f"git hooks: {problem}")
    timer = load_timer()
    folder = timer.state_dir(REPO)
    green_file = folder / GREEN_FILE if folder else None
    key = tree_key(REPO, deadline)
    if key is not None and green_file is not None and _read(green_file) == key:
        return failures  # every check passed on this exact tree before
    failures += run_checks(timer, deadline)
    # Store the key only after a fully green run on a tree that did not change while the checks ran, and only when
    # the check of that fits before the backstop (S0034 verifier F4).
    if not failures and key is not None and green_file is not None and tree_key(REPO, deadline) == key:
        try:
            green_file.parent.mkdir(parents=True, exist_ok=True)
            green_file.write_text(key)
        except OSError:
            pass
    return failures


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        event = {}
    if not isinstance(event, dict):
        event = {}
    try:
        failures = check(event)
    except BaseException as exc:  # noqa: BLE001 (the hook never crashes: a crash would let the session stop green)
        failures = [f"stop_checks itself failed, so the checks did not run: {exc!r}"]
    if not failures:
        return 0
    print("stop_checks: the tree is red:\n\n" + "\n\n".join(failures), file=sys.stderr)
    return 0 if event.get("stop_hook_active") else 2


if __name__ == "__main__":
    sys.exit(main())
