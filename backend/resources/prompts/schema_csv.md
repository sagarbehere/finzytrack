## CSV Rule Schema

A CSV rule is a YAML file with the following fields:

```yaml
name: string          # Human-readable rule name (required)
separator: ","        # Column separator; use "\t" for TSV (default: ",")
encoding: "utf-8"     # File encoding (default: "utf-8")
skip_lines_start: 0   # Lines to skip at the START of the file (before header)
                      # Count rows that are metadata/blank before the column header row.
                      # The header row itself is NOT a transaction — include it in skip_lines_start
                      # only if you want to skip it too, otherwise it is skipped automatically
                      # by the column index mapping (column names are not used, only indices).
                      # IMPORTANT: skip_lines_start counts from line 0. If the file has 2 metadata
                      # lines followed by a header line, skip_lines_start = 3 (skip all 3).
skip_lines_end: 0     # Lines to skip at the END of the file (footer rows, totals, blank lines)
date_format: "%Y-%m-%d"  # strftime format string, e.g. "%m/%d/%Y", "%d-%b-%Y"
decimal_separator: "."   # "." or "," depending on locale
negate_amounts: false    # Set true if the bank shows debits as positive and credits as negative

columns:              # 0-based column indices (required)
  date: 0             # (required) column index for the transaction date
  amount: 2           # Use either 'amount' OR 'amount_debit' + 'amount_credit', not both
  amount_debit: null  # Column index for money-out amounts (mutually exclusive with 'amount')
  amount_credit: null # Column index for money-in amounts  (mutually exclusive with 'amount')
  payee: null         # (optional) column index for payee/merchant name
  narration: null     # (optional) column index for description/narration
  memo: null          # (optional) column index for memo/reference

default_account: "Assets:Bank:Checking"  # (required) Beancount account for this source
default_currency: "USD"                  # (required) ISO currency code
```

### Key rules
- Use `amount` when there is a single signed amount column (negative = debit, positive = credit,
  or set `negate_amounts: true` if the signs are inverted).
- Use `amount_debit` + `amount_credit` when the file has separate columns for debit and credit.
- `skip_lines_start` must be set so that the FIRST row read is the first transaction row (not the header).
- Always look at the end of the file for footer rows (totals, blank lines) and set `skip_lines_end`.

### Example

```yaml
name: "Chase Checking"
separator: ","
encoding: "utf-8"
skip_lines_start: 1
skip_lines_end: 0
date_format: "%m/%d/%Y"
decimal_separator: "."
negate_amounts: false
columns:
  date: 0
  payee: 2
  narration: 3
  amount: 5
default_account: "Assets:Chase:Checking"
default_currency: "USD"
```
