"""D2 on copies of your own ledgers (the local tier; S0158): the engine's overlay load gives exactly Beancount's own
result (ADR-0010 guardrail 1, ADR-0046; roadmap M1b exit).

How to run it (from the repository's `engine/` folder, with the engine's environment):

  1. Copy each ledger, with every file it includes, its plugins and its documents, into one folder outside the
     repository, as a plain copy that follows links (never your live data folder), for example:

       cp -RL ~/MyLedgers/personal ~/ft-local-tier/personal

     The run refuses a folder that is not a plain, self-contained copy (see "What it reads"); edit the lines it
     names in the copy, never in your ledger.
  2. Run, naming each copy's root file:

       FT_LOCAL_TIER_DIR=~/ft-local-tier .venv/bin/python scripts/d2_local.py \\
           ~/ft-local-tier/personal/main.beancount ~/ft-local-tier/business/ledger.beancount

It prints one line per ledger, numbered L1, L2 … in the order given, and a total:

  L1 EQUAL entries=5821 errors=0 options=56
  L2 DIFFERENT entries=1203 errors=2 options=56 differ: candidates.entries=1 plain.entries=1
  d2-local: 1 EQUAL, 1 DIFFERENT, 0 REFUSED, 0 ERROR

`entries`, `errors` and `options` count what Beancount's own load gave. A DIFFERENT line names what differed as counts:
`plain.*` the overlay load of the files as they are, `candidates.*` the overlay load with every file's bytes given as
candidate text (as a save's preview gives the files it changes); `entries` and `errors` the positions that differ,
`options` the option keys, `refused` an overlay load that refused (`parity.differences`, the comparison the corpus
tests use). Nothing else is printed: no amount, account, payee, narration, file name or path (INV-23, PROCESS.md §8).
Exit status: 0 when every ledger is EQUAL, 2 when an input is refused, 1 otherwise (DIFFERENT or ERROR).

**What it reads.** Only files inside `FT_LOCAL_TIER_DIR` (INV-24), and only a plain, self-contained copy (owner,
2026-11-26: "Strict plain-copy rule (Recommended)"). The folder must be absolute, existing, and outside the
repository, the live data folder and the legacy worktree, and each root a file inside it (the oracle harness's rule,
`scripts/oracle/copies.py`). Before any load, the whole folder is checked (`refuse_tree`), and any of these refuses
the whole run, exit 2, nothing loaded:
  - a link of any kind, a file with a second hard link, or anything that is neither a file nor a folder: make the
    copy again with `cp -RL`;
  - a folder that cannot be listed or a file that cannot be read;
  - in any file, an `option "include"` line, whatever its value: write it as an `include` line in the copy;
  - in any file, an `include` line or an `option "documents"` value that is absolute, starts with `~` or `$`, has
    `..` in it as a folder, or holds a backslash: edit that line in the copy to a path relative to its file, below
    it.
  This is a plain text scan, looser than Beancount's grammar: a commented-out line or a binary file can cause a
  refusal too.
Then the overlay load runs under a policy that refuses any file, `pythonpath` folder or documents root outside the
folder before it is read (a second line); Beancount's load runs only after that, and follows the same includes. The
ledger's own process checks the folder again before it loads. Limits: a change made to the folder between the check
and the loads is not seen (do not edit the copy during a run); a `document` directive's file is checked for
existence, not read, wherever it is; a plugin's own code may read anything: it runs as Beancount runs it.

**What it writes.** Nothing: no mirror, no cache, no pickle (Beancount's load without its pickle cache,
`parity.beancount_load`), no bytecode (this process and each ledger's child write none).

**One load with plugins per engine process** (engine/CLAUDE.md): each ledger is compared in a fresh process, which runs
three loads (the overlay's two and Beancount's) and drops every module imported from inside the folder between them,
as the corpus tests do (`parity.fresh_plugins`). Stated limits: a plugin's state outside its own modules (a module of
the engine's environment it changes) is shared by the three loads; a plugin that leaves a process running that holds
the child's output open holds the run; an `ERROR` gives a fixed reason, never the failure's own message (it could
hold ledger text).
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, cast

if __name__ == "__main__":
    sys.dont_write_bytecode = True  # before the imports below: no bytecode from this run (the child also has `-B`)

ENGINE = Path(__file__).resolve().parents[1]
# The engine's source and tests beside this script (the comparison lives in the engine's tests, `tests.ledger.parity`),
# and the oracle harness's copy rule (`scripts/oracle/copies.py`, standard library only).
sys.path[:0] = [str(ENGINE / "src"), str(ENGINE), str(ENGINE.parent / "scripts" / "oracle")]

import copies  # noqa: E402

from ledger_engine.fs import this_os  # noqa: E402
from ledger_engine.ledger import fresh_load, overlay  # noqa: E402
from ledger_engine.ledger.core.ledger_set import Content  # noqa: E402
from ledger_engine.ledger.overlay import Loaded, LoadRefused, LoadResult  # noqa: E402

# isort: split
from tests.ledger import parity  # noqa: E402  (first-party from engine/, third-party from the root: kept apart)

type Verdict = Literal["EQUAL", "DIFFERENT", "REFUSED", "ERROR"]
VERDICTS: Final[tuple[Verdict, ...]] = ("EQUAL", "DIFFERENT", "REFUSED", "ERROR")
CHECKS: Final = ("plain", "candidates")
KINDS: Final = frozenset(f"{check}.{kind}" for check in CHECKS for kind in parity.KINDS)

REASONS: Final[Mapping[str, str]] = {
    "": "",
    "tier": (
        "FT_LOCAL_TIER_DIR must be set to an absolute path of an existing folder outside the repository, the live data "
        "folder and the legacy worktree"
    ),
    "root": "not a file inside FT_LOCAL_TIER_DIR (or it is inside the repository, the live data folder or the legacy "
    "worktree)",
    "link": "FT_LOCAL_TIER_DIR holds a link; make a plain copy (cp -RL) instead",
    "hardlink": "a file in FT_LOCAL_TIER_DIR has a second hard link; make a plain copy (cp -RL) instead",
    "special": "FT_LOCAL_TIER_DIR holds something that is neither a file nor a folder; remove it from the copy",
    "unreadable": "a file or folder in FT_LOCAL_TIER_DIR cannot be listed or read",
    "option-include": 'a file in FT_LOCAL_TIER_DIR uses option "include"; write it as an include line in the copy',
    "reaches-out": "an include or documents path in FT_LOCAL_TIER_DIR is absolute or has '..', '~', '$' or a "
    "backslash; edit that line in the copy",
    "read-refused": "the ledger reads a file outside FT_LOCAL_TIER_DIR; copy the whole ledger into it",
    "pythonpath-refused": "the ledger's pythonpath is outside FT_LOCAL_TIER_DIR; copy the whole ledger into it",
    "documents-refused": "a documents folder of the ledger is outside FT_LOCAL_TIER_DIR; copy it in, or remove the "
    "option from the copy",
    "encrypted": "the ledger has an encrypted file, which Finzytrack never decrypts (ADR-0059)",
    "beancount-failed": "Beancount's own load fails on this ledger (bean-check fails too)",
    "crash": "the check itself failed",
}
"""Every reason a line may give: fixed words, never a path or ledger text."""


@dataclass(frozen=True, slots=True)
class Report:
    """One ledger's result: only a verdict, counts, kinds from `KINDS` and a reason from `REASONS`."""

    verdict: Verdict
    entries: int = 0
    errors: int = 0
    options: int = 0
    kinds: dict[str, int] = field(default_factory=dict[str, int])
    reason: str = ""

    def encode(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "entries": self.entries,
            "errors": self.errors,
            "options": self.options,
            "kinds": self.kinds,
            "reason": self.reason,
        }

    @staticmethod
    def decode(raw: object) -> Report:
        """A child's answer, checked field by field: anything but a verdict, counts, known kinds and a known reason is
        an ERROR, so a line can never echo text the child (or a plugin in it) wrote."""
        try:
            return Report._decode(raw)
        except (TypeError, ValueError, KeyError):  # an unhashable reason or verdict, among others (S0158 V1-5)
            return Report("ERROR", reason="crash")

    @staticmethod
    def _decode(raw: object) -> Report:
        crash = Report("ERROR", reason="crash")
        if not isinstance(raw, dict):
            return crash
        d = cast("dict[str, object]", raw)
        verdict, reason, kinds = d.get("verdict"), d.get("reason"), d.get("kinds")
        counts = [d.get(k) for k in ("entries", "errors", "options")]
        if verdict not in VERDICTS or reason not in REASONS or not isinstance(kinds, dict):
            return crash
        kinds = cast("dict[object, object]", kinds)
        if not all(_count(c) for c in counts) or not all(k in KINDS and _count(v) for k, v in kinds.items()):
            return crash
        n = cast("list[int]", counts)
        return Report(verdict, n[0], n[1], n[2], cast("dict[str, int]", kinds), cast("str", reason))


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def line(case: str, report: Report) -> str:
    """The printed line: the case ID, the verdict, counts and fixed words only."""
    if report.verdict in ("REFUSED", "ERROR"):
        return f"{case} {report.verdict} ({REASONS[report.reason]})"
    out = f"{case} {report.verdict} entries={report.entries} errors={report.errors} options={report.options}"
    if report.kinds:
        out += " differ: " + " ".join(f"{k}={v}" for k, v in sorted(report.kinds.items()))
    return out


