# ADR-0030: Investment scope reset; prices

This record decides what investment features v0.4 keeps and cuts, and how prices are fetched and stored.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** investments, prices

## Context

About 7,300 lines of v0.3 investment code reached into portfolio-analytics and tax territory. Most of the investment lens's High findings lived there: windowed XIRR, per-class XIRR, opening lots counted at cost, app-side lot picking on an end-of-ledger table, and CAS full-history replay with a "replace" that deleted hand entries (review:16). The owner, as an average investor, wants holdings, net worth, a few understandable gains, and easy updates. Brokers already provide lot and tax reports.

## Decision

(owner, 2026-10-24)
- **Keep, rebuilt on the kernel:**
  1. holdings (units per account and security, from the ledger);
  2. value and net worth (units × price as of a date, one `PriceIndex`, automatic prices, currency conversion);
  3. "how much have I made" per holding, category (`exposure` / `asset-class`), account or instrument, as plain numbers: unrealized gain = value − cost; realized gain from Beancount's booking; income (dividends, interest); simple total return % = (unrealized + realized + income) ÷ cost;
  4. CDs and deposits: principal, interest earned per instrument, maturity; effective yield kept, labelled "effective yield (per year)" (ADR-0047);
  5. easy updates through holdings reconciliation and automatic prices (ADR-0032).
- **Cut:** XIRR, TWR, windowed and per-class returns (CAP-INV-XIRR, CAP-INV-TWR); app-side lot *computation* (frontend FIFO, the end-of-ledger lots table); tax modelling (holding-period labels, STT/TDS/stamp-duty cost roles, segregated portfolios, jurisdiction rules; CAP-INV-TAX-PROFILE later); CAS full-history replay and "replace" (CAP-INV-CAS-REPLACE); statement import and its undo (CAP-INV-CAS-UNDO). A first CAS import records opening positions at the registrar's cost as of a date.
- **Cost** is what the statement or broker reports, or what the user typed. Fees are recorded as the broker shows them; one charges-account field stays.
- **Vocabulary:** `asset-class` stays the instrument type; `exposure` (equity, debt, hybrid, gold, cash, real-estate) is added, separate from any tax class.
- **Prices:** sources (Yahoo, AMFI, I-bonds) behind one registry of pure request builders and parsers; history unadjusted, or units adjusted consistently; cadence and staleness rules (ADR-0025). Prices update in the background when a workspace opens and prices are old, with an off switch.
- **The price sidecar** (`<stem>.prices.beancount`) is a write-contract member file now (same path, same safety). "Include prices in the ledger" is an opt-in migration; the default is revisited with real numbers (including it costs about +26% load time and about 40 MB at 54k).
- Investment dashboards use the `ledger` step's value, cost, realized, unrealized and income measures (ADR-0029).

## Alternatives considered and rejected

- **Fixing XIRR/TWR and per-class returns** (boundary-flow returns). Rejected with the scope: the metrics are beyond the user's goal, and brokers provide them.
- **Keeping "replace" scoped to (account, commodity).** Rejected: matching replaces it.
- **Capitalizing charges by cost-policy rules** (the earlier accepted answer). Superseded by the reset: cost is what the statement says.

## Consequences

- A large deletion; most investment High findings disappear by construction.
- Booking methods are handled by Beancount, including specific-lot choice (ADR-0031).
- The user doc on investments shrinks and gains a booking-methods section.


## Amendment (2026-10-25): conversions whose cost does not divide exactly

Owner: "I accept your recommendations for the four questions in the design/inbox/" (2026-10-25, in session). Covers `design/inbox/answered/20261025-conversion-cost-rounding.md`.
- A conversion (merger, bonus, face-value change) keeps each lot's date and **total cost**. When the per-unit cost does not divide exactly (100 units at 10 INR becoming 300 units), the transaction gets a **rounding leg** to an account **the user picks** (never one Finzytrack invents, INV-15), so it balances exactly. The preview shows the leftover amount.
- No ledger-wide tolerance option is written for this.

## References

review:16 §1–§4; review:14 L5-11 and Q3; review:F §2 item 11; ADR-0025, ADR-0029, ADR-0031, ADR-0032, ADR-0047; CAP-INV-XIRR, CAP-INV-TWR, CAP-INV-CAS-REPLACE, CAP-PRICE-UPDATE.
