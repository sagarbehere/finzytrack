#!/usr/bin/env python3
"""Invariant coverage check (design/INVARIANTS.md; design/process/PROCESS.md, "Invariants").

Rules:
  - headings `### INV-NN: <title>`, numbered 01..N without gaps or duplicates;
  - each invariant has **Rule**, **Why**, **Enforced by** and **Status** bullets;
  - Status is `enforced`, `pending (BL-nnnn, by Mx)` or `review-only`;
  - `enforced`: every backticked file path on the Enforced-by line exists and mentions the ID, and at least
    one of them is a test, or a check script that a hook or CI workflow runs;
  - `pending`: the backlog item exists in design/backlog.md and is not marked done; with
    FT_MILESTONE_EXIT=<Mx> set (a milestone-exit run), a pending invariant due by Mx or earlier fails.
Exit 0 = ok, 1 = problems.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MILESTONES = ["M0", "M1a", "M1b", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11", "M12"]
HEAD = re.compile(r"^### (INV-(\d{2})): (.+)$")
BULLET = re.compile(r"^- \*\*(Rule|Why|Enforced by|Status):\*\*\s*(.*)$")
PENDING = re.compile(r"^pending \((BL-\d{4}), by (M\d+[ab]?)\)$")
PATH_TOKEN = re.compile(r"`([^`\s]+)`")
RUNNERS = [".githooks/pre-commit", ".githooks/pre-push", ".github/workflows/ci.yml"]


def parse(text: str) -> list[dict]:
    invs: list[dict] = []
    for line in text.splitlines():
        if m := HEAD.match(line):
            invs.append({"id": m.group(1), "num": int(m.group(2)), "title": m.group(3), "fields": {}})
        elif invs and (b := BULLET.match(line)):
            invs[-1]["fields"][b.group(1)] = b.group(2).strip()
    return invs


def looks_like_path(tok: str) -> bool:
    return "/" in tok and not tok.startswith(("http", "~")) and "." in tok.rsplit("/", 1)[-1]


def is_test(path: str) -> bool:
    name = Path(path).name
    return "/tests/" in f"/{path}" and (name.startswith("test_") or ".test." in name or ".spec." in name)


def runs_somewhere(path: str, root: Path) -> bool:
    for r in RUNNERS:
        p = root / r
        if p.exists() and path in p.read_text(encoding="utf-8"):
            return True
    return False


def backlog_state(backlog: str, bl_id: str) -> str | None:
    """Return the line that defines the backlog item, or None if it is absent."""
    for line in backlog.splitlines():
        if re.search(rf"(?<![\w-]){re.escape(bl_id)}(?![\w-])", line) and ("|" in line or line.lstrip().startswith(("-", "#"))):
            return line
    return None


def check(text: str, root: Path, backlog: str | None, milestone_exit: str | None) -> list[str]:
    errors: list[str] = []
    invs = parse(text)
    if not invs:
        return ["no invariants found"]
    nums = [i["num"] for i in invs]
    if nums != list(range(1, len(invs) + 1)):
        errors.append(f"invariant numbers must run 01..{len(invs):02d} without gaps or duplicates: {nums}")
    for inv in invs:
        iid, f = inv["id"], inv["fields"]
        for field in ("Rule", "Why", "Enforced by", "Status"):
            if not f.get(field):
                errors.append(f"{iid}: missing **{field}:**")
        status = f.get("Status", "")
        if status == "enforced":
            paths = [t for t in PATH_TOKEN.findall(f.get("Enforced by", "")) if looks_like_path(t)]
            if not paths:
                errors.append(f"{iid}: enforced, but Enforced by names no file")
            ok_runner = False
            for p in paths:
                fp = root / p
                if not fp.is_file():
                    errors.append(f"{iid}: enforcement file {p} does not exist")
                    continue
                if iid not in fp.read_text(encoding="utf-8", errors="replace"):
                    errors.append(f"{iid}: enforcement file {p} does not mention {iid}")
                if is_test(p) or runs_somewhere(p, root):
                    ok_runner = True
            if paths and not ok_runner:
                errors.append(f"{iid}: no enforcement file is a test or a check run by a hook or CI")
        elif status == "review-only":
            pass
        elif m := PENDING.match(status):
            bl_id, due = m.groups()
            if due not in MILESTONES:
                errors.append(f"{iid}: unknown milestone {due} (use one of {', '.join(MILESTONES)})")
            if backlog is None:
                errors.append(f"{iid}: pending on {bl_id}, but design/backlog.md does not exist")
            else:
                line = backlog_state(backlog, bl_id)
                if line is None:
                    errors.append(f"{iid}: pending on {bl_id}, which is not in design/backlog.md")
                elif re.search(r"\b(done|closed|removed)\b", line, re.I):
                    errors.append(f"{iid}: {bl_id} is closed but the invariant is still pending")
            if milestone_exit and due in MILESTONES and milestone_exit in MILESTONES \
                    and MILESTONES.index(due) <= MILESTONES.index(milestone_exit):
                errors.append(f"{iid}: still pending at the exit of {milestone_exit} (due by {due})")
        else:
            errors.append(f"{iid}: Status must be 'enforced', 'pending (BL-nnnn, by Mx)' or 'review-only', not {status!r}")
    return errors


def main() -> int:
    text = (ROOT / "design/INVARIANTS.md").read_text(encoding="utf-8")
    bl = ROOT / "design/backlog.md"
    errors = check(text, ROOT, bl.read_text(encoding="utf-8") if bl.exists() else None,
                   os.environ.get("FT_MILESTONE_EXIT"))
    for e in errors:
        print(f"check-invariants: {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