# ---- what may be read --------------------------------------------------------------------------------------------


def refused(path: Path) -> bool:
    """Whether `path` is outside the T-local folder (or in the repository, the live data folder or the legacy worktree,
    or the folder itself is unusable): `copies.refuse_local_tier_source` with no exception for the repo's corpora."""
    return copies.refuse_local_tier_source(path, allow_repo_corpora=False) is not None


def refuse_root(root: Path) -> str | None:
    """The reason (a `REASONS` key) a root given on the command line is refused, or None: the T-local folder must be
    usable, and the root a file inside it, links resolved."""
    try:
        tier = copies.local_tier_dir()[0]
        if tier is None:
            return "tier"
        if not root.is_absolute() or not root.is_file() or refused(root):  # links in the root's path resolved
            return "root"
    except Exception:  # noqa: BLE001 (any failure refuses, told by a fixed reason, never by its message)
        return "root"
    return refuse_tree(tier)


# Looser than Beancount's grammar on purpose (a false refusal is fine, a miss is not): the keyword anywhere not inside a
# word, any whitespace before the string, a string over several lines.
_STRING = rb'"((?:[^"\\]|\\.)*)"'
PATHS = re.compile(rb'(?<![\w-])(?:include|option\s*"documents")\s*' + _STRING, re.S)
OPTION_INCLUDE = re.compile(rb'(?<![\w-])option\s*"include"')


