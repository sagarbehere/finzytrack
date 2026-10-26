#!/usr/bin/env python3
"""T0 privacy scanner (INV-23): refuse secrets, account numbers and personal data in this public repo.

Usage:
    python3 scripts/privacy_scan.py --staged        # scan the staged blobs (pre-commit)
    python3 scripts/privacy_scan.py --all           # scan every tracked file (CI, T1)
    python3 scripts/privacy_scan.py --range BASE..TIP | TIP
                                    # every blob the range's commits add or change, and their messages
                                    # (pre-push, S0031)
    python3 scripts/privacy_scan.py --tag-message TAG_SHA   # a pushed annotated tag's message (pre-push)
    python3 scripts/privacy_scan.py --message FILE          # a commit message (commit-msg hook)
    python3 scripts/privacy_scan.py PATH [PATH...]  # scan files on disk

Rules (design/process/PROCESS.md, "Privacy gate"):
  card-number   13-19 digits (spaces/dashes allowed) that pass the Luhn check
  account-number a standalone run of 11-18 digits, or 9-10 digits on a line with an account word, or 6-10 digits
                right after one (`Acct #: ...`, `A/C No. ...`; S0033)
  upi-handle    a UPI address `<name>@<handle>` with a known UPI handle (`@okaxis`, `@ybl`, ...), not an email (S0033)
  statement-excerpt three or more statement lines (date, text, amount in columns) within six lines, in Markdown and
                text files and commit and tag messages (S0033); Beancount's own shapes (an ISO-dated line with an
                account name, a commodity code after the amount, or a commodity code alone as its text) do not count
  pan           Indian PAN (five letters, four digits, one letter)
  iban          IBAN with a valid mod-97 checksum
  email         any address outside the reserved example/test domains
  secret        API keys, tokens, private-key blocks, assigned credentials
  secret-entropy what detect-secrets 1.5.0 finds (vendor key formats, secret keywords, high-entropy strings),
                run from its pinned environment `scripts/secrets-scan/` with network verification off, on every
                blob whatever its name (BL-0035); a 64-hex value under a `sha256` or `checksum` key is a hash
                pin. A mark or pin excuses a hit only on its own line under every numbering detect-secrets may
                use, so in a YAML file with a NEL, LINE or PARAGRAPH SEPARATOR above the value it may not excuse:
                put the mark on a line of its own there (fails closed by design, S0016) If the tool cannot run, the scan fails (exit 2), never passes
  home-path     a real home directory (/Users/<name>, /home/<name>, /Volumes/<name>, ~<name>/, C:\\Users\\<name>)
  live-data-path the name of the owner's live data folder
  denylist      a literal from the owner's denylist (kept OUTSIDE the repo)

The owner's denylist (amounts, names, account fragments) is read from $FT_PRIVACY_DENYLIST or
~/.config/finzytrack/privacy-denylist.txt, one literal per line, '#' for comments. It is never
committed. A line containing `privacy-scan: allow <rule>` suppresses that rule on that line;
every suppression is visible in review.

Matches are printed redacted. Exit status: 0 clean, 1 findings, 2 usage or internal error (including a
Python older than 3.12, or detect-secrets unable to run).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import git_range

ALLOWED_EMAIL_DOMAINS = re.compile(
    r"(^|\.)(example\.(com|org|net)|example|test|invalid|localhost)$|^users\.noreply\.github\.com$",
    re.IGNORECASE,
)
# "icon@2x.png", "chunk@1.js": file names, not addresses
FILE_TLD = re.compile(r"\.(png|jpe?g|gif|webp|svg|ico|icns|js|mjs|cjs|ts|tsx|css|map|json|md|txt|py|wasm|woff2?)$", re.I)
ALLOWED_EMAILS = {"noreply@anthropic.com"}
# Obviously fake numbers that docs and tests may use (see the public-scrub rules).
ALLOWED_NUMBERS = {
    "000012345678",
    "4111111111111111",  # Visa test card
    "4242424242424242",  # common test card
    "5555555555554444",  # Mastercard test card
}
ACCOUNT_WORDS = re.compile(r"(?i)(acc(oun)?t|a/c|acct|card|iban|routing|ifsc|bank|upi|neft|imps|rtgs|ach|swift|sort.?code)")
# JSON/YAML keys whose numeric values are sizes, counts or timestamps, never account numbers (whole key only)
NUMERIC_KEY = re.compile(
    r"""["']?(?:[a-z0-9]+_)*(size|bytes|quota|at|ts|ms|time|timestamp|count|total|length|duration|epoch|nanos|micros|offset|seed)["']?\s*[:=]\s*$""",
    re.I,
)
EPOCH_MS = (1_000_000_000_000, 4_102_444_800_000)  # 2001..2100 in milliseconds
HOME_PLACEHOLDERS = {"<user>", "<name>", "you", "user", "me", "runner", "username", "$user", "${user}", "<username>", "shared"}
SEP = r"[ \-/_\u00a0]{1,2}"  # separators people put between digit groups (dots: DOTTED_CARD only)

