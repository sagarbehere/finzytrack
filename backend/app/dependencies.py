"""
Dependency functions for FastAPI routes.

All per-user service resolution flows through the chain:

    get_user_context  →  get_user_services  →  individual accessors

In desktop mode the chain collapses to a no-op auth + singleton lookup.
In hosted mode it reads X-User-ID, resolves the user's root directory,
and lazily creates / retrieves cached services from the ServiceRegistry.
"""

from fastapi import Depends, Request

from app.app_mode import UserContext
from app.middleware.auth import get_user_context
from app.service_registry import ServiceRegistry
from app.user_services import UserServices
from app.core.config_manager import ConfigManager
from app.core.ledger_manager import LedgerManager
from app.core.backup_manager import BackupManager
from app.core.csv_rules_manager import CsvRulesManager
from app.core.xls_rules_manager import XlsRulesManager
from app.email_import.rule_registry import AccountProfileRegistry
from app.services.sqlite_reader import SqliteReader
from app.services.sqlite_exporter import SQLiteExporter


async def get_user_services(
    request: Request,
    ctx: UserContext = Depends(get_user_context),
) -> UserServices:
    """Resolve the ``UserServices`` bundle for the current request's user."""
    registry: ServiceRegistry = request.app.state.registry
    return await registry.get_or_create(ctx)


# ── Individual accessors (thin wrappers for backward compatibility) ──────────
# Routers will be migrated to use get_user_services directly.
# These exist so that routers not yet migrated continue to work.


async def get_config_manager(
    services: UserServices = Depends(get_user_services),
) -> ConfigManager:
    return services.config_manager


async def get_beancount_manager(
    services: UserServices = Depends(get_user_services),
) -> LedgerManager:
    """Returns the LedgerManager (kept as get_beancount_manager for compat)."""
    return services.ledger_manager


async def get_backup_manager(
    services: UserServices = Depends(get_user_services),
) -> BackupManager:
    return services.backup_manager


async def get_csv_rules_manager(
    services: UserServices = Depends(get_user_services),
) -> CsvRulesManager:
    return services.csv_rules_manager


async def get_xls_rules_manager(
    services: UserServices = Depends(get_user_services),
) -> XlsRulesManager:
    return services.xls_rules_manager


async def get_email_registry(
    services: UserServices = Depends(get_user_services),
) -> AccountProfileRegistry:
    return services.email_registry


async def get_sqlite_reader(
    services: UserServices = Depends(get_user_services),
) -> SqliteReader:
    return services.sqlite_reader


async def get_sqlite_exporter(
    services: UserServices = Depends(get_user_services),
) -> SQLiteExporter:
    return services.sqlite_exporter
