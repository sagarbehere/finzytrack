# ADR-0031: Every booking method Beancount books is supported

This record decides how sales of multi-lot holdings work after app-side lot computation was cut.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** investments, ledger

## Context

The scope reset (ADR-0030) writes a sale as a lot reduction (`-5 VTI {}`) and lets Beancount book it. Under Beancount's default STRICT booking, that reduction is rejected when several lots could match. The owner's rule: no "Beancount supports it, but Finzytrack doesn't" (INV-03).

## Decision

(owner, 2026-10-24) Support every method Beancount 3.2.0 actually books. Beancount does all booking; the app does no lot math.

| Method | Beancount does | Finzytrack does |
|---|---|---|
| FIFO / LIFO / HIFO | picks lots automatically | writes `-N X {}`; previews which lots Beancount will use and the realized gain (a contract dry-run) |
| STRICT (default), STRICT_WITH_SIZE | requires the lots to be named | a **lot picker** listing lots held on the sale date; writes one posting per chosen lot with its lot spec |
| NONE | no lot matching | writes the reduction as given; views say "gains not tracked (booking NONE)" |
| AVERAGE | declared but refused by Beancount 3.2 | shown as unavailable with Beancount's reason; average cost stays a reporting figure |

- **Where the method lives:** the ledger-wide `option "booking_method"` and the per-account override on the `open` line, both read into the Ledger Profile. The account form gets a booking-method field and Settings the ledger default; writes go through the contract.
- **The lot list comes from Beancount's booked inventory as of the sale date**, label-aware: the same source as the `ledger` step. "Choose lots instead" is available on any account.
- **The first ambiguous sale on an account with no method** offers: pick lots now, or set the account's method, each explained in one line; FIFO is marked as matching Indian mutual-fund rules and most brokers' default; nothing is preselected.
- **Changing an account's method later** can change past realized gains; the preview shows "N past sales re-booked; realized gain changes by X" and needs explicit confirmation.
- **One-upload sync:** writes `{}` where the method decides; uses the statement's lot detail when available; on STRICT accounts without lot detail, the sale is listed for the user, never guessed.

## Alternatives considered and rejected

- **FIFO only.** Rejected: narrows Beancount; STRICT accounts would reject partial sales.
- **App-side lot computation** (legacy). Rejected: it pinned the wrong lot on backdated sales.

## Consequences

- The demo includes a fragment per booking method (ADR-0022).
- The v0.2.2 `lots` table name keeps working, served from as-of booked inventory (ADR-0048).

## References

review:19 §5, §4 Q-A; review:16 §4 (amended); ADR-0022, ADR-0024, ADR-0030, ADR-0048; INV-02, INV-03; CAP-INV-BOOKING-METHODS, CAP-SET-BOOKING-DEFAULT.

## Amendment (2026-10-27): wording matches Beancount

Owner: "Also, regarding the inbox questions: Yes to correcting the wording in the ADR sentences, Replace the line with the accurate list re: inv-24, defer the privacy list for now, and if it still matters, I'd pick the "slots are engine processes, queue lives in the sidecar" design and reword INV-28 and INV-31 and add INV-32 as proposed." (2026-10-27, in session), answering `design/inbox/answered/20261025-adr-wording-price-and-booking.md` (option 1).
- The table row "STRICT (default), STRICT_WITH_SIZE: requires the lots to be named" now reads: STRICT: a reduction must match one lot unambiguously (`{}` books when only one lot matches); STRICT_WITH_SIZE: as STRICT, and an exact-size lot wins. The decision itself, every booking method Beancount books is supported, is unchanged.