CARD = re.compile(rf"(?<![A-Za-z0-9])(?:\d(?:{SEP})?){{12,18}}\d(?![A-Za-z0-9])")
CARD_GROUPS = re.compile(r"^\d{4}(?:\D{1,2}\d{4}){2,3}(?:\D{1,2}\d{1,4})?$")  # 4-4-4-4 style
DOTTED_CARD = re.compile(r"(?<![\d.])\d{4}(?:\.\d{4}){2}\.\d{1,7}(?![\d.])")  # dddd.dddd.dddd.dddd, not floats
DIGIT_RUN = re.compile(r"(?<![A-Za-z0-9.])\d{9,18}(?![A-Za-z0-9.])")
GROUPED = re.compile(r"(?<![A-Za-z0-9.])\d{2,6}(?:[ \-]\d{2,6}){1,5}(?![A-Za-z0-9.])")
MASKED = re.compile(r"(?i)(?<![A-Za-z0-9])[x*•]{4,}[ \-]?\d{3,6}\b")
PAN = re.compile(r"\b[A-Z]{3}[ABCFGHLJPTK][A-Z]\d{4}[A-Z]\b")
PAN_LABELLED = re.compile(r"(?i)\bpan\b\W{0,5}([a-z]{3}[abcfghljptk][a-z]\d{4}[a-z])\b")
IBAN = re.compile(r"(?i)\b[A-Z]{2}\d{2}(?:[ \-]?[A-Z0-9]{4}){2,7}(?:[ \-]?[A-Z0-9]{1,4})?\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63})\b")  # TLD letters: not pkg@1.2.3
HOME = re.compile(r"(?:/Users/|/home/|/Volumes/|[A-Za-z]:\\+Users\\+)([A-Za-z0-9][A-Za-z0-9._-]*)")
OTHER_USER_HOME = re.compile(r"(?<![\w/])~([a-z][a-z0-9._-]*)/")  # another user's home, e.g. ~<name>/
# S0033 (seeded-defect run 1 A11): a UPI address (VPA), `<name>@<handle>`, whose handle is a known UPI suffix and is
# not followed by a dot and a domain (then it is an email, the email rule's). The list holds the common handles; an
# unknown one passes (stated limit). Placeholder names (`name@okaxis`) pass.
UPI_HANDLES = ("okaxis", "okhdfcbank", "okicici", "oksbi", "okbizaxis", "ybl", "ibl", "axl", "paytm", "ptyes",
               "ptaxis", "pthdfc", "ptsbi", "apl", "yapl", "rapl", "upi", "axisbank", "axisb", "icici", "hdfcbank",
               "sbi", "kotak", "kmbl", "indus", "pnb", "boi", "cnrb", "unionbank", "idfcbank", "idfcfirst",
               "freecharge", "airtel", "jio", "fbl", "federal", "rbl", "yesbank", "yesg", "aubank", "dbs", "citi",
               "hsbc", "abfspay", "ikwik", "mahb", "barodampay", "waaxis", "wahdfcbank", "waicici", "wasbi",
               "naviaxis", "superyes", "jupiteraxis")
UPI = re.compile(r"(?i)(?<![\w.@-])([a-z0-9][a-z0-9._-]{1,255})@(" + "|".join(UPI_HANDLES) + r")(?![\w@-]|\.[a-z])")
UPI_PLACEHOLDERS = {"name", "user", "username", "yourname", "example", "test", "someone", "xyz", "abc", "vpa",
                    "handle", "payee", "merchant"}
