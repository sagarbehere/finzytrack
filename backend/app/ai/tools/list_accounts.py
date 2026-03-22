import logging

from app.ai.tools.base import BaseTool
from app.core.beancount_manager import BeancountManager

logger = logging.getLogger(__name__)


class ListAccountsTool(BaseTool):
    @property
    def name(self) -> str:
        return "list_accounts"

    @property
    def description(self) -> str:
        return (
            "Return all account names from the user's Beancount ledger. "
            "Use this when you need to suggest or validate a Beancount account for a rule "
            "(e.g. default_account field). Ask the user which account to use rather than "
            "guessing, but use this list to offer concrete suggestions."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def __init__(self, beancount_manager: BeancountManager):
        self._manager = beancount_manager

    async def execute(self) -> dict:
        try:
            accounts = sorted(self._manager.cache.get_account_names())
            return {"success": True, "accounts": accounts}
        except Exception as e:
            logger.error(f"list_accounts failed: {e}")
            return {"success": False, "error": str(e)}
