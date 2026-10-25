#!/usr/bin/env python3
"""T1 check of EP-24 for Cargo (BL-0052; ADR-0050 and its 2026-10-25 amendment): every crate in
`shell/src-tauri/Cargo.lock` was published at least 14 days (14 × 24 h, as pnpm's `minimumReleaseAge` and uv's
`exclude-newer` count) before the commit that first put that version in the lockfile, or carries the security
exception: a journal line naming the crate and the advisory.

Cargo has no release-age setting, so the publish dates live beside the lock in `Cargo.lock.dates` (TSV: name,
version, published, exception). This script fills it from crates.io (`--update`) and checks it:
- every crates.io entry of the lock has exactly one row, and no row is stale;
- every entry is old enough at its adoption: the committer date of the earliest commit whose lockfile holds
  that name and version (the working tree counts as now), or its exception names a journal file under
  `design/journal/` with one line that mentions both the crate and an advisory ID (RUSTSEC-, GHSA-, CVE-);
- an entry adopted before this check existed (CHECK_SINCE) may instead carry `escape:<journal file>`, whose
  journal has a line naming the crate and the word "escape": the record of a violation found too late to undo
  (adoption is history). The marker is refused for anything adopted later, so it cannot become a way in;
- a dependency from anywhere but crates.io (git, another registry) is refused: it has no publish date;
- `--verify-since REF` re-reads from crates.io the date of every entry absent from the lock at REF (CI passes
  the previous push), and `--verify-all` every entry (nightly); a mismatch or a failed request fails.
A shallow clone fails closed: adoption dates need the lock's whole history.

Usage: python3 scripts/check_cargo_age.py [--update] [--verify-since REF | --verify-all]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import tomllib
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = "shell/src-tauri/Cargo.lock"
DATES = ROOT / "shell/src-tauri/Cargo.lock.dates"
CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"
MIN_AGE = timedelta(days=14)
CHECK_SINCE = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)  # when this check landed (S0013)
ADVISORY = re.compile(r"\b(RUSTSEC-\d{4}-\d{4}|GHSA(-[23456789cfghjmpqrvwx]{4}){3}|CVE-\d{4}-\d{4,})\b")
HEADER = "# name\tversion\tpublished (UTC, from crates.io)\texception (journal file, for the EP-24 security exception)\n"
USER_AGENT = "finzytrack-ci (https://github.com/sagarbehere/finzytrack)"

Key = tuple[str, str]


class CheckError(Exception):
    pass


def lock_entries(text: str) -> tuple[set[Key], list[str]]:
    """(crates.io entries, problems). Path packages (no source) are the workspace's own and are skipped."""
    entries, problems = set(), []
    for pkg in tomllib.loads(text).get("package", []):
        source = pkg.get("source")
        if source is None:
            continue
        if source != CRATES_IO:
            problems.append(f"{pkg['name']} {pkg['version']}: source {source!r} is not crates.io (no publish date)")
            continue
        entries.add((pkg["name"], pkg["version"]))
    return entries, problems


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True).stdout


def adoption_dates(working_text: str, now: datetime) -> dict[Key, datetime]:
    """Earliest committer date at which each entry appears in the lockfile's history; the working tree's
    entries not yet committed get `now`."""
    if git("rev-parse", "--is-shallow-repository").strip() != "false":
        raise CheckError("shallow clone: adoption dates need the lockfile's whole history (fetch-depth: 0)")
    first: dict[Key, datetime] = {}
    log = git("log", "--reverse", "--format=%H %cI", "--", LOCK).split("\n")
    for line in filter(None, log):
        sha, date = line.split(" ", 1)
        try:
            text = git("show", f"{sha}:{LOCK}")
        except subprocess.CalledProcessError:
            continue  # the commit deleted the file
        for key in lock_entries(text)[0]:
            first.setdefault(key, datetime.fromisoformat(date).astimezone(UTC))
    for key in lock_entries(working_text)[0]:
        first.setdefault(key, now)
    return first


def read_dates(text: str) -> tuple[dict[Key, tuple[datetime, str]], list[str]]:
    rows, problems = {}, []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) not in (3, 4):
            problems.append(f"{DATES.name}:{n}: expected 3 or 4 tab-separated columns")
            continue
        name, version, published = cols[:3]
        try:
            when = datetime.fromisoformat(published).astimezone(UTC)
        except ValueError:
            problems.append(f"{DATES.name}:{n}: bad date {published!r}")
            continue
        if (name, version) in rows:
            problems.append(f"{DATES.name}:{n}: duplicate row for {name} {version}")
        rows[(name, version)] = (when, cols[3].strip() if len(cols) == 4 else "")
    return rows, problems


