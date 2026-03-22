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

parsing_mode: "regex"       # "regex" is the standard mode

transaction_types:          # One entry per distinct email format from this bank
  - name: string            # e.g. "debit", "credit", "transfer"
    description: string
    version: "1.0"
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
    mapping:                # Map extraction field names to rule field names
      amount: "amount"
      timestamp: "date"
      # Other available targets: payee, narration, memo
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
- `imap_server` credentials must be left blank in the generated file — tell the user to fill them in.
- Create one `transaction_types` entry per distinct email format. Banks often send different
  formats for debits vs credits; each needs its own entry with its own extraction patterns.
- Every `pattern` must have EXACTLY ONE capture group `(...)`. The captured text is the extracted value.
- `cleanup: "remove_commas"` strips commas from amounts like "1,234.56" before parsing as float.
- Use `source: "email_header_date"` for the timestamp whenever possible — it avoids fragile regex
  on date strings and handles timezone correctly via the email headers.
- `amount_sign.field: "fixed"` with `value: "negative"` marks all transactions of this type as debits.
  Use `value: "positive"` for credit/deposit alert emails.

### Example (Chase debit alert)

```yaml
metadata:
  name: "Chase Checking Alerts"
  beancount_account: "Assets:Chase:Checking"
  default_currency: "USD"
  institution: "Chase"
  description: "Chase debit card transaction alerts"
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

parsing_mode: "regex"

transaction_types:
  - name: "debit_purchase"
    description: "Debit card purchase notification"
    email_filter:
      subject_regex: "Your .* transaction"
    extraction:
      amount:
        pattern: "\$([\d,]+\.\d{2})"
        type: "float"
        source: "body"
        cleanup: "remove_commas"
      merchant:
        pattern: "at (.+?) on"
        type: "string"
        source: "body"
      timestamp:
        type: "datetime"
        source: "email_header_date"
    mapping:
      amount: "amount"
      merchant: "payee"
      timestamp: "date"
    amount_sign:
      field: "fixed"
      value: "negative"
    error_handling:
      required_fields: ["amount", "timestamp"]
      partial_match_allowed: true
```
