"""Seed-content refresh — deliver new/updated bundled content to existing users.

`seed_config()` / `seed_data_with_currency()` are first-run only, so an upgrading
user never receives bundled content added or improved after their install (new
budget demo dashboards, a fresher demo ledger, …). This package closes that gap
by *walking the bundle at runtime* (no shipped manifest) and, on the user's
consent, refreshing only files they haven't touched — provenance decides what's
safe. See dev-docs/seed-content-refresh.md.
"""

from __future__ import annotations

from .bundle import SeedFile, content_digest, walk_bundle
from .refresh import (
    RefreshReport,
    apply_seed_refresh,
    preview_refresh,
    record_seed_baseline,
)

__all__ = [
    "SeedFile",
    "content_digest",
    "walk_bundle",
    "RefreshReport",
    "apply_seed_refresh",
    "preview_refresh",
    "record_seed_baseline",
]
