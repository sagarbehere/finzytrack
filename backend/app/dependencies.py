"""
Dependency functions for FastAPI routes.

This module provides helper functions that routes can use to access
application state and services without creating circular imports.
"""

from fastapi import Request
from app.user_services import UserServices
from app.core.config_manager import ConfigManager
from app.core.ledger_manager import LedgerManager
from app.core.backup_manager import BackupManager
from app.core.csv_rules_manager import CsvRulesManager
from app.core.xls_rules_manager import XlsRulesManager
from app.email_import.rule_registry import AccountProfileRegistry


def get_user_services(request: Request) -> UserServices:
    """Dependency to get the UserServices bundle from app state."""
    return request.app.state.services


# ── Individual accessors (thin wrappers for backward compatibility) ──────────
# Routers will be migrated to use get_user_services directly.
# These exist so that routers not yet migrated continue to work.

def get_config_manager(request: Request) -> ConfigManager:
    return request.app.state.services.config_manager


def get_beancount_manager(request: Request) -> LedgerManager:
    """Returns the LedgerManager (kept as get_beancount_manager for compat)."""
    return request.app.state.services.ledger_manager


def get_backup_manager(request: Request) -> BackupManager:
    return request.app.state.services.backup_manager


def get_csv_rules_manager(request: Request) -> CsvRulesManager:
    return request.app.state.services.csv_rules_manager


def get_xls_rules_manager(request: Request) -> XlsRulesManager:
    return request.app.state.services.xls_rules_manager


def get_email_registry(request: Request) -> AccountProfileRegistry:
    return request.app.state.services.email_registry
