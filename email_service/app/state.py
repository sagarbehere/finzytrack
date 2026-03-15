"""Application-level singletons."""
from app.config import get_config
from app.core.rule_registry import AccountProfileRegistry

_registry = None


def get_registry() -> AccountProfileRegistry:
    global _registry
    if _registry is None:
        config = get_config()
        _registry = AccountProfileRegistry(config.rules_path)
    return _registry
