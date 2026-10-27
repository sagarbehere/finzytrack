#!/usr/bin/env python3
"""Export the API contract offline (ADR-0038; ARCHITECTURE.md §4.1; S0046).

usage (from the repo root): backend/.venv/bin/python -I backend/scripts/export_openapi.py [--stdout]

Builds the app with `app.api.export_openapi` (no server, no I/O) and writes `contracts/openapi.json` (with `--stdout`,
prints it instead). A contract that breaks `app.api.contract.contract_problems` is refused with exit code 1 and not
written. `scripts/check_generated.py` runs it with `--stdout` and fails when the committed file differs; the
frontend's types are generated from the file (`frontend/scripts/gen-api-types.mjs`) in the same commit.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.api import export_openapi
from app.api.contract import contract_problems, render

OUT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.json"


def main(argv: list[str]) -> int:
    doc = export_openapi()
    problems = contract_problems(doc)
    if problems:
        for p in problems:
            sys.stderr.write(f"export_openapi: {p}\n")
        return 1
    text = render(doc)
    if "--stdout" in argv:
        sys.stdout.write(text)
    else:
        OUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
