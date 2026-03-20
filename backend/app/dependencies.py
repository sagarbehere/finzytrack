"""
Dependency functions for FastAPI routes.

This module provides helper functions that routes can use to access
application state and services without creating circular imports.
"""

from fastapi import Request
from app.core.config_manager import ConfigManager
from app.core.beancount_manager import BeancountManager
from app.core.backup_manager import BackupManager
from app.core.csv_rules_manager import CsvRulesManager
from app.core.xls_rules_manager import XlsRulesManager


def get_config_manager(request: Request) -> ConfigManager:
    """Dependency to get the ConfigManager from app state."""
    return request.app.state.config_manager


def get_beancount_manager(request: Request) -> BeancountManager:
    """Dependency to get Beancount manager from app state."""
    return request.app.state.beancount_manager


def get_backup_manager(request: Request) -> BackupManager:
    """Dependency to get Backup manager from app state."""
    return request.app.state.backup_manager



def get_csv_rules_manager(request: Request) -> CsvRulesManager:
    """Dependency to get CSV rules manager from app state."""
    return request.app.state.csv_rules_manager


def get_xls_rules_manager(request: Request) -> XlsRulesManager:
    """Dependency to get XLS rules manager from app state."""
    return request.app.state.xls_rules_manager