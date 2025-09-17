from pathlib import Path
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
