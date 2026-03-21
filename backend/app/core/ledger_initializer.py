import os
import logging
from datetime import datetime
import importlib.resources

from app.core.backup_manager import BackupManager

logger = logging.getLogger(__name__)

class LedgerInitializer:
    """Handles creation of initial ledger file for new users."""
    
    def __init__(self, ledger_file: str, backup_manager: BackupManager, default_currency: str = "USD"):
        self.ledger_file = ledger_file
        self.default_currency = default_currency
        self.backup_manager = backup_manager

    def get_initial_content(self) -> str:
        """Return the initial content for a new ledger file by loading from template."""
        template_content = self._load_template_content()
        
        # Substitute template variables
        today = datetime.now().strftime("%Y-%m-%d")
        template_content = template_content.replace("{created_date}", today)
        template_content = template_content.replace("{default_currency}", self.default_currency)
        
        return template_content
    
    def _load_template_content(self) -> str:
        """Load content from the default ledger template file."""
        try:
            # Try to load template from package resources
            template_path = "default-ledger.beancount"
            template_content = importlib.resources.files("app.templates").joinpath(template_path).read_text()
            logger.info(f"Successfully loaded template from package resources: {template_path}")
            return template_content
        except (FileNotFoundError, AttributeError, ImportError) as e:
            logger.warning(f"Failed to load template from package resources: {e}. Falling back to hardcoded content.")
            return self._get_hardcoded_content()
    
    def _get_hardcoded_content(self) -> str:
        """Fallback hardcoded content if template is not available."""
        return f''';; Finzytrack Ledger File
;; Created: {datetime.now().strftime("%Y-%m-%d")}

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
    
    def _load_template_from_path(self, template_path: str) -> str:
        """Load template content from a custom file path (for future extensibility)."""
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                return f.read()
        except (FileNotFoundError, PermissionError, UnicodeDecodeError) as e:
            logger.error(f"Failed to load custom template from {template_path}: {e}")
            raise
    
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