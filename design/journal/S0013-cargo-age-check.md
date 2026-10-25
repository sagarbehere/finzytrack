# S0013: The Cargo 14-day check (BL-0052)

## Brief
- Goal: a T1 check that every crate in `shell/src-tauri/Cargo.lock` was published at least 14 days before the commit that first put that version in the lock, or carries EP-24's security exception with a journal line naming the advisory; the publish dates kept beside the lock and re-read from crates.io for new entries (T1) and for all entries (nightly).
- Implements: EP-24; ADR-0050 item 12 and its 2026-10-25 amendment (security exception; "a T1 check of the publish dates is BL-0052"); BL-0052.
- Capabilities: none. Invariants touched: none.
- Risk class / lane: Medium / full (tooling, no ledger or user data; one verifier round unless a High).
- How the check could be loosened or bypassed (written before coding; each has a test or a stated limit):
  1. a lock entry with no date row → fails; 2. a row with a false old date → new entries are re-read from crates.io in T1, all entries nightly; 3. an exception citing a missing journal, a journal without an advisory ID, the crate and the advisory on different lines, the crate name only inside another name, a file outside `design/journal/` → each fails; 4. adoption taken from a later commit (re-adding a version resets the clock) → the earliest commit counts; 5. git, path-less foreign registries or other sources (no publish date) → refused; 6. uncommitted lock changes → adopted "now"; 7. a shallow clone (no history) → fails closed; 8. a failed crates.io request during verification → fails closed; 9. 13 days 23 hours counted as 14 calendar days → 14 × 24 h, as pnpm and uv count; 10. an exception left on a release that is old enough → fails (it would hide a later misuse); 11. a stale row for a crate no longer locked → fails. Limit: committer dates can be set by hand; that needs intent, and the verifier reads commit dates against the journal.
- Acceptance tests: `scripts/tests/test_check_cargo_age.py`; the check passes on the committed lock (or reports a real violation, which is then fixed in the lock).
- Expected doc impact: ENGINEERING.md EP-24 enforcement line; backlog BL-0052 done; roadmap §1; ci.yml and nightly.yml; pre-push (offline part only); this journal.
- Open questions: none.
- Base SHA: c640611.

## Decisions
- "14 days" is 14 × 24 h, as pnpm's `minimumReleaseAge: 20160` and uv's `exclude-newer = "14 days"` count it (agent).
- **Escape found by the check (PROCESS.md §9):** S0010 resolved `Cargo.lock` to versions "published on or before 2026-10-11" (calendar days) and committed at 2026-10-25 10:42 UTC, so three crates were adopted between 6 and 8 hours short of 14 × 24 h: cc 1.8.0 (13.9 days), serde_json 1.0.154 (13.8), syn 3.0.8 (13.8). They became 14 days old the same afternoon, so the lock needs no change; adoption is history and cannot be redone. The three rows carry `escape:S0013-cargo-age-check.md`, a marker the check accepts only for versions adopted before it existed (`CHECK_SINCE`, 2026-10-25 12:00 UTC), so it cannot be used for anything new. The gate that should have caught it is this one, and it now exists (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks with must-not-pass tests: age (14 days passes, one minute short fails); exception (7 refusals: none, missing journal, no advisory, split lines, name inside or at the start of another name, file outside the journal, path escaping it); escape marker (refused after CHECK_SINCE, without a journal line, with a security journal); stale exception; missing and stale rows; other sources (git, other registry, sparse URL); adoption (earliest commit, removal and return, working tree = now); shallow clone; bad rows.
- Self-mutation: 10 tried, 10 killed (the trailing name boundary survived first and got the prefix-name test).
- Population: every crates.io entry of `Cargo.lock`, 406 of 407 packages (the other is the shell itself).
