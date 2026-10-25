"""Shared rules for the Claude Code guard hooks (INV-24): keep agents away from the owner's real data.

These guards are a safety net, not a sandbox: they catch the common and the known-clever ways an agent
could touch the live data folder, delete ignored files, write into the read-only legacy worktree, stop or
call the owner's running app, or switch off the git gates (bypasses found by review:G are meta-tested in
scripts/tests/test_guards.py). The rules are in design/process/PROCESS.md §8 ("Agents and real data").
Commands are read as the shell reads them (`shell_lex`, BL-0042): text the shell only hands to a sink on an
allowlist is not refused for its words; everything it may execute is checked, including substitutions,
`-c` strings, `eval` and heredocs fed to a shell. Code inside a heredoc or `-c` string for another interpreter
(Python, node) gets the text checks only, as a script written with the Write tool and run would.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path

import shell_lex

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
    path = Path(os.path.expandvars(p)).expanduser()
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


WRAPPERS = {"sudo", "env", "command", "builtin", "exec", "nohup", "time", "xargs", "nice"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
MAX_DEPTH = 8


def _command(words: list[str]) -> list[str]:
    """The words of a simple command without `VAR=value` prefixes and wrappers such as sudo or xargs."""
    toks = list(words)
    while toks and re.match(r"^\w+=", toks[0]):
        toks = toks[1:]
    while toks and toks[0] in WRAPPERS:
        toks = toks[1:]
    return toks


def _message_values(toks: list[str]) -> set[int]:
    """Indexes of message values the shell passes along as plain data: `git commit -m/--message` and
    `gh … --title/--body/-t/-b`. Everything else, flags included, is still checked."""
    out: set[int] = set()
    if not toks:
        return out
    name = os.path.basename(toks[0])
    if name == "git":
        _, sub, args = _git_parts(toks)
        if sub != "commit":
            return out
        first = len(toks) - len(args)
        long_flags, short = ("--message",), "m"
    elif name == "gh":
        first, long_flags, short = 1, ("--title", "--body"), "tb"
    else:
        return out
    i = first
    while i < len(toks):
        t = toks[i]
        if t in long_flags:
            out.add(i + 1)
            i += 2
            continue
        if any(t.startswith(f + "=") for f in long_flags):
            out.add(i)
        elif re.match(r"^-[A-Za-z]+$", t) or (name == "git" and re.match(r"^-[A-Za-z]*m", t)):
            cluster = t[1:]
            hit = next((k for k, ch in enumerate(cluster) if ch in short), None)
            if hit is not None and name == "git" and hit + 1 < len(cluster):
                pass  # -mVALUE: the value is inside this word; keep the word (its flags must stay visible)
            elif hit is not None and hit == len(cluster) - 1:
                out.add(i + 1)
                i += 2
                continue
        i += 1
    return {k for k in out if k < len(toks)}


def _heredoc_is_data(toks: list[str], seg: shell_lex.Segment, output_is_data: bool) -> bool:
    """A quoted heredoc body is plain data only when it goes to a sink on this list and nowhere else."""
    if not toks or seg.procsub:
        return False
    name = os.path.basename(toks[0])
    if name in ("cat", "tee"):
        return output_is_data and not seg.piped
    if name == "git":
        _, sub, args = _git_parts(toks)
        return sub == "commit" and ("--file=-" in args or any(
            a in ("-F", "--file") and args[k + 1:k + 2] == ["-"] for k, a in enumerate(args)))
    return False


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


def _text_problem(text: str) -> str | None:
    """Checks on the raw text the shell may execute: the live data folder by any spelling, the owner's port,
    and process-stopping words next to the owner's processes."""
    if LIVE_NAME.search(text) or any(str(r).lower() in text.lower() for r in live_data_roots()):
        return "the owner's live data folder is off-limits to agents (INV-24)"
    if OWNER_URL.search(text):
        return f"port {OWNER_PORT} is the owner's running app; never call it (INV-24). Use your own instance"
    if re.search(r"\b(kill|pkill|killall|fuser)\b", text) and OWNER_PROC.search(text):
        return "this could stop the owner's running app; kill only processes you started, by PID"
    return None


def bash_problem(command: str, cwd: Path, output_is_data: bool = True, depth: int = 0) -> str | None:
    """Why this shell command is refused, or None if it may run.

    The command is lexed as a shell reads it (`shell_lex`). The text checks see everything the shell may
    execute; they skip only text it passes along as data to a sink: a quoted heredoc fed to `cat`/`tee`
    (not piped on) or to `git commit -F -`, and the message values of `git commit -m` and `gh --title/--body`.
    Command substitutions, `bash -c`/`sh -c`/`eval` strings and heredocs fed to a shell are checked as commands.
    `output_is_data` says whether this command's output is itself plain data (true at the top level and inside
    a substitution that is a message value; false where the output becomes code, as in `bash -c "$(…)"`)."""
    if depth > MAX_DEPTH:
        return "the command nests too deeply for the guard to read; refused to be safe"
    try:
        segments = shell_lex.lex(command)
    except shell_lex.LexError as e:
        return f"the guard cannot read this command ({e}); refused to be safe"
    texts: list[str] = []
    nested: list[tuple[str, bool]] = []  # (command, output_is_data)
    for seg in segments:
        toks = _command(seg.words)
        skip = _message_values(toks)
        texts.append(" ".join(w for k, w in enumerate(toks) if k not in skip))
        texts += [w for w in seg.words[:len(seg.words) - len(toks)]]  # VAR=value prefixes and wrappers
        name = os.path.basename(toks[0]) if toks else ""
        for doc in seg.heredocs:
            if doc.quoted and _heredoc_is_data(toks, seg, output_is_data):
                continue
            if name in SHELLS or name in ("source", ".", "cat", "tee"):
                # fed to a shell, or passed on by cat/tee to somewhere it may run: read it as a command too
                nested.append((doc.body, False))
            texts.append(doc.body)
        message_sink = bool(skip) or (name == "git" and _git_parts(toks)[1] == "commit")
        nested += [(sub, message_sink and not seg.piped) for sub in seg.subs]
        if name in SHELLS:
            # -c, also inside a flag cluster such as -lc or -ec: the next word is a command string
            k = next((k for k, t in enumerate(toks[1:], 1) if re.match(r"^-[A-Za-z]*c[A-Za-z]*$", t)), None)
            if k is not None and k + 1 < len(toks):
                nested.append((toks[k + 1], False))
        if name == "eval":
            nested.append((" ".join(toks[1:]), False))
    if problem := _text_problem("\n".join(texts)):
        return problem
    for sub, data in nested:
        if problem := bash_problem(sub, cwd, data, depth + 1):
            return problem
    here = cwd
    for seg in segments:
        toks = _command(seg.words)
        if not toks:
            continue
        skip = _message_values(toks)
        toks = [w for k, w in enumerate(toks) if k not in skip]
        cmd = os.path.basename(toks[0])
        args = toks[1:]
        plain = [a for a in args if not a.startswith("-")]
        if cmd == "cd":
            here = _resolve(plain[0], here) if plain else Path.home()
            continue
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
        # redirections
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
