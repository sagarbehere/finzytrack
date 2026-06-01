import logging
from pathlib import Path
from typing import List, Optional

from ruamel.yaml import YAML

from app.helpers.path_guard import guard_path
from app.schemas.csv_schemas import CsvRule, CsvRuleSummary, InvalidRuleSummary

logger = logging.getLogger(__name__)


class CsvRulesManager:
    """Read-only manager for CSV import rule YAML files."""

    def __init__(self, rules_dir: Optional[str] = None):
        self._rules_dir = Path(rules_dir) if rules_dir else None
        self._yaml = YAML(typ='safe')

    @property
    def rules_dir(self) -> Optional[str]:
        return str(self._rules_dir) if self._rules_dir else None

    def list_rules(self) -> tuple[List[CsvRuleSummary], List[InvalidRuleSummary]]:
        if not self._rules_dir or not self._rules_dir.is_dir():
            return [], []

        summaries: List[CsvRuleSummary] = []
        invalid: List[InvalidRuleSummary] = []
        for path in sorted(self._rules_dir.glob("*.yaml")) + sorted(self._rules_dir.glob("*.yml")):
            try:
                rule = self._load_rule_file(path)
                summaries.append(CsvRuleSummary(
                    filename=path.name,
                    name=rule.name,
                    default_account=rule.default_account,
                    default_currency=rule.default_currency,
                ))
            except Exception as e:
                logger.warning(f"Skipping invalid CSV rule file {path.name}: {e}")
                invalid.append(InvalidRuleSummary(filename=path.name, error=str(e)))
        return summaries, invalid

    def get_rule(self, filename: str) -> CsvRule:
        if not self._rules_dir:
            raise FileNotFoundError("No CSV rules directory configured")

        # Canonical path-traversal guard — raises APIError(INVALID_PATH, 403)
        # on escape, matching every other path-guarded endpoint.
        resolved = guard_path(
            self._rules_dir / filename, self._rules_dir, context="CSV rule filename"
        )

        if not resolved.is_file():
            raise FileNotFoundError(f"CSV rule file not found: {filename}")

        return self._load_rule_file(resolved)

    def _load_rule_file(self, path: Path) -> CsvRule:
        with open(path, 'r', encoding='utf-8') as f:
            data = self._yaml.load(f)
        return CsvRule.model_validate(data)
