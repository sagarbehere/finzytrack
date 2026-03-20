import logging
from pathlib import Path
from typing import List, Optional

from ruamel.yaml import YAML

from app.schemas.xls_schemas import XlsRule, XlsRuleSummary

logger = logging.getLogger(__name__)


class XlsRulesManager:
    """Read-only manager for XLS import rule YAML files."""

    def __init__(self, rules_dir: Optional[str] = None):
        self._rules_dir = Path(rules_dir) if rules_dir else None
        self._yaml = YAML(typ='safe')

    @property
    def rules_dir(self) -> Optional[str]:
        return str(self._rules_dir) if self._rules_dir else None

    def list_rules(self) -> List[XlsRuleSummary]:
        if not self._rules_dir or not self._rules_dir.is_dir():
            return []

        summaries: List[XlsRuleSummary] = []
        for path in sorted(self._rules_dir.glob("*.yaml")) + sorted(self._rules_dir.glob("*.yml")):
            try:
                rule = self._load_rule_file(path)
                summaries.append(XlsRuleSummary(
                    filename=path.name,
                    name=rule.name,
                    default_account=rule.default_account,
                    default_currency=rule.default_currency,
                ))
            except Exception as e:
                logger.warning(f"Skipping invalid XLS rule file {path.name}: {e}")
        return summaries

    def get_rule(self, filename: str) -> XlsRule:
        if not self._rules_dir:
            raise FileNotFoundError("No XLS rules directory configured")

        # Path traversal protection
        resolved = (self._rules_dir / filename).resolve()
        if not resolved.is_relative_to(self._rules_dir.resolve()):
            raise ValueError(f"Invalid filename: {filename}")

        if not resolved.is_file():
            raise FileNotFoundError(f"XLS rule file not found: {filename}")

        return self._load_rule_file(resolved)

    def _load_rule_file(self, path: Path) -> XlsRule:
        with open(path, 'r') as f:
            data = self._yaml.load(f)
        return XlsRule.model_validate(data)
