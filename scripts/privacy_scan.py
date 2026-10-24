#!/usr/bin/env python3
"""T0 privacy scanner (INV-23): refuse secrets, account numbers and personal data in this public repo.

Usage:
    python3 scripts/privacy_scan.py --staged        # scan the staged blobs (pre-commit)
    python3 scripts/privacy_scan.py --all           # scan every tracked file (CI, T1)
    python3 scripts/privacy_scan.py PATH [PATH...]  # scan files on disk

Rules (design/process/PROCESS.md, "Privacy gate"):
  card-number   13-19 digits (spaces/dashes allowed) that pass the Luhn check
  account-number a standalone run of 11-18 digits, or 9-10 digits next to an account word
  pan           Indian PAN (five letters, four digits, one letter)
  iban          IBAN with a valid mod-97 checksum
  email         any address outside the reserved example/test domains
  secret        API keys, tokens, private-key blocks, assigned credentials
  home-path     a real home directory (/Users/<name>, /home/<name>, C:\\Users\\<name>)
  denylist      a literal from the owner's denylist (kept OUTSIDE the repo)

The owner's denylist (amounts, names, account fragments) is read from $FT_PRIVACY_DENYLIST or
~/.config/finzytrack/privacy-denylist.txt, one literal per line, '#' for comments. It is never
committed. A line containing `privacy-scan: allow <rule>` suppresses that rule on that line;
every suppression is visible in review.

Matches are printed redacted. Exit status: 0 clean, 1 findings, 2 usage or internal error.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ALLOWED_EMAIL_DOMAINS = re.compile(
    r"(^|\.)(example\.(com|org|net)|example|test|invalid|localhost)$|^users\.noreply\.github\.com$",
    re.IGNORECASE,
)
ALLOWED_EMAILS = {"noreply@anthropic.com"}
# Obviously fake numbers that docs and tests may use (see the public-scrub rules).
ALLOWED_NUMBERS = {
    "000012345678",
    "4111111111111111",  # Visa test card
    "4242424242424242",  # common test card
    "5555555555554444",  # Mastercard test card
}
ACCOUNT_WORDS = re.compile(r"\b(acc(oun)?t|a/c|acct|card|iban|routing|ifsc|bank-account|account[-_]number)\b", re.I)
# JSON/YAML keys whose numeric values are sizes, counts or timestamps, never account numbers
NUMERIC_KEY = re.compile(r"[\"']?\w*(size|bytes|quota|time|_at|^at|\bat|ts|ms|count|total|length|duration|epoch|nanos|micros)\w*[\"']?\s*[:=]\s*$", re.I)
EPOCH_MS = (1_000_000_000_000, 4_102_444_800_000)  # 2001..2100 in milliseconds
HOME_PLACEHOLDERS = {"<user>", "<name>", "you", "user", "me", "runner", "username", "$user", "${user}", "<username>"}

CARD = re.compile(r"(?<![\w.-])(?:\d[ -]?){12,18}\d(?![\w.-])")
DIGIT_RUN = re.compile(r"(?<![\w.:/-])\d{9,18}(?![\w.:/-])")
PAN = re.compile(r"\b[A-Z]{3}[ABCFGHLJPTK][A-Z]\d{4}[A-Z]\b")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63})\b")  # TLD letters: not pkg@1.2.3
HOME = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\+Users\\+)([A-Za-z0-9][A-Za-z0-9._-]*)")
SECRETS: list[tuple[str, re.Pattern[str]]] = [
    ("openai-key", re.compile(r"\bsk-(?:proj-|ant-[a-z0-9]+-)?[A-Za-z0-9_-]{20,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|untrusted comment: rsign encrypted secret" r" key")),
    (
        "assigned-credential",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['\"]([^'\"\s]{12,})['\"]"
        ),
    ),
]
PLACEHOLDER_VALUE = re.compile(r"(?i)^(x+|\*+|<[^>]+>|\$\{?[A-Z_]+\}?|changeme|placeholder|your[-_].*|dummy.*|test.*|fake.*)$")
ALLOW_MARK = re.compile(r"privacy-scan:\s*allow\s+([a-z-]+(?:\s*,\s*[a-z-]+)*)")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    sample: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.sample}"


def redact(text: str) -> str:
    t = text.strip()
    if len(t) <= 4:
        return "*" * len(t)
    return f"{t[:2]}…{t[-1]} ({len(t)} chars)"


def luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in moved)
    return int(num) % 97 == 1


def load_denylist() -> list[str]:
    path = os.environ.get("FT_PRIVACY_DENYLIST") or str(Path.home() / ".config/finzytrack/privacy-denylist.txt")
    p = Path(path)
    if not p.is_file():
        return []
    items = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            items.append(s)
    return items


def scan_text(path: str, text: str, denylist: list[str]) -> list[Finding]:
    out: list[Finding] = []
    lowered_deny = [(d, d.lower()) for d in denylist]
    for lineno, line in enumerate(text.splitlines(), start=1):
        allowed: set[str] = set()
        for m in ALLOW_MARK.finditer(line):
            allowed.update(r.strip() for r in m.group(1).split(","))

        def add(rule: str, sample: str) -> None:
            if rule not in allowed:
                out.append(Finding(path, lineno, rule, redact(sample)))

        card_spans: list[tuple[int, int]] = []
        for m in CARD.finditer(line):
            digits = re.sub(r"[ -]", "", m.group(0))
            if 13 <= len(digits) <= 19 and digits not in ALLOWED_NUMBERS and luhn_ok(digits) and len(set(digits)) > 2:
                add("card-number", digits)
                card_spans.append(m.span())
        for m in DIGIT_RUN.finditer(line):
            digits = m.group(0)
            if digits in ALLOWED_NUMBERS or any(a <= m.start() < b for a, b in card_spans):
                continue
            if len(set(digits)) <= 2:  # 000000000000, 111111111 and the like are placeholders
                continue
            if NUMERIC_KEY.search(line[: m.start()]):
                continue
            if len(digits) == 13 and EPOCH_MS[0] <= int(digits) < EPOCH_MS[1] and not ACCOUNT_WORDS.search(line):
                continue
            if len(digits) >= 11 or ACCOUNT_WORDS.search(line):
                add("account-number", digits)
        for m in PAN.finditer(line):
            add("pan", m.group(0))
        for m in IBAN.finditer(line):
            if iban_ok(m.group(0)):
                add("iban", m.group(0))
        for m in EMAIL.finditer(line):
            addr, domain = m.group(0), m.group(1)
            if addr.lower() in ALLOWED_EMAILS or ALLOWED_EMAIL_DOMAINS.search(domain):
                continue
            add("email", addr)
        for rule, rx in SECRETS:
            for m in rx.finditer(line):
                if rule == "assigned-credential" and PLACEHOLDER_VALUE.match(m.group(2)):
                    continue
                add("secret", m.group(0))
        for m in HOME.finditer(line):
            if m.group(1).lower() not in HOME_PLACEHOLDERS:
                add("home-path", m.group(0))
        low = line.lower()
        for original, d in lowered_deny:
            if d in low:
                add("denylist", original)
    return out


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def staged_files() -> list[str]:
    out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.decode("utf-8").split("\0") if p]


def tracked_files() -> list[str]:
    out = git("ls-files", "-z")
    return [p for p in out.decode("utf-8").split("\0") if p]


def run(paths: list[str], reader, denylist: list[str]) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    binaries: list[str] = []
    for p in paths:
        data = reader(p)
        if data is None:
            continue
        if is_binary(data):
            binaries.append(p)
            continue
        findings.extend(scan_text(p, data.decode("utf-8", errors="replace"), denylist))
    return findings, binaries


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true", help="scan staged blobs (pre-commit)")
    g.add_argument("--all", action="store_true", help="scan all tracked files")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    denylist = load_denylist()
    if args.staged:
        paths = staged_files()

        def reader(p: str) -> bytes | None:
            return git("show", f":{p}")
    elif args.all:
        paths = tracked_files()

        def reader(p: str) -> bytes | None:
            fp = Path(p)
            return fp.read_bytes() if fp.is_file() else None
    else:
        if not args.paths:
            ap.error("give --staged, --all or paths")
        paths = args.paths

        def reader(p: str) -> bytes | None:
            fp = Path(p)
            return fp.read_bytes() if fp.is_file() else None

    findings, binaries = run(paths, reader, denylist)
    for f in findings:
        print(f, file=sys.stderr)
    if not args.quiet:
        note = "" if denylist else " (no owner denylist found; amount/name literals not checked)"
        print(
            f"privacy-scan: {len(paths)} file(s), {len(findings)} finding(s), "
            f"{len(binaries)} binary file(s) not scanned{note}",
            file=sys.stderr,
        )
        for b in binaries:
            print(f"privacy-scan: binary not scanned: {b}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
