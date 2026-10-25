# Which date do the SQL tables `account_balances` and `lots` show: today, or the end of the ledger?
- Asked: 2026-10-25 by implementer/S0021-mirror-surface
- blocks: nothing now (the two tables' rows in `design/specs/mirror/surface.toml` are marked `owner:` and the rest of S0021 lands); the M1a mirror-build slice cannot build these two tables until this is answered
- Rubric item: 2 (what a number means) and 8 (two plausible readings, costly to reverse once users rely on it)
- ADRs checked: ADR-0025 ("current balances and net worth exclude entries dated after today"; it speaks of balances the app shows, not of the SQL tables); ADR-0048 (`lots` is "served from Beancount's as-of booked inventory; the end-of-ledger computation stays deleted", but it does not say which date); ADR-0031 (lots as of a sale date, for the lot picker, not the SQL table); ADR-0015 and ADR-0016 (how the mirror is built and queried, not this). None says which date these two tables use, and the released user docs say both "Current balance" and "final balances computed from all postings".

## Context (plain language, a concrete example first)

Say today is 25 October. Your ledger already holds a rent payment of 20,000 INR dated 1 November (a scheduled, future-dated entry), and a mutual-fund purchase of 10 units dated 5 November.

- **v0.2.2** fills the SQL table `account_balances` from *every* entry, including future-dated ones. So `SELECT balance FROM account_balances WHERE account = 'Assets:Bank:Checking'` already shows the balance *after* the 1 November rent. The `lots` table likewise already shows the 10 units bought on 5 November.
- **v0.4's app screens** follow ADR-0025: a "current" balance leaves out entries dated after today, as a bank statement does. So the account screen would say the balance *before* the rent, while a dashboard reading `account_balances` with your own SQL would say the balance *after* it, unless these two tables also stop at today.

For users with no future-dated entries, both readings give identical numbers. The difference appears only for scheduled entries.

There is a cost to "today": the mirror is a file built when the ledger changes. If it counts only up to today, it must be rebuilt when the date changes (for example, the first time the app is used after midnight), or it would keep yesterday's "today". That rebuild is automatic and short at demo size; at 54k entries it is one full ledger load, done in the background while the previous version stays readable.

## Options
1. **Today, for both tables** (recommended). `account_balances` and `lots` leave out entries dated after the build date, in your time zone; the mirror records that date and is rebuilt when the date changes. Your own SQL then shows the same numbers as the app's screens. Anyone who wants the end-of-ledger figure can still sum `postings` without a date filter. The user docs say plainly "as of today; future-dated entries excluded".
2. **End of the ledger, for both tables**, as v0.2.2 did and as the docs' "computed from all postings" says. No date dependency, but SQL and screens disagree when future-dated entries exist, and for `lots` this contradicts ADR-0048's "the end-of-ledger computation stays deleted" (so it would need a new ADR).
3. **Mixed:** `account_balances` at the end of the ledger (docs wording), `lots` as of today (ADR-0048). Not recommended: two tables next to each other with two different dates is confusing.

## Recommendation
Option 1. It matches ADR-0025 and ADR-0048, keeps SQL and screens in agreement, and changes nothing for ledgers without future-dated entries. The rebuild-at-date-change is a cache refresh, not a new risk to your data.

## Answer (owner, verbatim, with date)
