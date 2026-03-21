"""
Email service configuration.

Loads global defaults from the file at EMAIL_CONFIG_PATH env var,
defaulting to config/config.yaml (relative to email_service/).

IMAP credentials and per-account settings are in individual account YAML files
under rules_directory — not in this global config.
"""
import os
import re
import yaml
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, Field

# Base directory of the email_service package
_BASE_DIR = Path(__file__).resolve().parent.parent


def _expand_env_vars(value: str) -> str:
    """Expand ${VAR_NAME} references to environment variable values."""
    def replace(m):
        var_name = m.group(1)
        val = os.environ.get(var_name)
        if val is None:
            raise ValueError(f"Environment variable '{var_name}' is not set (referenced in account YAML)")
        return val
    return re.sub(r'\$\{([^}]+)\}', replace, value)


class LLMConfig(BaseModel):
    provider: Literal["openai", "anthropic"] = "openai"
    api_url: str = ""   # Only used when provider=openai
    api_key: str = ""
    model: str = ""
    temperature: float = 0.1
    max_tokens: int = 1024


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8100


class EmailServiceConfig(BaseModel):
    rules_directory: str = "config/email_rules/"
    default_lookback_days: int = 7
    max_emails: int = 500         # max emails to fetch per request; truncate with warning
    imap_timeout_secs: int = 30   # socket timeout for IMAP operations; 0 = no timeout
    parsing_mode: str = "regex"   # "regex" or "llm"; overridden per account or per rule
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)

    @property
    def rules_path(self) -> Path:
        """Resolve rules_directory relative to email_service/ base dir."""
        p = Path(self.rules_directory)
        if not p.is_absolute():
            p = _BASE_DIR / p
        return p.resolve()


_config: Optional[EmailServiceConfig] = None


def load_config(config_path: Optional[str] = None) -> EmailServiceConfig:
    """
    Load configuration from file.

    Priority:
      1. Explicit config_path argument (set by CLI -c option)
      2. EMAIL_CONFIG_PATH environment variable
      3. Default: config/config.yaml relative to email_service/
    """
    global _config
    if _config is not None:
        return _config

    resolved_path = Path(
        config_path
        or os.environ.get('EMAIL_CONFIG_PATH', str(_BASE_DIR / 'config' / 'config.yaml'))
    ).resolve()

    if not resolved_path.exists():
        # Return default config (email service starts with defaults)
        _config = EmailServiceConfig()
        return _config

    with open(resolved_path, 'r') as f:
        raw = yaml.safe_load(f) or {}

    _config = EmailServiceConfig.model_validate(raw)
    return _config


def get_config() -> EmailServiceConfig:
    return load_config()