def exception_ok(name: str, ref: str, journal_dir: Path, adopted: datetime) -> bool:
    """The security exception cites a journal file with one line naming the crate and an advisory; an
    `escape:` marker (adopted before CHECK_SINCE only) cites one with a line naming the crate and "escape"."""
    escape = ref.startswith("escape:")
    if escape:
        ref = ref.removeprefix("escape:")
        if adopted >= CHECK_SINCE:
            return False
    path = (journal_dir / ref).resolve()
    if not ref or path.parent != journal_dir.resolve() or not path.is_file():
        return False
    crate = re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])")
    marker = re.compile(r"(?i)\bescape\b") if escape else ADVISORY
    return any(crate.search(line) and marker.search(line) for line in path.read_text(encoding="utf-8").splitlines())


def evaluate(entries: set[Key], rows: dict[Key, tuple[datetime, str]], adopted: dict[Key, datetime],
             journal_dir: Path) -> list[str]:
    problems = []
    for key in sorted(entries - rows.keys()):
        problems.append(f"{key[0]} {key[1]}: no row in {DATES.name} (run --update)")
    for key in sorted(rows.keys() - entries):
        problems.append(f"{key[0]} {key[1]}: row in {DATES.name} but not in the lock (run --update)")
    for key in sorted(entries & rows.keys()):
        published, exception = rows[key]
        age = adopted[key] - published
        if age >= MIN_AGE:
            if exception:
                problems.append(f"{key[0]} {key[1]}: old enough, so the exception {exception!r} must go")
            continue
        if exception and exception_ok(key[0], exception, journal_dir, adopted[key]):
            continue
        problems.append(f"{key[0]} {key[1]}: published {published:%Y-%m-%d %H:%M} UTC, adopted "
                        f"{adopted[key]:%Y-%m-%d %H:%M} UTC, {age.total_seconds() / 86400:.1f} days old "
                        "(EP-24 needs 14, or the security exception with a journal line)")
    return problems


def published_on_crates_io(name: str, version: str) -> datetime:
    req = urllib.request.Request(f"https://crates.io/api/v1/crates/{name}/{version}",
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310 (fixed https host)
        data = json.load(response)
    return datetime.fromisoformat(data["version"]["created_at"].replace("Z", "+00:00")).astimezone(UTC)


def fetch_all(keys: list[Key]) -> dict[Key, datetime]:
    out = {}
    for i, (name, version) in enumerate(keys):
        if i:
            time.sleep(1)  # crates.io crawler policy: at most one request per second
        out[(name, version)] = published_on_crates_io(name, version)
    return out


def write_dates(rows: dict[Key, tuple[datetime, str]]) -> None:
    lines = [HEADER]
    for (name, version), (when, exception) in sorted(rows.items()):
        stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append("\t".join([name, version, stamp] + ([exception] if exception else [])) + "\n")
    DATES.write_text("".join(lines), encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true", help="add missing rows from crates.io, drop stale ones")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--verify-since", metavar="REF")
    group.add_argument("--verify-all", action="store_true")
    args = parser.parse_args(argv)
    lock_path = ROOT / LOCK
    if not lock_path.exists():
        return 0  # no shell package yet
    working = lock_path.read_text(encoding="utf-8")
    entries, problems = lock_entries(working)
    text = DATES.read_text(encoding="utf-8") if DATES.exists() else ""
    rows, row_problems = read_dates(text)
    problems += row_problems
    try:
        if args.update:
            missing = sorted(entries - rows.keys())
            rows = {k: v for k, v in rows.items() if k in entries}
            rows.update({k: (d, "") for k, d in fetch_all(missing).items()})
            write_dates(rows)
            print(f"check-cargo-age: {len(missing)} rows added, {DATES.name} has {len(rows)} rows")
        to_verify: list[Key] = []
        if args.verify_all:
            to_verify = sorted(entries & rows.keys())
        elif args.verify_since:
            try:
                before = lock_entries(git("show", f"{args.verify_since}:{LOCK}"))[0]
            except subprocess.CalledProcessError:
                before = set()  # the lock did not exist there: verify everything
            to_verify = sorted((entries - before) & rows.keys())
        for key, actual in fetch_all(to_verify).items():
            if abs(actual - rows[key][0]) > timedelta(seconds=1):
                problems.append(f"{key[0]} {key[1]}: {DATES.name} says {rows[key][0]:%Y-%m-%dT%H:%M:%SZ}, "
                                f"crates.io says {actual:%Y-%m-%dT%H:%M:%SZ}")
        adopted = adoption_dates(working, datetime.now(UTC))
    except (CheckError, OSError, subprocess.CalledProcessError, KeyError, ValueError) as e:
        print(f"check-cargo-age: {e}", file=sys.stderr)
        return 1
    problems += evaluate(entries, rows, adopted, ROOT / "design/journal")
    for p in problems:
        print(f"check-cargo-age: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