# S0033 (A06): 6-10 digits, plain or in groups split by spaces or dashes (not date-shaped), right after an account
# word and an optional "No", "Number", "#" or ":" (the line-wide account context above takes 9 or more digits;
# shorter ones only here, where they cannot be anything else). Sentence punctuation may follow the number; a decimal
# or digit group may not (`account 1234567.89` is an amount; S0033 verifier F3).
ACCOUNT_NEAR = re.compile(r"(?i)(?<![a-z0-9])(?:a/c|acct|acc|account|ac(?=\s*no\b))\b\.?"
                          r"(?:\s*(?:no|num(?:ber)?)\b\.?)?\s*(?:[#:\-]\s*)*(\d{2,10}(?:[ \-]\d{2,8}){0,3})(?!\d|[.,]\d|[a-z])")
DATE_SHAPED = re.compile(r"\d{4}[ \-]\d{1,2}[ \-]\d{1,2}|\d{1,2}[ \-]\d{1,2}[ \-]\d{4}")
# S0033 (A05): a statement excerpt in prose: three or more lines within six that each read date, text, amount, with
# column gaps (two spaces, a tab, `|`, `,` or `;`) between them, in Markdown and text files and in commit and tag
# messages. Code and data files are the staged-file guard's (its sniff of data-like files; BL-0023). Beancount
# directives (`2026-03-01 balance ...`) are not statement lines.
STATEMENT_SUFFIXES = {".md", ".markdown", ".rst", ".adoc", ".txt"}
_DATE = (r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}"
         r"|\d{1,2}[ -](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ ,-]*\d{2,4})")
_GAP = r"(?:\s*[|,;\t]\s*|\s{2,})"
_BEANCOUNT = r"(?:\*|!|txn\b|open\b|close\b|balance\b|price\b|pad\b|note\b|document\b|event\b|custom\b|commodity\b|query\b|\")"
STATEMENT_LINE = re.compile(rf"(?i)^[\s|>]*(?P<date>{_DATE}){_GAP}(?!{_BEANCOUNT})(?=[^\n]*[a-z])"
                            rf"(?P<text>[^\n]{{2,}}?){_GAP}"
                            r"[-+]?(?:\d{1,3}(?:,\d{2,3})+|\d+)\.\d{2}\b(?P<after>[^\n]*)")
