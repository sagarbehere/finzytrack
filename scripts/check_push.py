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
  - no replacement object or graft changes what git shows (S0031 verifier F2): with a `refs/replace/*` ref or
    `info/grafts`, every local read (checkout, status, rev-list, cat-file) can see other objects than the ones
    `git push` sends, so either one refuses the push; every git read here also runs with replacements off
    (the hooks export GIT_NO_REPLACE_OBJECTS=1 too).
A ref deletion pushes no commit and is let through here (the guard hook refuses deletions by agents).

On success it prints one line per pushed ref for `check_staged.py --range` and `privacy_scan.py --range`:
  - an existing remote branch: REMOTE_SHA..LOCAL_SHA (git reports REMOTE_SHA from the remote itself);
  - a new remote branch, or a remote sha this clone does not have: MERGE_BASE..LOCAL_SHA with the remote's own
    `next` (read with `git ls-remote` at the URL the push goes to, never from this clone's remote-tracking refs,
    which a commit can move); when the remote has none, FLOOR..LOCAL_SHA, where FLOOR is the commit that started
    v0.4 from an empty tree (so the legacy history below it is not read); the bare LOCAL_SHA (its whole history)
    only when FLOOR is not below it. A fresh remote receives the whole history, so it is scanned whole: today
    that fails on three blobs that later commits fixed (they are already public on origin), which is right.
  and, for a pushed annotated tag, `tag TAG_SHA` (its message is scanned by `privacy_scan.py --tag-message`).

With `--still HEAD_SHA` (the last step of the pre-push hook, S0031 verifier F3) it checks only that HEAD is still
HEAD_SHA and the tree is still clean: another process (a parallel agent, an editor's autosave) that changed the
tree during the ~90 s of checks changed what they tested. Stated limit: a change made and undone between the two
checks is not seen.

Usage (from .githooks/pre-push):  python3 scripts/check_push.py <remote> [<push url>] < <pre-push stdin>
                                  python3 scripts/check_push.py --still <HEAD sha>
Exit 0 = lines printed (maybe none), 1 = refused.
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
                 "frontend/test-results/", "frontend/playwright-report/", "build/", "backend/mutants/",
                 "backend/htmlcov/", "htmlcov/", "mutants/")
# Claude Code's own ignored state (agent worktrees, schedules, local settings): no git hook or test runner reads
# it. Its hooks folder is excluded: the guard meta-tests import from it.
TRUSTED_CLAUDE = (".claude/",)
UNTRUSTED_CLAUDE = (".claude/hooks/",)
# The commit that started v0.4 from an empty tree on next ("next: start v0.4 from an empty tree; .gitignore first").
FLOOR = "2777d57fb52ab00252a022545aeb4e1161cfae41"  # a commit name  privacy-scan: allow secret-entropy
CODE_SUFFIXES = {".py", ".pyc", ".pyo", ".pth", ".pyi", ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs",
                 ".cjs", ".vue", ".toml", ".cfg", ".ini", ".rs", ".sh", ".bash", ".zsh", ".json", ".jsonc", ".json5",
                 ".yaml", ".yml"}
CODE_NAMES = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "Makefile", ".importlinter", ".npmrc",
              ".python-version", ".editorconfig", ".browserslistrc"}
# Tool configuration and ignore files by name pattern: `.prettierignore`, `.eslintrc*`, `.prettierrc*`, ...
CODE_NAME = re.compile(r"^\.(?:[a-z0-9-]+ignore|[a-z0-9-]+rc(?:\..*)?)$", re.I)


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
    proc = subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false", *args], capture_output=True,
                          text=True, errors="surrogateescape", env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"})
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
        return p.name in CODE_NAMES or p.suffix.lower() in CODE_SUFFIXES or bool(CODE_NAME.match(p.name))

    def trusted(rel: str) -> bool:
        if rel.startswith(TRUSTED_CLAUDE) and not rel.startswith(UNTRUSTED_CLAUDE) and rel != ".claude/":
            return True
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
    replaced = git("for-each-ref", "--format=%(refname)", "refs/replace/").split()
    grafts = git("rev-parse", "--git-path", "info/grafts").strip()
    if replaced or (grafts and os.path.exists(grafts)):
        problems.append(f"replacement objects or grafts change what git shows ({(replaced or [grafts])[0]}); the "
                        "checks would not read what is pushed. Remove them (git replace -d, or the grafts file) first")
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


def object_type(sha: str) -> str:
    return git("cat-file", "-t", sha, check=False).strip()


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
            elif tip and commit_of(FLOOR) and _is_ancestor(FLOOR, tip):
                base = FLOOR
        out.append(f"{base}..{tip}" if base else f"{tip}")
        if object_type(r.local_sha) == "tag":
            out.append(f"tag {r.local_sha}")
    return out


def _is_ancestor(a: str, b: str) -> bool:
    return subprocess.run(["git", "--no-replace-objects", "merge-base", "--is-ancestor", a, b],
                          capture_output=True).returncode == 0


def main(argv: list[str] | None = None, stdin: str | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--still"]:
        return still(argv[1] if len(argv) > 1 else "")
    remote = (argv[1] if len(argv) > 1 and argv[1] else argv[0]) if argv else ""  # the push URL when git gives it
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


def still(head_at_start: str) -> int:
    """The end of the pre-push hook: HEAD and the tree are what the checks started on (S0031 verifier F3)."""
    problems = []
    head = commit_of("HEAD")
    if not SHA.match(head_at_start) or head != head_at_start:
        problems.append(f"HEAD moved during the checks ({head_at_start[:12] or '?'} -> {(head or '?')[:12]})")
    problems += tree_problems()
    for p in problems:
        print(f"pre-push: refused: the tree changed while the checks ran: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
