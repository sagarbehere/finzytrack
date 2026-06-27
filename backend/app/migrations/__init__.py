"""Versioned migration of on-disk user assets (recipes, and future config/rules).

Breaking changes to user assets are upgraded by a general versioned-migration
runner (the standard Alembic/Rails pattern applied to user assets), not one-off
scripts. The recipe v1→v2 migration is the first registered entry.

See dev-docs/refactored-dashboard-recipes.md §4.12.

Public surface:
  - run_startup_migrations(config_dir, *, write_fn=None) — apply all registered
    asset migrations to a config directory on launch (idempotent, safe).
  - recipe_migration: the recipe v1→v2 conversion core (shared by the CLI at
    scripts/migrate_recipes.py).
"""

from .runner import run_startup_migrations

__all__ = ["run_startup_migrations"]
