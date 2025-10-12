from pathlib import Path
from typing import Dict, Any
from ruamel.yaml import YAML

from app.config import Config, OFXAccountMapping
from app.core.backup_manager import BackupManager

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
