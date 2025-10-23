from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from ruamel.yaml import YAML
import logging

from app.config import Config, OFXAccountMapping
from app.core.backup_manager import BackupManager

logger = logging.getLogger(__name__)

class ConfigManager:
    """Manages the application's configuration data and persistence."""

    def __init__(self, config: Config, backup_manager: BackupManager):
        self.config = config
        self.backup_manager = backup_manager

    def get_config(self) -> Config:
        """Returns the raw configuration data object."""
        return self.config

    def add_ofx_mapping(self, mapping: OFXAccountMapping) -> None:
        """
        Adds a new OFX account mapping and saves it to the config file
        using an atomic, backed-up write.
        """
        config_file = self.config.config_file_path or Path('./config/config.yaml')
        config_file.parent.mkdir(parents=True, exist_ok=True)

        with self.backup_manager.atomic_write(str(config_file)) as f:
            yaml = YAML()
            yaml.indent(mapping=2, sequence=4, offset=2)
            yaml.preserve_quotes = True
            yaml.width = 4096
            
            data = yaml.load(f) or {}

            if 'ofx_account_mappings' not in data:
                data['ofx_account_mappings'] = []

            mapping_dict = mapping.model_dump(exclude_none=True)
            data['ofx_account_mappings'].insert(0, mapping_dict)

            f.seek(0)
            yaml.dump(data, f)
            f.truncate()

        # Update the in-memory config object as well
        self.config.ofx_account_mappings.insert(0, mapping)

    def save_metabase_state(self, state: Dict[str, Any]) -> None:
        """
        Save Metabase runtime state (initialized, credentials, etc.) to config file.

        Args:
            state: Dictionary with Metabase state fields to update
                   (e.g., initialized, admin_password, session_token, database_id)
        """
        config_file = self.config.config_file_path or Path('./config/config.yaml')
        config_file.parent.mkdir(parents=True, exist_ok=True)

        with self.backup_manager.atomic_write(str(config_file)) as f:
            yaml = YAML()
            yaml.indent(mapping=2, sequence=4, offset=2)
            yaml.preserve_quotes = True
            yaml.width = 4096

            data = yaml.load(f) or {}

            # Ensure analytics.metabase section exists
            if 'analytics' not in data:
                data['analytics'] = {}
            if 'metabase' not in data['analytics']:
                data['analytics']['metabase'] = {}

            # Update state fields
            data['analytics']['metabase'].update(state)

            f.seek(0)
            yaml.dump(data, f)
            f.truncate()

        # Update in-memory config as well
        for key, value in state.items():
            setattr(self.config.analytics.metabase, key, value)

    def reset_metabase_state(self) -> None:
        """
        Resets the Metabase configuration to its default (un-initialized) state.
        """
        from app.config import MetabaseConfig
        default_metabase_config = MetabaseConfig()

        reset_state = {
            "initialized": default_metabase_config.initialized,
            "admin_password": default_metabase_config.admin_password,
            "session_token": default_metabase_config.session_token,
            "database_id": default_metabase_config.database_id
        }

        self.save_metabase_state(reset_state)

    def reload_config(self, new_config_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Reload configuration from new data.

        Validates and updates the in-memory config object. Determines if a restart
        is required based on which fields changed.

        Hot-reloadable fields (no restart required):
        - ml.* (enabled, training_data_file)
        - features.* (duplicate_detection, auto_categorization)
        - backup.* (enabled, retention_count, cleanup_on_exceed)
        - logging.level (and other logging settings)
        - accounts.* (default_currency, default_unknown_account)

        Restart-required fields:
        - server.* (host, port)
        - security.cors_origins
        - ledger_file
        - backup.backup_dir (BackupManager initialized at startup)
        - logging.file (log file handlers set at startup)

        Args:
            new_config_data: Dictionary with new configuration data (from YAML)

        Returns:
            Tuple of (restart_required: bool, restart_reason: Optional[str])
        """
        # Validate and create new config
        new_config = Config.model_validate(new_config_data)

        # Check if restart-required fields changed
        restart_required = False
        restart_reasons = []

        # Server settings require restart
        if (new_config.server.host != self.config.server.host or
            new_config.server.port != self.config.server.port):
            restart_required = True
            restart_reasons.append("server settings changed")

        # CORS origins require restart (middleware configured at startup)
        if new_config.security.cors_origins != self.config.security.cors_origins:
            restart_required = True
            restart_reasons.append("CORS origins changed")

        # Ledger file path change requires restart (BeancountManager initialized at startup)
        if new_config.ledger_file != self.config.ledger_file:
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

        # Update in-memory config
        old_config = self.config
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

        return restart_required, restart_reason
