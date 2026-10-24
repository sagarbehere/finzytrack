# ADR-0047: Product defaults for v0.4.0

This record lists the product defaults settled by common practice or by keeping v0.3 behaviour.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** product

## Context

The final review collected small product calls that practice settles. The owner accepted all of them, including one change to the demo refresh rule.

## Decision

(owner, 2026-10-24)
- **Dates** are shown as today (ISO dates in tables). There is no date-format setting in v0.4.0.
- **"Today"** uses the computer's time zone, as one setting the whole app follows (ADR-0023).
- **No telemetry and no crash-reporting service.** Crashes go into the local logs that "Report a problem" includes (ADR-0039).
- **Update check** is on, at most daily, asks before downloading, and can be switched off (ADR-0007).
- **English only.**
- **CSV copy/download** stays where it is today (tree tables). Wider CSV export and printing come after v0.4.0.
- **Backups are always on;** the setting controls only how many are kept. v0.4.0 has a "Show backups folder" action and a documented manual restore; an in-app restore screen comes later.
- **Prices update in the background** when a workspace opens and prices are old, with an off switch. The user docs say that fetching tells the price sources which symbols the user holds.
- **Effective yield on CDs** is kept, labelled "effective yield (per year)".
- **The assistant's warning cards are dropped;** each number shows where it came from instead.
- **Demo refresh:** the demo is replaced on update without asking; if it was edited, one previous copy is kept in a hidden folder and the notice mentions it (ADR-0022).

## Alternatives considered and rejected

- The deferred items (in-app restore screen, wider CSV export, printing, a date-format setting, i18n, fiscal year) are not rejected; they come after v0.4.0.

## Consequences

- None of these needs an owner question during the build; a change to any of them is an escalation.

## References

review:F §2 item 11, §4; ADR-0007, ADR-0022, ADR-0023, ADR-0030, ADR-0039.
