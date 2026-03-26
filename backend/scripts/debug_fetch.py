#!/usr/bin/env python3
"""
Debug tool for email account YAML development.

Usage (run from the backend/ directory):
    # Verbose fetch from a configured account profile
    python scripts/debug_fetch.py server --config ./config/config.yaml \
        --profile axis-bank-savings --since-date 2026-03-01 --verbose

    # Test one account YAML file against one .eml file
    python scripts/debug_fetch.py rule --rule config/email_rules/axis-bank-savings.yaml \
                               --email-file /path/to/sample.eml

    # Show only unmatched emails
    python scripts/debug_fetch.py server --config ./config/config.yaml \
        --profile axis-bank-savings --since-date 2026-03-08 --unmatched-only
"""
import argparse
import sys
from pathlib import Path

# Allow running from the backend directory
sys.path.insert(0, str(Path(__file__).parent.parent))


def cmd_server(args):
    """Run a synchronous fetch for debugging (no SSE, prints progress to stdout)."""
    import asyncio
    from app.config import Config
    from app.email_import.rule_registry import AccountProfileRegistry
    from app.email_import.fetch_service import stream_fetch
    import json

    config = Config.from_yaml_file(args.config)
    rules_path = Path(config.email_rules_dir)
    registry = AccountProfileRegistry(rules_path)

    result = None

    async def _run():
        nonlocal result
        since_date = None
        until_date = None
        if args.since_date:
            from datetime import date as date_type
            since_date = date_type.fromisoformat(args.since_date)
        if args.until_date:
            from datetime import date as date_type
            until_date = date_type.fromisoformat(args.until_date)

        async for event_str in stream_fetch(
            profile_id=args.profile,
            config=config,
            registry=registry,
            since_date=since_date,
            until_date=until_date,
        ):
            # event_str is "data: {...}\n\n"
            data_line = event_str.strip()
            if data_line.startswith('data: '):
                evt = json.loads(data_line[6:])
                if evt['phase'] not in ('complete',):
                    print(f"  [{evt['phase']}] {evt['message']}", flush=True)
                if evt['phase'] == 'complete':
                    from app.email_import.result_schemas import FetchResult
                    result = FetchResult.model_validate(evt['result'])

    asyncio.run(_run())
    print(f"\n{'='*60}")
    print(f"FETCH SUMMARY: {args.profile}")
    print(f"  Fetched: {result.stats.emails_fetched} emails")
    print(f"  Parsed:  {result.stats.transactions_parsed} transactions")
    print(f"  Unmatched: {result.stats.unmatched}")
    print(f"  Errors: {result.stats.extraction_errors}")

    if not args.unmatched_only and not args.errors_only:
        print(f"\n--- PARSED TRANSACTIONS ({len(result.transactions)}) ---")
        for t in result.transactions:
            print(f"  {t.date} | {t.amount:>12} | {t.payee:<30} | {t.source_rule}")

    if result.unmatched_emails and (args.verbose or args.unmatched_only):
        print(f"\n--- UNMATCHED EMAILS ({len(result.unmatched_emails)}) ---")
        for u in result.unmatched_emails:
            print(f"  From: {u.from_address}")
            print(f"  Subj: {u.subject}")
            print(f"  Why:  {u.reason}\n")

    if result.extraction_errors and (args.verbose or args.errors_only):
        print(f"\n--- EXTRACTION ERRORS ({len(result.extraction_errors)}) ---")
        for e in result.extraction_errors:
            print(f"  Rule: {e.rule_matched}")
            print(f"  Subj: {e.subject}")
            print(f"  Why:  {e.reason}\n")


def cmd_rule_email(args):
    import email as email_module
    from email.utils import parsedate_to_datetime
    from datetime import timezone
    from datetime import datetime

    from app.email_import.rule_parser import EmailRuleParser
    from app.email_import.imap_client import _extract_body, _decode_header_value

    rule_path = Path(args.rule).expanduser()
    email_path = Path(args.email_file).expanduser()

    parser = EmailRuleParser(rule_path)
    print(f"Account profile loaded: {parser.display_name} [{parser.profile_id}]")
    print(f"  Beancount account: {parser.beancount_account}")
    print(f"  Transaction types: {len(parser.rule.transaction_types)}")

    with open(email_path, 'rb') as f:
        msg = email_module.message_from_bytes(f.read())

    # Extract body
    body_text = _extract_body(msg)
    from_address = _decode_header_value(msg.get('From', ''))
    subject = _decode_header_value(msg.get('Subject', ''))
    date_raw = msg.get('Date', '')
    try:
        email_date = parsedate_to_datetime(date_raw)
    except Exception:
        email_date = datetime.now(tz=timezone.utc)
    message_id = msg.get('Message-ID', '<no-id>')

    print(f"\nFrom:    {from_address}")
    print(f"Subject: {subject}")
    print(f"Date:    {email_date}")

    print(f"\n--- STRIPPED BODY TEXT ---\n{body_text[:2000]}\n{'---'}")

    txn_type = parser.find_matching_type(from_address, subject, body_text)
    if txn_type is None:
        print("No matching transaction type found for this email.")
        return

    print(f"Matched transaction type: {txn_type.name}")

    # For rule-only test, use defaults for parsing mode
    try:
        data = parser.parse_email(
            txn_type=txn_type,
            body_text=body_text,
            subject=subject,
            email_date=email_date,
            message_id=message_id,
            parsing_mode="regex",
            llm_config=None,
        )
        print(f"\n--- EXTRACTED DATA ---")
        for k, v in data.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print(f"\nExtraction failed: {e}")


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Debug email account YAML development')
    sub = p.add_subparsers(dest='cmd')

    sv = sub.add_parser('server', help='Fetch from a configured account profile')
    sv.add_argument('--config', '-c', default='./config/config.yaml', help='Path to config file')
    sv.add_argument('--profile', required=True, help='profile_id (filename without .yaml)')
    sv.add_argument('--since-date', type=str, default=None, help='ISO date: 2026-03-01')
    sv.add_argument('--until-date', type=str, default=None, help='ISO date: 2026-03-15')
    sv.add_argument('--verbose', action='store_true')
    sv.add_argument('--unmatched-only', action='store_true')
    sv.add_argument('--errors-only', action='store_true')

    rv = sub.add_parser('rule', help='Test an account YAML file against a .eml file')
    rv.add_argument('--rule', required=True, help='path to account YAML file')
    rv.add_argument('--email-file', required=True)

    args = p.parse_args()
    if args.cmd == 'server':
        cmd_server(args)
    elif args.cmd == 'rule':
        cmd_rule_email(args)
    else:
        p.print_help()