def refuse_tree(tier: Path) -> str | None:
    """Why the T-local folder is not a plain, self-contained copy, before any load reads it (owner, 2026-11-26: "Strict
    plain-copy rule (Recommended)"; S0158 round 2), or None. Every folder is listed and every entry looked at without
    following anything, and the first of these refuses the whole run:
    - a link of any kind, inside or out (`link`; `cp -RL` copies what a link points to);
    - a file with a second hard link (`hardlink`), or an entry that is neither a file nor a folder (`special`);
    - a folder that cannot be listed or a file that cannot be read, or any other failure of the check (`unreadable`);
    - in any file's text, `option "include"` with any value (`option-include`; the owner's rule names it outright);
    - in any file's text, an `include` line or an `option "documents"` value that is absolute, starts with `~` or `$`,
      has `..` as a segment, or holds a backslash (`reaches-out`): with none of these and no link, a relative path (a
      glob included) names only things under the file's folder, or for `documents` under the root's, both inside the
      T-local folder.
    A plain text scan of every file, not a parse, looser than Beancount's grammar: a commented-out line, a string that
    is not a directive or a binary file can refuse a ledger that would load only from inside (a false refusal, by
    design); a line Beancount reads as an include is not missed. `insert_pythonpath` adds the folder of the file that
    sets it, inside the folder. The tier's own path may be spelled through a link (`/tmp`); nothing below it may.
    Limit: the folder is checked, then loaded; a change made to it between the two (by the owner) is not seen."""
    try:
        return _first_problem(tier)
    except Exception:  # noqa: BLE001 (any failure refuses, told by a fixed reason, never by its message)
        return "unreadable"


