# Is a non-ISO operating currency (for example `RMB`) still "cash" in v0.4?
- Asked: 2026-10-25 by orchestrator/S0021-mirror-surface (verifier finding F2)
- blocks: nothing now (`commodities.is_currency` in `design/specs/mirror/surface.toml` is marked `owner:` this file); the M1a mirror-build slice and the M8 Profile need the answer before they classify commodities
- Rubric item: 2 (what a number means: which balances count as money) and 4 (completing ADR-0025)
- ADRs checked: ADR-0025 ("`operating_currency` means only 'report in these' … ISO-4217 codes count as cash by default. `Currency` is a role the Profile assigns, not a lexical class"; it rejects `operating_currency` as the *exclusive* cash whitelist, and does not say what happens to an operating currency that is not an ISO code); ADR-0048 (old mirror names keep working); ADR-0024 (the Profile serves ledger facts).

## Context (plain language, a concrete example first)

A Beancount user in China often writes `option "operating_currency" "RMB"` (the ISO code is `CNY`; `RMB` and Taiwan's `NTD` are common informal codes). Their bank accounts hold `RMB`.

- **v0.2.2:** when `operating_currency` is set, a code is cash exactly when it is in that list. So `RMB` is cash (`is_currency = 1`), and a foreign `USD` account is not (the defect ADR-0025 fixed).
- **v0.4, ADR-0025 applied literally** (a declared `asset-class` decides; else an ISO 4217 code is cash; else not): `USD` becomes cash (the fix), but `RMB`, not being an ISO code, becomes a holding (`is_currency = 0`). The user's own reporting currency would drop out of Cash & Deposits and out of any SQL that filters `WHERE is_currency = 1`.

ADR-0025's own sentence "Currency is a role the Profile assigns, not a lexical class" suggests the ledger's declared reporting currency should count, but the ADR does not say so.

## Options
1. **An operating currency is always cash, in addition to ISO codes** (recommended). Rule: a declared `asset-class` decides first (cash-like → cash, anything else → holding); else the code is cash if it is an ISO 4217 code **or** listed in `operating_currency`; else it is a holding. `RMB` stays cash, a foreign `USD` account becomes cash (ADR-0025's fix), and `VTI` stays a holding.
2. **ADR-0025 literally: ISO codes only.** Users with a non-ISO reporting currency must add `asset-class: "cash"` to its `commodity` directive; the release notes say so.

## Recommendation
Option 1. It keeps ADR-0025's fix (foreign cash is cash), never demotes the currency a user reports in, and needs no edit to anyone's ledger.

## Answer (owner, verbatim, with date)
Owner: "I accept the recommendations for all three questions currently in the inbox" (2026-10-25, in session). Accepted: Option 1: an operating currency is always cash, in addition to ISO codes.
