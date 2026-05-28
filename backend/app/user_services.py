"""
UserServices — groups all per-user service managers into a single object.

In desktop mode, one UserServices instance exists for the lifetime of the app.
In hosted mode (future), each user gets their own instance via ServiceRegistry.
"""

from dataclasses import dataclass

from app.core.ledger_manager import LedgerManager
from app.core.config_manager import ConfigManager
from app.core.backup_manager import BackupManager
from app.core.csv_rules_manager import CsvRulesManager
from app.core.xls_rules_manager import XlsRulesManager
from app.email_import.rule_registry import AccountProfileRegistry
from app.services.sqlite_exporter import SQLiteExporter
from app.services.sqlite_reader import SqliteReader


@dataclass
class UserServices:
    """All service managers scoped to a single user's data.

    Each field corresponds to a manager that was previously stored
    individually in ``app.state``.  Grouping them simplifies
    dependency injection: routers receive one ``UserServices`` object
    instead of N individual dependencies.
    """

    ledger_manager: LedgerManager
    config_manager: ConfigManager
    backup_manager: BackupManager
    csv_rules_manager: CsvRulesManager
    xls_rules_manager: XlsRulesManager
    email_registry: AccountProfileRegistry
    sqlite_exporter: SQLiteExporter
    sqlite_reader: SqliteReader
