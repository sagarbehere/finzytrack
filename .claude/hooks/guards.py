"""Shared rules for the Claude Code guard hooks (INV-24): keep agents away from the owner's real data.

These guards are a safety net, not a sandbox: they catch the common ways an agent could touch the live data
folder, delete ignored files, write into the read-only legacy worktree, or switch off the git gates (meta-tested
in scripts/tests/test_guards.py). Commands built on purpose to slip past the parser are a stated limit, not a
target (S0054). The owner's running app is not guarded: agents never stop, start or call it, and ask the owner
when it is in their way (INV-24, a working rule; owner, 2026-10-29). The rules are in design/process/PROCESS.md §8.

The first line since 2026-11-21 is Claude Code's sandbox (`.claude/settings.json`): the OS refuses every agent shell
write outside the checkout, the temp folder and the tool caches, and the live data folder and the legacy worktree by
name (owner, 2026-11-21: "Sandbox + land (Recommended)", "Finzytrack only (Recommended)";
design/inbox/answered/20261121-guard-shapes-or-sandbox.md). This guard is the second line. The sandbox does not
cover the commands its settings exempt (`excludedCommands`: `git push`, and `gh api`, `gh workflow run`, `gh run
list/view/watch`, which can change things on GitHub, though not the local disk); for them this guard is the only line.

Generated files (INV-19, BL-0307): a write to a file with a GENERATED first line is refused, except a copy in the
system temp folder (`$TMPDIR`, `/tmp`, `/private/var/folders`) that is outside the repository by every spelling (a
symlink, `..`, a case variant, a relative path) and has one link; such a copy, a reviewer's for example, may be
written or removed. Elsewhere the rule refuses any GENERATED file, as before S0139, since macOS spells a repository
file in ways a path comparison cannot see (a hard link, the data volume's firmlink path, `/.vol/<dev>/<inode>`).

Option values: the value of a glued `--opt=VALUE` is checked for the live data folder; for a writer, the value of an
option that names where it writes (`DEST_OPTS`: `--prefix=`, `--target=`, `--target-directory=`, ...), and of `-tDIR`
for cp, mv, ln and install, is checked by every write rule.

Stated limits (S0139; the guard is a safety net, PROCESS.md §11; the sandbox covers the two real-data folders):
- Shapes the parser cannot see (S0139 round 2, R2-2 and R2-3, behind the sandbox; owner, 2026-11-21: "Sandbox + land
  (Recommended)"): git's own path options and a path git writes (`git -C DIR`, `git --work-tree=DIR`, `git add`), short
  glued options (`curl -oFILE`, `tar -CDIR`, `sort -oFILE`, `cp -rtDIR`), value options in the space form or by an
  alias (`pip install --target DIR`, `pip -t DIR`, `pnpm -C DIR`, `pnpm --dir DIR`, `yarn --cwd DIR`, `npm -C DIR`,
  `uv --directory DIR`), and a firmlink spelling of the legacy worktree. A list of shapes cannot be complete.
- A hard link's name outside the repository (or anywhere outside the temp folder) is refused for `rm` too, though
  removing a name changes no byte of the other (R2-5; the base refused it as well).
- A glued option that writes but is not in `DEST_OPTS` (`--log-file=`, `--temp-dir=`, `--partial-dir=`,
  `--write-batch=`, `--cache-dir=`, `--target-dir=`, `--wheel-dir=`, `--in-place=` and more) is checked only for the
  live data folder, so its value may name the legacy worktree, `.git/config` or a generated file (S0139 round 3,
  V3-1; round 1 refused these, before R2-4 narrowed the check to the list; owner, 2026-11-21: "Okay, let's go with
  option 1"). The sandbox refuses writes to the legacy worktree and to `.git/config`, and `scripts/check_generated.py`
  catches a changed generated file at commit.
- A write aimed at a generated file's folder, not at the file (`cp page.md design/generated/`, `rm -r
  design/generated`), is not seen: `scripts/check_generated.py`, INV-19's gate, catches the drift at commit and push.
- In-place edits spelled other than `sed -i...`/`perl -i...`/`--in-place` (`perl -pi -e`, `sed -Ei`) are not read as
  writes, so they are not refused in the legacy worktree (owner, 2026-11-21: "Stated limit only"; a list of shapes
  cannot be complete, and the worktree is under git, so a stray write is visible and reversible). The live data
  folder is still refused for them: any argument naming it is refused, read or write.
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


def repo_roots() -> list[Path]:
    """The repository whose generated files INV-19 covers: the checkout this hook lives in, plus a test's stand-in
    (`FT_GUARD_REPO` only adds a folder, so it can only refuse more)."""
    roots = [REPO]
    if os.environ.get("FT_GUARD_REPO"):
        roots.append(Path(os.environ["FT_GUARD_REPO"]).expanduser())
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
    """Why an agent may not read or write this path, or None if it may. The live data folder is refused for reading
    and writing; the legacy worktree, `.git/config` and `.git/hooks`, `corpora/MANIFEST.tsv` and a generated file
    (except a one-link copy in the temp folder, outside the repository) for writing. Limits: the module docstring."""
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
        # INV-19 covers the repository's generated files. The one exception is a copy in the system temp folder,
        # such as a reviewer's (BL-0307): outside the repository by every spelling, and with one link. Anything else
        # with a GENERATED first line stays refused, as before S0139, since macOS spells a repository file in ways
        # a path comparison cannot see (a hard link, the data volume's firmlink path, `/.vol/<dev>/<inode>`).
        if writing and p.is_file() and _generated(p) and not _a_temp_copy(p):
            return "this file is generated (GENERATED header); edit its source and rerun the generator (INV-19)"
    return None


def temp_roots() -> list[Path]:
    """The system temp folder by its names: `$TMPDIR`, `/tmp` (macOS: `/private/tmp`) and `/private/var/folders`."""
    roots = [Path("/tmp"), Path("/private/tmp"), Path("/private/var/folders")]
    if os.environ.get("TMPDIR"):
        roots.append(Path(os.environ["TMPDIR"]))
    return roots


def _a_temp_copy(p: Path) -> bool:
    """A generated file's copy that may be written or removed (S0139 R2-1): in the temp folder, outside the
    repository, and its only link (a hard link may be the repository's file by another name, F1)."""
    return (any(_under(p, r) for r in temp_roots()) and not any(_under(p, r) for r in repo_roots())
            and _links(p) == 1)


def _links(p: Path) -> int:
    try:
        return os.stat(p).st_nlink
    except OSError:
        return 2  # cannot tell: refuse, as a hard link would be


GLUED = re.compile(r"^--([\w-]+)=(.+)$")  # `--opt=VALUE`
GLUED_T = {"cp", "mv", "ln", "install"}  # their `-tDIR` names the destination folder
# Options whose value is where a command writes. Other glued values are read (`--exclude-from=`, `--reference=`,
# `--requirement=`), so they are checked only for the live data folder (S0139 R2-4).
DEST_OPTS = {"prefix", "target", "target-directory", "root", "dir", "directory", "cwd", "output", "output-dir",
             "out-dir", "outdir", "backup-dir", "work-tree", "git-dir", "install-dir", "dest", "destination"}


def _glued(cmd: str, args: list[str], dest_only: bool = False) -> list[str]:
    """The values glued to their options: `--opt=VALUE`, and `-tDIR` where `-t` names a folder (S0139 F3-live);
    with `dest_only`, only the options that name where the command writes."""
    values = [m.group(2) for a in args if (m := GLUED.match(a)) and (not dest_only or m.group(1) in DEST_OPTS)]
    if cmd in GLUED_T:
        values += [a[2:] for a in args if re.match(r"^-t.", a)]
    return values


def _first_problem(args: list[str], cwd: Path) -> str | None:
    """The first argument a writer may not write, with its own reason."""
    for a in args:
        if problem := path_problem(a, cwd, writing=True):
            return f"{a}: {problem}"
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
        # Writers: the refusal names its own reason (BL-0307), not one text for every rule.
        problem = None
        glued = _glued(cmd, args, dest_only=True)  # where an option says it writes (S0139 F3-live, R2-4)
        if cmd in WRITE_ANY_ARG or cmd in ("sed", "perl") and any(a.startswith("-i") or a == "--in-place" for a in args):
            problem = _first_problem(plain + glued, here)
            if not problem and any(_under(here, r) for r in legacy_roots()):
                problem = "it runs inside the legacy worktree, which is read-only"
        elif cmd in WRITE_LAST_ARG:
            dest = None
            if "-t" in args and args.index("-t") + 1 < len(args):
                dest = args[args.index("-t") + 1]
            elif plain:
                dest = plain[-1]
            problem = _first_problem(([dest] if dest else []) + glued, here)
            if not problem and cmd == "mv":
                problem = _first_problem(plain, here)
        elif cmd in ("npm", "pnpm", "yarn", "pip", "uv"):
            prefix = args[args.index("--prefix") + 1:args.index("--prefix") + 2] if "--prefix" in args else []
            problem = _first_problem(prefix + glued, here)  # `--prefix DIR`, `--prefix=DIR`, `--target=DIR`
        if problem:
            return f"{cmd} refused: {problem}"
        for a in plain + _glued(cmd, args):  # every option value, for the live data folder
            if (p := path_problem(a, here, writing=False)) and "live data" in p:
                return p
        if any(_under(here, r) for r in legacy_roots()) and cmd not in READONLY_CMDS and cmd not in ("sed", "find"):
            return f"running {cmd} inside the legacy worktree is refused (it may write); read it from outside"
    return None
