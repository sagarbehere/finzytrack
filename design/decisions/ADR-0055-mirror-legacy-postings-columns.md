# ADR-0055: The mirror keeps the documented v0.2.2 `postings` columns `transaction_content_hash` and `source_account`, computed by the mirror

This record decides what the mirror's documented SQL surface does with two columns whose ledger keys v0.4 no longer writes (closes the open item in ARCHITECTURE.md §14; review:28 §7).

- **Status:** Accepted
- **Date:** 2026-10-25
- **Deciders:** owner: "yes, record the ADR and close Q12" (2026-10-25, in session, accepting the recommendation)
- **Area:** mirror, compatibility

## Context

v0.4 stops writing the ledger keys `content_hash` and `source_account` (ADR-0026), and the import matcher never uses `content_hash` (I5: tolerated, never matched). But the released user docs (`reference/querying-data.md`) document the mirror's `postings` table with the columns `transaction_content_hash` ("SHA256 content-based hash of the transaction"), `source_account` and `source_account_type` ("the originating Assets or Liabilities account for this transaction"). Users' own SQL and dashboards may select them, and v0.2.2 mirror names keep working through 0.4.x (ADR-0048). No v0.2.2 seed dashboard uses either column. The stored `content_hash` values are stale after edits and random in the demo. The legacy `source_account` key is the only record of the originating statement for transactions between two of the user's own accounts (about 6% of demo entries), which INV-21 protects.

## Decision

- **`postings.transaction_content_hash`:** the column stays through 0.4.x. The mirror fills it with a hash **computed from the entry** (a canonical form, documented in the generated mirror reference), never copied from the ledger key. Nothing in the app reads it. It may be dropped after 0.4.x with a release-note notice.
- **`postings.source_account` and `source_account_type`:** the columns stay, **computed by the mirror**: the legacy `source_account` key when present (INV-21 (a)); else the leg that carries the entry's `import-key`; else the single Assets or Liabilities leg; else NULL. v0.4 never writes the key.
- Both are part of the versioned documented surface (INV-05) with their computation rules in the generated mirror reference.

## Alternatives considered and rejected

- **Drop `transaction_content_hash` now.** Defensible (unlikely to be queried), rejected as the less safe reading of ADR-0048.
- **Copy the stored `content_hash` values.** Rejected: stale after edits, random in the demo, and misleading under its documented meaning.
- **Drop `source_account`.** Rejected: useful provenance, and for transfer-type legacy entries the stored key is the only record (INV-21).

## Consequences

- The mirror row builder (engine, ADR-0054) computes both fields; goldens pin the rule order and the hash's canonical form.
- The user docs on the `v0.4` branch describe both columns as computed.

## References

ADR-0015, ADR-0026, ADR-0048, ADR-0054; INV-05, INV-21; review:28 §3 items 1–2, §7; finding I5.
