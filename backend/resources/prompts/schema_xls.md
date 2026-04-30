## XLS Rule Schema

> **Important:** For XLS/XLSX files, always use `write_xls_rule` to save the rule — never `write_csv_rule`.

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
columns:              # 1-based column indices (column 1 = leftmost column in the sheet)
  date: 1             # (required)
  amount: null        # Use either 'amount' OR 'amount_debit' + 'amount_credit'
  amount_debit: null
  amount_credit: null
  payee: null
  narration: null
  memo: null
default_account: "Assets:Bank:Savings"  # (required)
default_currency: "USD"                 # (required) — infer from bank's country (INR, USD, EUR, GBP…)
```

### Key rules
- Use `sheet_name` rather than `sheet_index` when the bank may add/reorder sheets in future exports.
- **Use the actual sheet name from the parse hint output** — the file content sent to you is
  preceded by a line like `=== Sheet: OpTransactionHistory ===`, and `OpTransactionHistory`
  is what goes into `sheet_name`. **Do not copy the placeholder value from the example
  below** — every bank's sheet name is different, and using the wrong name will fail with
  "Sheet not found" at parse time.
- Date cells that Excel stores as actual date objects are handled automatically regardless of `date_format`.
  Set `date_format` only for cells that contain the date as a plain string.
- `skip_lines_start` and `skip_lines_end` refer to rows within the selected sheet, not the whole file.
- If the file was shown with multiple sheets, identify which sheet has the transaction data and set
  `sheet_index` (or `sheet_name`) accordingly.
- **`skip_lines_end` is NOT usually 0 for XLS.** Unlike CSV, XLS footer rows are not auto-filtered:
  they often contain numeric data (opening/closing balances, page totals) that the parser cannot
  distinguish from real transactions and will import as garbage entries. Many bank XLS exports
  have **20+ footer rows**. You must count them explicitly. Use the parse hint's
  "trailing rows detected" count as a starting point, then verify against the last rows visible in
  the file. When in doubt, err on the side of skipping more footer rows.
- **`skip_lines_start` counting:** the value is **(first transaction row − 1)**. Use the file
  preview's left gutter to find the row of the first real transaction; everything above it is
  skipped. Blank rows count. This usually equals the column header row number, but **only when
  transactions begin immediately after the headers**. If the file has any superfluous rows
  between the headers and the first transaction (blank separators, sub-headers, totals,
  "Statement period" lines, etc.), `skip_lines_start` is greater than the header row number.
  Always derive it from the first transaction row, not from the header row.
- **`payee` vs `narration`:** a column named "Transaction Remarks", "Remarks", "Description",
  "Particulars", or similar free-text description should be mapped to `payee`, not `narration`.
  Use `narration` only when there is a second, distinct description column alongside a payee column.
- **`memo`** maps to reference/voucher number columns — look for "Chq./Ref.No.", "Reference No",
  "UTR No", "Transaction ID". These short alphanumeric reference codes are distinct from narration.
  **If such a column exists, always populate `memo`.** Many bank statements include one —
  do not leave `memo: null` if you can see it.

### Example (ICICI Bank — separate debit/credit columns, many header/footer rows)

```yaml
name: "ICICI Bank Savings"
sheet_name: "OpTransactionHistory"   # ALWAYS read this from the `=== Sheet: ... ===`
                                      # line in the parse hint output — every bank uses a
                                      # different sheet name. The value here is the real
                                      # name for one ICICI export; do not copy verbatim
                                      # for other banks or other ICICI exports.
skip_lines_start: 13
skip_lines_end: 28
date_format: "%d/%m/%Y"
columns:
  date: 4
  payee: 6
  memo: 3                   # Chq./Ref.No. — always populate when a reference column exists
  amount_debit: 7
  amount_credit: 8
default_account: "Assets:ICICI:Savings"
default_currency: "INR"
```
