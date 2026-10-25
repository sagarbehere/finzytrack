#!/usr/bin/env python3
"""The pre-push hook's first step (S0031, the M0 gate red team H1 and H2): make the checks see what is pushed.

The pre-push checks run on the working tree. They test the pushed commits only when the tree IS the pushed commit,
so this script refuses the push unless:
  - every ref being pushed points at HEAD (a tag is peeled to its commit); pushing another branch or an older
    commit is refused, as is a ref name or sha that does not resolve;
  - the tree is clean: `git status` shows no tracked change and no untracked file that is not ignored (read with
    the file-system monitor off and untracked files shown, whatever the clone's config says);
  - no index entry hides a change from `git status` (assume-unchanged or skip-worktree);
  - no ignored file could change what the checks run: an ignored source or config file (`.py`, `.ts`, `.toml`,
    `conftest.py`, ...) is refused anywhere except in the dot-named cache folders of TRUSTED_DIR_NAMES and the
    dependency and build folders of TRUSTED_PATHS, whose content the runners do not collect; inside `__pycache__`
    only bytecode (`.pyc`) is accepted, which Python never imports without its source.
    Stated limit: a forged `.pyc` carrying a tracked source's timestamp and size would run instead of that source;
    building one is a deliberate act, out of reach like the guard's deliberately built commands (BL-0062).
A ref deletion pushes no commit and is let through here (the guard hook refuses deletions by agents).

On success it prints one range per pushed ref for `check_staged.py --range` and `privacy_scan.py --range`:
  - an existing remote branch: REMOTE_SHA..LOCAL_SHA (git reports REMOTE_SHA from the remote itself);
  - a new remote branch, or a remote sha this clone does not have: MERGE_BASE..LOCAL_SHA with the remote's own
    `next` (read with `git ls-remote`, never from this clone's remote-tracking refs, which a commit can move),
    or the bare LOCAL_SHA (its whole history) when there is none.

Usage (from .githooks/pre-push):  python3 scripts/check_push.py <remote> < <pre-push stdin>
Exit 0 = ranges printed (maybe none), 1 = refused.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath

ZERO = "0" * 40
SHA = re.compile(r"^[0-9a-f]{40}$")
# Ignored cache folders matched by name anywhere: a dot name is not an importable package, pytest skips dot
# folders and the test globs do not match them.
TRUSTED_DIR_NAMES = {".venv", ".pytest_cache", ".ruff_cache", ".import_linter_cache", ".mypy_cache", ".hypothesis",
                     ".vite", ".pnpm-store", ".stryker-tmp"}
# Dependency and build folders, matched by their exact place (a `node_modules` elsewhere could be imported by
# unittest discovery as a package): their content is installed or generated, and no runner collects code there.
TRUSTED_PATHS = ("frontend/node_modules/", "shell/src-tauri/target/", "shell/src-tauri/gen/", "frontend/dist/",
                 "frontend/test-results/", "frontend/playwright-report/", "build/")
CODE_SUFFIXES = {".py", ".pyc", ".pyo", ".pth", ".pyi", ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs",
                 ".cjs", ".vue", ".toml", ".cfg", ".ini", ".rs", ".sh", ".bash", ".zsh"}
CODE_NAMES = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "package.json", "tsconfig.json", ".eslintrc",
              ".importlinter", "Makefile", "Cargo.toml", "Cargo.lock"}


@dataclass(frozen=True)
class Ref:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


def parse_refs(text: str) -> tuple[list[Ref], list[str]]:
    refs: list[Ref] = []
    problems: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 4 or not SHA.match(parts[1]) or not SHA.match(parts[3]):
            problems.append(f"pre-push input line {n} is not '<local ref> <sha> <remote ref> <sha>': {line[:120]!r}")
            continue
        refs.append(Ref(*parts))
    return refs, problems


def git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-c", "core.fsmonitor=false", *args], capture_output=True, text=True,
                          errors="surrogateescape")
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {proc.stderr.strip()[-300:]}")
    return proc.stdout if proc.returncode == 0 else ""


def commit_of(sha: str) -> str | None:
    out = git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", check=False).strip()
    return out if SHA.match(out) else None


def untrusted_ignored(entries: list[str], walk=os.walk) -> list[str]:
    """Ignored source and config files outside the trusted folders. `entries` are `git status --ignored` paths:
    a folder ends with '/' and is searched, skipping trusted folders inside it."""
    bad: list[str] = []

    def is_code(rel: str) -> bool:
        p = PurePosixPath(rel)
        if p.parent.name == "__pycache__" and p.suffix == ".pyc":
            return False  # bytecode beside its source's folder; never imported without the source
        return p.name in CODE_NAMES or p.suffix.lower() in CODE_SUFFIXES

    def trusted(rel: str) -> bool:
        return rel.startswith(TRUSTED_PATHS) or any(part in TRUSTED_DIR_NAMES for part in PurePosixPath(rel).parts)

    for entry in entries:
        if trusted(entry):
            continue
        if not entry.endswith("/"):
            if is_code(entry):
                bad.append(entry)
            continue
        for dirpath, dirnames, filenames in walk(entry):
            rel_dir = dirpath.replace(os.sep, "/").rstrip("/") + "/"
            dirnames[:] = [d for d in dirnames if not trusted(rel_dir + d + "/")]
            bad += [rel_dir + f for f in filenames if is_code(rel_dir + f)]
    return bad


def tree_problems() -> list[str]:
    problems: list[str] = []
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none")
    dirty = [e for e in status.split("\0") if e]
    if dirty:
        problems.append(f"the working tree differs from HEAD ({len(dirty)} entr(y/ies), e.g. {dirty[0]!r}); the checks "
                        "would test files that are not pushed. Commit or remove them first")
    hidden = [ln[2:] for ln in git("ls-files", "-v", "-z").split("\0") if ln[:1].islower() or ln[:1] == "S"]
    if hidden:
        problems.append(f"{len(hidden)} index entr(y/ies) hide changes from git status (assume-unchanged or "
                        f"skip-worktree), e.g. {hidden[0]!r}; clear them with git update-index first")
    ignored = git("status", "--porcelain=v1", "-z", "--ignored=traditional", "--untracked-files=normal")
    entries = [e[3:] for e in ignored.split("\0") if e.startswith("!! ")]
    bad = untrusted_ignored(entries)
    if bad:
        problems.append(f"ignored source or config file(s) the checks could load, e.g. {bad[0]!r} ({len(bad)} in "
                        "all); move or remove them first")
    return problems


def remote_next(remote: str) -> str | None:
    if not remote:
        return None
    out = git("ls-remote", "--", remote, "refs/heads/next", check=False).split()
    return out[0] if out and SHA.match(out[0]) else None


def ranges_for(refs: list[Ref], remote: str) -> list[str]:
    out: list[str] = []
    for r in refs:
        if r.local_sha == ZERO:
            continue
        tip = commit_of(r.local_sha)
        base = r.remote_sha if r.remote_sha != ZERO and commit_of(r.remote_sha) else None
        if base is None:
            nxt = remote_next(remote)
            if nxt and commit_of(nxt):
                mb = git("merge-base", r.local_sha, nxt, check=False).strip()
                base = mb if SHA.match(mb) else None
        out.append(f"{base}..{tip}" if base else f"{tip}")
    return out


def main(argv: list[str] | None = None, stdin: str | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    remote = argv[0] if argv else ""
    refs, problems = parse_refs(sys.stdin.read() if stdin is None else stdin)
    head = commit_of("HEAD")
    for r in refs:
        if r.local_sha == ZERO:
            continue
        tip = commit_of(r.local_sha)
        if tip is None:
            problems.append(f"{r.local_ref}: {r.local_sha[:12]} is not a commit here")
        elif tip != head:
            problems.append(f"{r.local_ref} ({tip[:12]}) is not HEAD ({(head or '?')[:12]}); the checks run on the "
                            "working tree, so only HEAD can be pushed. Check out what you push, or push HEAD")
    if not problems:
        problems += tree_problems()
    if problems:
        for p in problems:
            print(f"pre-push: refused: {p}", file=sys.stderr)
        return 1
    for rng in ranges_for(refs, remote):
        print(rng)
    return 0


if __name__ == "__main__":
    sys.exit(main())
