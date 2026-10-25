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
  statement (OFX markers, or five or more dated lines with amounts), in corpora/ and fixtures/ too, except the
  synthetic test assets named in SYNTHETIC_ASSETS (BL-0023).
Refused: a MANIFEST.tsv row that is malformed or whose owner review date is not a real date up to today.

Refused: a submodule (its content is outside this repo's scans).

Usage: python3 scripts/check_staged.py            (checks the index)
       python3 scripts/check_staged.py --paths A B (checks the given repo paths; for tests)
       python3 scripts/check_staged.py --range BASE..TIP | TIP
             (the pre-push hook, S0031: every blob any commit of the range adds or changes, under the path it has
             there, so a file added and removed again, or one that skipped pre-commit, is still checked; the
             corpora/MANIFEST.tsv of TIP decides which exports are reviewed)
Exit 0 = ok, 1 = refused, 2 = the range cannot be read.
"""

from __future__ import annotations

import argparse
import re
import fnmatch
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import PurePosixPath

import git_range

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
# Synthetic test assets our own tools write, whose dated lines with amounts are not statements (BL-0023). A new
# statement-like file in corpora/ or fixtures/ needs an owner-reviewed MANIFEST row or an entry here (code review).
SYNTHETIC_ASSETS = ("corpora/ledgers/inv21/goldens.json", "corpora/ledgers/oracle/nasty-cases.json")


def matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name.lower(), p) for p in patterns)


def in_allowed_place(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return path.startswith(DATA_ALLOWED_UNDER) or any(seg in DATA_ALLOWED_SEGMENTS for seg in parts[:-1])


def parse_manifest(text: str, today: date | None = None) -> tuple[dict[str, str], list[str]]:
    """MANIFEST.tsv rows: <path>\t<sha256>\t<owner review date YYYY-MM-DD>\t<note>. Returns (entries, problems)."""
    entries: dict[str, str] = {}
    problems: list[str] = []
    today = today or date.today()
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.startswith("#"):
            continue
        cols = raw.split("\t")
        ok = len(cols) >= 3 and re.fullmatch(r"[0-9a-fA-F]{64}", cols[1]) is not None and cols[0].strip() == cols[0] != ""
        try:
            reviewed = date.fromisoformat(cols[2]) if ok and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cols[2]) else None
        except ValueError:
            reviewed = None
        if not ok or reviewed is None or reviewed > today:
            problems.append(f"{MANIFEST} line {n}: needs <path> TAB <sha256> TAB <owner review date YYYY-MM-DD, not in "
                            "the future> TAB <note>; only the owner adds rows")
            continue
        entries[cols[0]] = cols[1].lower()
    return entries, problems


def as_text_bytes(blob: bytes) -> bytes:
    """UTF-16/UTF-32 text with a BOM (Excel's "Unicode text" export) re-encoded as UTF-8, so the byte patterns see it."""
    for bom, codec in ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
                       (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if blob.startswith(bom):
            return blob.decode(codec, errors="replace").encode("utf-8")
    return blob


def looks_like_statement(blob: bytes) -> bool:
    """Content sniffing for renamed exports: OFX markers, or many dated lines carrying amounts."""
    head = as_text_bytes(blob[:800_000])[:200_000]
    return bool(OFX_MARK.search(head)) or len(STATEMENT_LINE.findall(head)) >= 5


def parses_as_json(blob: bytes) -> bool:
    try:
        json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    return True


def is_binary(blob: bytes) -> bool:
    head = blob[:8192]
    if head.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")):
        return False  # UTF-16 text; the privacy scanner decodes it
    return b"\0" in head


def check(paths: list[str], blob_of, symlinks: dict[str, str] | None = None,
          submodules: set[str] | None = None) -> list[str]:
    problems: list[str] = []
    manifest_blob = blob_of(MANIFEST)
    manifest, manifest_problems = (parse_manifest(manifest_blob.decode("utf-8", errors="replace"))
                                   if manifest_blob is not None else ({}, []))
    if MANIFEST in paths:
        problems += manifest_problems
    for path in paths:
        name = PurePosixPath(path).name
        if submodules and path in submodules:
            problems.append(f"{path}: a submodule; its content is outside this repo's scans, refused")
            continue
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
        elif blob is not None and path in SYNTHETIC_ASSETS and not reviewed and not parses_as_json(blob):
            problems.append(f"{path}: a named synthetic asset must stay JSON written by our tools; refused")
        elif blob is not None and PurePosixPath(name).suffix.lower() in SNIFF_EXTS and path not in SYNTHETIC_ASSETS \
                and not reviewed and looks_like_statement(blob):
            problems.append(f"{path}: content looks like a bank/broker statement (dated lines with amounts or OFX "
                            f"markers); move an anonymized, owner-reviewed copy to corpora/ with a {MANIFEST} entry")
    return problems


def check_range(spec: str) -> list[str]:
    """`check` over every (path, blob) the range adds or changes; one blob at a time, so two versions of one path
    are both checked. MANIFEST.tsv itself is checked in each version the range has."""
    _, tip = git_range.parse_spec(spec)
    blobs = git_range.changed_blobs(spec)
    contents = git_range.read_blobs([b.sha for b in blobs if b.mode != "160000"])
    tip_manifest = git_range.blob_at(tip, MANIFEST)
    problems: list[str] = []
    for b in blobs:
        data = contents.get(b.sha)

        def blob_of(p: str, b=b, data=data) -> bytes | None:
            if p == b.path:
                return data
            return tip_manifest if p == MANIFEST else None

        symlinks = {b.path: (data or b"").decode("utf-8", errors="replace")} if b.mode == "120000" else None
        found = check([b.path], blob_of, symlinks, {b.path} if b.mode == "160000" else None)
        problems += [f"{p} (blob {b.sha[:12]})" for p in found]
    return list(dict.fromkeys(problems))


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--paths", nargs="*", help="repo paths to check against the working tree")
    g.add_argument("--range", help="BASE..TIP or TIP (40-hex commit names): every blob the range adds or changes")
    args = ap.parse_args(argv)
    symlinks: dict[str, str] = {}
    submodules: set[str] = set()
    if args.range is not None:
        try:
            problems = check_range(args.range)
        except git_range.RangeError as exc:
            print(f"check-staged: {exc}", file=sys.stderr)
            return 2
        for p in problems:
            print(f"check-staged: REFUSED {p}", file=sys.stderr)
        return 1 if problems else 0
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
            elif entry and entry[0] == "160000":
                submodules.add(p)

        def blob_of(p: str) -> bytes | None:
            r = subprocess.run(["git", "show", f":{p}"], capture_output=True)
            return r.stdout if r.returncode == 0 else None

    problems = check(paths, blob_of, symlinks if args.paths is None else None, submodules)
    for p in problems:
        print(f"check-staged: REFUSED {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
