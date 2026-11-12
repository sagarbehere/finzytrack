"""Shared rules for the Claude Code guard hooks (INV-24): keep agents away from the owner's real data.

These guards are a safety net, not a sandbox: they catch the common ways an agent could touch the live data
folder, delete ignored files, write into the read-only legacy worktree, or switch off the git gates (meta-tested
in scripts/tests/test_guards.py). Commands built on purpose to slip past the parser are a stated limit, not a
target (S0054). The owner's running app is not guarded: agents never stop, start or call it, and ask the owner
when it is in their way (INV-24, a working rule; owner, 2026-10-29). The rules are in design/process/PROCESS.md §8.
"""

from __future__ import annotations

import glob
import os
import re
import shlex
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def live_data_roots() -> list[Path]:
    roots = [Path.home() / "FinzytrackData"]
    if os.environ.get("FT_LIVE_DATA"):
        roots.append(Path(os.environ["FT_LIVE_DATA"]).expanduser())
    return roots


def legacy_roots() -> list[Path]:
    roots = [REPO.parent / "finzytrack-legacy", Path.home() / "experiments" / "finzytrack-legacy"]
    if os.environ.get("FT_LEGACY_ROOT"):
        roots.append(Path(os.environ["FT_LEGACY_ROOT"]).expanduser())
    return roots


# Back-compat names used by other scripts and tests.
def live_data_root() -> Path:
    return live_data_roots()[-1]


def legacy_root() -> Path:
    return legacy_roots()[-1]


def _norm(p: Path) -> str:
    """Resolved, lower-cased path string: macOS disks are case-insensitive."""
    try:
        r = p.resolve(strict=False)
    except OSError:
        r = p
    return str(r).rstrip("/").lower()


def _resolve(p: str, cwd: Path) -> Path:
    path = Path(os.path.expandvars(p))
    try:
        path = path.expanduser()
    except RuntimeError:
        pass  # `~unknownuser`: the shell leaves it as written, so the guard reads it literally too
    if not path.is_absolute():
        path = cwd / path
    return path


def _under(p: Path, root: Path) -> bool:
    a, b = _norm(p), _norm(root)
    return a == b or a.startswith(b + "/")


def _is_ancestor(p: Path, root: Path) -> bool:
    a, b = _norm(p), _norm(root)
    return b.startswith(a + "/") or a in ("", "/")


def _generated(p: Path) -> bool:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return "GENERATED" in fh.readline()
    except OSError:
        return False


LIVE_NAME = re.compile(r"(?i)finzy\W*track\W*data|finzy\*|\*data\b.*finzy|ft_live_data")


def _expand(raw: str, cwd: Path) -> list[Path]:
    paths = [_resolve(raw, cwd)]
    if any(c in raw for c in "*?["):
        pattern = str(_resolve(raw, cwd))
        paths += [Path(m) for m in glob.glob(pattern)[:200]]
    return paths


def path_problem(raw: str, cwd: Path, writing: bool) -> str | None:
    """Why an agent may not read or write this path, or None if it may."""
    if LIVE_NAME.search(raw):
        return "the owner's live data folder is off-limits to agents (INV-24); use copies under a temp dir"
    for p in _expand(raw, cwd):
        if any(_under(p, r) for r in live_data_roots()):
            return "the owner's live data folder is off-limits to agents (INV-24); use copies under a temp dir"
        if writing and any(_under(p, r) for r in legacy_roots()):
            return "the legacy worktree is read-only; copy code out of it, never write into it"
        rp = Path(_norm(p))  # resolved (symlinks, `..`) and lower-cased: macOS disks are case-insensitive
        if writing and ".git" in rp.parts and (rp.name == "config" or "hooks" in rp.parts[rp.parts.index(".git"):]):
            return "writing .git/config or .git/hooks could switch off the git gates (BL-0055); refused"
        if writing and rp.name == "manifest.tsv" and rp.parent.name == "corpora":
            return ("only the owner adds rows to corpora/MANIFEST.tsv (INV-23: owner-reviewed exports); ask the owner "
                    "to review the anonymized file and add its row")
        if writing and p.is_file() and _generated(p):
            return "this file is generated (GENERATED header); edit its source and rerun the generator (INV-19)"
    return None


READONLY_GIT = {"log", "show", "diff", "status", "grep", "ls-files", "ls-tree", "rev-parse", "cat-file", "blame",
                "describe", "shortlog", "reflog", "rev-list", "name-rev", "for-each-ref"}
READONLY_CMDS = {"ls", "cat", "head", "tail", "grep", "rg", "wc", "file", "stat", "du", "diff", "cmp", "less", "more",
                 "awk", "cut", "sort", "uniq", "tree", "pwd", "echo", "printf", "true", "test", "[", "md5", "shasum",
                 "sha256sum", "realpath", "readlink", "basename", "dirname", "jq", "cd"}
