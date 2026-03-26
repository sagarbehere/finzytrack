from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING
from ruamel.yaml import YAML
import logging

from app.config import Config, OFXAccountMapping
from app.core.backup_manager import BackupManager
from app.exceptions import APIError

if TYPE_CHECKING:
    from app.core.beancount_manager import BeancountManager
    from app.services.db_sync_manager import DBSyncManager

logger = logging.getLogger(__name__)

class ConfigManager:
    """Manages the application's configuration data and persistence."""

    def __init__(self, config: Config, backup_manager: BackupManager):
        self.config = config
        self.backup_manager = backup_manager
        # Optional references for hot-reloading; set via set_ledger_services()
        self._beancount_manager: Optional[BeancountManager] = None
        self._sqlite_sync_manager: Optional[DBSyncManager] = None

    def set_ledger_services(
        self,
        beancount_manager: BeancountManager,
        sqlite_sync_manager: DBSyncManager,
    ) -> None:
        """
        Provide references to services needed for hot ledger switching.

        Called once during app startup after all services are created.
        """
        self._beancount_manager = beancount_manager
        self._sqlite_sync_manager = sqlite_sync_manager

    def get_config(self) -> Config:
        """Returns the raw configuration data object."""
        return self.config

    def _get_ofx_mappings_path(self) -> Path:
        """Get the path to the OFX mappings file."""
        if self.config.ofx_mappings_file:
            return Path(self.config.ofx_mappings_file)
        # Default: same directory as config file
        config_dir = (self.config.config_file_path or Path('./config/config.yaml')).parent
        return config_dir / 'ofx_mappings.yaml'

    def get_ofx_mappings(self) -> List[OFXAccountMapping]:
        """Load OFX account mappings from the dedicated mappings file."""
        mappings_file = self._get_ofx_mappings_path()
        if not mappings_file.exists():
            return []

        yaml = YAML()
        with open(mappings_file, 'r') as f:
            data = yaml.load(f)

        if not data:
            return []

        return [OFXAccountMapping.model_validate(m) for m in data]

    def add_ofx_mapping(self, mapping: OFXAccountMapping) -> None:
        """
        Adds a new OFX account mapping to the dedicated mappings file
        using an atomic, backed-up write.
        """
        mappings_file = self._get_ofx_mappings_path()
        mappings_file.parent.mkdir(parents=True, exist_ok=True)

        with self.backup_manager.atomic_write(str(mappings_file)) as f:
            yaml = YAML()
            yaml.indent(mapping=2, sequence=4, offset=2)
            yaml.preserve_quotes = True
            yaml.width = 4096

            data = yaml.load(f) or []

            mapping_dict = mapping.model_dump(exclude_none=True)
            data.insert(0, mapping_dict)

            f.seek(0)
            yaml.dump(data, f)
            f.truncate()

    def reload_config(self, new_config_data: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Reload configuration from new data.

        Validates and updates the in-memory config object. Determines if a restart
        is required based on which fields changed.

        Hot-reloadable fields (no restart required):
        - ledger_file (hot-switched: validates file, updates cache & SQLite)
        - ml.* (enabled, training_data_file)
        - features.* (duplicate_detection, auto_categorization)
        - backup.* (enabled, retention_count, cleanup_on_exceed)
        - logging.level (and other logging settings)
        - accounts.* (default_currency, default_unknown_account)

        Restart-required fields:
        - server.* (host, port)
        - security.cors_origins
        - backup.backup_dir (BackupManager initialized at startup)
        - logging.file (log file handlers set at startup)

        Args:
            new_config_data: Dictionary with new configuration data (from YAML)

        Returns:
            Tuple of (restart_required, restart_reason, notice).
            ``notice`` is an optional informational message for the user
            (e.g. "new ledger file was created").
        """
        # Validate and create new config
        new_config = Config.model_validate(new_config_data)

        # Check if restart-required fields changed
        restart_required = False
        restart_reasons = []
        notice: Optional[str] = None

        # Server settings require restart
        if (new_config.server.host != self.config.server.host or
            new_config.server.port != self.config.server.port):
            restart_required = True
            restart_reasons.append("server settings changed")

        # CORS origins require restart (middleware configured at startup)
        if new_config.security.cors_origins != self.config.security.cors_origins:
            restart_required = True
            restart_reasons.append("CORS origins changed")

        # Ledger file path change — hot-switch if services are available
        if new_config.ledger_file != self.config.ledger_file:
            if self._beancount_manager is not None and self._sqlite_sync_manager is not None:
                notice = self._switch_ledger(new_config.ledger_file)
            else:
                restart_required = True
                restart_reasons.append("ledger file path changed")

        # Backup directory change requires restart (BackupManager initialized at startup)
        if new_config.backup.backup_dir != self.config.backup.backup_dir:
            restart_required = True
            restart_reasons.append("backup directory changed")

        # Log file path change requires restart (log handlers set at startup)
        if new_config.logging.file != self.config.logging.file:
            restart_required = True
            restart_reasons.append("log file path changed")

        # Update in-memory config, preserving the config file path
        old_config = self.config
        new_config.config_file_path = old_config.config_file_path
        self.config = new_config

        # Apply hot-reloadable settings that need runtime updates
        if new_config.logging.level != old_config.logging.level:
            try:
                logging.getLogger().setLevel(new_config.logging.level)
                logger.info(f"Updated logging level to {new_config.logging.level}")
            except Exception as e:
                logger.warning(f"Failed to update logging level: {e}")

        restart_reason = "; ".join(restart_reasons) if restart_reasons else None

        if restart_required:
            logger.warning(f"Config reload requires restart: {restart_reason}")
        else:
            logger.info("Config reloaded successfully (no restart required)")

        return restart_required, restart_reason, notice

    def _switch_ledger(self, new_ledger_file: str) -> Optional[str]:
        """
        Hot-switch to a different ledger file at runtime.

        If the file does not exist it is created from the default template
        using LedgerInitializer. Validates accessibility, then updates all
        dependent services. Raises APIError with a user-friendly message
        if the new file cannot be used.

        Returns:
            An optional notice string for the user (e.g. when a new file
            was created), or None.
        """
        assert self._beancount_manager is not None
        assert self._sqlite_sync_manager is not None

        ledger_path = Path(new_ledger_file)
        notice: Optional[str] = None

        # If file doesn't exist, try to create a new ledger
        if not ledger_path.exists():
            # Point the initializer at the new path and attempt creation
            self._beancount_manager.ledger_initializer.ledger_file = new_ledger_file
            created = self._beancount_manager.ledger_initializer.ensure_ledger_exists()
            if not created:
                raise APIError(
                    f"Ledger file does not exist and could not be created: {new_ledger_file}",
                    code="LEDGER_CREATE_FAILED",
                    status_code=400,
                    details={"path": new_ledger_file},
                )
            notice = f"Ledger file did not exist — a new ledger was created at {new_ledger_file}"
            logger.info(notice)

        if not ledger_path.is_file():
            raise APIError(
                f"Ledger path is not a file: {new_ledger_file}",
                code="LEDGER_INVALID",
                status_code=400,
                details={"path": new_ledger_file},
            )

        if not os.access(ledger_path, os.R_OK):
            raise APIError(
                f"Ledger file is not readable: {new_ledger_file}",
                code="LEDGER_NOT_READABLE",
                status_code=400,
                details={"path": new_ledger_file},
            )

        # Switch the beancount manager (cache, initializer, etc.)
        self._beancount_manager.switch_ledger(new_ledger_file)

        # Update the sync manager so mtime checks use the new file
        self._sqlite_sync_manager.ledger_file = new_ledger_file

        # Trigger an immediate cache parse + SQLite re-export so the
        # new ledger's data is available right away
        try:
            entries = self._beancount_manager.cache.get_entries()
            # Notify cache invalidation callbacks (triggers debounced SQLite export)
            for callback in self._beancount_manager._on_cache_invalidated_callbacks:
                try:
                    callback(entries)
                except Exception as e:
                    logger.error(f"Error in cache invalidation callback after ledger switch: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Failed to parse new ledger after switch: {e}", exc_info=True)
            raise APIError(
                f"Ledger file could not be parsed: {e}",
                code="LEDGER_PARSE_ERROR",
                status_code=400,
                details={"path": new_ledger_file, "error": str(e)},
            )

        logger.info(f"Hot-switched to ledger: {new_ledger_file}")
        return notice
