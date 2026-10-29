# Which "ISO 4217 codes" count as cash in the mirror's `commodities.is_currency`?
- Asked: 2026-10-28 by the implementer of S0050-mirror-build
- blocks: the `commodities` table of the mirror build (S0050 builds every other table; the rows of this one wait, BL-0120)
- Rubric item: 2 (finance semantics a user sees: cash or holding) and 8 (both readings plausible, costly to reverse: it is ADR-0025's one-time reclassification of existing ledgers)
- ADRs checked: ADR-0025 and its 2026-10-25 amendment ("ISO-4217 codes count as cash by default"; "else a code is cash when it is an ISO 4217 code or listed in `operating_currency`") name the standard but not which of its codes, and the standard holds more than money; ADR-0024 (Beancount decides) does not apply, since Beancount has no currency list; the mirror spec (`design/specs/mirror/surface.toml`, `commodities.is_currency`) repeats ADR-0025's words.

## Context (plain language, a concrete example first)
The mirror marks every commodity as cash (1) or a holding (0). The rule you decided: a declared `asset-class` decides first; otherwise a code is cash when it is an ISO 4217 code or an operating currency. Example: in an INR ledger, `USD` in a bank account is cash because USD is an ISO 4217 code; `VTI` is a holding.

ISO 4217 is not only a list of money. Taken literally, it also contains:
- **precious metals**: `XAU` (gold), `XAG` (silver), `XPT`, `XPD`. Someone who records gold as `10 XAU {150000 INR}` holds an investment. Under the literal rule, gold with no `asset-class` becomes cash: it leaves the investments views and joins Cash & Deposits.
- **fund and accounting units**: `XDR` (IMF drawing rights), `CLF`, `COU`, `BOV`, `USN`, the bond-market units `XBA`-`XBD`, and `XSU`, `XUA`.
- **test and "no currency" codes**: `XTS`, `XXX`.
- **historic codes** (the standard's withdrawn list): `DEM`, `FRF`, `ITL` and others replaced by the euro or redenominated. An old ledger with a 1999 `DEM` account is cash under a "current and historic" reading and a holding under a "current only" reading.

There is also a source question: Python has no ISO 4217 list, so the engine needs one. It changes a few times a year (a new code such as `VES`, `ZWG`).

## Options
1. **Current national currencies only**: ISO 4217 List One, without the metals, funds, bond units, `XDR`, `XTS` and `XXX`; historic codes are not cash. A pinned copy of the list (with its publication date) lives in the engine and is updated as a slice.
2. **Option 1 plus the historic national currencies** (List Three without the same X-codes). Old ledgers with pre-euro accounts keep them as cash.
3. **The whole standard literally** (Lists One and Three, metals and funds included). Simplest to state; gold and silver become cash unless the ledger says `asset-class`.
4. **Ask the ledger**: no built-in list at all; only `asset-class` and `operating_currency` decide. This reverses ADR-0025's amendment (a USD bank account in an INR ledger becomes a holding again), so it is listed only for completeness.

## Recommendation
Option 2. Every code that was ever a country's money is cash, which is what a user means by "currency", including old pre-euro accounts; metals, funds and accounting units are holdings unless the ledger declares `asset-class: "cash"`, which still decides first. Gold is the case that matters most: option 3 would move it out of investments without the user asking. The list is pinned in the engine with its source date; a later change of the standard is a small slice, and its effect is visible because the mirror is rebuilt.

## Answer (owner, verbatim, with date)
