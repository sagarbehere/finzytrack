"""Backward-compatibility shim — use app.core.ledger_manager.LedgerManager instead."""

from app.core.ledger_manager import LedgerManager as BeancountManager  # noqa: F401

__all__ = ["BeancountManager"]
