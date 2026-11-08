# ADR-0025: Finance semantics defaults

This record fixes the meaning of the core numbers users see: balances, currencies, future-dated entries and stale prices.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** domain kernel, finance

## Context

The review found period balances summed as floats with Equity treated as a flow, "as of" meaning different things on different paths, a foreign-currency bank account hidden by the cash rule, and dashboards showing two different Total Assets for the same day once a future-dated entry existed (review:11 L3-06, L3-13; review:14 L5-01). The owner is not a finance expert, so these follow finance and Beancount/Fava practice.

## Decision

(owner, 2026-10-24)
- **Period balances:** Assets, Liabilities and Equity are cumulative to the end date; Income and Expenses cover the period. When totals are shown, Retained Earnings and Net Income are synthesized (Fava/Beancount clamp semantics). Sums are exact.
- **Current balances and net worth exclude entries dated after today**, as a bank's ledger balance does. An "include scheduled" toggle shows them.
- **Currency vs holding:** `operating_currency` means only "report in these" (Beancount/Fava semantics). ISO-4217 codes count as cash by default. `Currency` is a role the Profile assigns, not a lexical class. This is a one-time reclassification of existing ledgers, visible in Cash & Deposits. Foreign cash still needs FX conversion for reporting totals.
- **Stale prices never exclude a position** from totals (that would understate net worth). A position is valued at its last price and badged with the price date. Staleness thresholds: daily cadence 7 days, monthly 45 days, manual never.
- **Every position is valued** (units × price as of the date), whatever its cost basis, so holdings bought with only an `@` price never vanish.
- **Realized gains come from Beancount's booking** (lot reductions), not from sale-leg annotations.
- **Fiscal year:** deferred (CAP-FISCAL-YEAR).

## Alternatives considered and rejected

- **Equity as a period flow.** Rejected: it is not how a balance sheet works.
- **`operating_currency` as the "is cash" whitelist** (legacy). Rejected: it conflates "reporting currency" with "cash" and hides foreign bank accounts.
- **Excluding stale positions.** Rejected: understates net worth.

## Consequences

- One backend function per financial question (the `ledger` step measures, ADR-0029) implements these rules once.
- The investment measures follow the same rules (ADR-0030).

## Amendment (2026-10-25, owner-accepted; S0021)

Owner: "I accept the recommendations for all three questions currently in the inbox" (owner, 2026-10-25, in session); `design/inbox/answered/20261025-mirror-is-currency.md` and `20261025-mirror-as-of-date.md`.
- **Cash or holding:** a declared `asset-class` decides first (cash-like → cash, anything else → holding); else a code is cash when it is an ISO 4217 code **or** listed in `operating_currency`; else it is a holding. A non-ISO reporting currency (`RMB`, `NTD`) is never demoted; a foreign ISO currency account is cash (the fix above stands).
- **The mirror's summary tables follow "current = up to today":** `account_balances` and `lots` count entries dated up to the build date in the user's time zone; the mirror records that date and is rebuilt when it changes. SQL then shows the same numbers as the screens; the end-of-ledger figure stays available by summing `postings`.

## Amendment (2026-11-08, owner-accepted; S0086)

