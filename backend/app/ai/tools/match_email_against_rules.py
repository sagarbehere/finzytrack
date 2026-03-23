"""Tool: check an uploaded email against all existing email rule files."""
import logging

from app.ai.tools.base import BaseTool

logger = logging.getLogger(__name__)


class MatchEmailAgainstRulesTool(BaseTool):
    @property
    def name(self) -> str:
        return "match_email_against_rules"

    @property
    def description(self) -> str:
        return (
            "Check an uploaded email against all existing email rule files. "
            "Call this FIRST when a .eml file is uploaded, before drafting any new rule. "
            "Returns one of three outcomes: "
            "'full_match' (email already handled — no action needed), "
            "'sender_match_no_type' (bank is known but this email format is new — add a transaction type to the existing rule), "
            "'no_match' (unknown sender — create a new rule file)."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "email_sender": {
                    "type": "string",
                    "description": "The From address of the email, e.g. 'alerts@axis.bank.in'",
                },
                "email_subject": {
                    "type": "string",
                    "description": "Subject line of the email",
                },
                "email_body": {
                    "type": "string",
                    "description": "Plain text body of the email",
                },
            },
            "required": ["email_sender", "email_subject", "email_body"],
        }

    def __init__(self, registry):
        # registry is AccountProfileRegistry | None
        self._registry = registry

    async def execute(self, email_sender: str, email_subject: str, email_body: str) -> dict:
        if not self._registry or not self._registry.parsers:
            return {
                "success": True,
                "outcome": "no_match",
                "message": "No existing email rules found. A new rule file needs to be created.",
                "matched_rule": None,
                "matched_type": None,
                "sender_matched_rules": [],
            }

        sender_matches = []   # rules where bank_emails matches the sender
        full_match_rule = None
        full_match_type = None

        for profile_id, parser in self._registry.parsers.items():
            from_lower = email_sender.lower()
            sender_matched = any(be.lower() in from_lower for be in parser.rule.bank_emails)
            if not sender_matched:
                continue

            sender_matches.append({
                "rule_file": parser.path.name,
                "rule_name": parser.display_name,
                "profile_id": profile_id,
                "bank_emails": parser.rule.bank_emails,
                "transaction_types": [t.name for t in parser.rule.transaction_types],
            })

            # Check for a matching transaction type (subject + body filter)
            if full_match_rule is None:
                matched_type = parser.find_matching_type(email_sender, email_subject, email_body)
                if matched_type:
                    full_match_rule = {
                        "rule_file": parser.path.name,
                        "rule_name": parser.display_name,
                        "profile_id": profile_id,
                    }
                    full_match_type = matched_type.name

        if full_match_rule:
            return {
                "success": True,
                "outcome": "full_match",
                "message": (
                    f"This email is already handled by rule '{full_match_rule['rule_name']}' "
                    f"({full_match_rule['rule_file']}), transaction type '{full_match_type}'. "
                    f"No new rule or transaction type is needed."
                ),
                "matched_rule": full_match_rule,
                "matched_type": full_match_type,
                "sender_matched_rules": sender_matches,
            }

        if sender_matches:
            names = ", ".join(f"'{r['rule_name']}' ({r['rule_file']})" for r in sender_matches)
            return {
                "success": True,
                "outcome": "sender_match_no_type",
                "message": (
                    f"Sender '{email_sender}' matches existing rule(s): {names}. "
                    f"However, no transaction type in those rules matches this email's subject/body. "
                    f"A new transaction type should be added to the existing rule file rather than "
                    f"creating a new file."
                ),
                "matched_rule": sender_matches[0],
                "matched_type": None,
                "sender_matched_rules": sender_matches,
            }

        return {
            "success": True,
            "outcome": "no_match",
            "message": (
                f"No existing rule handles sender '{email_sender}'. "
                f"A new rule file needs to be created."
            ),
            "matched_rule": None,
            "matched_type": None,
            "sender_matched_rules": [],
        }