# S0033 verifier F4: Beancount's own shapes in specs (bean-query register output, balance histories, price tables)
# are not statements. A line with an ISO date (the only date Beancount writes) does not count when its text holds an
# account name under one of the five standard roots, or its amount is followed by a commodity code (`45.20 USD`; a
# `Dr`/`Cr` marker is a statement's), or its text is a commodity code alone and no `Dr`/`Cr` follows (`| INFY |`, a
# price table). Case matters: statement narrations are upper case (`EXPENSES:FOOD`, `UPI:SWIGGY` still count).
# Stated limit: an ISO-dated statement whose amounts carry a currency code, or whose narration is one upper-case
# word, is read as Beancount; the other date forms are never excused.
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
BEAN_ACCOUNT = re.compile(r"(?<![\w:])(?:Assets|Liabilities|Equity|Income|Expenses)(?::[A-Z0-9][A-Za-z0-9-]*)+(?![\w:])")
_COMMODITY = r"[A-Z](?:[A-Z0-9'._-]{0,22}[A-Z0-9])?"
COMMODITY_AFTER = re.compile(rf"^[ \t]+(?!(?i:cr|dr)\b){_COMMODITY}(?![\w'.-])")
COMMODITY_ALONE = re.compile(rf"[\s|,;]*{_COMMODITY}[\s|,;]*")
DR_CR = re.compile(r"(?i)\b(?:cr|dr)\b")
STATEMENT_MIN_LINES, STATEMENT_WINDOW = 3, 6
LIVE_DATA_NAME = re.compile(r"(?i)finzytrack[_-]?data\b")  # the owner's live data folder, by name  privacy-scan: allow live-data-path
SECRETS: list[tuple[str, re.Pattern[str]]] = [
    ("openai-key", re.compile(r"\bsk-(?:proj-|ant-[a-z0-9]+-)?[A-Za-z0-9_-]{20,}")),
    ("stripe-like-key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("hf-groq-key", re.compile(r"\b(?:hf|gsk)_[A-Za-z0-9]{30,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/-]{20,}=*)")),
    ("url-credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://([^/\s:@]+):([^/\s@]{3,})@")),
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|untrusted comment: rsign encrypted secret" r" key")),
    (
        "assigned-credential",
        re.compile(
            r"(?i)[\"']?\b[a-z0-9_-]{0,40}?(api[_-]?key|apikey|secret|token|password|passwd|pwd|credentials?)[a-z0-9_-]{0,40}[\"']?"
            r"\s*[:=]\s*[\"']?([^\"'\s,}]{12,})"
        ),
    ),
]
PLACEHOLDER_VALUE = re.compile(
    r"(?i)^(x+|\*+|\.+|<[^>]+>|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|changeme|placeholder|your[-_].*|dummy.*|test.*|fake.*|example.*"
    r"|redacted|none|null|true|false|[a-z_]+\(.*|[a-z_]+\.[a-z_.]+.*|os\.environ.*)$"
)
# Files whose job is to guard the live data folder must name it; nothing else may (scrub rule 2).
LIVE_DATA_GUARD_FILES = (".claude/settings.json", ".claude/hooks/", "scripts/oracle/", "scripts/tests/test_guards.py",
                         "scripts/tests/test_privacy_scan.py")
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
    s = re.sub(r"[ \-]", "", raw).upper()
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


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def scan_text(path: str, text: str, denylist: list[str]) -> list[Finding]:
    out: list[Finding] = []
    lowered_deny = [(d, d.lower()) for d in denylist]
    for lineno, line in enumerate(text.splitlines(), start=1):
        allowed: set[str] = set()
        for m in ALLOW_MARK.finditer(line):
            allowed.update(r.strip() for r in m.group(1).split(","))
        account_context = bool(ACCOUNT_WORDS.search(line))

        def add(rule: str, sample: str) -> None:
            if rule not in allowed:
                out.append(Finding(path, lineno, rule, redact(sample)))

        card_spans: list[tuple[int, int]] = []
        for m in [*CARD.finditer(line), *DOTTED_CARD.finditer(line)]:
            digits = _digits(m.group(0))
            if not account_context:
                raw = m.group(0)
                if raw != digits and not CARD_GROUPS.match(raw):
                    continue  # irregular grouping (a short prefix, then a long id) is not a card
                if raw == digits and len(digits) == 13 and EPOCH_MS[0] <= int(digits) < EPOCH_MS[1]:
                    continue  # epoch milliseconds
            if 13 <= len(digits) <= 19 and digits not in ALLOWED_NUMBERS and luhn_ok(digits) and len(set(digits)) > 2:
                add("card-number", digits)
                card_spans.append(m.span())
        account_spans: list[tuple[int, int]] = []
        for m in DIGIT_RUN.finditer(line):
            digits = m.group(0)
            if digits in ALLOWED_NUMBERS or any(a <= m.start() < b for a, b in card_spans):
                continue
            if len(set(digits)) <= 2:  # 000000000000, 111111111 and the like are placeholders
                continue
            if not account_context:
                if NUMERIC_KEY.search(line[: m.start()]):
                    continue
                if len(digits) == 13 and EPOCH_MS[0] <= int(digits) < EPOCH_MS[1]:
                    continue
            if len(digits) >= 11 or account_context:
                add("account-number", digits)
                account_spans.append(m.span())
        for m in ACCOUNT_NEAR.finditer(line):
            digits = _digits(m.group(1))
            if 6 <= len(digits) <= 10 and len(set(digits)) > 2 and digits not in ALLOWED_NUMBERS \
                    and not DATE_SHAPED.fullmatch(m.group(1)) \
                    and not any(a <= m.start(1) < b for a, b in account_spans + card_spans):
                add("account-number", digits)
        if account_context:
            for m in GROUPED.finditer(line):
                digits = _digits(m.group(0))
                if 9 <= len(digits) <= 18 and digits not in ALLOWED_NUMBERS and len(set(digits)) > 2 \
                        and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", m.group(0)):
                    add("account-number", digits)
            for m in MASKED.finditer(line):
                add("account-number", m.group(0))
        for m in PAN.finditer(line):
            add("pan", m.group(0))
        for m in PAN_LABELLED.finditer(line):
            if not PAN.fullmatch(m.group(1)):
                add("pan", m.group(1))
        for m in IBAN.finditer(line):
            if iban_ok(m.group(0)):
                add("iban", m.group(0))
        for m in EMAIL.finditer(line):
            addr, domain = m.group(0), m.group(1)
            if addr.lower() in ALLOWED_EMAILS or ALLOWED_EMAIL_DOMAINS.search(domain) or FILE_TLD.search(domain):
                continue
            add("email", addr)
        for m in UPI.finditer(line):
            if m.group(1).lower() not in UPI_PLACEHOLDERS:
                add("upi-handle", m.group(0))
        for rule, rx in SECRETS:
            for m in rx.finditer(line):
                if rule == "assigned-credential":
                    value = m.group(m.lastindex or 0)
                    # secrets mix letters and digits; plain identifiers and type names are code
                    if PLACEHOLDER_VALUE.match(value) or not (re.search(r"\d", value) and re.search(r"[A-Za-z]", value)):
                        continue
                if rule == "url-credentials" and PLACEHOLDER_VALUE.match(m.group(2)):
                    continue
                add("secret", m.group(0))
        for m in HOME.finditer(line):
            if m.group(1).lower() not in HOME_PLACEHOLDERS:
                add("home-path", m.group(0))
        for m in OTHER_USER_HOME.finditer(line):
            add("home-path", m.group(0))
        if not path.startswith(LIVE_DATA_GUARD_FILES):
            for m in LIVE_DATA_NAME.finditer(line):
                add("live-data-path", m.group(0))
        low = line.lower()
        for original, d in lowered_deny:
            if d in low:
                add("denylist", original)
    return out + statement_excerpts(path, text)


