"""Versioned migration of on-disk user assets (recipes, and future config/rules).

Breaking changes to user assets are upgraded by a general versioned-migration
runner (the standard Alembic/Rails pattern applied to user assets), not one-off
scripts. The recipe v1→v2 migration is the first registered entry.

Migrations are applied on user consent via the startup-task framework
(app/startup_tasks), not automatically at launch. See dev-docs/upgrades.md and
dev-docs/refactored-dashboard-recipes.md §4.12.

Public surface:
  - apply_recipe_migration(recipes_dir) — apply the recipe v1→v2 migration with
    backups (idempotent, safe). Called by the recipe startup task on consent.
  - recipe_migration: the conversion core + read-only detect_pending (shared by
    the CLI at scripts/migrate_recipes.py).
"""

from .runner import apply_recipe_migration

__all__ = ["apply_recipe_migration"]
