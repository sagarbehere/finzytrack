# ADR-0052: The default currency lives in the ledger; the tool setting is only an override

This record decides where the default (operating) currency is stored (review:G F-10, register row 81).

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner (review:15 §3 Q2, accepted)
- **Area:** workspaces, settings, ledger

## Context

v0.3's setup wizard writes the chosen currency as `option "operating_currency"` into the starter ledger, and the tool config also has `accounts.default_currency`. Two sources can disagree.

## Decision

- The currency is a **fact in the ledger** (`option "operating_currency"`), per INV-04. Creating a workspace writes it into the new ledger.
- `accounts.default_currency` is **derived from the ledger** (the first operating currency, through the Ledger Profile). A tool setting exists only as an explicit **override**, stored in `.finzytrack/` and shown as such.
- Opening an existing ledger never writes an operating-currency option into it.

## Alternatives considered and rejected

- **The tool setting as the source.** Rejected: facts belong in the ledger (INV-04), and other Beancount tools read the option.
- **Writing the option into existing ledgers on open.** Rejected: opening never writes (INV-30), and the user's ledger is theirs.

## Consequences

- CAP-SETUP-CURRENCY and the workspace settings read the Ledger Profile first; the override UI says it overrides the ledger.

## References

review:15 §3 Q2; review:23 §1.3; review:F §3.3; INV-04, INV-30; CAP-SETUP-CURRENCY.