def statement_scope(path: str) -> bool:
    """Markdown and text files, and commit and tag messages (labelled `... message` by the message scans)."""
    return path.endswith(" message") or os.path.splitext(path)[1].lower() in STATEMENT_SUFFIXES


def statement_line(line: str) -> bool:
    """Date, text, amount in columns, and not one of Beancount's own shapes (the comment above BEAN_ACCOUNT)."""
    m = STATEMENT_LINE.search(line)
    if not m:
        return False
    if not ISO_DATE.fullmatch(m.group("date")):
        return True
    text, after = m.group("text"), m.group("after")
    if BEAN_ACCOUNT.search(text) or COMMODITY_AFTER.match(after):
        return False
    return not (COMMODITY_ALONE.fullmatch(text) and not DR_CR.search(after))


def statement_excerpts(path: str, text: str) -> list[Finding]:
    """One finding per run of STATEMENT_MIN_LINES statement lines within STATEMENT_WINDOW lines; a line carrying
    `privacy-scan: allow statement-excerpt` does not count."""
    if not statement_scope(path):
        return []
    hits = [n for n, line in enumerate(text.splitlines(), start=1) if statement_line(line)
            and "statement-excerpt" not in {r.strip() for m in ALLOW_MARK.finditer(line) for r in m.group(1).split(",")}]
    out: list[Finding] = []
    k, need = 0, STATEMENT_MIN_LINES - 1
    while k + need < len(hits):
        if hits[k + need] - hits[k] < STATEMENT_WINDOW:
            out.append(Finding(path, hits[k], "statement-excerpt", f"{STATEMENT_MIN_LINES}+ dated lines with amounts"))
            last = hits[k + need]
            while k < len(hits) and hits[k] - last < STATEMENT_WINDOW:  # one finding per excerpt
                last = hits[k]
                k += 1
        else:
            k += 1
    return out


SECRETS_ENV = Path(__file__).resolve().parent / "secrets-scan"
# A pin in a lockfile or pin file: a 64-hex value under a key named exactly `sha256` or `checksum`. The key
# must start at a word boundary, so `api_secret_sha256` is not a pin.
HASH_PIN = re.compile(r"""(?<![\w-])["']?(?:sha256|checksum)["']?\s*[:=]\s*["']([0-9a-f]{64})["']""")
SAFE_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,12}")
# detect-secrets skips files by name (lock files) and extension (.css, .svg, .map, ...); every blob is scanned.
DS_DISABLED_FILTERS = ("detect_secrets.filters.heuristic.is_lock_file",
                       "detect_secrets.filters.heuristic.is_non_text_file")
CANDIDATE = re.compile(r"""["']([^"']+)["']|([^\s"'=:,]+)""")


class ToolError(Exception):
    pass


