## XLS Rule Schema

An XLS rule is identical to a CSV rule except for two additional fields that identify which
sheet to read. All other fields (columns, skip_lines_start, skip_lines_end, etc.) work the same way.

```yaml
name: string
sheet_index: 0        # 0-based index of the sheet to read (default: 0)
sheet_name: null      # Sheet name (overrides sheet_index if provided)
skip_lines_start: 0   # Rows to skip at the START — same counting rules as CSV
skip_lines_end: 0     # Rows to skip at the END
date_format: "%Y-%m-%d"
decimal_separator: "."
negate_amounts: false
columns:              # 0-based column indices
  date: 0             # (required)
  amount: null        # Use either 'amount' OR 'amount_debit' + 'amount_credit'
  amount_debit: null
  amount_credit: null
  payee: null
  narration: null
  memo: null
default_account: "Assets:Bank:Savings"  # (required)
default_currency: "USD"                 # (required)
```

### Key rules
- Use `sheet_name` rather than `sheet_index` when the bank may add/reorder sheets in future exports.
- Date cells that Excel stores as actual date objects are handled automatically regardless of `date_format`.
  Set `date_format` only for cells that contain the date as a plain string.
- `skip_lines_start` and `skip_lines_end` refer to rows within the selected sheet, not the whole file.
- If the file was shown with multiple sheets, identify which sheet has the transaction data and set
  `sheet_index` (or `sheet_name`) accordingly.

### Example (ICICI Bank — separate debit/credit columns, many header/footer rows)

```yaml
name: "ICICI Bank Savings"
sheet_index: 0
skip_lines_start: 13
skip_lines_end: 28
date_format: "%d/%m/%Y"
columns:
  date: 3
  payee: 5
  amount_debit: 6
  amount_credit: 7
default_account: "Assets:ICICI:Savings"
default_currency: "INR"
```
