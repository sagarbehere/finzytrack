"""
Factory functions for creating, starting up, and shutting down per-user
service bundles.

``create_user_services`` is the single place where all managers for a user
are instantiated and wired together.  Both desktop mode (single user at
app startup) and hosted mode (lazy per-user creation via ServiceRegistry)
call through here.
"""

import logging
import shutil
import sys
from pathlib import Path

from app.config import Config, SQLITE_AUTO_SYNC, SQLITE_SYNC_DEBOUNCE_SECONDS, SQLITE_ENABLE_WAL
from app.core.backup_manager import BackupManager
from app.core.config_manager import ConfigManager
from app.core.csv_rules_manager import CsvRulesManager
from app.core.xls_rules_manager import XlsRulesManager
from app.core.ledger_initializer import LedgerInitializer
from app.core.ledger_manager import LedgerManager
from app.services.sqlite_exporter import SQLiteExporter
from app.services.db_sync_manager import DBSyncManager
from app.email_import.rule_registry import AccountProfileRegistry
from app.user_services import UserServices
from app.write_lock import WriteLockManager

logger = logging.getLogger(__name__)


# ── Seed config location (dev vs. packaged) ────────────────────────────────

_SEED_CONFIG_DIR_DEV = Path(__file__).parents[1] / "resources" / "seed_config"
_SEED_CONFIG_DIR_FROZEN = (
    Path(getattr(sys, "_MEIPASS", "")) / "backend" / "seed_config"
)
SEED_CONFIG_DIR = (
    _SEED_CONFIG_DIR_FROZEN if getattr(sys, "frozen", False) else _SEED_CONFIG_DIR_DEV
)


def seed_config(config_dir: Path) -> None:
    """Copy seed config template to *config_dir* on first run.

    Copies the bundled ``seed_config/`` (default config.yaml, empty rule
    directories, default dashboard recipes) into the working config
    directory.  Skipped if *config_dir* already exists — user data is
    never overwritten.
    """
    if config_dir.exists():
        return
    if not SEED_CONFIG_DIR.is_dir():
        return
    shutil.copytree(SEED_CONFIG_DIR, config_dir)
    logger.info("Seeded config directory → %s", config_dir)


# ── Service creation ────────────────────────────────────────────────────────


def create_user_services(config: Config, user_id: str = "local") -> UserServices:
    """Create a complete ``UserServices`` bundle from a ``Config``.

    This mirrors steps 1–7 of the original ``create_app()`` service
    instantiation, extracted so that both desktop startup and the hosted
    ``ServiceRegistry`` share the same logic.
    """
    # 1. BackupManager
    backup_manager = BackupManager(
        backup_dir=Path(config.backup_dir),
        retention_count=config.backup.retention_count,
    )

    # 2. ConfigManager
    config_manager = ConfigManager(config=config, backup_manager=backup_manager)

    # 2b. Rule managers
    csv_rules_manager = CsvRulesManager(rules_dir=config.csv_rules_dir)
    xls_rules_manager = XlsRulesManager(rules_dir=config.xls_rules_dir)

    # 2c. Email import
    email_rules_path = Path(config.email_rules_dir)
    email_registry = AccountProfileRegistry(email_rules_path)
    if config.email_import.enabled:
        logger.info(
            "Email import enabled: %d profiles from %s",
            email_registry.profile_count,
            email_rules_path,
        )
    else:
        logger.info("Email import disabled (email_import.enabled=false)")

    # 3. LedgerInitializer
    ledger_initializer = LedgerInitializer(
        ledger_file=config.ledger_file,
        default_currency=config.accounts.default_currency,
        backup_manager=backup_manager,
    )

    # 4. LedgerManager (with per-user write lock)
    write_lock = WriteLockManager(user_id=user_id)
    ledger_manager = LedgerManager(
        ledger_file=config.ledger_file,
        backup_manager=backup_manager,
        ledger_initializer=ledger_initializer,
        write_lock=write_lock,
    )

    # 5. SQLite exporter + debounced sync
    sqlite_exporter = SQLiteExporter(
        sqlite_path=config.sqlite_export_path,
        enable_wal=SQLITE_ENABLE_WAL,
    )
    db_sync_manager = DBSyncManager(
        exporter=sqlite_exporter,
        ledger_file=config.ledger_file,
        delay=SQLITE_SYNC_DEBOUNCE_SECONDS,
        db_type="sqlite",
    )

    # 5b. Hot ledger switching references
    config_manager.set_ledger_services(ledger_manager, db_sync_manager)

    # 6. Ensure ledger exists (skip when setup is incomplete)
    if config.setup_complete:
        try:
            if not ledger_initializer.ensure_ledger_exists():
                raise RuntimeError(
                    f"Failed to create ledger file at {config.ledger_file}"
                )
            logger.info("Ledger verified/created: %s", config.ledger_file)
        except Exception as e:
            logger.error(
                "Fatal: Cannot initialize ledger file %s: %s",
                config.ledger_file,
                e,
            )
            raise RuntimeError(
                f"Failed to initialize ledger file {config.ledger_file}: {e}"
            ) from e
    else:
        logger.info("Setup not complete — skipping ledger initialization")

    # 7. Register SQLite sync callback
    if SQLITE_AUTO_SYNC:
        ledger_manager.register_cache_invalidation_callback(
            db_sync_manager.on_ledger_changed
        )
        logger.info("SQLite auto-sync enabled with debouncing")

    return UserServices(
        ledger_manager=ledger_manager,
        config_manager=config_manager,
        backup_manager=backup_manager,
        csv_rules_manager=csv_rules_manager,
        xls_rules_manager=xls_rules_manager,
        email_registry=email_registry,
        sqlite_exporter=sqlite_exporter,
        db_sync_manager=db_sync_manager,
    )


async def startup_user_services(services: UserServices, config: Config) -> None:
    """Run async post-creation setup (e.g. SQLite export on startup)."""
    if config.setup_complete and services.db_sync_manager._needs_export():
        try:
            entries = services.ledger_manager.cache.get_entries()
            await services.sqlite_exporter.export_entries(entries)
            logger.info("SQLite exported on startup for user services")
        except Exception as e:
            logger.error("Failed to export SQLite on startup: %s", e)


async def shutdown_user_services(services: UserServices) -> None:
    """Cancel pending async tasks for graceful cleanup."""
    try:
        await services.db_sync_manager.cancel_pending_sync()
    except Exception as e:
        logger.error("Error cancelling pending sync: %s", e)
