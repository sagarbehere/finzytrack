#!/usr/bin/env python3
"""Recipe v1 → v2 migration CLI (seed-config path).

Thin wrapper over the shared conversion core in
``backend/app/migrations/recipe_migration.py`` — the same code the startup
runner applies to the user's active config (§4.12 "one function, two call
sites"). Use this to dogfood-migrate the committed seed recipes and to
hand-migrate any directory.

Usage:
    python scripts/migrate_recipes.py <recipes_dir> [<recipes_dir> ...]
    python scripts/migrate_recipes.py --check <recipes_dir>   # report only, no writes
"""

from __future__ import annotations

import sys
from pathlib import Path

# Import the shared core from the backend package.
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.migrations.recipe_migration import migrate_recipes_dir  # noqa: E402


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    write = "--check" not in argv
    if not args:
        print(__doc__)
        return 2
    overall_errors = 0
    for d in args:
        path = Path(d)
        if not path.is_dir():
            print(f"not a directory: {path}", file=sys.stderr)
            overall_errors += 1
            continue
        report = migrate_recipes_dir(path, write=write)
        print(f"[{path}] {report.summary()}")
        for oid, dash_id in report.rehomed_orphans:
            print(f"  orphan widget '{oid}' rehomed → dashboard '{dash_id}'")
        for line in report.errors:
            print(f"  ! {line}")
        overall_errors += len(report.errors)
    return 1 if overall_errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
