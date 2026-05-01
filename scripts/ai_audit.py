#!/usr/bin/env python3
"""Aggregate AI validator failures from data/ai_diagnostics.jsonl.

Usage:
  python scripts/ai_audit.py                  # summary across all-time
  python scripts/ai_audit.py --top 20         # show top 20 hints/fields
  python scripts/ai_audit.py --since 7d       # only entries from last 7 days
  python scripts/ai_audit.py --tool preview_recipe   # filter by tool
  python scripts/ai_audit.py --raw            # print one record per line

The log is append-only and never rotated automatically. Delete the file (or
truncate it) when you've consumed its insights and want to reset the counters.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "backend" / "data" / "ai_diagnostics.jsonl"


def parse_since(spec: str) -> datetime | None:
    if not spec:
        return None
    m = re.fullmatch(r"(\d+)([dhm])", spec)
    if not m:
        raise ValueError(f"--since expects e.g. '7d', '24h', '30m'; got {spec!r}")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
    return datetime.now(timezone.utc) - delta


def _candidate_paths(path: Path) -> list[Path]:
    """The active file plus any rotated backups (.1 .2 .3 ...)."""
    out: list[Path] = []
    if path.is_file():
        out.append(path)
    for i in range(1, 100):
        backup = path.with_suffix(path.suffix + f".{i}")
        if backup.is_file():
            out.append(backup)
        else:
            break
    return out


def load_records(path: Path, since: datetime | None, tool: str | None):
    paths = _candidate_paths(path)
    if not paths:
        print(f"No diagnostics log at {path}. Nothing to audit.", file=sys.stderr)
        return
    for p in paths:
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if tool and rec.get("tool") != tool:
                    continue
                if since:
                    ts_raw = rec.get("ts", "")
                    try:
                        ts = datetime.fromisoformat(ts_raw)
                    except ValueError:
                        continue
                    if ts < since:
                        continue
                yield rec


def main() -> int:
    p = argparse.ArgumentParser(description="Aggregate AI validator failures")
    p.add_argument("--path", type=Path, default=DEFAULT_PATH, help=f"Path to JSONL (default: {DEFAULT_PATH.relative_to(ROOT)})")
    p.add_argument("--since", default="", help="Only include entries from the last Nd/Nh/Nm")
    p.add_argument("--tool", default="", help="Filter by tool name")
    p.add_argument("--top", type=int, default=10, help="Show top N hints / fields (default: 10)")
    p.add_argument("--raw", action="store_true", help="Print each record verbatim")
    args = p.parse_args()

    since = parse_since(args.since) if args.since else None
    tool = args.tool or None

    if args.raw:
        for rec in load_records(args.path, since, tool):
            print(json.dumps(rec, ensure_ascii=False))
        return 0

    by_tool: Counter[str] = Counter()
    by_field: Counter[str] = Counter()
    by_hint: Counter[str] = Counter()
    total = 0
    for rec in load_records(args.path, since, tool):
        total += 1
        by_tool[rec.get("tool", "?")] += 1
        for f in rec.get("first_fields", []):
            by_field[f] += 1
        for h in rec.get("hints", []):
            by_hint[h] += 1

    if total == 0:
        print(f"No diagnostics records matched (path={args.path}, since={args.since or 'all-time'}, tool={tool or 'any'}).")
        return 0

    def render(title: str, counter: Counter[str]) -> None:
        print(f"\n{title}")
        if not counter:
            print("  (none)")
            return
        for name, count in counter.most_common(args.top):
            print(f"  {count:5d}  {name}")

    print(f"=== AI validator audit  ({total} failures, since={args.since or 'all-time'}, tool={tool or 'any'}) ===")
    render("By tool:", by_tool)
    render(f"Top {args.top} fields:", by_field)
    render(f"Top {args.top} hints:", by_hint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