def _first_problem(tier: Path) -> str | None:
    folders = [str(tier)]
    while folders:
        with os.scandir(folders.pop()) as listing:
            entries = list(listing)
        for entry in entries:
            if entry.is_symlink():
                return "link"
            st = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                folders.append(entry.path)
            elif not stat.S_ISREG(st.st_mode):
                return "special"
            elif st.st_nlink > 1:
                return "hardlink"
            else:
                with open(entry.path, "rb") as f:
                    data = f.read()
                if OPTION_INCLUDE.search(data):
                    return "option-include"
                if any(_leaves(m.group(1)) for m in PATHS.finditer(data)):
                    return "reaches-out"
    return None


def _leaves(value: bytes) -> bool:
    """Whether a path as written could name something outside its file's folder."""
    if b"\\" in value or value.startswith((b"/", b"~", b"$")) or re.match(rb"[A-Za-z]:", value):
        return True
    return b".." in re.split(rb"/", value)


@dataclass(frozen=True, slots=True)
class TierPolicy:
    """The overlay load's policy here: every file, `pythonpath` folder and documents root inside the T-local folder.
    Plugins are allowed: a user plugin's module is found on the `pythonpath` this policy confines (or in the engine's
    own environment)."""

    def may_read(self, path: str) -> bool:
        return not refused(Path(path))

    def may_run_plugin(self, name: str) -> bool:
        return True

    def may_add_pythonpath(self, path: str) -> bool:
        return not refused(Path(path))

    def may_scan_documents(self, path: str) -> bool:
        return not refused(Path(path))


# ---- one ledger (in its fresh process) ---------------------------------------------------------------------------


def measure(root: Path) -> Report:
    """One ledger's comparison: the overlay load of the files as they are, under `TierPolicy` (a refusal of the policy,
    or of an encrypted file, stops here: Beancount's load is not run); Beancount's own load; the overlay load with every
    file's bytes as candidates. Each load forgets the modules it imported from the T-local folder."""
    tier, _ = copies.local_tier_dir()
    if tier is None:
        return Report("REFUSED", reason="tier")
    if (why := refuse_root(root)) is not None:
        return Report("REFUSED", reason=why)
    policy = TierPolicy()
    with parity.fresh_plugins(tier):
        plain = overlay.load(str(root), {}, policy)
    if isinstance(plain, LoadRefused) and plain.reason in ("read-refused", "pythonpath-refused", "documents-refused"):
        return Report("REFUSED", reason=plain.reason)
    if isinstance(plain, LoadRefused) and plain.reason == "encrypted":
        return Report("REFUSED", reason="encrypted")
    try:
        with parity.fresh_plugins(tier):
            expected = parity.beancount_load(root)
    except Exception:  # noqa: BLE001 (Beancount's own load raised: told by a fixed reason, never by its message)
        return Report("ERROR", reason="beancount-failed")
    checks: list[tuple[str, LoadResult]] = [("plain", plain)]
    if isinstance(plain, Loaded):
        # The files the overlay's parse read (the ones a save could edit), never a name a plugin added to `include`
        # (S0158 V1-3); a `.asc` file is refused as a candidate (ADR-0010 guardrail 4).
        files = [Path(f) for f, state in plain.fingerprint.files if isinstance(state, Content)]
        files = [f for f in files if not f.name.lower().endswith(".asc")]  # each one the policy let it read
        candidates = {str(f): f.read_bytes() for f in files}
        with parity.fresh_plugins(tier):
            checks.append(("candidates", overlay.load(str(root), candidates, policy)))
    kinds = {
        f"{check}.{kind}": n for check, result in checks for kind, n in parity.differences(result, expected).items()
    }
    entries, errors, options = expected
    return Report("DIFFERENT" if kinds else "EQUAL", len(entries), len(errors), len(options), kinds)


