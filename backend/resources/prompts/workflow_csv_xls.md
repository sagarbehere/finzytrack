## Workflow for CSV and XLS files

The frontend displays the uploaded file as a **numbered table**: row numbers appear in the left
gutter (starting at 1) and column indices appear along the top row (starting at 0). The user can
see this table and refer to specific rows and columns by number.

**Do not try to auto-generate and save a rule without user confirmation. Always follow these steps:**

1. **Identify the column structure** from the parse hint and the column header row visible in the file content.
   - The parse hint is a **best-effort guess from a heuristic** and can be wrong on files with
     unusual layouts. Treat it as a starting point, not gospel.
   - **Cross-check** the parse hint against the column header row you can see in the file
     content. If the parse hint's `skip_lines_start` doesn't line up with where the column
     header row actually appears, trust what you see in the file and adjust.
   - Map columns to schema fields (`date`, `payee`, `memo`, `amount_debit`, `amount_credit`)
     based on the column header names. State each mapping once.
   - **Do not analyze individual transaction values.** They are irrelevant to rule generation —
     the rule only needs the column structure, not the contents.
   - Once you've identified the column structure, **do not re-derive or re-verify** your
     conclusions. Proceed directly to step 2 (present the confirmation checklist).

2. **Present a confirmation checklist.** Show all your guesses at once in a single numbered list.
   Tell the user to confirm each item or give the correct value. Include:
   - Header row: "Row N contains the column headers — correct?"
   - Date column: "Column X ('Name') — correct?"
   - Date format: "Format looks like `%d/%m/%Y` (e.g. '01/03/2026') — correct?"
   - Amounts: "Separate debit (col X) and credit (col Y) columns — correct?" or "Single amount column at col X — correct?"
   - Description/payee: "Column X ('Name') — correct?" or "No description column found — shall I leave it blank?"
   - Reference/memo: "Column X ('Name') — correct?" or "No reference column — shall I leave it blank?"
   - Footer rows to skip: "N footer rows to skip — correct?"
   - Beancount account: call `list_accounts` first, then suggest the most likely match
   - Currency: state your inference (e.g. "INR based on the bank") and ask if correct
   - Filename: suggest a slug-style name (e.g. `icici-savings.yaml`)

3. **Wait for the user's response.** They may say "all good" or correct specific items.
   Ask a follow-up only if something is genuinely unclear.

4. **Show the complete YAML** in a code block.

5. **Save the file** using the correct tool for the file type. **Only call the write tool
   after the user has confirmed the checklist** — even if the user's original message says
   "save it" or "create it", treat that as the request to start this workflow, not as
   permission to skip steps 2–4.
   - CSV/TSV file → `write_csv_rule`
   - XLS/XLSX file → `write_xls_rule` (**never** `write_csv_rule`)

6. **After saving**, tell the user the file path and remind them to use the Import panel
   to actually import transactions.

### Notes
- If the file has both "Value Date" and "Transaction Date" columns, always prefer Transaction Date.
- If validation of a saved rule finds 0 transactions, read the rule back, identify what is wrong,
  show the corrected YAML, and save again.
