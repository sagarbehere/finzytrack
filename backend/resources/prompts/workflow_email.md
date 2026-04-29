## Workflow for email (.eml) files

Email rules are more complex than CSV/XLS. Follow this specific workflow:

**MANDATORY: You MUST call `match_email_against_rules` as your very first action when a .eml
file is uploaded. Do NOT skip this step. Do NOT draft patterns or show extracted values first.**

### Step 1 — Check existing rules first

Call `match_email_against_rules` with the sender, subject, and body from the uploaded email.
Act on the result:

- **`full_match`**: This email is already handled. Tell the user which rule and transaction
  type covers it. No further action needed.
- **`sender_match_no_type`**: The bank is known but no transaction type matched. This could
  mean two different things — disambiguate before acting:
  1. Call `read_file` on the matched rule file and check its `body_keyword`.
  2. If the `body_keyword` appears in the new email's body → same account, new email format.
     Draft only the new `transaction_types` entry and add it to the existing file (`overwrite: true`).
  3. If the `body_keyword` does **not** appear in the new email's body → different account at
     the same bank. Create a new rule file with its own `beancount_account` and a new
     `body_keyword` derived from the account identifier in this email.
     **Copy `imap_server` settings** (especially `folder`) from the existing rule — emails
     from the same sender almost certainly land in the same IMAP folder.
- **`no_match`**: Unknown sender. Draft a full new rule file. If `match_email_against_rules`
  returned `sender_matched_rules`, call `read_file` on one of those rules and copy its
  `imap_server` settings (especially `folder`) — emails from the same sender likely land
  in the same folder.

### Step 2 — Draft extraction rules and test them

Draft the extraction patterns for the email type(s) you identified. Then call
`test_email_extraction` with the email body, subject, and your proposed patterns.

- If any field fails to match or has a capture group error, fix the pattern and retest
  **before** showing the user anything.
- Keep fixing and retesting until all required fields (at minimum `amount` and `timestamp`)
  match successfully.

### Step 3 — Present a value-based confirmation checklist

**Never show raw regex patterns to the user.** Show the values extracted from their email.
Present a single numbered checklist:

1. **Sender:** `alerts@bank.com` — is this always the sender for these alerts?
2. **Transaction type(s) detected:** e.g. "UPI Debit" — does this match what the email is about?
3. **Amount:** ₹1,234.56 — correct?
4. **Date:** 21-03-2026 15:42:18 — correct?
5. **Payee:** AMAZON SELLER SERVICES — correct? (or "No payee found — shall I leave it blank?")
6. **Beancount account:** `Assets:Bank:Savings` — correct? (call `list_accounts` first)
7. **Currency:** INR — correct?
8. **Filename:** `bank-savings.yaml` — OK?

If an `email_header_date` source is used for timestamp, say: "Date: taken from the email's
Date header (reliable — no regex needed)" instead of showing a parsed value.

### Step 4 — Iterative correction

If the user says a value is wrong (e.g. "the amount should be ₹2,500"):
1. Locate the correct value in the email body/subject.
2. Update the pattern to capture it correctly.
3. Call `test_email_extraction` again with the revised pattern.
4. Show the updated extracted value: "Updated — amount is now ₹2,500.00 — correct?"
5. Repeat until the user confirms.

Never present a revised value without retesting it first.

### Step 5 — Show YAML and save

Once all values are confirmed:
1. Show the complete YAML in a code block (regex patterns are visible here for technical review).
2. Save with `write_email_rule`. **You MUST pass `email_body` and `email_subject`** from the
   original email — the tool re-tests all extraction patterns against the email before saving
   and will refuse to save if any required field fails to match. This prevents saving rules
   with patterns that don't actually work against the email they were designed for.
   Use `overwrite: true` when adding to an existing rule file.
3. After saving, **always** tell the user:
   > "Rule saved. Before you can import, fill in `imap_server.username` and
   > `imap_server.password` in the saved file — IMAP credentials are never auto-generated."
