## CSV Rule Schema

A CSV rule is a YAML file with the following fields:

```yaml
name: string          # Human-readable rule name (required)
separator: ","        # Column separator; use "\t" for TSV (default: ",")
encoding: "utf-8"     # File encoding (default: "utf-8")
skip_lines_start: 0   # Non-blank lines to skip at the START of the file.
                      # The parser removes truly blank lines before counting,
                      # so only count non-blank lines (metadata, statement period,
                      # column header row). Include the column header row in this
                      # count — column names are not used, only indices.
skip_lines_end: 0     # Non-blank lines to skip at the END of the file.
                      # IMPORTANT: rows where the date column contains text that
                      # cannot be parsed as a date (e.g. footer disclaimers, legend
                      # entries, totals rows with labels) are automatically skipped
                      # by the parser. Set skip_lines_end only for numeric/amount
                      # footer rows that could accidentally parse as valid dates.
                      # For most bank statement footers, skip_lines_end: 0 is correct.
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
default_currency: "INR"                  # (required) ISO currency code — infer from the
                                         # bank's country (INR for India, USD for USA,
                                         # EUR for Europe, etc.). Never assume USD.
```

### Key rules

- Use `amount` when there is a single signed amount column (negative = debit, positive = credit,
  or set `negate_amounts: true` if the signs are inverted).
- Use `amount_debit` + `amount_credit` when the file has separate columns for debit and credit.
- **Counting `skip_lines_start`:** blank lines are stripped before counting. Count only non-blank
  lines before (and including) the column header row. Example: 15 metadata lines + 1 statement
  period line + 1 column header = `skip_lines_start: 17`, even if there are blank lines interspersed.
- **`skip_lines_end` is usually 0** for bank statements. Footer text (disclaimers, legend, footnotes)
  contains no valid dates and is filtered out automatically. Only set `skip_lines_end > 0` if the
  footer contains rows that look like transactions (e.g. a "Total" row with numeric amounts).
- **Currency:** always infer from the bank's country and account context. Indian bank → INR,
  US bank → USD, Eurozone → EUR. If the account is explicitly a foreign-currency account
  (e.g. an NRE/NRO USD account in India), use that currency. Ask the user if unclear.

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
