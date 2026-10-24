# ADR-0023: Money, dates and number formatting

This record decides the types for money and dates, the rounding policy, and how numbers are formatted.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** domain kernel

## Context

The review found the sale form rounding units to 2 decimals, user numbers re-scaled (which loosened Beancount's tolerance tenfold), 26 local formatters with 5 locale policies, a frontend/backend rounding-mode mismatch, and 12 UTC "today" defaults (review:11 L3-01, L3-05, L3-08, L3-10).

## Decision

(owner, 2026-10-24)
- **Money types:** `Decimal` in Python (plus Beancount types), `TEXT` in SQLite, a JSON string on the wire, and a branded `Money` over `decimal.js` in TypeScript. Float appears only at display and in the documented REAL result of `dsum` (INV-06, ADR-0016).
- **Rounding:** a number the user or a file wrote **keeps its written scale**. Only derived numbers are quantized: cash HALF_EVEN to the currency's precision; per-unit prices at 10 decimals. HALF_EVEN on both sides. Never pad commodity units; pad only weight-currency legs of priced or cost postings to that currency's minor units.
- **Display precision per commodity** comes from the Ledger Profile (`dcontext`). One `format.ts` wraps `Intl.NumberFormat` once.
- **Number formatting follows the currency, not the user's locale:** lakh/crore grouping and compact units for INR, NPR, PKR, BDT and LKR; thousands/millions for all others. Non-currency commodities follow their price currency, falling back to the reporting currency. **The decimal mark and group separator are one app-wide setting** (default `.` and `,`), never varying per row. A per-currency override exists in settings. Implementation: format with the currency's grouping, then substitute the app-wide separators.
- **Dates are civil dates** in the user's time zone. The frontend uses `Temporal.PlainDate` via `temporal-polyfill` (dropped once WebKit ships Temporal); native `Date` is used for no calendar logic. The backend uses `datetime.date`, `zoneinfo` and `python-dateutil` in one `domain/dates.py`. "Today" is always `today(tz, clock)`; there is never a UTC "today" (INV-07). Email header dates are converted to the user's time zone.
- Dates display as ISO in tables; there is no date-format setting in v0.4.0 (ADR-0047).

## Alternatives considered and rejected

- **Money libraries** (Dinero.js, currency.js, py-moneyed). Rejected: they assume ISO currencies with fixed minor units, which conflicts with Beancount commodities, high-precision prices and written-scale tolerance.
- **Grouping by the user's locale** (CLDR practice, the review's first answer). Rejected by the owner: INR amounts read in lakhs and crores wherever the user is.
- **Per-row decimal marks.** Rejected: `€1.234,56` next to `₹1,234.56` is confusing.
- **A new backend date library.** Rejected: the stdlib plus dateutil is enough.

## Consequences

- One concept counter per formatter and per "today" helper (EP-23).
- Tests run with `TZ` west of UTC and one job east of UTC.

## References

review:11 §1–§3 (Q1, Q4, Q9, library decisions); review:F §2 item 11; ADR-0016, ADR-0024, ADR-0047; INV-06, INV-07; CAP-UI-MONEY-FORMAT, CAP-SET-NUMBER-FORMAT.
