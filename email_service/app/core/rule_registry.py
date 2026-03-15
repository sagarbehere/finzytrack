"""Scan accounts directory and manage all EmailRuleParser instances."""
import logging
from pathlib import Path
from typing import Dict, List, Optional

from app.core.rule_parser import EmailRuleParser
from app.schemas.result_schemas import ProfileInfo

logger = logging.getLogger(__name__)


class AccountProfileRegistry:
    def __init__(self, rules_directory: Path):
        self.rules_directory = rules_directory
        self._parsers: Dict[str, EmailRuleParser] = {}   # profile_id → parser
        self._load_all()

    def _load_all(self):
        self._parsers = {}
        if not self.rules_directory.exists():
            logger.warning(f"Rules directory does not exist: {self.rules_directory}")
            return

        for yaml_file in sorted(self.rules_directory.glob('*.yaml')):
            try:
                parser = EmailRuleParser(yaml_file)
                self._parsers[parser.profile_id] = parser
                logger.info(
                    f"Loaded account profile: {parser.display_name} "
                    f"({len(parser.rule.transaction_types)} types) "
                    f"[{parser.profile_id}] from {yaml_file.name}"
                )
            except Exception as e:
                logger.error(f"Failed to load account file {yaml_file}: {e}")

    def reload(self):
        """Re-scan rules directory and reload all parsers. Called by POST /reload."""
        self._load_all()

    def get_parser_by_profile_id(self, profile_id: str) -> Optional[EmailRuleParser]:
        """Return the parser for a given profile_id, or None if not found."""
        return self._parsers.get(profile_id)

    def list_profiles(self) -> List[ProfileInfo]:
        """Return list of ProfileInfo for GET /profiles response."""
        result = []
        for profile_id, parser in self._parsers.items():
            result.append(ProfileInfo(
                name=parser.display_name,
                profile_id=profile_id,
                beancount_account=parser.beancount_account,
                default_currency=parser.default_currency,
                lookback_days=parser.account_lookback_days,
                file=parser.path.name,
            ))
        return result

    @property
    def parsers(self) -> Dict[str, EmailRuleParser]:
        return self._parsers
