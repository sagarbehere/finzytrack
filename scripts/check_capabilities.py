#!/usr/bin/env python3
"""T1 capability-map check (design/process/PROCESS.md, "Project memory").

Checks design/capabilities.toml:
  - every row has the required fields with allowed values; IDs are unique and look like CAP-<AREA>-<NAME>;
  - planned/partial/built rows name a milestone M0-M12; cut and deferred rows block nothing;
  - every `built` row has at least one test tagged with its ID somewhere under backend/, frontend/ or shell/;
  - every ADR it cites exists in design/decisions/; every backlog ID it cites exists in design/backlog.md
    (reported, not failed, until the backlog is seeded);
  - design/capabilities.md is up to date (via scripts/check_generated.py).
Exit 0 = ok, 1 = problems.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["id", "area", "capability", "v04", "status", "release_blocking"]
V04 = {"keep", "change", "cut", "new", "deferred"}
STATUS = {"planned", "partial", "built", "cut", "deferred"}
BLOCKING = {"yes", "no", "n/a"}
MILESTONE = re.compile(r"^M(1[0-2]|[0-9])$")
CAP_ID = re.compile(r"^CAP-[A-Z0-9]+(-[A-Z0-9]+)+$")
TEST_DIRS = ("backend", "frontend", "shell", "scripts/tests")


def tagged_test_exists(cap_id: str) -> bool:
    dirs = [d for d in TEST_DIRS if (ROOT / d).exists()]
    if not dirs:
        return False
    r = subprocess.run(["git", "grep", "-l", "-F", cap_id, "--", *dirs], cwd=ROOT, capture_output=True, text=True)
    return any("test" in line for line in r.stdout.splitlines())


def check(caps: list[dict], adr_ids: set[str], backlog_text: str | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    seen: set[str] = set()
    for i, c in enumerate(caps):
        where = c.get("id", f"row {i + 1}")
        for f in REQUIRED:
            if not c.get(f):
                errors.append(f"{where}: missing field '{f}'")
        cid = c.get("id", "")
        if cid in seen:
            errors.append(f"{cid}: duplicate id")
        seen.add(cid)
        if cid and not CAP_ID.match(cid):
            errors.append(f"{cid}: id must look like CAP-<AREA>-<NAME>")
        if c.get("v04") not in V04:
            errors.append(f"{where}: v04 must be one of {sorted(V04)}")
        status = c.get("status")
        if status not in STATUS:
            errors.append(f"{where}: status must be one of {sorted(STATUS)}")
        if c.get("release_blocking") not in BLOCKING:
            errors.append(f"{where}: release_blocking must be one of {sorted(BLOCKING)}")
        if status in {"planned", "partial", "built"} and not MILESTONE.match(c.get("milestone", "")):
            errors.append(f"{where}: a {status} row needs a milestone M0-M12")
        if status in {"cut", "deferred"} and c.get("release_blocking") == "yes":
            errors.append(f"{where}: a {status} row cannot block the release")
        if status == "built" and not tagged_test_exists(cid):
            errors.append(f"{cid}: status built but no test is tagged with {cid}")
        for adr in c.get("adrs", []):
            if adr not in adr_ids:
                errors.append(f"{where}: cites {adr}, which does not exist in design/decisions/")
        if backlog_text is not None:
            for b in c.get("backlog", []):
                if not re.search(rf"(?<![\w.-]){re.escape(b)}(?![\w-])", backlog_text):
                    notes.append(f"{where}: backlog ID {b} not found in design/backlog.md")
    return errors, notes


def main() -> int:
    with (ROOT / "design/capabilities.toml").open("rb") as fh:
        caps = tomllib.load(fh)["capability"]
    adr_ids = {m.group(0) for p in (ROOT / "design/decisions").glob("ADR-*.md") if (m := re.match(r"ADR-\d{4}", p.name))}
    backlog = ROOT / "design/backlog.md"
    errors, notes = check(caps, adr_ids, backlog.read_text(encoding="utf-8") if backlog.exists() else None)
    for n in notes:
        print(f"check-capabilities: note: {n}", file=sys.stderr)
    for e in errors:
        print(f"check-capabilities: {e}", file=sys.stderr)
    gen = subprocess.run([sys.executable, str(ROOT / "scripts/check_generated.py"), "--only", "design/capabilities.md"])
    return 1 if errors or gen.returncode else 0


if __name__ == "__main__":
    sys.exit(main())
