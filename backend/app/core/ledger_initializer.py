import os
import logging
from pathlib import Path
from datetime import datetime

from app.core.backup_manager import BackupManager

logger = logging.getLogger(__name__)

class LedgerInitializer:
    """Handles creation of initial ledger file for new users."""
    
    def __init__(self, ledger_file: str, backup_manager: BackupManager, default_currency: str = "USD"):
        self.ledger_file = ledger_file
        self.default_currency = default_currency
        self.backup_manager = backup_manager

    def get_initial_content(self) -> str:
        """Return the initial content for a new ledger file."""
        today = datetime.now().strftime("%Y-%m-%d")
        
        return f''';; Finzytrack Ledger File
;; Created: {today}

;; Base accounts - modify as needed
1970-01-01 open Assets:Cash                {self.default_currency}
1970-01-01 open Assets:Bank:Checking       {self.default_currency}
1970-01-01 open Assets:Bank:Savings        {self.default_currency}
1970-01-01 open Liabilities:CreditCard     {self.default_currency}
1970-01-01 open Income:Salary              {self.default_currency}
1970-01-01 open Expenses:Food              {self.default_currency}
1970-01-01 open Expenses:Unknown           {self.default_currency}
1970-01-01 open Equity:Opening-Balances    {self.default_currency}

;; Your transactions will appear below this line
'''
    
    def ensure_ledger_exists(self) -> bool:
        """Ensure ledger file exists, create if missing using atomic_write."""
        if os.path.exists(self.ledger_file):
            return True
        
        if not self.backup_manager:
            raise ValueError("BackupManager is not configured on LedgerInitializer")

        try:
            logger.info(f"Ledger file not found. Creating a new one at {self.ledger_file}")
            initial_content = self.get_initial_content()
            
            # atomic_write will yield an empty, writable file handle
            with self.backup_manager.atomic_write(self.ledger_file) as f:
                f.write(initial_content)
            
            return True
        except Exception as e:
            logger.error(f"Failed to create ledger file: {e}", exc_info=True)
            return False