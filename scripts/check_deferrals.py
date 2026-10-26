#!/usr/bin/env python3
"""Deferral check (S0033, seeded-defect run 1 E10): a pushed change may not move an open item's exit gate later
without citing a decision.

Usage: python3 scripts/check_deferrals.py --range BASE..TIP | TIP     (the pre-push hook, per pushed range)

`design/backlog.md` at BASE (what the remote has) is compared with TIP (what is pushed). An item that was `open` or
`in-progress` at BASE is refused at TIP when it
  - has a later milestone, or none (a bare `M1` counts as `M1a`, ADR-0046),
  - is now `parked`, `post-v0.4` or `removed`,
  - is gone (rows are never deleted; `removed` keeps the record), or the whole file is gone,
unless the item's row at TIP carries a citation it did not carry at BASE: an `ADR-nnnn` whose file exists in
`design/decisions/` at TIP, or an owner quote written `owner: "<words>"`. A deleted row cannot carry one, so it is
always refused. An ID on more than one row at TIP is refused, cited or not (S0033 verifier F2: a reader keeping
the first row and one keeping the last would disagree, so a done row could hide an open duplicate). A fixme test's
`due` must equal its row's milestone (scripts/check_invariants.py, S0029), so moving a fixme later needs this row
move and is refused here.

Only BASE and TIP are compared: a move undone inside the range passes. A bare TIP (no base: a push with no remote
`next` below the v0.4 floor) has nothing to compare and passes. Stated limit: whether the cited ADR or quote says what
the row claims is read in review, not here.
Exit 0 = ok, 1 = refused, 2 = the range cannot be read.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable

import git_range
from check_invariants import MILESTONES, due_milestone, duplicate_ids, table_rows

BACKLOG = "design/backlog.md"
OPEN = ("open", "in-progress")
DEFERRED = ("parked", "post-v0.4", "removed")
ADR = re.compile(r"(?<![\w-])ADR-(\d{4})(?!\d)")
OWNER_QUOTE = re.compile(r"(?<![\w-])owner:\s*\"([^\"\n|]*[^\"\s|][^\"\n|]*)\"")


def citations(line: str, adr_exists: Callable[[str], bool]) -> set[str]:
    """The decisions a row's text cites: existing ADRs and owner quotes."""
    found = {f"ADR-{m.group(1)}" for m in ADR.finditer(line) if adr_exists(f"ADR-{m.group(1)}")}
    return found | {f'owner: "{m.group(1).strip()}"' for m in OWNER_QUOTE.finditer(line)}


def later(base_cell: str, tip_cell: str) -> bool:
    base_due, tip_due = due_milestone(base_cell), due_milestone(tip_cell)
    if tip_due not in MILESTONES:
        return True
    return base_due in MILESTONES and MILESTONES.index(tip_due) > MILESTONES.index(base_due)


def problems(base: str | None, tip: str | None, adr_exists: Callable[[str], bool]) -> list[str]:
    if base is None:
        return []
    base_rows = [r for r in table_rows(base) if r.get("Status") in OPEN]
    if not base_rows:
        return []
    if tip is None:
        return [f"{BACKLOG} is gone, with {len(base_rows)} open item(s); rows are never deleted"]
    tip_table = table_rows(tip)
    # an ID on two rows would let one row be read and the other hide (S0033 verifier F2); never compare such a tip
    out = [f"{bid}: the ID is on more than one row of {BACKLOG}; an item has exactly one"
           for bid in duplicate_ids(tip_table)]
    if out:
        return out
    tip_rows = {r.get("ID", ""): r for r in tip_table}
    for r in base_rows:
        bid, was = r.get("ID", ""), f"{r.get('Status')} at {r.get('Milestone')}"
        now = tip_rows.get(bid)
        if now is None:
            out.append(f"{bid}: was {was} and is gone from {BACKLOG}; rows are never deleted (status `removed`, "
                       "with the owner's decision cited, keeps the record)")
            continue
        status, cell = now.get("Status", ""), now.get("Milestone", "")
        if status in DEFERRED:
            change = f"is now {status}"
        elif status in OPEN and later(r.get("Milestone", ""), cell):
            change = f"moved to {cell!r}"
        else:
            continue
        new = citations(" | ".join(now.values()), adr_exists) - citations(" | ".join(r.values()), adr_exists)
        if not new:
            out.append(f"{bid}: was {was} and {change}, which moves its milestone exit; cite the decision on the row "
                       'in the same push (an existing `ADR-nnnn`, or the owner\'s words as owner: "...")')
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--range", required=True, help="BASE..TIP or TIP (40-hex commit names)")
    args = ap.parse_args(argv)
    try:
        base, tip = git_range.parse_spec(args.range)
        if base is None:
            return 0
        base_text, tip_text = (git_range.blob_at(c, BACKLOG) for c in (base, tip))
        decisions = git_range.tree_names(tip, "design/decisions")
    except git_range.RangeError as exc:
        print(f"check-deferrals: {exc}", file=sys.stderr)
        return 2

    def adr_exists(adr: str) -> bool:
        return any(name.startswith(f"{adr}-") and name.endswith(".md") for name in decisions)

    found = problems(*(t.decode("utf-8", errors="replace") if t is not None else None for t in (base_text, tip_text)),
                     adr_exists)
    for p in found:
        print(f"check-deferrals: REFUSED {p}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
