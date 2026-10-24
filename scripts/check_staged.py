#!/usr/bin/env python3
"""T0 staged-file guard (INV-23, INV-24): refuse real-data files before they reach a commit.

Refused everywhere: secrets files (.env and variants except .env.example, .envrc, .netrc, secrets.*),
  and symlinks that point outside the repo.
Refused outside `corpora/`, `fixtures/` folders and the app's shipped content folder: ledgers in any
  plain-text-accounting format, databases, app config (config.yaml), keys and certificates.
Refused unless listed in `corpora/MANIFEST.tsv` with the same SHA-256 and an owner review date (the
  anonymization rule, design/process/PROCESS.md "Privacy gate"): bank and broker exports (spreadsheets,
  OFX/QFX/QIF, PDF, mail, archives); binary files other than images and fonts (the privacy scanner cannot
  read them); data-like text files (.txt, .json, .xml, no extension, ...) whose content looks like a
  statement (OFX markers, or five or more dated lines with amounts).

Usage: python3 scripts/check_staged.py            (checks the index)
       python3 scripts/check_staged.py --paths A B (checks the given repo paths; for tests)
Exit 0 = ok, 1 = refused.
"""

from __future__ import annotations

import argparse
import re
import fnmatch
import hashlib
import subprocess
import sys
from pathlib import PurePosixPath

DATA_PATTERNS = ["*.beancount", "*.bean", "*.beancount.*", "*.ledger", "*.journal", "*.hledger", "*.bc", "*.gpg",
                 "ledger.db", "ledger.db-*", "*.db", "*.db-*", "*.sqlite", "*.sqlite3", "config.yaml", "*.key", "*.pem",
                 "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*", "*.keystore"]
# Secrets files: refused everywhere, fixtures and corpora included (fake secrets go in test code, built at runtime).
SECRET_PATTERNS = [".env", "*.env", ".env.*", ".envrc", ".netrc", "_netrc", "secrets.yaml", "secrets.yml",
                   "secrets.json", "credentials", "credentials.json", "*.secret", "*.secrets"]
# Binary blobs the privacy scanner cannot read: only these media types may be committed.
# Content sniffing applies to data-like files; source code and Markdown are left to the privacy scanner.
SNIFF_EXTS = {"", ".txt", ".dat", ".xml", ".json", ".html", ".htm", ".log", ".prn", ".out", ".text"}
MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".icns", ".woff", ".woff2", ".ttf", ".otf"}
OFX_MARK = re.compile(rb"OFXHEADER|<OFX>|<STMTTRN>|<BANKTRANLIST>", re.I)
STATEMENT_LINE = re.compile(
    rb"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}[ -][A-Za-z]{3}[ -]\d{2,4})[^\n]{0,200}?[,;\t|][^\n]{0,200}?-?\d[\d,]*\.\d{2}\b"
)
DATA_ALLOWED_UNDER = ("corpora/", "backend/src/app/content/")  # test assets; shipped starter/seed ledgers
DATA_ALLOWED_SEGMENTS = ("fixtures",)
ENV_EXAMPLE = ".env.example"
EXPORT_PATTERNS = ["*.csv", "*.tsv", "*.xls", "*.xlsx", "*.xlsm", "*.xlsb", "*.ods", "*.numbers", "*.ofx", "*.qfx",
                   "*.qif", "*.qbo", "*.pdf", "*.eml", "*.mbox", "*.msg", "*.zip", "*.gz", "*.tgz", "*.7z", "*.rar", "*.tar"]
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


def looks_like_statement(blob: bytes) -> bool:
    """Content sniffing for renamed exports: OFX markers, or many dated lines carrying amounts."""
    head = blob[:200_000]
    return bool(OFX_MARK.search(head)) or len(STATEMENT_LINE.findall(head)) >= 5


def is_binary(blob: bytes) -> bool:
    head = blob[:8192]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False  # UTF-16 text; the privacy scanner decodes it
    return b"\0" in head


def check(paths: list[str], blob_of, symlinks: dict[str, str] | None = None) -> list[str]:
    problems: list[str] = []
    manifest_blob = blob_of(MANIFEST)
    manifest = parse_manifest(manifest_blob.decode("utf-8")) if manifest_blob is not None else {}
    for path in paths:
        name = PurePosixPath(path).name
        if symlinks and path in symlinks:
            target = symlinks[path]
            if target.startswith(("/", "~")) or ".." in PurePosixPath(target).parts:
                problems.append(f"{path}: symlink pointing outside the repo ({target!r}); refused")
            continue
        blob = blob_of(path)
        reviewed = blob is not None and manifest.get(path) == hashlib.sha256(blob).hexdigest()
        if name == ENV_EXAMPLE or path == MANIFEST:
            continue
        if matches(name, SECRET_PATTERNS):
            problems.append(f"{path}: secrets file; never committed (use .env.example with placeholders)")
        elif matches(name, EXPORT_PATTERNS):
            if not reviewed:
                problems.append(f"{path}: looks like a bank/broker export; only owner-reviewed, anonymized "
                                f"files listed in {MANIFEST} (path, sha256, review date) may be committed")
        elif matches(name, DATA_PATTERNS) and not in_allowed_place(path):
            problems.append(f"{path}: real-data file type outside corpora/ or a fixtures/ folder")
        elif blob is not None and is_binary(blob) and PurePosixPath(name).suffix.lower() not in MEDIA_EXTS and not reviewed:
            problems.append(f"{path}: binary file the privacy scan cannot read; only images and fonts, or files "
                            f"listed in {MANIFEST}, may be committed")
        elif blob is not None and PurePosixPath(name).suffix.lower() in SNIFF_EXTS and not in_allowed_place(path) \
                and not reviewed and looks_like_statement(blob):
            problems.append(f"{path}: content looks like a bank/broker statement (dated lines with amounts or OFX "
                            f"markers); move an anonymized, owner-reviewed copy to corpora/ with a {MANIFEST} entry")
    return problems


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--paths", nargs="*", help="repo paths to check against the working tree")
    args = ap.parse_args(argv)
    symlinks: dict[str, str] = {}
    if args.paths is not None:
        paths = args.paths

        def blob_of(p: str) -> bytes | None:
            try:
                with open(p, "rb") as fh:
                    return fh.read()
            except OSError:
                return None
    else:
        out = git("diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z")
        paths = [p for p in out.decode("utf-8").split("\0") if p]
        symlinks = {}
        for p in paths:
            entry = git("ls-files", "-s", "--", p).decode("utf-8").split()
            if entry and entry[0] == "120000":
                symlinks[p] = git("show", f":{p}").decode("utf-8", errors="replace")

        def blob_of(p: str) -> bytes | None:
            r = subprocess.run(["git", "show", f":{p}"], capture_output=True)
            return r.stdout if r.returncode == 0 else None

    problems = check(paths, blob_of, symlinks if args.paths is None else None)
    for p in problems:
        print(f"check-staged: REFUSED {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
