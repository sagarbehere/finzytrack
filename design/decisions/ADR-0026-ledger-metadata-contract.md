# ADR-0026: The ledger metadata contract; one import identity key; optional cleanup

This record decides which metadata keys v0.4 writes into ledgers, how entries are identified without stored ids, and which legacy keys it keeps reading.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** ledger format, import

## Context

Every transaction the legacy app creates carries three machinery lines (`id`, `content_hash`, `source_account`). `content_hash` never deduplicated anything, is never recomputed on edit, and changes on a rename. Other keys leak tool internals into the books. The import design, as first written, would have added up to eight more keys per imported entry. Established tools (beangulp, smart_importer, Fava) write no ids; beancount-import writes provenance on the matched posting.

## Decision

(owner, 2026-10-24)
- **App-written keys, the whole list.** Entry meta is built from this list only, never from client-supplied meta:
  - `import-key` on **every anchor posting** of an imported line (never on the transaction): a space-separated set of elements `v<N>:<kind>:<ref>[~dup<n>]`. Kinds: `ofx`, `upi`/`imps`/`neft`/`rrn`, `brk`, `email` (a hash of the Message-ID), `auth`, `cas`, `h` (content fallback, no account in the hash). Refs outside `[A-Za-z0-9._/-]` are hashed. Elements are appended (link, settle, confirmed duplicate), never recomputed or reordered;
  - `txn-date` (transaction, a date) when it differs from the posting date;
  - `original` (anchor posting, an Amount) for a foreign-currency line;
  - `statement-amount` (anchor posting, an Amount) when the user overrides a statement amount;
  - `memo` (transaction) when it differs from the narration, or when the committed payee differs from the statement's raw description.
- **Not written any more:** `id`, `content_hash`, `source_account`, `external_id`, `external_id_type`, `source_rule`, **`status`**, **`import-batch`**, `import-key-override`, the `stmt_*` keys, `advisor`, and the `custom "statement-import"` record.
- **Pending is derived:** pending ⇔ flag `!` ∧ every element of the anchor's `import-key` is of kind `auth`. Settling appends the statement element and sets `*`.
- **Format:** keys are **kebab-case**. The unreleased snake_case vocabulary is renamed now (`interest-rate`, `maturity-date`, `interest-account`, `dividend-of`, `fetch-symbol`, `issue-date`); released snake_case keys (`account_number`, `ifsc_code`, `swift_bic`) stay. Values are written typed (dates, Amounts) and read tolerantly. Key names never carry a version; computed values do (`v1:`). No brand prefix. The contract version lives in `design/`, not in the ledger.
- **Identity without stored ids:** `TransactionId` = legacy `id`/`transaction_id` if present, else `k:` + the first `import-key` element, else the content fallback hash + ordinal. Every mutating request carries `(TransactionId, entry_hash)` and is refused on mismatch; re-planning re-resolves through a fresh mirror; write responses return an old→new id map; undo journals store canonical entry text, not ids.
- **Read forever (INV-21):** every key a released version wrote that a v0.4 reader needs (the id scheme, `external_id`/`external_id_type`/`ofx_id` for exact matching, `source_account` for fallback ids and the mirror's source-account column, transaction-level `ofx_memo` as an alias of `memo`, every `import-key` version, the released vocabulary). Every other key passes through untouched.
- **Price symbol:** read both `fetch-symbol` and bean-price's `price:`; write only `fetch-symbol`.
- **Bank account key:** the released `account_number` on the `open` directive (plus `ofx-bank-id` for the OFX bank id).
- **Optional, consented cleanup migration** (Settings; never the default): rewrite legacy refs into posting-level `import-key` first, then remove machinery keys, rename the snake_case vocabulary, and optionally remove legacy ids (off by default). Line-level edits through the write contract, a preview with samples, a backup, idempotent, and a fail-closed proof that every exact-match key survives.

## Alternatives considered and rejected

- **Keep writing `id`.** Rejected: it is what lets a whole-entry replace overwrite a concurrent Fava edit, and the content address plus `entry_hash` is safer.
- **`import-key` plus `external_id` plus a kind key.** Rejected: the same fact twice.
- **`import-batch` for later undo.** Rejected: tool state in the ledger; undo comes from the D1 undo journal, and a late removal selects by `import-key` and date.
- **A stored `status` key.** Rejected: derivable, and a generic name invites collisions.
- **A brand prefix (`ft-…`).** Rejected: it would lock the books to one tool.

## Consequences

- Entries v0.4 creates are read-only to the legacy app (no legacy `id`).
- Which documented mirror columns (`transaction_content_hash`, `source_account`) the `r_*` views keep is decided in a later view-surface ADR.

## References

review:28 §3, §4, §5, §6; review:12 §2, §3 Q1–Q3; review:D1 §12.6 (M28 class); ADR-0014, ADR-0027, ADR-0028; INV-17, INV-21.
