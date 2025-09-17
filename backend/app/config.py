"""
Configuration management for Finzytrack Backend using Pydantic.

Handles loading configuration from YAML files with automatic validation.
Supports CLI argument overrides using flattened argument names.
"""
import os
import yaml
from typing import Dict, Any, List, Optional
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import ValidationError as PydanticValidationError


class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing."""
    pass


class ServerConfig(BaseModel):
    """Server configuration settings."""
    host: str = Field(default="127.0.0.1", description="Server host address")
    port: int = Field(default=8000, ge=1, le=65535, description="Server port")


class DefaultsConfig(BaseModel):
    """Default application settings."""
    currency: str = Field(default="USD", description="Default currency code")
    unknown_account: str = Field(default="Expenses:Unknown", description="Default account for unknown transactions")


class MLConfig(BaseModel):
    """Machine learning configuration."""
    enabled: bool = Field(default=True, description="Enable ML categorization")
    training_data_file: Optional[str] = Field(default="./data/training.beancount", description="Path to training data file")


class FeaturesConfig(BaseModel):
    """Feature toggle configuration."""
    duplicate_detection: bool = Field(default=True, description="Enable duplicate detection")
    auto_categorization: bool = Field(default=True, description="Enable automatic categorization")


class BackupConfig(BaseModel):
    """Backup system configuration."""
    enabled: bool = Field(default=True, description="Enable backup system")
    retention_count: int = Field(default=100, ge=1, description="Number of backups to retain")
    cleanup_on_exceed: bool = Field(default=True, description="Automatically cleanup old backups")


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: str = Field(default="INFO", description="Logging level")
    file: str = Field(default="./logs/finzytrack.log", description="Log file path")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log message format"
    )
    
    @field_validator('level')
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            raise ValueError(f'Logging level must be one of: {valid_levels}')
        return v.upper()


class SecurityConfig(BaseModel):
    """Security-related configuration."""
    cors_origins: List[str] = Field(
        default=["http://127.0.0.1:3000", "http://localhost:3000"],
        description="List of allowed CORS origins for the frontend"
    )


class OFXAccountMapping(BaseModel):
    """OFX account mapping for exact account detection."""
    institution: str = Field(..., description="Institution name from OFX")
    institution_fid: Optional[str] = Field(None, description="Financial Institution ID from OFX")
    account_type: str = Field(..., description="Account type from OFX (empty string for credit cards)")
    account_id: str = Field(..., description="Full account ID from OFX")
    currency: str = Field(..., description="Currency from OFX")
    beancount_account: str = Field(..., description="Corresponding Beancount account")

# # Legacy class for backward compatibility
# class AccountMapping(BaseModel):
#     """Legacy account mapping for OFX import (deprecated - use OFXAccountMapping)."""
#     institution: str = Field(..., description="Bank institution name")
#     account_type: str = Field(..., description="Account type (CHECKING, SAVINGS, etc.)")
#     account_id: str = Field(..., description="Account ID (masked)")
#     beancount_account: str = Field(..., description="Corresponding Beancount account")
#     currency: str = Field(default="USD", description="Account currency")


class Config(BaseModel):
    """Main application configuration with nested sections."""
    
    # File paths
    ledger_file: str = Field(default="./data/ledgers/main.beancount", description="Path to main Beancount ledger")
    backup_dir: str = Field(default="./data/backups", description="Backup directory path")
    
    # Nested configuration sections
    server: ServerConfig = Field(default_factory=ServerConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    ml: MLConfig = Field(default_factory=MLConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    
    # OFX account mappings
    ofx_account_mappings: List[OFXAccountMapping] = Field(default_factory=list, description="OFX account mappings")

    config_file_path: Optional[Path] = Field(
        default=None,
        exclude=True,
        description="Path to the config file this configuration was loaded from"
    )
    
    @model_validator(mode='after')
    def validate_directory_paths(self) -> 'Config':
        """Validate that required directories exist for file operations."""
        # Validate ledger file directory exists (so we can create/read ledger file)
        ledger_dir = Path(self.ledger_file).parent
        if not ledger_dir.exists():
            raise ValueError(f"Ledger file directory does not exist: {ledger_dir}")
        
        # Validate backup directory exists or can be created
        backup_path = Path(self.backup_dir)
        if not backup_path.exists():
            # Try to create backup directory
            try:
                backup_path.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as e:
                raise ValueError(f"Cannot create backup directory {backup_path}: {e}")
        
        # Validate training data file exists and is readable (if training file is specified)
        if self.ml.enabled and self.ml.training_data_file is None:
            raise ValueError("ML is enabled but no training data file is specified.")
        if self.ml.enabled and self.ml.training_data_file:
            training_file = Path(self.ml.training_data_file)
            if not training_file.exists():
                raise ValueError(f"ML training data file does not exist: {training_file}")
            if not training_file.is_file():
                raise ValueError(f"ML training data path is not a file: {training_file}")
            try:
                with open(training_file, 'r') as f:
                    f.read(1)  # Test readability by reading first byte
            except (PermissionError, OSError) as e:
                raise ValueError(f"ML training data file is not readable: {training_file} ({e})")
        
        return self
    
    @classmethod
    def from_yaml_file(cls, config_path: str, cli_overrides: Optional[Dict[str, Any]] = None) -> 'Config':
        """Load configuration from YAML file with optional CLI overrides."""
        if not os.path.exists(config_path):
            raise ConfigurationError(f"Config file not found: {config_path}")
        
        try:
            with open(config_path, 'r') as f:
                yaml_data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML in config file: {e}")
        except Exception as e:
            raise ConfigurationError(f"Failed to read config file: {e}")
        
        # Apply CLI overrides to YAML data
        if cli_overrides:
            yaml_data = cls._apply_cli_overrides(yaml_data, cli_overrides)
        
        # Create and validate config using Pydantic
        try:
            config = cls.model_validate(yaml_data)
            # Store the config file path for later use
            config.config_file_path = Path(config_path)
            return config
        except PydanticValidationError as e:
            error_messages = []
            for error in e.errors():
                loc = " -> ".join(str(x) for x in error['loc'])
                error_messages.append(f"{loc}: {error['msg']}")
            
            raise ConfigurationError(
                f"Configuration validation failed:\n" + 
                "\n".join(f"  - {msg}" for msg in error_messages)
            )
    
    @staticmethod
    def _apply_cli_overrides(yaml_data: Dict[str, Any], cli_overrides: Dict[str, Any]) -> Dict[str, Any]:
        """Apply CLI overrides to YAML data using flattened key mapping."""
        data = yaml_data.copy()
        
        # Mapping of flattened CLI args to nested YAML paths
        cli_mapping = {
            # Server settings
            'server-host': ('server', 'host'),
            'server-port': ('server', 'port'),
            
            # File paths (top-level)
            'ledger-file': ('ledger_file',),
            'backup-dir': ('backup_dir',),
            
            # ML settings
            'ml-enabled': ('ml', 'enabled'),
            'ml-training-data-file': ('ml', 'training_data_file'),
            
            # Logging settings
            'logging-level': ('logging', 'level'),
            'logging-file': ('logging', 'file'),
        }
        
        for cli_key, value in cli_overrides.items():
            if value is not None and cli_key in cli_mapping:
                path = cli_mapping[cli_key]
                
                # Navigate/create nested structure
                current = data
                for key in path[:-1]:
                    if key not in current:
                        current[key] = {}
                    current = current[key]
                
                # Set the final value
                current[path[-1]] = value
        
        return data
    
    # def get_account_mapping(self, institution: str, account_type: str, account_id: str, institution_fid: Optional[str] = None) -> Optional[OFXAccountMapping]:
    #     """Find matching account mapping."""
    #     for mapping in self.ofx_account_mappings:
    #         # Institution match - prefer FID if available, fallback to name
    #         institution_match = False
    #         if institution_fid and mapping.institution_fid:
    #             institution_match = mapping.institution_fid == institution_fid
    #         else:
    #             institution_match = mapping.institution.upper() == institution.upper()

    #         if (institution_match and
    #             mapping.account_type.upper() == account_type.upper() and
    #             mapping.account_id == account_id):  # Full account ID match!
    #             return mapping

    #     return None
    
    