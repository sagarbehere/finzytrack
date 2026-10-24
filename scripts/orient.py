#!/usr/bin/env python3
"""Backing script for the `orient` skill: a compact briefing on a feature or change request.

Usage: python3 scripts/orient.py <term> [<term> ...]
Searches (case-insensitive, any term) the capability map, ADRs, invariants, specs, backlog, architecture
and the code, and prints the matches grouped by source, with file:line references. Read-only.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_PER_SOURCE = 15


def rx(terms: list[str]) -> re.Pattern[str]:
    return re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)


def capabilities(pat: re.Pattern[str]) -> list[str]:
    path = ROOT / "design/capabilities.toml"
    if not path.exists():
        return []
    with path.open("rb") as fh:
        caps = tomllib.load(fh)["capability"]
    out = []
    for c in caps:
        hay = " ".join(str(c.get(k, "")) for k in ("id", "capability", "v04_note", "legacy_entry", "backlog"))
        if pat.search(hay):
            out.append(f"{c['id']} [{c['status']}, {c.get('milestone', '—')}, blocks release: {c['release_blocking']}]: "
                       f"{c['capability'][:140]}")
    return out


def adrs(pat: re.Pattern[str]) -> list[str]:
    out = []
    for p in sorted((ROOT / "design/decisions").glob("ADR-*.md")):
        text = p.read_text(encoding="utf-8")
        if pat.search(text):
            title = text.splitlines()[0].lstrip("# ").strip() if text else p.stem
            status = re.search(r"(?im)^\W*status\W*:?\W*(\w+)", text)
            out.append(f"{p.relative_to(ROOT)}: {title}" + (f" ({status.group(1)})" if status else ""))
    return out


def lines_in(pattern: re.Pattern[str], *paths: str) -> list[str]:
    out = []
    for rel in paths:
        base = ROOT / rel
        files = sorted(base.rglob("*.md")) if base.is_dir() else ([base] if base.exists() else [])
        for f in files:
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    out.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()[:160]}")
    return out


def code(terms: list[str]) -> list[str]:
    dirs = [d for d in ("backend/src", "frontend/src", "shell/src-tauri/src", "scripts") if (ROOT / d).exists()]
    if not dirs:
        return []
    args = ["git", "grep", "-n", "-i", "-I"]
    for t in terms:
        args += ["-e", t]
    r = subprocess.run([*args, "--", *dirs], cwd=ROOT, capture_output=True, text=True)
    return [line[:160] for line in r.stdout.splitlines()]


def section(title: str, items: list[str]) -> None:
    print(f"\n## {title} ({len(items)})")
    for item in items[:MAX_PER_SOURCE]:
        print(f"- {item}")
    if len(items) > MAX_PER_SOURCE:
        print(f"- … {len(items) - MAX_PER_SOURCE} more; narrow the terms")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    pat = rx(argv)
    print(f"# Orientation for: {' '.join(argv)}")
    section("Capabilities", capabilities(pat))
    section("ADRs", adrs(pat))
    section("Invariants", lines_in(re.compile(rf"^### INV-.*({pat.pattern})", re.I), "design/INVARIANTS.md")
            or lines_in(pat, "design/INVARIANTS.md"))
    section("Specs", lines_in(pat, "design/specs"))
    section("Backlog", lines_in(pat, "design/backlog.md"))
    section("Architecture", lines_in(pat, "design/ARCHITECTURE.md"))
    section("Code", code(argv))
    print("\nNext: read the listed ADRs (with their rejected alternatives) and spec Orientation blocks; "
          "note gaps (no row, no ADR, no spec) in the brief.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
