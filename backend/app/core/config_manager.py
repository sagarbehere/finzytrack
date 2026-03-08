from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
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