def suffix(path: str) -> str:
    ext = os.path.splitext(path.replace("\\", "/").rsplit("/", 1)[-1])[1]
    return ext if SAFE_SUFFIX.fullmatch(ext) else ".txt"


YAML_SUFFIXES = frozenset({".yaml", ".yml", ".eyaml"})


def _path(key) -> str:
    """The repo path of a scan key: the path itself, or (path, blob sha) in a range scan."""
    return key[0] if isinstance(key, tuple) else key


def _label(key) -> str:
    return f"{key[0]} (blob {key[1][:12]})" if isinstance(key, tuple) else key


def detect_secrets(texts: dict) -> dict:
    """{path: [(line, hashed_secret, suffix scanned as)]} from detect-secrets over the given texts. Each text is
    written to the temporary folder under generated names, never under its own path, so no path can leave the
    folder or be read as an option (EP-30). detect-secrets' rules depend on the file type, and neither choice
    finds everything (YAML scalars and unquoted values only as `.txt`; Go or C keywords only under their own
    suffix), so every text is scanned twice: as `blob00000.txt` and as `blob00000-t<suffix>`, and the findings
    are united."""
    if not texts:
        return {}
    names: dict[str, tuple[str, str]] = {}
    for i, path in enumerate(texts):
        names[f"blob{i:05d}.txt"] = (path, ".txt")
        if suffix(_path(path)) != ".txt":
            names[f"blob{i:05d}-t{suffix(_path(path))}"] = (path, suffix(_path(path)))
    with tempfile.TemporaryDirectory() as tmp:
        for name, (path, _) in names.items():
            (Path(tmp) / name).write_text(texts[path], encoding="utf-8", newline="")
        filters = [arg for f in DS_DISABLED_FILTERS for arg in ("--disable-filter", f)]
        cmd = ["uv", "run", "--project", str(SECRETS_ENV), "--locked", "--quiet", "detect-secrets", "scan",
               "--no-verify", *filters, "--", *names]
        try:
            # PYTHONUTF8: under a non-UTF-8 locale detect-secrets would skip a file it cannot decode, silently
            env = {**os.environ, "PYTHONUTF8": "1"}
            proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=300, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ToolError(f"detect-secrets could not run: {e}") from e
        if proc.returncode != 0:
            raise ToolError(f"detect-secrets failed: {proc.stderr.strip()[-500:]}")
        try:
            results = json.loads(proc.stdout)["results"]
        except (ValueError, KeyError) as e:
            raise ToolError(f"detect-secrets output unreadable: {e}") from e
        if not set(results) <= set(names):
            raise ToolError(f"detect-secrets reported unknown files: {sorted(set(results) - set(names))[:3]}")
    out: dict[str, list[tuple[int, str, str]]] = {}
    for name, found in results.items():
        path, as_suffix = names[name]
        out.setdefault(path, []).extend((r["line_number"], r["hashed_secret"], as_suffix) for r in found)
    return out


def text_lines(text: str, as_suffix: str) -> list[str]:
    """The lines as detect-secrets numbered them: universal newlines (CRLF, CR, LF); for YAML its transformer
    reads with PyYAML, which also ends lines at NEL, LINE SEPARATOR and PARAGRAPH SEPARATOR."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if as_suffix in YAML_SUFFIXES:
        for sep in ("\x85", "\u2028", "\u2029"):
            text = text.replace(sep, "\n")
    return text.split("\n")


def located(line: str, hashed: str) -> str:
    """The token on `line` whose SHA-1 is detect-secrets' `hashed_secret`, or ""."""
    tokens = [m.group(1) or m.group(2) for m in CANDIDATE.finditer(line)]
    return next((t for t in tokens if hashlib.sha1(t.encode()).hexdigest() == hashed), "")  # noqa: S324


def excused(line: str, hashed: str) -> bool:
    """The line carries the rule's allow mark, or the value on it is a hash pin."""
    allowed = {r.strip() for m in ALLOW_MARK.finditer(line) for r in m.group(1).split(",")}
    token = located(line, hashed)
    return "secret-entropy" in allowed or bool(token and any(m.group(1) == token for m in HASH_PIN.finditer(line)))