def child_main(root: Path) -> int:
    """The per-ledger process: its answer on the standard output it was given; what the loads or a plugin print goes
    nowhere (it could hold ledger text)."""
    answer = os.fdopen(os.dup(1), "w", encoding="utf-8")
    quiet = os.open(os.devnull, os.O_WRONLY)
    os.dup2(quiet, 1)
    os.dup2(quiet, 2)
    try:  # the placeholder mode pinned as every engine process pins it (ADR-0070); a no-op off Windows
        report = measure(root) if this_os.pin_placeholder_mode() else Report("ERROR", reason="crash")
    except BaseException:  # noqa: BLE001 (any failure is told as ERROR, never by its message)
        report = Report("ERROR", reason="crash")
    answer.write(json.dumps(report.encode()) + "\n")
    answer.flush()
    return 0


def in_fresh_process(root: Path) -> Report:
    """`measure` in a fresh process (one load with plugins per engine process): this interpreter with no bytecode
    written (`-B`, `PYTHONDONTWRITEBYTECODE`) and the environment the engine gives a load it starts
    (`fresh_load.environment`: `PYTHONHASHSEED=0`), plus the folders the copy rule reads."""
    env = fresh_load.environment()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in (copies.LOCAL_TIER_ENV, "FT_LIVE_DATA", "FT_LEGACY_ROOT"):
        if name in os.environ:
            env[name] = os.environ[name]
    done = subprocess.run(  # noqa: S603 (this interpreter and this script, no shell)
        [sys.executable, "-B", "-s", "-P", __file__, "--child", str(root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        check=False,
    )
    lines = done.stdout.decode("utf-8", errors="replace").splitlines()
    if done.returncode != 0 or not lines:
        return Report("ERROR", reason="crash")
    try:
        return Report.decode(json.loads(lines[-1]))
    except ValueError:
        return Report("ERROR", reason="crash")


def say(text: str) -> None:
    sys.stdout.write(text + "\n")


# ---- the run -----------------------------------------------------------------------------------------------------


def _guarded(child: Callable[[Path], Report], root: Path) -> Report:
    """One ledger's report; any failure of the run itself is an ERROR with a fixed reason, never a traceback."""
    try:
        return child(root)
    except Exception:  # noqa: BLE001 (told by a fixed reason, never by its message)
        return Report("ERROR", reason="crash")


def main(argv: list[str], child: Callable[[Path], Report] = in_fresh_process) -> int:
    if len(argv) == 2 and argv[0] == "--child":
        return child_main(Path(argv[1]))
    if not argv or any(a.startswith("-") for a in argv):
        sys.stderr.write("usage: FT_LOCAL_TIER_DIR=<folder> .venv/bin/python scripts/d2_local.py ROOT [ROOT ...]\n")
        return 2
    roots = [Path(a).expanduser() for a in argv]
    cases = [f"L{i}" for i in range(1, len(roots) + 1)]
    refusals = {case: why for case, root in zip(cases, roots, strict=True) if (why := refuse_root(root)) is not None}
    if refusals:  # every input is checked before any ledger is read
        for case, why in refusals.items():
            say(line(case, Report("REFUSED", reason=why)))
        say("d2-local: nothing was loaded")
        return 2
    say(f"d2-local: {len(roots)} ledger(s), Beancount's own load against the overlay load")
    reports = [_guarded(child, root) for root in roots]
    for case, report in zip(cases, reports, strict=True):
        say(line(case, report))
    totals = ", ".join(f"{sum(r.verdict == v for r in reports)} {v}" for v in VERDICTS)
    say(f"d2-local: {totals}")
    if any(r.verdict == "REFUSED" for r in reports):
        return 2
    return 0 if all(r.verdict == "EQUAL" for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
