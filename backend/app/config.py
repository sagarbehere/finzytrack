"""
Configuration management for Finzytrack Backend using Pydantic.

Handles loading configuration from YAML files with automatic validation.
Supports CLI argument overrides using flattened argument names.
"""
import os
import yaml
from enum import Enum
from typing import Dict, Any, List, Literal, Optional
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import ValidationError as PydanticValidationError


class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing."""
    pass


# ── Fixed paths (not user-configurable) ──────────────────────────────────────
SQLITE_EXPORT_PATH = "./data/ledger.db"
BACKUP_DIR = "./data/backups"
LOG_FILE = "./logs/finzytrack.log"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
CORS_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]


class ServerConfig(BaseModel):
    """Server configuration settings."""
    host: str = Field(default="127.0.0.1", description="Server host address")
    port: int = Field(default=8000, ge=1, le=65535, description="Server port")


class AccountsConfig(BaseModel):
    """Account-related configuration settings."""
    default_currency: str = Field(default="USD", description="Default currency code")
    default_unknown_account: str = Field(default="Expenses:Unknown", description="Default account for unknown transactions")


class CategorizationEngine(str, Enum):
    """Categorization engine selection."""
    CLASSIFIER = "classifier"  # scikit-learn classifier trained on local beancount history
    AI = "ai"                  # AI-based categorization (future)
    LLM = "llm"                # Alias for "ai" (backward compat)


class CategorizationConfig(BaseModel):
    """Transaction auto-categorization configuration."""
    enabled: bool = Field(default=True, description="Enable auto-categorization")
    engine: CategorizationEngine = Field(
        default=CategorizationEngine.CLASSIFIER,
        description="Categorization engine: 'classifier' (scikit-learn) or 'ai' (requires ai.llm to be configured)"
    )
    training_data_file: Optional[str] = Field(default="./data/training.beancount", description="Path to training data file (used when engine=local)")


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

    @field_validator('level')
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            raise ValueError(f'Logging level must be one of: {valid_levels}')
        return v.upper()


class LLMConfig(BaseModel):
    """LLM API configuration for natural language features."""
    provider: Literal["openai", "anthropic"] = Field(
        default="openai",
        description="LLM provider: 'openai' (any OpenAI-compatible endpoint incl. LM Studio, Ollama, OpenAI, Groq) or 'anthropic' (Anthropic API directly)"
    )
    api_url: str = Field(default="", description="OpenAI-compatible API base URL — only used when provider=openai (e.g. http://127.0.0.1:1234 or https://api.openai.com)")
    api_key: str = Field(default="", description="API key (required for cloud providers, leave empty for local LLMs)")
    model: str = Field(default="", description="Model name (e.g. gpt-4o, claude-sonnet-4-6, llama-3.1-8b-instruct)")
    temperature: float = Field(default=0.1, ge=0.0, le=2.0, description="Sampling temperature (0=deterministic, 2=very random)")
    max_tokens: int = Field(default=0, ge=0, description="Maximum tokens in LLM response. 0 = use model default (Anthropic requires a value > 0).")
    max_tool_rounds: int = Field(default=12, ge=1, le=50, description="Maximum tool-call round-trips per user message in the AI assistant.")


class AIConfig(BaseModel):
    """AI and machine learning settings."""
    categorization: CategorizationConfig = Field(default_factory=CategorizationConfig, description="Transaction auto-categorization settings")
    llm: LLMConfig = Field(default_factory=LLMConfig, description="LLM API settings for natural language features")


class OFXAccountMapping(BaseModel):
    """OFX account mapping for exact account detection."""
    institution: str = Field(..., description="Institution name from OFX")
    institution_fid: Optional[str] = Field(None, description="Financial Institution ID from OFX")
    account_type: str = Field(..., description="Account type from OFX (empty string for credit cards)")
    account_id: str = Field(..., description="Full account ID from OFX")
    currency: str = Field(..., description="Currency from OFX")
    beancount_account: str = Field(..., description="Corresponding Beancount account")


class SQLiteConfig(BaseModel):
    """SQLite export configuration."""
    auto_sync_enabled: bool = Field(default=True, description="Enable automatic sync on ledger changes")
    sync_debounce_seconds: float = Field(default=5.0, ge=0.0, description="Debounce delay in seconds before syncing")
    enable_wal: bool = Field(default=True, description="Enable WAL mode for concurrent access")


class AnalyticsConfig(BaseModel):
    """Analytics and reporting configuration."""
    sqlite: SQLiteConfig = Field(default_factory=SQLiteConfig, description="SQLite export settings")


class EmailImportConfig(BaseModel):
    """Email import configuration (formerly the email_service microservice)."""
    enabled: bool = Field(default=False, description="Enable email import functionality")
    default_lookback_days: int = Field(default=7, ge=1, description="Default number of days to look back for emails")
    max_emails: int = Field(default=500, ge=1, description="Max emails to fetch per request; truncates with warning")
    imap_timeout_secs: int = Field(default=30, ge=0, description="Socket timeout for IMAP operations; 0 = no timeout")
    parsing_mode: str = Field(default="regex", description="Default parsing mode: 'regex' or 'ai'; overridden per account or per rule")


class Config(BaseModel):
    """Main application configuration with nested sections."""
    
    # File paths
    ledger_file: str = Field(default="./data/ledgers/main.beancount", description="Path to main Beancount ledger")
    
    # Nested configuration sections
    server: ServerConfig = Field(default_factory=ServerConfig)
    accounts: AccountsConfig = Field(default_factory=AccountsConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    ai: AIConfig = Field(default_factory=AIConfig, description="AI and machine learning settings")

    # Analytics configuration
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig, description="Analytics and reporting settings")

    # Email import (merged from email_service microservice)
    email_import: EmailImportConfig = Field(
        default_factory=EmailImportConfig,
        description="Email import settings (IMAP fetch, rule parsing)"
    )

    config_file_path: Optional[Path] = Field(
        default=None,
        description="Path to the config file this configuration was loaded from"
    )

    @property
    def config_dir(self) -> Path:
        """The config/ directory — conventional subdirectories live here.

        This is always ``./config/`` relative to CWD (which the launcher sets
        to the backend dir in dev or the app dir when packaged). The config
        *file* itself may live elsewhere (e.g. ``data/config.yaml`` for dev
        overrides), but the conventional directories are always under config/.
        """
        return Path('./config')

    @property
    def csv_rules_dir(self) -> str:
        return str(self.config_dir / 'csv_rules')

    @property
    def xls_rules_dir(self) -> str:
        return str(self.config_dir / 'xls_rules')

    @property
    def ofx_mappings_file(self) -> str:
        return str(self.config_dir / 'ofx_mappings.yaml')

    @property
    def recipes_dir(self) -> str:
        return str(self.config_dir / 'recipes')

    @property
    def email_rules_dir(self) -> str:
        return str(self.config_dir / 'email_rules')

    @model_validator(mode='after')
    def validate_directory_paths(self) -> 'Config':
        """Validate that required directories exist for file operations."""
        # Validate ledger file directory exists or can be created
        ledger_dir = Path(self.ledger_file).parent
        if not ledger_dir.exists():
            # Try to create ledger directory
            try:
                ledger_dir.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as e:
                raise ValueError(f"Cannot create ledger directory {ledger_dir}: {e}")
        
        # Validate backup directory exists or can be created
        backup_path = Path(BACKUP_DIR)
        if not backup_path.exists():
            # Try to create backup directory
            try:
                backup_path.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as e:
                raise ValueError(f"Cannot create backup directory {backup_path}: {e}")
        
        # Note: training data for the classifier comes from the ledger cache (the main
        # ledger file), not from training_data_file directly. The field is kept for
        # reference/future use. No hard validation here — insufficient training data
        # is handled gracefully at runtime by initialize_classifier().
        
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
                "Configuration validation failed:\n" + 
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

            # ML settings
            'ml-enabled': ('ml', 'enabled'),
            'ml-training-data-file': ('ml', 'training_data_file'),

            # Logging settings
            'logging-level': ('logging', 'level'),
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
    
    