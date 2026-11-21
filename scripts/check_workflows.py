#!/usr/bin/env python3
"""The workflow files parse as GitHub reads them (S0140, BL-0292).

CI runs only when started by hand (ADR-0060), so a workflow file GitHub cannot parse was found only when a manual run
did not start (S0129's 89a61f03: a step name holding `: ` in a plain scalar). The pre-push hook runs this check, so
such a file is refused at push. Every `*.yml` and `*.yaml` file in `.github/workflows/` (the working tree, which the
push gate has already proved to be what is pushed) must:
  - be UTF-8;
  - parse as one YAML 1.2 document (the version GitHub reads: `on:` is the text "on", not true), with no duplicate key;
  - hold a mapping at the top with a non-empty `on` and a non-empty `jobs` mapping.
A folder with no workflow file is refused too, so a wrong folder cannot pass by checking nothing.

Stated limits: GitHub's own schema is not checked (unknown keys, a job without `runs-on` or `steps`, expressions,
action references, `needs` naming a missing job); actionlint would check it, and is not a dependency. YAML parsers
differ at the edges, so a file this parser reads may still be one GitHub refuses.

Runs in the oracle environment, which pins the YAML library (ruamel.yaml; no other project environment has one):
  uv run --project scripts/oracle --locked python scripts/check_workflows.py [--folder DIR]
Exit 0 = every file passes, 1 = refused (one line per problem on stderr).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / ".github" / "workflows"
SUFFIXES = (".yml", ".yaml")


def workflow_files(folder: Path) -> list[Path]:
    """The files GitHub reads as workflows: `*.yml` and `*.yaml` directly in the folder."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in SUFFIXES and p.is_file())


def problems_of(raw: bytes) -> list[str]:
    """What keeps GitHub from reading `raw` as a workflow; empty when nothing does."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"not UTF-8 (byte {e.start})"]
    try:
        doc = YAML(typ="safe", pure=True).load(text)
    except YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark is not None else ""
        problem = getattr(e, "problem", None) or str(e).splitlines()[0]
        return [f"not YAML GitHub can parse: {where}{problem}"]
    if not isinstance(doc, dict):
        return [f"the top level is {type(doc).__name__ if doc is not None else 'empty'}, not a mapping"]
    out: list[str] = []
    if doc.get("on") in (None, "", [], {}):
        out.append("no `on` (what starts the workflow)")
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        out.append("no `jobs` mapping with at least one job")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", type=Path, default=FOLDER, help="the workflow folder (default: the repository's)")
    args = parser.parse_args(argv)
    files = workflow_files(args.folder)
    if not files:
        print(f"workflow files: refused: no workflow file (*.yml, *.yaml) in {args.folder}", file=sys.stderr)
        return 1
    found = [(path, p) for path in files for p in problems_of(path.read_bytes())]
    for path, p in found:
        print(f"workflow files: refused: {path.name}: {p}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