def entropy_findings(texts: dict) -> list[Finding]:
    """One finding per (line, value). A hit is excused only when its line shows the mark or the pin under every
    numbering detect-secrets may have used for it: universal newlines always, and for a YAML scan also PyYAML's
    breaks (it falls back to universal numbering when the file does not parse). Duplicates are dropped only
    after that decision, so an excused hit never hides another (S0016 rounds 3 and 4)."""
    out: list[Finding] = []
    for path, hits in detect_secrets(texts).items():
        universal = text_lines(texts[path], ".txt")
        yaml_lines = text_lines(texts[path], ".yaml")
        reported: set[tuple[int, str]] = set()
        for lineno, hashed, as_suffix in sorted(set(hits)):
            mappings = [universal, yaml_lines] if as_suffix in YAML_SUFFIXES else [universal]
            candidates = [m[lineno - 1] if 0 < lineno <= len(m) else "" for m in mappings]
            if all(excused(line, hashed) for line in candidates) or (lineno, hashed) in reported:
                continue
            reported.add((lineno, hashed))
            token = next((t for t in (located(line, hashed) for line in candidates) if t), "")
            out.append(Finding(_label(path), lineno, "secret-entropy", redact(token) if token else "(value not located)"))
    return out


def decode(data: bytes) -> str | None:
    """Text content of a blob, including UTF-16/32 text; None for real binaries."""
    for bom, enc in ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(enc, errors="replace")
    head = data[:8192]
    if b"\0" not in head:
        return data.decode("utf-8", errors="replace")
    if len(head) >= 4 and head[1::2].count(0) > 0.9 * len(head[1::2]):
        return data.decode("utf-16-le", errors="replace")
    if len(head) >= 4 and head[0::2].count(0) > 0.9 * len(head[0::2]):
        return data.decode("utf-16-be", errors="replace")
    return None


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def staged_files() -> list[str]:
    out = git("diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z")
    return [p for p in out.decode("utf-8").split("\0") if p]


def tracked_files() -> list[str]:
    out = git("ls-files", "-z")
    return [p for p in out.decode("utf-8").split("\0") if p]


def run(paths: list[str], reader, denylist: list[str]) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    binaries: list[str] = []
    texts: dict[str, str] = {}
    for p in paths:
        data = reader(p)
        if data is None:
            continue
        text = decode(data)
        if text is None:
            binaries.append(p)  # refused by scripts/check_staged.py unless an allowed media type
            continue
        findings.extend(scan_text(p, text, denylist))
        texts[p] = text
    findings.extend(entropy_findings(texts))
    return findings, binaries


# Findings already in v0.4's public history (S0031 verifier R2-4), excused in a range scan only, each by the exact
# blob or commit and rule: the range from the v0.4 floor (scripts/check_push.py FLOOR) passes on known history and
# refuses anything new. A known blob added again at the tip still fails the scan of all tracked files. Never add a
# row for a blob or commit that is not on origin already.
HISTORY_BASELINE = frozenset({
    ("design/capabilities.md", "4ba70bdd3a2f19ca0e41a3037ea02e2d1cf7cc9d", "live-data-path"),  # privacy-scan: allow secret-entropy
    ("design/capabilities.toml", "1dadc5ffb7ee0fbd5d83238ee7a6ce79400b19e9", "live-data-path"),  # privacy-scan: allow secret-entropy
    ("scripts/tests/test_legacy_api_tests.py", "3f62c80631948379e586d738ef50539a90295c6f", "secret-entropy"),  # privacy-scan: allow secret-entropy
    ("commit", "b81e81e884e6ac34490ac05d218bf37b812fa930", "home-path"),  # privacy-scan: allow secret-entropy
})
# A slice ID opens most subjects (`S0016-detect-secrets: ...`); detect-secrets reads `secrets: <word>` as an
# assigned secret. The entropy rule sees the subject with the slug removed (`S0016: ...`); the other rules see it
# whole, and a value after the slug is still scanned (S0031 verifier R2-3).
SLICE_SUBJECT = re.compile(r"\A(\s*S\d{4})-[a-z0-9-]+:", re.I)


def for_entropy(message: str) -> str:
    return SLICE_SUBJECT.sub(r"\1:", message, count=1)


