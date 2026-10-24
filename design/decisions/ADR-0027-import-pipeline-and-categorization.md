# ADR-0027: Import pipeline on the backend, anchored on postings; tiered categorization

This record decides how statements, alerts and files become ledger entries, and how open slots are categorized.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** import

## Context

The legacy import overwrote the cash leg of investment rows during autocategorize, never matched entries it had not created, committed the same file twice, dropped rows silently, lost tags and links, and had six converters each inventing ids (review:12 L4-01 … L4-15).

## Decision

(owner, 2026-10-24)
- **Principles:** the statement is the book of record for its account; every line is one **anchor posting** on a known Assets or Liabilities account, the other side an **open slot**; nothing is silently dropped, guessed or defaulted (every row lands in a bucket with counts shown, INV-14); re-import is idempotent by construction, enforced at commit; pure core, thin shell.
- **Pipeline on the backend** (`importing/`): read → adapt → normalize → resolve account → reconcile → match → suggest. One tabular reader on the kernel parsers. The frontend reviews and edits **Drafts** (one Draft contract, generated to TS; transfer pairs have two anchors; statement-sourced fields are read-only).
- **Matching is posting-level** on the anchor account, whatever created the ledger entry: a versioned idempotency key per anchor (no account name in it), a date window, extracted references (UPI/IMPS/NEFT), and one-to-one assignment. Outcomes: duplicate, transfer-counterpart, settle-pending. Matching runs at parse and again at commit; duplicates are skipped and reported, with an explicit override (`~dup<n>`).
- **Commit** re-matches against the current ledger version, builds entries with the domain builders (meta from the ADR-0026 contract only), and writes through the write contract (ADR-0008). Import sessions spill to `local/<key>/import-sessions/` and are deleted on commit, discard or after N days.
- **Categorization, in tiers, each suggestion showing its tier:** (1) the user's explicit rules; (2) **merchant memory**, derived from the ledger itself (no new store), keyed by a per-source merchant key such as the UPI VPA, and taught by one confirmation: a consistent merchant fills the slot, a mixed merchant offers ranked one-click choices and stays open, "always ask" is available; (3) fuzzy merchant match; (4) an ML model chosen by evaluation, with channel tokens as stopwords, direction and amount features, batch prediction and a model cached per ledger version; (5) optional AI suggestion when allowed; (6) otherwise the slot stays open. Only open slots are suggestion targets. A benchmark per tier and per source (including one-shot accuracy) is a T2 regression check.
- **Rules:** one tabular rule format for CSV and XLS, one atomic `RuleStore` write path (the AI uses it too), a reader that keeps existing user rule files working, typo checks warn first.
- **Email alerts:** status follows the transaction type: card alerts are authorizations (committed as `!` with an `auth` element, settled from the statement); Indian UPI/IMPS/NEFT alerts are final debits (`*`, linked by reference).
- **Foreign-currency card lines** stay in the card's currency; the original amount goes in `original`; if foreign units should show, the price goes on the expense leg, never on the card posting.
- **Bank identifiers live on the account's `open` directive** (`account_number`, `ofx-bank-id`). `ofx_mappings.yaml` is retired with a one-time, consented migration through the write contract. OFX account auto-create is removed; the source account is validated as open for the line's date range.
- Refunds go to the contra-expense by default (`Income:Refunds` is a Profile option).

## Alternatives considered and rejected

- **Parsing on the frontend with per-format converters** (legacy). Rejected: six converters, six id schemes.
- **Transaction-level fuzzy dedupe on `source_account`.** Rejected: blind to entries the importer did not create.
- **Reviving the content-hash tier.** Rejected (ADR-0026).
- **Strict "last account wins" merchant memory.** Rejected: big merchants are categorized by what was bought.

## Consequences

- Statement reconciliation is the same pipeline in reconcile mode (ADR-0028).
- Broker formats beyond CAS are deferred (ADR-0043).

## References

review:12 §1, §2, §3, §6; review:28 §4.1; review:19 §4 Q5; ADR-0008, ADR-0026, ADR-0028, ADR-0043; INV-14, INV-15.
