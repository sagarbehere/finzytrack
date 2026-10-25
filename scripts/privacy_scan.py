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
  secret-entropy what detect-secrets 1.5.0 finds (vendor key formats, secret keywords, high-entropy strings),
                run from its pinned environment `scripts/secrets-scan/` with network verification off, on every
                blob whatever its name (BL-0035); a 64-hex value under a `sha256` or `checksum` key is a hash
                pin. If the tool cannot run, the scan fails (exit 2), never passes
  home-path     a real home directory (/Users/<name>, /home/<name>, /Volumes/<name>, ~<name>/, C:\\Users\\<name>)
  live-data-path the name of the owner's live data folder
  denylist      a literal from the owner's denylist (kept OUTSIDE the repo)

The owner's denylist (amounts, names, account fragments) is read from $FT_PRIVACY_DENYLIST or
~/.config/finzytrack/privacy-denylist.txt, one literal per line, '#' for comments. It is never
committed. A line containing `privacy-scan: allow <rule>` suppresses that rule on that line;
every suppression is visible in review.

Matches are printed redacted. Exit status: 0 clean, 1 findings, 2 usage or internal error.
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


def detect_secrets(texts: dict[str, str]) -> dict[str, list[tuple[int, str]]]:
    """{path: [(line, hashed_secret)]} from detect-secrets over the given texts. Each text is written to the
    temporary folder under a generated name (`blob00000.txt`, ...), never under its own path, so no path can
    leave the folder or be read as an option (EP-30); the results are mapped back by that name."""
    if not texts:
        return {}
    # The file's own suffix is kept when it is plain (detect-secrets picks its keyword rules by file type);
    # nothing else of the path reaches the tool.
    names = {f"blob{i:05d}{suffix(path)}": path for i, path in enumerate(texts)}
    with tempfile.TemporaryDirectory() as tmp:
        for name, path in names.items():
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
    return {names[name]: [(r["line_number"], r["hashed_secret"]) for r in found] for name, found in results.items()}


def entropy_findings(texts: dict[str, str]) -> list[Finding]:
    out: list[Finding] = []
    for path, hits in detect_secrets(texts).items():
        # as detect-secrets reads files (universal newlines): CRLF, CR and LF each end a line, nothing else does
        lines = texts[path].replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for lineno, hashed in sorted(set(hits)):  # one finding per value and line, whichever plugins found it
            line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            allowed = {r.strip() for m in ALLOW_MARK.finditer(line) for r in m.group(1).split(",")}
            if "secret-entropy" in allowed:
                continue
            tokens = [m.group(1) or m.group(2) for m in CANDIDATE.finditer(line)]
            token = next((t for t in tokens if hashlib.sha1(t.encode()).hexdigest() == hashed), "")  # noqa: S324
            if token and any(m.group(1) == token for m in HASH_PIN.finditer(line)):
                continue  # a hash pin, not a secret
            out.append(Finding(path, lineno, "secret-entropy", redact(token) if token else "(value not located)"))
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

    try:
        findings, binaries = run(paths, reader, denylist)
    except ToolError as e:
        print(f"privacy-scan: {e} (the secret-entropy rule cannot be skipped; run `uv sync --project "
              f"scripts/secrets-scan`)", file=sys.stderr)
        return 2
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
