"""
Dependency functions for FastAPI routes.

This module provides helper functions that routes can use to access
application state and services without creating circular imports.
"""

from fastapi import Request
from app.core.config_manager import ConfigManager
from app.core.beancount_manager import BeancountManager
from app.core.backup_manager import BackupManager


def get_config_manager(request: Request) -> ConfigManager:
    """Dependency to get the ConfigManager from app state."""
    return request.app.state.config_manager


def get_beancount_manager(request: Request) -> BeancountManager:
    """Dependency to get Beancount manager from app state."""
    return request.app.state.beancount_manager


def get_backup_manager(request: Request) -> BackupManager:
    """Dependency to get Backup manager from app state."""
    return request.app.state.backup_manager