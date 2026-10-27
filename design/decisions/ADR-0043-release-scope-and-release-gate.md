# ADR-0043: Release scope: exactly four capabilities are deferred; the release gate

This record decides what v0.4.0 may ship without, and the gate that must hold before release.

- **Status:** Accepted. Amended by ADR-0062 (2026-10-27): Share mode is cut, so it is no longer a release-blocking item.
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** release, scope

## Context

Without a mark, any one of about 65 new features could hold the release. The final review proposed a longer "may follow in 0.4.x" list. The owner's steering is the shortest path to a releasable v0.4.0.

## Decision

(owner, 2026-10-24)
- **Every `planned` capability row in `design/capabilities.md` is release-blocking except exactly four**, deferred to after v0.4.0 (rows with status `deferred` or `cut`, e.g. the twelve deferred-by-decision rows of review:24 §17, were never in v0.4.0's scope):
  1. the workspace export/import archive (CAP-WS-EXPORT, CAP-WS-IMPORT);
  2. broker formats beyond CAS;
  3. the beta update channel (CAP-SET-UPDATE-CHANNEL beyond stable);
  4. Finzytrack AI mode (CAP-AI-FINZYTRACK). Bring-your-own providers and local models stay.
- **Release-blocking despite being proposed for deferral:** Share mode, Bring-in, the command palette and recents, partial dashboard runs and SQL over steps, reconcile-from-assistant and attach-intent, settling pending card alerts from a statement.
- Deferred rows stay in the map with this ADR; they are built if time allows and never hold a milestone exit.
- **Release gate** (release = merge `next` into `main`):
  1. every mutator promoted (ADR-0014); property and corpus suites green at 54k;
  2. the real-ledger write corpus run locally on copies of the owner's ledgers, with the oracle comparison and the D2 equivalence suite;
  3. a **4-week dogfood window** on real data, including one month-end reconciliation and one broker sync, with synchronous D2 reconciliation and zero mismatches;
  4. the upgrade matrix from v0.2.2: adoption of fixtures and real copies, run twice and interrupted mid-run; the old install byte-identical and still starting afterwards;
  5. packaged builds on the three platforms (ADR-0007) pass `--smoke-test` and an acceptance subset;
  6. legal advice on the licence and the bundle obtained and applied (ADR-0042);
  7. architecture checks green with no exemptions; every full load through the overlay and the semaphore; the 54k memory test;
  8. a mutation-score floor on `app/ledger` and `app/domain` (the floor is set by a later ADR from M1 data);
  9. release notes listing every capability removed relative to v0.2.2 with its ADR, the keys Finzytrack no longer writes, the downgrade comment-loss warning, and an upgrade recommendation. User docs ship from the docs branch `v0.4`.

## Alternatives considered and rejected

- **Everything blocks.** Rejected: the biggest lever on time to release.
- **The final review's longer deferral list.** Rejected beyond the four items; the rest is in scope.

## Consequences

- INV-27's export round-trip test needs the export/import archive; until it exists, INV-27 is enforced by the raw-folder round trip and the layout tests.

## Amendment (2026-10-27): Share mode cut (ADR-0062)

Owner: "Drop the coexistence policy; I approve the needed changes to INV-26 and ADR-0020. The rest of the pending work you have identified should be done." (2026-10-27, in session).
- **Share mode** leaves the list "Release-blocking despite being proposed for deferral": ADR-0062 removes it, and its capability row CAP-LEGACY-COEXIST is `cut`. Copy adoption (CAP-LEGACY-ADOPT) stays release-blocking. The four deferred capabilities and the release gate are unchanged; the gate's "upgrade matrix from v0.2.2" is adoption by Copy.

## References

review:F §2 item 3; review:21 §3 step 8; review:27 §0.1; ADR-0007, ADR-0014, ADR-0020, ADR-0034; INV-27.
