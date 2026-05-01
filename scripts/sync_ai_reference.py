#!/usr/bin/env python3
"""Sync source-of-truth files from frontend/ into backend/resources/.

Two destinations:

  ai_reference/  — files read by the AI assistant's `read_reference` tool
                   (recipes.ts, generators.ts, …) when its in-context schema
                   docs aren't enough.
  schemas/       — JSON Schema files used by backend validators
                   (recipe.schema.json drives recipe_validation.py).

Both must be present at runtime in dev mode AND bundled by PyInstaller for the
desktop build. Run this:

  - Once after a fresh git clone (otherwise validators and read_reference fail)
  - Automatically at backend startup in dev mode (see app/main.py lifespan)
  - Before each desktop build (desktop/build.py invokes it)
  - After editing any source file (otherwise the bundled copy is stale)

The script is idempotent: it overwrites existing copies and removes stale ones.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (frontend_relative_src, dest_subdir_under_backend_resources, dest_filename)
SYNC_ITEMS: list[tuple[str, str, str]] = [
    # AI assistant reference files (allowlisted by app/ai/reference.py)
    ("frontend/src/types/recipes.ts",                 "ai_reference", "recipes.ts"),
    ("frontend/src/recipes/functions/generators.ts",  "ai_reference", "generators.ts"),
    # JSON Schemas authoritative for backend validators
    ("frontend/src/types/recipe.schema.json",         "schemas",      "recipe.schema.json"),
]


def main() -> int:
    expected_per_dir: dict[Path, set[str]] = {}
    missing: list[str] = []

    for rel_src, dest_subdir, dest_name in SYNC_ITEMS:
        dest_dir = ROOT / "backend" / "resources" / dest_subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        expected_per_dir.setdefault(dest_dir, set()).add(dest_name)

        src = ROOT / rel_src
        if not src.is_file():
            missing.append(rel_src)
            continue

        shutil.copy2(src, dest_dir / dest_name)
        print(f"  synced {rel_src} -> {dest_subdir}/{dest_name}")

    # Remove any stale files in each managed dir
    for dest_dir, expected in expected_per_dir.items():
        for existing in dest_dir.iterdir():
            if existing.is_file() and existing.name not in expected:
                existing.unlink()
                print(f"  removed stale {dest_dir.name}/{existing.name}")

    if missing:
        print("ERROR: source files missing:", *missing, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
