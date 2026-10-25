"""Shared rules for the Claude Code guard hooks (INV-24): keep agents away from the owner's real data.

These guards are a safety net, not a sandbox: they catch the common and the known-clever ways an agent
could touch the live data folder, delete ignored files, write into the read-only legacy worktree, stop or
call the owner's running app, or switch off the git gates (bypasses found by review:G are meta-tested in
scripts/tests/test_guards.py). The rules are in design/process/PROCESS.md §8 ("Agents and real data").
"""

from __future__ import annotations

import glob
import os
import re
import shlex
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OWNER_PORT = "8001"


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


OWNER_URL = re.compile(rf"(?i)(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|::1)\s*:\s*{OWNER_PORT}\b")
KILLERS = {"kill", "pkill", "killall", "fuser"}
OWNER_PROC = re.compile(rf"(?i)\b{OWNER_PORT}\b|app\.main|run-v0\.3|python|uvicorn|backend|finzytrack|beancount")
READONLY_GIT = {"log", "show", "diff", "status", "grep", "ls-files", "ls-tree", "rev-parse", "cat-file", "blame",
                "describe", "shortlog", "reflog", "rev-list", "name-rev", "for-each-ref"}
READONLY_CMDS = {"ls", "cat", "head", "tail", "grep", "rg", "wc", "file", "stat", "du", "diff", "cmp", "less", "more",
                 "awk", "cut", "sort", "uniq", "tree", "pwd", "echo", "printf", "true", "test", "[", "md5", "shasum",
                 "sha256sum", "realpath", "readlink", "basename", "dirname", "jq", "cd"}
WRITE_LAST_ARG = {"mv", "cp", "rsync", "ln", "install", "scp"}
WRITE_ANY_ARG = {"rm", "rmdir", "unlink", "truncate", "shred", "chmod", "chown", "chflags", "touch", "tee", "mkdir",
                 "xattr", "setfacl"}


def _join_continuations(command: str) -> str:
    return command.replace("\\\n", " ")


def _split(command: str) -> list[list[str]]:
    segs: list[list[str]] = []
    for part in re.split(r"\|\||&&|;|\||\n|\$\(|`|\)", command):
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


GATED_GIT = {"commit", "push", "add", "clean", "stash", "tag", "config", "worktree"}
SINGLE = re.compile(r"'[^']*'")
DOUBLE = re.compile(r'"(?:[^"\\]|\\.)*"')
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)


def _unquoted(command: str) -> str:
    """The command with heredoc bodies and quoted strings blanked: what the shell word-splits."""
    text = HEREDOC.sub("<<HEREDOC\n", command)
    return DOUBLE.sub('""', SINGLE.sub("''", text))


def _expansion_in_gated_git(command: str) -> bool:
    """An unquoted $-expansion or backtick in the same simple command as a gated git subcommand (BL-0055): its
    output is word-split into arguments, so it can carry a flag (`git commit -m x $(echo --no-verify)`)."""
    for part in re.split(r"\|\||&&|;|\||\n", _unquoted(command)):
        words = part.split()
        if "git" not in [os.path.basename(w) for w in words]:
            continue
        i = [os.path.basename(w) for w in words].index("git")
        _, sub, _ = _git_parts(words[i:])
        if sub in GATED_GIT and re.search(r"\$|`", part):
            return True
    return False


def bash_problem(command: str, cwd: Path) -> str | None:
    """Why this shell command is refused, or None if it may run."""
    # Heredoc bodies are data; quoted text is not skipped (bash opens a quoted /dev/tcp redirect target too).
    if re.search(r"/dev/(tcp|udp)/", HEREDOC.sub("<<HEREDOC\n", command)):
        return "bash network redirects (/dev/tcp, /dev/udp) are refused (BL-0055); use a real client on your own instance"
    if _expansion_in_gated_git(_join_continuations(command)):
        return ("an unquoted $-expansion or backtick next to git commit/push/add/clean/stash/tag/config/worktree is "
                "refused: its output could carry flags (BL-0055); quote it, or run the commands separately")
    if LIVE_NAME.search(command) or any(str(r).lower() in command.lower() for r in live_data_roots()):
        return "the owner's live data folder is off-limits to agents (INV-24)"
    if OWNER_URL.search(command):
        return f"port {OWNER_PORT} is the owner's running app; never call it (INV-24). Use your own instance"
    if re.search(r"\b(kill|pkill|killall|fuser)\b", command) and OWNER_PROC.search(command):
        return "this could stop the owner's running app; kill only processes you started, by PID"
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
        if cmd in KILLERS:
            if cmd == "fuser" and "k" in _short_flags(args):
                return "fuser -k could stop the owner's running app; kill only processes you started, by PID"
            if any(a == "-1" or re.match(r"^-\w+$", a) and a.endswith("1") and len(args) > 1 and args[-1] == "-1" for a in args) \
                    or "-1" in args:
                return "kill -1 signals every process; refused"
            if OWNER_PROC.search(" ".join(args)) or cmd in ("pkill", "killall") and not plain:
                return "this could stop the owner's running app; kill only processes you started, by PID"
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
