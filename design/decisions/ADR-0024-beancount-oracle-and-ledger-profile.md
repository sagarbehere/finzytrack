# ADR-0024: Beancount is the oracle; one Ledger Profile serves ledger facts

This record decides where ledger semantics come from and how the frontend learns ledger facts.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** domain kernel, ledger

## Context

v0.3 had six balance checks with three tolerance models, an account-name validator that rejected names Beancount accepts and accepted names it rejects, hardcoded root names, five price lookups with three semantics, and frontend copies of backend regexes (review:11).

## Decision

(owner, 2026-10-24)
- **Beancount is the oracle** for booking, interpolation, tolerance, validation, account grammar, currency grammar, the price map and the printer (INV-02). Finzytrack wraps it in `ledger.beancount_port` and never re-implements it (EP-03).
- **Account grammar** = Beancount's parser grammar (`valid_account_regexp(options)` plus the lexer), not `account.is_valid`, which wrongly rejects names that load. `parse_account` and `Tolerance` construction live in `ledger` behind the port; `domain.AccountName` and `domain.Tolerance` are value types whose only constructors are there.
- **Custom root names** (`option "name_assets"` …) are supported end to end: roots come from the Profile, and the mirror computes a canonical account kind.
- **Tolerance:** the backend wraps `infer_tolerances` / `compute_residual`. A frontend live indicator is a port pinned by vectors generated from Beancount, and it reports "indeterminate" for postings that need booking. Batch import review asks the backend.
- **One `PriceIndex`** with Beancount price-map semantics: as-of, quote-currency specific, same-day duplicates resolved (last by id).
- **The Ledger Profile**, backend-owned and served per workspace and ledger version: root names, operating and reporting currencies, tolerance options, per-commodity display precision, currency roles, the unknown account, the metadata vocabulary and the ledger version. The frontend reads it through one query hook and never invents ledger facts.
- **Beancount-generated vectors** (`gen_domain_vectors.py`) pin the frontend ports and the backend wrappers.

## Alternatives considered and rejected

- **Own validators, regexes and tolerance math.** Rejected: every re-implementation drifted.
- **Refusing ledgers with custom roots.** Rejected: refusing ledgers that Beancount loads is user-hostile (INV-03).
- **A generated account regex on the frontend.** Rejected: the frontend asks the backend.

## Consequences

- Concept counters guard tolerance, balance-check and account-grammar implementations at one each.
- The import-linter contract allows only `beancount_port` to import `beancount`.

## References

review:11 L3-04, L3-09, L3-12, L3-14, L3-17, §2, Q7; review:25 §2.3, §2.6; review:F §3.10 (E-01 option b); ADR-0023, ADR-0025; INV-02, INV-03; EP-03.

## Amendment (2026-10-27): wording matches Beancount

Owner: "Also, regarding the inbox questions: Yes to correcting the wording in the ADR sentences, Replace the line with the accurate list re: inv-24, defer the privacy list for now, and if it still matters, I'd pick the "slots are engine processes, queue lives in the sidecar" design and reword INV-28 and INV-31 and add INV-32 as proposed." (2026-10-27, in session), answering `design/inbox/answered/20261025-adr-wording-price-and-booking.md` (option 1).
- The parenthetical "same-day duplicates resolved (last by id)" in the `PriceIndex` decision now reads: same-day duplicates resolved as Beancount's price map does (the vectors in `corpora/vectors/` pin the rule). The decision itself, Beancount's price map as the oracle, is unchanged.