def run_range(spec: str, denylist: list[str]) -> tuple[list[Finding], list[str], int]:
    """`run` over every (path, blob) the range adds or changes and every commit message; findings name the path
    and the blob, or the commit. HISTORY_BASELINE rows are excused."""
    blobs = [b for b in git_range.changed_blobs(spec) if b.mode != "160000"]  # submodules: refused by check_staged
    contents = git_range.read_blobs([b.sha for b in blobs])
    findings: list[Finding] = []
    binaries: list[str] = []
    texts: dict = {}
    origin: dict[str, tuple[str, str]] = {}  # label -> (path or "commit", sha)
    for b in blobs:
        text = decode(contents[b.sha])
        label = _label((b.path, b.sha))
        origin[label] = (b.path, b.sha)
        if text is None:
            binaries.append(label)
            continue
        findings += [Finding(label, f.line, f.rule, f.sample) for f in scan_text(b.path, text, denylist)]
        texts[(b.path, b.sha)] = text
    messages = git_range.commit_messages(spec)  # S0031 verifier F4: a message is published like a file
    for sha, message in messages:
        label = f"commit {sha[:12]} message"
        origin[label] = ("commit", sha)
        findings += scan_text(label, message, denylist)
        texts[label] = for_entropy(message)
    findings.extend(entropy_findings(texts))
    kept = [f for f in findings if (*origin.get(f.path, ("", "")), f.rule) not in HISTORY_BASELINE]
    return kept, binaries, len(blobs) + len(messages)


def run_message(label: str, message: str, denylist: list[str]) -> list[Finding]:
    """A commit or tag message, everything above git's scissors line (`git commit -v` puts the diff below it)."""
    message = message.split("# ------------------------ >8 ------------------------", 1)[0]
    return scan_text(label, message, denylist) + entropy_findings({label: for_entropy(message)})


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 12):  # the scanner and its tests use 3.12 features; refuse rather than crash
        print("privacy-scan: needs Python 3.12 or newer; refusing to pass", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true", help="scan staged blobs (pre-commit)")
    g.add_argument("--all", action="store_true", help="scan all tracked files")
    g.add_argument("--range", help="BASE..TIP or TIP (40-hex commit names): every blob the range adds or changes, "
                   "and every commit message of the range")
    g.add_argument("--tag-message", metavar="TAG_SHA", help="the message of an annotated tag object (pre-push)")
    g.add_argument("--message", metavar="FILE", help="a commit message file (the commit-msg hook)")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    denylist = load_denylist()
    if args.tag_message is not None or args.message is not None:
        if args.paths:
            ap.error("--tag-message and --message take no paths")
        try:
            if args.tag_message is not None:
                label, text = f"tag {args.tag_message[:12]} message", git_range.tag_message(args.tag_message)
            else:
                label, text = "commit message", Path(args.message).read_text(encoding="utf-8", errors="replace")
            findings = run_message(label, text, denylist)
        except (git_range.RangeError, OSError) as e:
            print(f"privacy-scan: {e}", file=sys.stderr)
            return 2
        except ToolError as e:
            print(f"privacy-scan: {e} (the secret-entropy rule cannot be skipped)", file=sys.stderr)
            return 2
        return report(findings, [], 1, denylist, args.quiet)
    if args.range is not None:
        if args.paths:
            ap.error("--range takes no paths")
        try:
            findings, binaries, count = run_range(args.range, denylist)
        except git_range.RangeError as e:
            print(f"privacy-scan: {e}", file=sys.stderr)
            return 2
        except ToolError as e:
            print(f"privacy-scan: {e} (the secret-entropy rule cannot be skipped; run `uv sync --project "
                  f"scripts/secrets-scan`)", file=sys.stderr)
            return 2
        return report(findings, binaries, count, denylist, args.quiet)
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

    try:
        findings, binaries = run(paths, reader, denylist)
    except ToolError as e:
        print(f"privacy-scan: {e} (the secret-entropy rule cannot be skipped; run `uv sync --project "
              f"scripts/secrets-scan`)", file=sys.stderr)
        return 2
    return report(findings, binaries, len(paths), denylist, args.quiet)


def report(findings: list[Finding], binaries: list[str], count: int, denylist: list[str], quiet: bool) -> int:
    for f in findings:
        print(f, file=sys.stderr)
    if not quiet:
        note = "" if denylist else " (no owner denylist found; amount/name literals not checked)"
        print(
            f"privacy-scan: {count} file(s), {len(findings)} finding(s), "
            f"{len(binaries)} binary file(s) not scanned{note}",
            file=sys.stderr,
        )
        for b in binaries:
            print(f"privacy-scan: binary not scanned: {b}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
