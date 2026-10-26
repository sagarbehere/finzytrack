#!/usr/bin/env python3
"""Build step: write the demo ledger (or a test/perf corpus) into an output folder (ADR-0022; L10-18).

usage (cwd = backend/):
    .venv/bin/python scripts/gen_demo.py --size {demo,5k,54k} --out DIR [--anchor YYYY-MM-DD | --source-date-epoch N]

With neither flag the anchor is `SOURCE_DATE_EPOCH` from the environment (a release build sets it to the tag's
commit time, `git log -1 --format=%ct <tag>`). With no anchor at all the build refuses: it never uses the build
clock, so two builds of one tag give the same bytes. The output folder must be new or empty, and outside the
source tree except under the ignored `build/` folder: the build never rewrites a tracked file (review:L10 §1.5).
It writes the ledger files and `generated-for.json` (the inputs they were made for).
Exit 0 = written, 2 = refused or failed (no output folder is left; missing parent folders may have been created).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND / "src"))

from app.content.demo import (  # noqa: E402 (the package lives in backend/src)
    Size,
    anchor_from_epoch,
    anchor_from_iso,
    default_fragments,
    generate,
)

GENERATED_FOR = "generated-for.json"


class Refused(Exception):
    pass


def repo_parts(out: Path) -> tuple[str, ...] | None:
    """The parts of *out* below the repository, or None when it is outside.

    The repository is matched by file identity (`samefile`) on each existing ancestor, so another spelling of it
    on a case-insensitive disk is still the repository (S0027 verifier F4).
    """
    for ancestor in (out, *out.parents):
        if ancestor.exists() and os.path.samefile(ancestor, REPO):
            return out.parts[len(ancestor.parts) :]
    return None


def output_folder(raw: str) -> Path:
    """The resolved output folder, or Refused: inside the source tree (outside `build/`), or not empty."""
    out = Path(raw).resolve()
    parts = repo_parts(out)
    if parts is not None and parts[:1] != ("build",):
        msg = f"{out} is in the source tree; write to a temp folder or under {REPO / 'build'} (L10-18)"
        raise Refused(msg)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        msg = f"{out} exists and is not empty; the build never overwrites files"
        raise Refused(msg)
    return out


def write_all(out: Path, files: Mapping[str, str]) -> None:
    """Write *files* into a temp folder beside *out*, then rename it to *out*; on any failure, leave nothing."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    try:
        for rel, text in files.items():
            (tmp / rel).write_text(text, encoding="utf-8", newline="\n")
        if out.exists():
            out.rmdir()  # checked empty by output_folder; rmdir refuses anything else
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def resolve_anchor(iso: str | None, epoch: str | None) -> date:
    """The anchor from a flag, else from SOURCE_DATE_EPOCH; never from the clock (ValueError or Refused)."""
    if iso is not None:
        return anchor_from_iso(iso)
    if epoch is not None:
        return anchor_from_epoch(epoch)
    if "SOURCE_DATE_EPOCH" in os.environ:
        return anchor_from_epoch(os.environ["SOURCE_DATE_EPOCH"])
    msg = "no anchor: give --anchor, --source-date-epoch or SOURCE_DATE_EPOCH (the build clock is never used)"
    raise Refused(msg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", required=True, choices=[s.value for s in Size])
    parser.add_argument("--out", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--anchor", help="the anchor date, YYYY-MM-DD")
    group.add_argument("--source-date-epoch", help="seconds since 1970-01-01 UTC (as SOURCE_DATE_EPOCH)")
    args = parser.parse_args(argv)
    try:
        anchor = resolve_anchor(args.anchor, args.source_date_epoch)
        out = output_folder(args.out)
        ledger = generate(anchor, Size(args.size), default_fragments())  # fully, before anything is written
        write_all(out, {**ledger.files, GENERATED_FOR: ledger.generated_for_json()})
    except (Refused, ValueError, OSError) as e:
        sys.stderr.write(f"gen_demo: refused: {e}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
