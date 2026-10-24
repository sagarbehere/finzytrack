# ADR-0032: Broker sanity check, holdings reconciliation and one-upload sync

This record decides how a user keeps investment holdings in step with the broker or registrar.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** investments, import

## Context

The owner occasionally checks whether Finzytrack's investment value matches the broker's portal. When it does not, the cause is usually changed units (for example an uncaptured dividend reinvestment) or a stale price. Recording reinvestments and splits by hand is too much work.

## Decision

(owner, 2026-10-24)
- **A per-account Holdings view laid out like a broker's positions page:** symbol, units, price, price date with its age, value, cost, gain, and the account total. Stale prices are highlighted by the cadence rules, with one-click Refresh prices.
- **Holdings reconciliation**, the investment analogue of statement reconciliation (ADR-0028), with the same report shape (matched / missing / extra / differs + arithmetic), per (folio or account, security) against ledger lots as of the statement date.
- **"Compare with broker"** accepts, from lightest up: a typed total value; a pasted or uploaded positions export; a full statement or CAS. It explains differences in plain words: units differ (usually a reinvestment, with a one-click draft), price differs (both prices and dates side by side), cost or gain differs (with the usual reasons).
- **The primary workflow is "one upload, one confirm":** upload a single broker statement or CAS; Finzytrack infers every event that explains the difference (reinvestments, splits and other unit changes, buys, sells, fees, dividends), builds the entries, and shows one summary ("7 changes … after this your holdings match the statement") for a single confirmation. Anything that cannot be inferred unambiguously is listed, never guessed.
- **Durable result in the ledger:** when units reconcile, units `balance` assertions dated the check date record "matches broker as of X". Cost differences against the registrar are explained, not forced.
- Writes go through the write contract. Positions exports and CAS are import adapters.
- **Scope for v0.4.0:** CAS is release-blocking; broker formats beyond CAS are deferred (ADR-0043).

## Alternatives considered and rejected

- **Single-event quick actions as the main path.** Kept only as secondary tools for a closer look.
- **CAS full-history replay.** Cut (ADR-0030).

## Consequences

- The investment slice reuses the import matcher through a declared slice edge (`investments → importing.usecases`).

## References

review:16 §2, §5; review:27 §0.1 (M8); ADR-0027, ADR-0028, ADR-0030, ADR-0043; INV-14; CAP-INV-COMPARE-BROKER, CAP-INV-CAS-IMPORT.
