#!/usr/bin/env python3
"""Self-mutation runner: seed a few mutants before handing a slice to the verifier (ADR-0057, adjustment 2).

A plan file (TOML, kept in the session scratchpad, not in the repo) lists the test command and the mutants:

    cmd = "python3 -m unittest discover -s scripts/tests -q"
    timeout = 120                      # seconds per run; optional
    [[mutant]]
    id = "drop-containment"
    file = "scripts/oracle/copies.py"
    find = "if not inside(p, root):"   # must occur exactly once in the file
    replace = "if False:"

Every run (the baseline, then each mutant) happens in a fresh copy of the working tree: tracked files plus
untracked files that are not ignored, so uncommitted work is tested and ignored files (data, envs, caches)
are not copied. The checkout itself is never written (INV-24): a symlink is copied as a symlink only when it
is relative and stays inside the tree; any other symlink is refused, since the test command could write
through it. A mutant changes only its `find` bytes; the rest of the file, line endings included, is kept.

Outcomes: killed (the command failed), killed (timeout), survived (the command passed). A baseline that
fails or times out, a malformed plan, a mutant whose `find` text is not exactly once in its file, or a mutant
file outside the copy is an error: no mutant is counted. Exit 0 = every mutant killed, 1 = at least one survived, 2 = error.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT = 120


@dataclass(frozen=True)
class Mutant:
    id: str
    file: str
    find: str
    replace: str


class PlanError(Exception):
    pass


def load(path: Path) -> tuple[str, int, list[Mutant]]:
    data = tomllib.loads(path.read_text())
    cmd = data.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        raise PlanError("plan: `cmd` (the test command) is required")
    timeout = data.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise PlanError("plan: `timeout` must be a positive number of seconds")
    raw = data.get("mutant", [])
    if not isinstance(raw, list) or not raw:
        raise PlanError("plan: no [[mutant]] entries")
    out: list[Mutant] = []
    for m in raw:
        fields = [m.get(k) if isinstance(m, dict) else None for k in ("id", "file", "find", "replace")]
        if not all(isinstance(f, str) for f in fields) or not fields[0] or not fields[2]:
            raise PlanError(f"plan: each mutant needs text fields id, file, find, replace (got {m!r})")
        out.append(Mutant(*fields))
    ids = [m.id for m in out]
    if len(set(ids)) != len(ids):
        raise PlanError("plan: mutant ids must be unique")
    return cmd, timeout, out


def tree_files(repo: Path) -> list[str]:
    """Tracked plus untracked-not-ignored paths that exist in the working tree."""
    res = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "-c", "-o", "--exclude-standard"],
        check=True, capture_output=True,
    )
    names = sorted({n for n in res.stdout.decode().split("\0") if n})
    return [n for n in names if os.path.lexists(repo / n)]


def check_links(repo: Path, files: list[str]) -> None:
    """Refuse symlinks that are absolute or resolve outside the tree: the command could write through them."""
    root = repo.resolve()
    for name in files:
        src = repo / name
        if src.is_symlink():
            link = os.readlink(src)
            if os.path.isabs(link) or not (src.parent / link).resolve().is_relative_to(root):
                raise PlanError(f"refused: symlink {name} -> {link} leaves the tree (the copy would write through it)")


def snapshot(repo: Path, files: list[str], dest: Path) -> None:
    for name in files:
        src, dst = repo / name, dest / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copy2(src, dst)


def target(copy: Path, m: Mutant) -> Path:
    """The mutant's file inside the copy. Containment holds because validate() accepts only files of the
    working tree and check_links() refuses symlinks that leave it."""
    return copy / m.file


def validate(repo: Path, files: list[str], mutants: list[Mutant]) -> None:
    known = set(files)
    for m in mutants:
        p = target(repo, m)
        if Path(m.file).as_posix() not in known or not p.is_file():
            raise PlanError(f"{m.id}: {m.file!r} is not a file of the working tree")
        n = p.read_bytes().count(m.find.encode())
        if n != 1:
            raise PlanError(f"{m.id}: `find` occurs {n} times in {m.file} (must be exactly once)")


def run_cmd(cmd: str, cwd: Path, timeout: int) -> str:
    """'pass', 'fail' or 'timeout'. The whole process group is killed on timeout."""
    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        return "pass" if proc.wait(timeout=timeout) == 0 else "fail"
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        return "timeout"


def run_once(repo: Path, files: list[str], work: Path, cmd: str, timeout: int, m: Mutant | None) -> str:
    copy = Path(tempfile.mkdtemp(prefix="run-", dir=work))
    try:
        snapshot(repo, files, copy)
        if m is not None:
            p = target(copy, m)
            p.write_bytes(p.read_bytes().replace(m.find.encode(), m.replace.encode(), 1))
        return run_cmd(cmd, copy, timeout)
    finally:
        shutil.rmtree(copy, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", type=Path, help="TOML plan file (cmd, timeout, [[mutant]])")
    ap.add_argument("--repo", type=Path, default=ROOT, help="checkout to copy (default: this repo)")
    ap.add_argument("--work", type=Path, help="folder for the copies (default: a temp folder)")
    a = ap.parse_args(argv)
    repo = a.repo.resolve()
    try:
        cmd, timeout, mutants = load(a.plan)
        files = tree_files(repo)
        check_links(repo, files)
        validate(repo, files, mutants)
        if a.work is not None:
            work = a.work.resolve()
            if work.is_relative_to(repo):
                raise PlanError("--work must be outside the checkout")
            work.mkdir(parents=True, exist_ok=True)
            own_work = False
        else:
            work = Path(tempfile.mkdtemp(prefix="mutants-"))
            own_work = True
    except (PlanError, OSError, ValueError, subprocess.CalledProcessError) as e:  # TOMLDecodeError is a ValueError
        print(f"mutants: error: {e}", file=sys.stderr)
        return 2
    try:
        base = run_once(repo, files, work, cmd, timeout, None)
        if base != "pass":
            print(f"mutants: error: the baseline ({base}) does not pass on the unmutated copy; "
                  "no mutant is counted", file=sys.stderr)
            return 2
        survived = 0
        width = max(len(m.id) for m in mutants)
        for m in mutants:
            r = run_once(repo, files, work, cmd, timeout, m)
            label = {"fail": "killed", "timeout": "killed (timeout)", "pass": "survived"}[r]
            survived += r == "pass"
            print(f"{m.id:<{width}}  {label}")
        print(f"{len(mutants)} tried, {len(mutants) - survived} killed, {survived} survived")
        return 1 if survived else 0
    except (PlanError, OSError) as e:
        print(f"mutants: error: {e}", file=sys.stderr)
        return 2
    finally:
        if own_work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
