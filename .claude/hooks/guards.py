"""Shared rules for the Claude Code guard hooks (INV-24): keep agents away from the owner's real data.

These guards are a safety net, not a sandbox: they catch the common ways an agent could touch the live
data folder, delete ignored files, write into the read-only legacy worktree or stop the owner's running
app. The rules themselves are in design/process/PROCESS.md ("Agents and real data").
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def live_data_root() -> Path:
    return Path(os.environ.get("FT_LIVE_DATA", Path.home() / "FinzytrackData")).expanduser()


def legacy_root() -> Path:
    return Path(os.environ.get("FT_LEGACY_ROOT", REPO.parent / "finzytrack-legacy")).expanduser()


def _resolve(p: str, cwd: Path) -> Path:
    path = Path(os.path.expandvars(p)).expanduser()
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve(strict=False)
    except OSError:
        return path


def _under(p: Path, root: Path) -> bool:
    try:
        root_r = root.resolve(strict=False)
    except OSError:
        root_r = root
    return p == root_r or root_r in p.parents


def _generated(p: Path) -> bool:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return "GENERATED" in fh.readline()
    except OSError:
        return False


def path_problem(raw: str, cwd: Path, writing: bool) -> str | None:
    """Why an agent may not read or write this path, or None if it may."""
    p = _resolve(raw, cwd)
    if writing and p.is_file() and _generated(p):
        return "this file is generated (GENERATED header); edit its source and rerun the generator (INV-19)"
    if _under(p, live_data_root()) or "FinzytrackData" in raw:
        return "the owner's live data folder is off-limits to agents (INV-24); use copies under a temp dir"
    if writing and _under(p, legacy_root()):
        return "the legacy worktree is read-only; copy code out of it, never write into it"
    return None


GIT_CLEAN_IGNORED = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?\s+clean\b[^;&|]*\s-(?:[a-wyzA-WYZ]*)[xX]")
NO_VERIFY = re.compile(r"\bgit\b[^;&|]*\s--no-verify\b")
FORCE_ADD = re.compile(r"\bgit\b[^;&|]*\badd\b[^;&|]*(\s-f\b|\s--force\b|\s-[a-zA-Z]*f[a-zA-Z]*\b)")
KILL_WORDS = re.compile(r"\b(kill|pkill|killall)\b")
OWNER_APP = re.compile(r"\b8001\b|app\.main|run-v0\.3|\bpython3?\b|uvicorn")
FORCE_PUSH = re.compile(r"\bgit\b[^;&|]*\bpush\b[^;&|]*(\s--force\b|\s-f\b|\s--force-with-lease\b|\s--delete\b|\s:\S+|\s--tags\b|\s--mirror\b|\sv\d+\.\d+\.\d+\b)")
WRITE_LAST_ARG = {"mv", "cp", "rsync", "ln", "install", "tee"}
WRITE_ANY_ARG = {"rm", "rmdir", "unlink", "truncate", "shred", "chmod", "chown"}
READONLY_GIT = {"log", "show", "diff", "status", "grep", "ls-files", "ls-tree", "rev-parse", "cat-file", "blame", "describe", "branch", "tag"}


def _segments(command: str) -> list[list[str]]:
    segs: list[list[str]] = []
    for part in re.split(r"\|\||&&|;|\||\n", command):
        try:
            toks = shlex.split(part, comments=True)
        except ValueError:
            toks = part.split()
        if toks:
            segs.append(toks)
    return segs


def bash_problem(command: str, cwd: Path) -> str | None:
    """Why this shell command is refused, or None if it may run."""
    if NO_VERIFY.search(command):
        return "skipping the git hooks (--no-verify / -n on commit) is refused; fix what the gate reports"
    if FORCE_ADD.search(command):
        return "git add -f would add an ignored file (possibly real data); refused"
    if GIT_CLEAN_IGNORED.search(command):
        return "git clean -x/-X deletes ignored files (real data, archives); never run it in this repo"
    if "FinzytrackData" in command or str(live_data_root()) in command:
        return "the owner's live data folder is off-limits to agents (INV-24)"
    if KILL_WORDS.search(command) and OWNER_APP.search(command):
        return "this could stop the owner's running app; kill only processes you started, by PID"
    if FORCE_PUSH.search(command):
        return "force pushes, remote deletes and pushing tags are refused (a v* tag triggers the release build)"
    for toks in _segments(command):
        # redirections into the legacy worktree
        for i, t in enumerate(toks):
            if t in (">", ">>") and i + 1 < len(toks) and path_problem(toks[i + 1], cwd, writing=True):
                return "writes into the legacy worktree are refused; it is read-only"
            if t.startswith((">", ">>")) and len(t) > 2 and path_problem(t.lstrip(">"), cwd, writing=True):
                return "writes into the legacy worktree are refused; it is read-only"
        cmd = os.path.basename(toks[0])
        if cmd == "git" and "commit" in toks and "-n" in toks[toks.index("commit"):]:
            return "skipping the git hooks (git commit -n) is refused; fix what the gate reports"
        args = [a for a in toks[1:] if not a.startswith("-")]
        if cmd in WRITE_ANY_ARG and any(path_problem(a, cwd, writing=True) for a in args):
            return f"{cmd} inside the legacy worktree is refused; it is read-only"
        if cmd in WRITE_LAST_ARG and args and path_problem(args[-1], cwd, writing=True):
            return f"{cmd} into the legacy worktree is refused; it is read-only"
        if cmd == "git" and "-C" in toks:
            i = toks.index("-C")
            if i + 2 < len(toks) and path_problem(toks[i + 1], cwd, writing=True):
                sub = toks[i + 2]
                if sub not in READONLY_GIT:
                    return f"git {sub} in the legacy worktree is refused; it is read-only"
    return None
