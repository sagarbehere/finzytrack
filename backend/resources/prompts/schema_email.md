## Email Rule Schema

An email rule describes how to extract transaction fields from bank notification emails
using regex patterns. Each rule file covers one email account / bank.

```yaml
metadata:
  name: string              # Display name in the UI
  beancount_account: string # Target Beancount account
  default_currency: "USD"
  institution: string       # Bank/institution name (informational)
  description: string       # Brief description
  version: "1.0"

imap_server:                # IMAP connection details — leave blank, user fills these in
  server: ""                # e.g. "imap.gmail.com"
  port: 993
  username: ""              # Email address
  password: ""              # Password or app password
  folder: "INBOX"

lookback_days: 30           # How many days back to fetch emails (optional)
bank_emails:                # Sender addresses to accept; others are ignored
  - "alerts@chase.com"

body_keyword: "XX1234"      # Plain string added to IMAP SEARCH for server-side pre-filtering.
                            # Use the masked account number from the email (e.g. "XX8968").
                            # Required when the same bank sends alerts for multiple accounts
                            # from the same sender address — pins this rule to one account.

parsing_mode: "regex"       # "regex" is the standard mode

transaction_types:          # One entry per distinct email format from this bank
  - name: string            # e.g. "UPI_Debit", "NEFT_Credit", "debit_purchase"
    description: string
    email_filter:
      subject_regex: null   # Regex matched against subject (optional pre-filter)
      body_regex: null      # Regex matched against body (optional pre-filter)
    extraction:             # Named fields to extract from the email
      amount:
        pattern: "\$([\d,]+\.\d{2})"  # Python regex with EXACTLY ONE capture group
        type: "float"       # string | float | integer | datetime
        source: "body"      # body | subject | email_header_date
        cleanup: "remove_commas"      # remove_commas | strip_whitespace | null
        multiline: false
        optional: false
      timestamp:
        pattern: null       # omit pattern when source is email_header_date
        type: "datetime"
        source: "email_header_date"
        format: null        # strptime format string, only needed if source=body/subject
        timezone: null      # e.g. "+05:30" — use when emails lack timezone info
    mapping:                # Map extraction field names → output field names
      amount: "amount"
      timestamp: "date"
      # Available output targets:
      #   amount    — transaction amount (required)
      #   date      — transaction date (required)
      #   payee     — merchant/counterparty name
      #   narration — transaction description
      #   memo      — additional note
      #   external_id      — unique transaction reference (e.g. NEFT ref number, UPI ID)
      #   external_id_type — literal string label for the ID type (e.g. "NEFT", "UPI")
      #                      Unlike other mappings, this is a literal value, not an extracted field.
    amount_sign:
      field: "fixed"        # "fixed" or the name of an extracted field
      value: "negative"     # "negative" | "positive" (only when field=="fixed")
      # When field is an extracted field (e.g. a "type" field in the email):
      # positive_values: ["Credit", "Deposit"]
      # negative_values: ["Debit", "Purchase"]
    error_handling:
      required_fields: ["amount", "timestamp"]
      partial_match_allowed: true
```

### Key rules
- **One rule file per bank account.** If a user has two accounts at the same bank, create two
  separate rule files. Use `body_keyword` (IMAP server-side pre-filter) and `body_regex` on each
  transaction type to pin the rule to a specific account's masked number (e.g. "XX8968"). This
  prevents emails for one account from matching the rule for another.
- **Always include `body_keyword`** — set it to the masked account number found in the email
  body (e.g. "XX7317"). This is critical for users with multiple accounts at the same bank.
- `imap_server` credentials must be left blank in the generated file — tell the user to fill them in.
- **`imap_server.folder`** defaults to `"INBOX"` but many users filter bank emails into dedicated
  folders. When creating a rule for a sender that already appears in another rule, copy the
  `folder` value from that existing rule — emails from the same sender land in the same folder.
