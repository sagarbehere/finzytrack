"""Load one account YAML file and use it to match and parse emails."""
import re
import logging
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from app.schemas.rule_schemas import RuleFile, TransactionTypeDef
from app.core.regex_extractor import extract_fields, ExtractionError
from app.core.llm_extractor import extract_fields_llm, LLMExtractionError
from app.schemas.config_schemas import LLMConfig

logger = logging.getLogger(__name__)


def _expand_env_vars(value: str) -> str:
    """Expand ${VAR_NAME} references to environment variable values."""
    import re as _re
    def replace(m):
        var_name = m.group(1)
        val = os.environ.get(var_name)
        if val is None:
            raise ValueError(f"Environment variable '{var_name}' is not set")
        return val
    return _re.sub(r'\$\{([^}]+)\}', replace, value)


class EmailRuleParser:
    """
    Manages one account YAML file (credentials + parsing rules).
    Pre-compiles all regex patterns at load time.
    """

    def __init__(self, rule_file_path: Path):
        self.path = rule_file_path
        self.profile_id = rule_file_path.stem   # filename without .yaml
        with open(rule_file_path, 'r') as f:
            raw = yaml.safe_load(f)

        # Expand env vars in IMAP credentials before validation
        if 'imap_server' in raw:
            srv = raw['imap_server']
            if 'username' in srv:
                srv['username'] = _expand_env_vars(srv['username'])
            if 'password' in srv:
                srv['password'] = _expand_env_vars(srv['password'])

        self.rule = RuleFile.model_validate(raw)
        self._compiled_subject_patterns: Dict[str, Optional[re.Pattern]] = {}
        self._compiled_body_patterns: Dict[str, Optional[re.Pattern]] = {}
        self._precompile_patterns()

    def _precompile_patterns(self):
        for txn_type in self.rule.transaction_types:
            pattern = txn_type.email_filter.subject_regex
            if pattern:
                try:
                    self._compiled_subject_patterns[txn_type.name] = re.compile(
                        pattern, re.IGNORECASE
                    )
                except re.error as e:
                    logger.error(
                        f"[{self.profile_id}] Invalid subject_regex for "
                        f"'{txn_type.name}': {e}. This type will never match."
                    )
                    self._compiled_subject_patterns[txn_type.name] = None
            else:
                self._compiled_subject_patterns[txn_type.name] = None

            body_pattern = txn_type.email_filter.body_regex
            if body_pattern:
                try:
                    self._compiled_body_patterns[txn_type.name] = re.compile(
                        body_pattern, re.IGNORECASE
                    )
                except re.error as e:
                    logger.error(
                        f"[{self.profile_id}] Invalid body_regex for "
                        f"'{txn_type.name}': {e}. Body filter will be skipped."
                    )
                    self._compiled_body_patterns[txn_type.name] = None
            else:
                self._compiled_body_patterns[txn_type.name] = None

    @property
    def display_name(self) -> str:
        return self.rule.metadata.name

    @property
    def beancount_account(self) -> str:
        return self.rule.metadata.beancount_account

    @property
    def default_currency(self) -> str:
        return self.rule.metadata.default_currency

    @property
    def bank_emails(self):
        return self.rule.bank_emails

    @property
    def imap_folder(self) -> str:
        return self.rule.imap_server.folder

    @property
    def account_lookback_days(self) -> Optional[int]:
        return self.rule.lookback_days

    @property
    def account_parsing_mode(self) -> Optional[str]:
        return self.rule.parsing_mode

    def find_matching_type(
        self, from_address: str, subject: str, body_text: str = ""
    ) -> Optional[TransactionTypeDef]:
        """
        Return the first transaction type that matches from+subject+body.
        Returns None if the sender doesn't match any bank_email, or no
        transaction type passes all filters (subject_regex, body_regex).
        First match wins within the transaction_types list.
        """
        from_lower = from_address.lower()
        if not any(be.lower() in from_lower for be in self.rule.bank_emails):
            return None

        for txn_type in self.rule.transaction_types:
            subject_compiled = self._compiled_subject_patterns.get(txn_type.name)
            if subject_compiled is not None and not subject_compiled.search(subject):
                continue

            body_compiled = self._compiled_body_patterns.get(txn_type.name)
            if body_compiled is not None and not body_compiled.search(body_text):
                continue

            return txn_type

        return None

    def parse_email(
        self,
        txn_type: TransactionTypeDef,
        body_text: str,
        subject: str,
        email_date: datetime,
        message_id: str,
        parsing_mode: str,
        llm_config: Optional[LLMConfig] = None,
    ) -> Dict[str, Any]:
        """
        Extract structured transaction data from an email.

        Returns dict with keys: amount, payee, date, external_id, external_id_type,
        masked_account, source_rule.

        Raises ExtractionError or LLMExtractionError on failure.
        """
        effective_mode = txn_type.parsing_mode or self.account_parsing_mode or parsing_mode

        if effective_mode == 'llm':
            raw = extract_fields_llm(
                body_text=body_text,
                subject=subject,
                llm_config=llm_config,
                explicit_sign_rule=txn_type.amount_sign.model_dump() if txn_type.amount_sign else None,
            )
        else:
            raw = extract_fields(
                extraction_defs=txn_type.extraction,
                body_text=body_text,
                subject=subject,
                email_header_date=email_date,
                required_fields=txn_type.error_handling.required_fields,
            )
            raw = self._apply_mapping_and_sign(raw, txn_type, email_date)

        # Resolve date
        txn_date = raw.get('date')
        if isinstance(txn_date, datetime):
            txn_date = txn_date.date()

        # Resolve external_id fallback to Message-ID
        external_id = raw.get('external_id')
        external_id_type = raw.get('external_id_type')
        if not external_id and txn_type.error_handling.partial_match_allowed:
            external_id = message_id
            external_id_type = 'EMAIL_MESSAGE_ID'

        return {
            'date': txn_date,
            'amount': raw.get('amount', Decimal('0')),
            'payee': raw.get('payee') or '',
            'external_id': external_id,
            'external_id_type': external_id_type,
            'masked_account': raw.get('masked_account'),
            'source_rule': f"{self.profile_id}/{txn_type.name}",
        }

    def _apply_mapping_and_sign(
        self,
        extracted: Dict[str, Any],
        txn_type: TransactionTypeDef,
        email_date: datetime,
    ) -> Dict[str, Any]:
        """Apply the mapping and amount_sign rules to the raw extracted fields."""
        mapping = txn_type.mapping
        result: Dict[str, Any] = {}

        # Map extracted fields to output fields
        for extracted_name, output_name in mapping.items():
            if extracted_name == 'external_id_type':
                # Literal string value, not an extracted field
                result['external_id_type'] = output_name
            elif extracted_name == 'payee_template':
                # Only used as fallback — store for later
                result['_payee_template'] = output_name
            elif extracted_name in extracted:
                result[output_name] = extracted[extracted_name]

        # Apply payee fallback template
        if not result.get('payee') and '_payee_template' in result:
            template = result.pop('_payee_template')
            try:
                result['payee'] = template.format(**extracted)
            except KeyError:
                result['payee'] = template
        result.pop('_payee_template', None)

        # Apply amount sign
        amount = result.get('amount', Decimal('0'))
        if isinstance(amount, (int, float)):
            amount = Decimal(str(amount))

        sign_rule = txn_type.amount_sign
        if sign_rule:
            if sign_rule.field == 'fixed':
                if sign_rule.value == 'negative':
                    amount = -abs(amount)
                else:
                    amount = abs(amount)
            else:
                # Field-based sign
                type_value = str(extracted.get(sign_rule.field, '')).lower()
                if any(v.lower() in type_value for v in sign_rule.negative_values):
                    amount = -abs(amount)
                elif any(v.lower() in type_value for v in sign_rule.positive_values):
                    amount = abs(amount)

        result['amount'] = amount

        # Fallback date to email header date
        if 'date' not in result:
            result['date'] = email_date

        return result