WRITE_LAST_ARG = {"mv", "cp", "rsync", "ln", "install", "scp"}
WRITE_ANY_ARG = {"rm", "rmdir", "unlink", "truncate", "shred", "chmod", "chown", "chflags", "touch", "tee", "mkdir",
                 "xattr", "setfacl"}


def _split(command: str) -> list[list[str]]:
    """The simple commands the shell runs. Heredoc bodies are data (S0054); line continuations join a command."""
    segs: list[list[str]] = []
    for part in re.split(r"\|\||&&|;|\||\n|\$\(|`|\)", _without_heredoc_bodies(command).replace("\\\n", " ")):
        try:
            toks = shlex.split(part, comments=True)
        except ValueError:
            toks = part.split()
        while toks and re.match(r"^\w+=", toks[0]):  # VAR=value prefixes
            toks = toks[1:]
        while toks and toks[0] in {"sudo", "env", "command", "builtin", "exec", "nohup", "time", "xargs", "nice"}:
            toks = toks[1:]
        if toks:
            segs.append(toks)
    return segs


def _git_parts(toks: list[str]) -> tuple[list[str], str | None, list[str]]:
    """(global options, subcommand, subcommand args) of a git invocation."""
    i, opts = 1, []
    while i < len(toks) and toks[i].startswith("-"):
        opts.append(toks[i])
        if toks[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(toks):
            opts.append(toks[i + 1])
            i += 1
        i += 1
    sub = toks[i] if i < len(toks) else None
    return opts, sub, toks[i + 1:] if sub else []


def _short_flags(args: list[str]) -> set[str]:
    return {c for a in args if re.match(r"^-[A-Za-z]+$", a) for c in a[1:]}


def _git_problem(toks: list[str], cwd: Path) -> str | None:
    opts, sub, args = _git_parts(toks)
    joined = " ".join(toks)
    # Reading a setting is fine (git config --get / --list / -l); setting or overriding it is not.
    read_only_config = sub == "config" and len(args) >= 1 and (
        args[0] in ("--get", "--get-all", "--list", "-l", "--show-origin") or (len(args) == 1 and not args[0].startswith("-")))
    if re.search(r"(?i)core\.hooks?path|hookspath", joined) and not read_only_config:
        return "changing or overriding core.hooksPath switches off the gates; refused"
    if re.search(r"(?i)finzytrack\.allowpush", joined) and not read_only_config:
        return "the push gate is the owner's switch (git config finzytrack.allowPush); agents never set it"
    if any(re.match(r"^--no-v", a) for a in args) or (sub == "commit" and "n" in _short_flags(args)):
        return "skipping the git hooks (--no-verify / commit -n) is refused; fix what the gate reports"
    if sub == "add" and ("f" in _short_flags(args) or "--force" in args):
        return "git add -f would add an ignored file (possibly real data); refused"
    if sub == "clean" and (_short_flags(args) & {"x", "X", "e"} or any(a.startswith(("--exclude",)) for a in args)):
        return "git clean -x/-X/-e deletes ignored files (real data, archives); never run it in this repo"
    if sub == "stash" and ("a" in _short_flags(args) or "--all" in args):
        return "git stash --all sweeps up ignored files (real data) and can lose them; refused"
    if sub == "push":
        if "f" in _short_flags(args) or "d" in _short_flags(args) or any(
                a.startswith(("--force", "--delete", "--tags", "--mirror", "--all", "--prune")) for a in args):
            return "force pushes, deletes, --tags/--mirror/--all pushes are refused"
        for a in args:
            if a.startswith("+") or ":" in a and not a.startswith(("http", "git@", "ssh:")) or re.match(r"^v\d+\.\d+", a) \
                    or a in ("main", "legacy-final"):
                return "push refspecs that force, delete, retarget main or push tags are refused"
    if sub == "tag" and ("d" in _short_flags(args) or "--delete" in args):
        return "deleting tags (legacy-final, v*) is refused"
    if sub == "worktree" and args[:1] in (["remove"], ["prune"], ["move"]):
        return "removing or moving worktrees (the legacy worktree is the oracle) is refused"
    # git -C <legacy> <writing subcommand>
    target = cwd
    if "-C" in opts:
        target = _resolve(opts[opts.index("-C") + 1], cwd)
    if any(_under(target, r) for r in legacy_roots()) and sub not in READONLY_GIT \
            and not (sub in ("branch", "tag") and not _short_flags(args) & {"d", "D", "m", "M", "f"} and not args[1:2]):
        return f"git {sub} in the legacy worktree is refused; it is read-only"
    return None


# A heredoc: its opener's line (kept, the shell runs it), then the body up to the delimiter line. A here-string
# (<<<) opens nothing; a body without its end line stays code. Openers inside quotes or comments are not told
# apart (a stated limit: only a command built on purpose has one, PROCESS.md §11).
HEREDOC = re.compile(r"((?<!<)<<(?!<)-?[ \t]*(['\"]?)(\w+)\2[^\n]*\n).*?^[ \t]*\3[ \t]*$", re.DOTALL | re.MULTILINE)
# A shell reads the body; `.` is left out (it is also an argument, `git add .`).
SHELLS = re.compile(r"^(?:ba|z|da|k|mk|fi|c|tc)?sh$|^(?:eval|source)$")


def _without_heredoc_bodies(command: str) -> str:
    """The lines a shell runs (S0054): a heredoc body is data unless a shell word stands on its opener's line
    (`bash <<EOF`, `cat <<EOF | sh`, `cat > x.sh <<EOF && bash x.sh`); that refuses more than needed."""
    def body(m: re.Match[str]) -> str:
        line = command[command.rfind("\n", 0, m.start()) + 1:m.end(1)]
        words = re.findall(r"[^\s;&|()<>'\"`]+", line)
        return m.group(0) if any(SHELLS.match(os.path.basename(w)) for w in words) else m.group(1)
    return HEREDOC.sub(body, command)


def bash_problem(command: str, cwd: Path) -> str | None:
    """Why this shell command is refused, or None if it may run. S0054: the guard refuses acts aimed at the owner's
    data, the legacy worktree and the git gates; text that only mentions them passes (the live data folder
    excepted), and commands built on purpose to slip past the parser are a stated limit (PROCESS.md §11)."""
    if LIVE_NAME.search(command) or any(str(r).lower() in command.lower() for r in live_data_roots()):
        return "the owner's live data folder is off-limits to agents (INV-24)"
    here = cwd
    for toks in _split(command):
        cmd = os.path.basename(toks[0])
        args = toks[1:]
        plain = [a for a in args if not a.startswith("-")]
        if cmd == "cd":
            here = _resolve(plain[0], here) if plain else Path.home()
            continue
        # redirections, for every command, git included (BL-0055)
        for i, t in enumerate(toks):
            target = None
            if t in (">", ">>", "1>", "2>", "&>") and i + 1 < len(toks):
                target = toks[i + 1]
            elif re.match(r"^\d?>>?[^>&]", t):
                target = re.sub(r"^\d?>>?", "", t)
            elif t.startswith("of="):
                target = t[3:]
            if target and path_problem(target, here, writing=True):
                return path_problem(target, here, writing=True)
        if cmd == "git":
            if problem := _git_problem(toks, here):
                return problem
            continue
        if cmd == "find":
            start = [a for a in args if not a.startswith(("-", "(", "!"))][:1]
            root = _resolve(start[0], here) if start else here
            if any(_is_ancestor(root, r) or _under(root, r) for r in live_data_roots()):
                return "find over a folder that contains the owner's live data is refused; search inside the repo"
            if any(a in ("-delete", "-exec", "-execdir", "-ok") for a in args) and any(_under(root, r) for r in legacy_roots()):
                return "find -delete/-exec in the legacy worktree is refused; it is read-only"
        writes = False
        if cmd in WRITE_ANY_ARG or cmd in ("sed", "perl") and any(a.startswith("-i") or a == "--in-place" for a in args):
            writes = any(path_problem(a, here, writing=True) for a in plain) or any(
                _under(here, r) for r in legacy_roots())
        elif cmd in WRITE_LAST_ARG:
            dest = None
            if "-t" in args and args.index("-t") + 1 < len(args):
                dest = args[args.index("-t") + 1]
            elif plain:
                dest = plain[-1]
            writes = bool(dest and path_problem(dest, here, writing=True))
            if cmd == "mv" and any(path_problem(a, here, writing=True) for a in plain):
                writes = True
        elif cmd in ("npm", "pnpm", "yarn", "pip", "uv") and "--prefix" in args:
            i = args.index("--prefix")
            writes = i + 1 < len(args) and bool(path_problem(args[i + 1], here, writing=True))
        if writes:
            return f"{cmd} would write into the legacy worktree (read-only) or the live data folder; refused"
        for a in plain:
            if (p := path_problem(a, here, writing=False)) and "live data" in p:
                return p
        if any(_under(here, r) for r in legacy_roots()) and cmd not in READONLY_CMDS and cmd not in ("sed", "find"):
            return f"running {cmd} inside the legacy worktree is refused (it may write); read it from outside"
    return None
