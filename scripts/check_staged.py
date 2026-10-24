#!/usr/bin/env python3
"""T0 staged-file guard (INV-23, INV-24): refuse real-data files before they reach a commit.

Refused anywhere outside test fixtures and `corpora/`:
  ledgers (*.beancount, *.bean), env files (.env, *.env, except .env.example), mirror databases
  (ledger.db*, *.sqlite, *.sqlite3), app config (config.yaml), keys (*.key, *.pem).
Refused everywhere unless listed in `corpora/MANIFEST.tsv` with the same SHA-256 and an owner
review date (the anonymization rule, design/process/PROCESS.md "Privacy gate"):
  bank and broker exports (*.csv, *.xls, *.xlsx, *.ofx, *.qfx, *.qif, *.pdf, *.eml, *.mbox).

Usage: python3 scripts/check_staged.py            (checks the index)
       python3 scripts/check_staged.py --paths A B (checks the given repo paths; for tests)
Exit 0 = ok, 1 = refused.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import subprocess
import sys
from pathlib import PurePosixPath

DATA_PATTERNS = ["*.beancount", "*.bean", ".env", "*.env", ".env.*", "ledger.db", "ledger.db-*",
                 "*.sqlite", "*.sqlite3", "config.yaml", "*.key", "*.pem"]
DATA_ALLOWED_UNDER = ("corpora/",)
DATA_ALLOWED_SEGMENTS = ("fixtures",)
ENV_EXAMPLE = ".env.example"
EXPORT_PATTERNS = ["*.csv", "*.xls", "*.xlsx", "*.ofx", "*.qfx", "*.qif", "*.pdf", "*.eml", "*.mbox"]
MANIFEST = "corpora/MANIFEST.tsv"


def matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name.lower(), p) for p in patterns)


def in_allowed_place(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return path.startswith(DATA_ALLOWED_UNDER) or any(seg in DATA_ALLOWED_SEGMENTS for seg in parts[:-1])


def parse_manifest(text: str) -> dict[str, str]:
    """MANIFEST.tsv rows: <path>\t<sha256>\t<owner review date YYYY-MM-DD>\t<note>."""
    entries: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        cols = raw.split("\t")
        if len(cols) >= 3 and len(cols[1]) == 64 and len(cols[2]) == 10:
            entries[cols[0]] = cols[1].lower()
    return entries


def check(paths: list[str], blob_of) -> list[str]:
    problems: list[str] = []
    manifest_blob = blob_of(MANIFEST)
    manifest = parse_manifest(manifest_blob.decode("utf-8")) if manifest_blob is not None else {}
    for path in paths:
        name = PurePosixPath(path).name
        if name == ENV_EXAMPLE:
            continue
        if matches(name, EXPORT_PATTERNS):
            blob = blob_of(path)
            digest = hashlib.sha256(blob).hexdigest() if blob is not None else ""
            if manifest.get(path) != digest:
                problems.append(f"{path}: looks like a bank/broker export; only owner-reviewed, anonymized "
                                f"files listed in {MANIFEST} (path, sha256, review date) may be committed")
        elif matches(name, DATA_PATTERNS) and not in_allowed_place(path):
            problems.append(f"{path}: real-data file type outside corpora/ or a fixtures/ folder")
    return problems


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--paths", nargs="*", help="repo paths to check against the working tree")
    args = ap.parse_args(argv)
    if args.paths is not None:
        paths = args.paths

        def blob_of(p: str) -> bytes | None:
            try:
                with open(p, "rb") as fh:
                    return fh.read()
            except OSError:
                return None
    else:
        out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
        paths = [p for p in out.decode("utf-8").split("\0") if p]

        def blob_of(p: str) -> bytes | None:
            r = subprocess.run(["git", "show", f":{p}"], capture_output=True)
            return r.stdout if r.returncode == 0 else None

    problems = check(paths, blob_of)
    for p in problems:
        print(f"check-staged: REFUSED {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