- Create one `transaction_types` entry per distinct email format. Banks often send different
  formats for debits vs credits; each needs its own entry with its own extraction patterns.
- Every `pattern` must have EXACTLY ONE capture group `(...)`. The captured text is the extracted value.
- `cleanup: "remove_commas"` strips commas from amounts like "1,234.56" before parsing as float.
- Use `source: "email_header_date"` for the timestamp whenever possible — it avoids fragile regex
  on date strings and handles timezone correctly via the email headers.
- `amount_sign.field: "fixed"` with `value: "negative"` marks all transactions of this type as debits.
  Use `value: "positive"` for credit/deposit alert emails.
- When the email contains a unique reference number (NEFT ref, UPI ID, etc.), extract it and map
  it to `external_id`. Set `external_id_type` to a literal string like `"NEFT"` or `"UPI"`.
- **When calling `write_email_rule`, always pass `email_body` and `email_subject`** from the
  original email. The tool re-runs all extraction patterns against the email before saving and
  will reject the rule if any required field fails to match. This is a safety net — do not skip it.

### Example 1 — Separate debit and credit transaction types (most common)

Banks typically send different email formats for debits vs credits. Create one `transaction_types`
entry for each:

```yaml
metadata:
  name: "Chase Checking Alerts"
  beancount_account: "Assets:Chase:Checking"
  default_currency: "USD"
  institution: "Chase"
  description: "Chase checking account transaction alerts"
  version: "1.0"

imap_server:
  server: ""
  port: 993
  username: ""
  password: ""
  folder: "INBOX"

lookback_days: 30
bank_emails:
  - "no.reply.alerts@chase.com"

body_keyword: "XX4829"       # masked account number from the email

parsing_mode: "regex"

transaction_types:
  - name: "debit_purchase"
    description: "Debit card purchase notification"
    email_filter:
      subject_regex: "Your .* transaction"
      body_regex: "XX4829"
    extraction:
      amount:
        pattern: '\$([\d,]+\.\d{2})'
        type: "float"
        source: "body"
        cleanup: "remove_commas"
      merchant:
        pattern: 'at (.+?) on'
        type: "string"
        source: "body"
      reference:
        pattern: 'Ref #([A-Z0-9]+)'
        type: "string"
        source: "body"
        optional: true
      timestamp:
        type: "datetime"
        source: "email_header_date"
    mapping:
      amount: "amount"
      merchant: "payee"
      reference: "external_id"
      external_id_type: "CARD"    # literal value, not an extracted field
      timestamp: "date"
    amount_sign:
      field: "fixed"
      value: "negative"           # debit = money out
    error_handling:
      required_fields: ["amount", "timestamp"]
      partial_match_allowed: true

  - name: "deposit"
    description: "Deposit/credit notification"
    email_filter:
      subject_regex: "Deposit received"
      body_regex: "XX4829"
    extraction:
      amount:
        pattern: '\$([\d,]+\.\d{2})'
        type: "float"
        source: "body"
        cleanup: "remove_commas"
      timestamp:
        type: "datetime"
        source: "email_header_date"
    mapping:
      amount: "amount"
      timestamp: "date"
    amount_sign:
      field: "fixed"
      value: "positive"           # credit = money in
    error_handling:
      required_fields: ["amount", "timestamp"]
      partial_match_allowed: true
```

### Example 2 — Field-based amount sign (single email format for both debits and credits)

Some banks send one email format with a "Debit"/"Credit" label. Extract that label as a field
and point `amount_sign.field` at it instead of using `"fixed"`:

```yaml
extraction:
  txn_type:
    pattern: 'Type: (Debit|Credit)'
    type: "string"
    source: "body"
amount_sign:
  field: "txn_type"
  negative_values: ["Debit"]
  positive_values: ["Credit"]
```

The rest of the rule (envelope, other extractions, mapping, `error_handling`) is identical
to Example 1.