Owner decisions refine "cash or holding" above; S0086 built them in the mirror (`commodities.is_currency`). All were given in session and relayed by the orchestrator.
- **Which ISO 4217 codes count** (owner, 2026-10-29: "Yes co every code that was ever a country's money (for currency codes that count as cash), moving BL-0119 to M4, to keeping the per-file locks as a second line and having retry recover first."; the other parts answer other questions; `design/inbox/answered/20261028-iso-currency-list.md`, option 2): every code that was ever a country's money, current and historic; precious metals, funds and accounting units are not. The list is pinned in the engine (`ledger_engine.mirror.core.iso4217`), derived from the ISO 4217 Maintenance Agency's List One and List Three, whose SHA-256 the module records.
- **A code held at cost is a holding** (owner, 2026-11-08, "Held at cost = holding (Recommended)"): "A code that any lot in the ledger holds at cost ({...}) is a holding, unless it is an operating currency or declares asset-class: \"cash\". Uses the ledger's own structure, no names. Fixes AMD/BND/BEL/ZAL as people record stocks. Cost: a foreign currency deliberately held at cost to track forex gains (USD {83 INR}) becomes a holding unless declared, as it was in v0.2.2. A ticker recorded without cost (10 BEL @ 250 INR) stays cash, with the docs saying so." Why: many tickers are spelt like ISO codes (`AMD` the chip maker and the Armenian dram, `BND` a bond fund and the Brunei dollar, `BEL` an Indian stock and a Belgian franc code); under the ISO list alone they counted as cash (S0086's finance red team, round 1 F1 and F2). Note (red team round 2 F4): "as it was in v0.2.2" holds only in a ledger with `operating_currency` set; without it, v0.2.2 counted such USD as cash.
- **Only a code held at cost and never without is a holding** (owner, 2026-11-08, "Only-at-cost = holding (Recommended)"; it narrows the decision above after red team round 2 F1 showed one forex lot taking a plain USD bank balance out of every view): "A code is a holding only when the ledger holds it at cost and never holds it without cost in an account. Stock tickers (AMD, BEL) are always bought at cost: still holdings. USD with a Wise lot plus a plain bank balance: cash, and the lot's USD counts as USD cash too (it is money). Edge: a ticker that also sits somewhere without cost (padded, or transferred plain) counts as cash; the docs and the advice row say so."
- **The order, and the tag** (owner, 2026-11-08, "Operating first; tag wins"): "(a) An operating currency is always cash (confirm S0086's order). (b) Keep: the declared tag wins over held at cost; docs and advice warn that a fund tagged cash shows as units in cash and leaves net worth."
- **A plain posting counts only outside the lot's account** (owner, 2026-11-08, "One more fix: stricter reading"; it narrows the decision above after S0086's red team round 3 showed sales and splits written without `{}`, an RSU's income leg and a pad into a lot account each turning a ticker into cash): "Before landing, change the rule: a posting without cost counts only in an account that never holds that code at cost. Fixes the sale-without-{}, the split and the RSU leg, plus a pad into a lot account. Costs another fix round and a review round on this rule."
- **The ISO source files** (owner, 2026-11-08, "Keep out of the repo (Recommended)"): "Remove the two XML files before pushing. The pinned code list (just the codes, facts) stays. The source test runs only when the files are present locally (T-local), otherwise it is skipped with its reason; the hashes in the module still name the exact files. Nothing of uncertain licence becomes public." The test reads them from the folder named by `FT_ISO4217_DIR`.

**The rule, first that applies** (the owner's order):
1. a code listed in `operating_currency` is cash, whatever its `asset-class`;
2. a declared `asset-class` (its text, trimmed, not empty) decides: `cash` or `currency` in any case is cash, anything else a holding;
3. a code the ledger holds only at cost is a holding: it is the units of some posting with a cost (`{...}`, `{}` included, a purchase or a sale from a lot), and no posting of it without cost sits in an account that can hold a balance and never holds that code at cost. Every transaction counts, future-dated and padding ones included. A price (`@`) is not a cost; the currency a cost or price is stated in is not held by that posting. "An account that can hold a balance" is one of kind Assets or Liabilities (by the ledger's `name_*` options, ADR-0024): a plain leg in an Income, Expenses or Equity account (an RSU's income leg in shares, a fee paid in shares, a pad's source) does not count. This reading of the owner's "an account" is S0086's (agent): the owner's own examples (the RSU leg, a pad into a lot account) are holdings only under it, since their other plain leg is in an Income or Equity account that never holds the code at cost; the owner may still choose otherwise;
4. a code that was ever a country's money in ISO 4217 is cash;
5. anything else is a holding.

**Consequences, stated plainly:** USD bought at cost to track forex gains beside a plain USD bank balance (or a USD credit card) is cash, and the lot's USD shows as cash too; the lot then leaves the holdings and gains views, so its forex gain is not shown there. USD only ever held as lots at cost is a holding unless declared; so is USD in an account that holds a USD lot at cost when every other USD posting is outside the balance sheet (a NONE-booking bank account with a short lot, S0086 round 3). A sale or a split written without `{}` in the lot's own account, an RSU's income leg in shares and a pad into a lot account leave a ticker a holding. A ticker spelt like an ISO code that the ledger holds anywhere without cost (bought with only a price, `10 BEL @ 250 INR`; padded; opened or moved without its cost) is read as the currency it spells, cash, until the ledger declares its `asset-class`. A fund declared `asset-class: "cash"` (`LIQUIDBEES`) is cash even when bought at cost: it then shows in the cash views as a count of its units (50 `LIQUIDBEES`, not their value in INR) and leaves the holdings and net worth (the owner's words above). Money with no ISO code (`RMB`, `NTD`, `NIS`, `CNH`, a stablecoin) is a holding unless it is an operating currency or declares `asset-class: "cash"`; sub-units with their own codes outside ISO (`GBX` pence, `ZAC` cents, `ILA` agorot) are holdings; funds and indexed units ISO flags (`CLF`, `MXV`, `UYI`) are holdings. The advice that tells the user about such codes is BL-0207 (M8).

**What depends on it (M8):** `is_currency` decides how the investment screens treat a code. A cash code gets no price fetched (CAP-PRICE-UPDATE), is not offered as a security by the investment forms (CAP-INV-QA-BUY, CAP-INV-QA-ADD-HOLDING), and is valued at the FX rate (CAP-PRICE-FX). So a ticker misread as cash (`BND` bought with only a price, `@`) gets no price update and is valued as Brunei dollars, and a currency misread as a holding is priced like a security; those capability rows cite BL-0207, whose advice names such codes.

**Alternatives rejected:** the ISO list alone (the 2026-10-29 rule as first built in S0086): every ISO-spelt ticker became cash and left the holdings views. Any lot at cost makes a holding (S0086 round 1): one forex lot took every balance of that currency, a plain bank account included, out of the cash views. A plain posting in any account counts (S0086 round 2): a sale or split written without `{}`, an RSU's income leg or a pad turned a ticker into cash.

## References

review:11 Q2, Q3, Q5, Q6, Q8, L3-06, L3-13; review:14 L5-01, M-NC, L5-11; ADR-0024, ADR-0029, ADR-0030; INV-02.
