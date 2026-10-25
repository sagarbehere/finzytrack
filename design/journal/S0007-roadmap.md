# S0007: The roadmap on `next`

## Brief
- Goal: give the owner a roadmap to read in `design/`: the milestone order M0–M12 with G1 and G2, what each milestone delivers and requires, the dogfood rules, the sequencing, and a "where we are" section. Port it from review:27 (whose header says it becomes `design/specs/roadmap.md` at `next` setup, which did not happen), shortened, and corrected for decisions made after it: ADR-0053 (Vue), ADR-0054 (engine process), ADR-0056 (python-build-standalone; Linux AppImage + .deb), ADR-0057 (slice count). Owner request: "Go ahead and port the roadmap while I review the pilot process changes" (2026-10-25, in session).
- Implements: review:27 (the plan), ADR-0046, ADR-0043, ADR-0044, ADR-0014; the ADRs above for the corrections.
- Capabilities: none.
- Invariants touched: INV-23 (public repo: no home paths, no private details; review:27's paths to the owner's folders are left out), INV-19 (the file is hand-written, not generated; per-milestone capability lists are not copied, they stay in `capabilities.md`).
- Risk class / lane: Low / light (docs only; no behaviour).
- Acceptance: every decision the roadmap states matches its ADR or review:27 (the verifier checks a sample against the sources); nothing in it contradicts an accepted ADR; the privacy scan passes; the counts it gives match `capabilities.toml` and ADR-0057.
- Expected doc impact: new `design/specs/roadmap.md`; `design/specs/README.md` (written so far); `design/README.md` reading order; root `CLAUDE.md` pointer. No user docs.
- Open questions: none.
- Base SHA: edd213f.

## Decisions
- A short summary, not a copy: review:27 is about 20,000 words with per-milestone ID lists that the capability map and the backlog already hold; the roadmap points to them (agent).
- Authority: the ADRs decide and review:27 keeps the binding exits (ADR-0046); the roadmap only summarises and is fixed wherever it differs (agent, after the verifier's F3).

## Verifier
- Round 1, head da18b04: **PASS WITH FINDINGS** (Low kept). Attacks tried (7): every row, exit, dogfood rule and G1/G2 criterion against review:27 and ADR-0043/44/45/46/07/14/04/53/54/56/57; the 2026-10-25 ADR amendments; counts against `capabilities.toml`; "where we are" against the journals, the backlog and the pending-by-M0 invariants; the privacy scan and a grep for private details (none); T1 checks; the authority chain.
- Findings and resolution:
  - F1 Medium (ADR-0043): the inherited exit gate required every capability row built, with no exception for the four deferred capabilities, so M3, M8, M9 and M11 would be blocked. Fixed: "release-blocking rows"; deferred rows never hold an exit.
  - F2 Medium (INV-11, INV-12): "Left in M0" missed BL-0011, BL-0012 (both pending-by-M0 invariants, so M0 exit blockers), BL-0019, BL-0023, BL-0024. Fixed: a new item 3 lists them.
  - F3 Low (ADR-0046): the roadmap claimed precedence over review:27. Fixed: it summarises; ADRs decide; review:27 keeps the binding exits.
  - F4 Low (ADR-0008 amendment): "synchronous reconciliation" is now "before the next write"; the 5 s target includes the mirror update; M1b lists the write-cost measures and the bounded parse cache; §6 gains the S4 open point.
  - F5 Low: the slice count covers the rest of M0 through M12 (170–256 for M1–M12 alone); the ADR's label is left as recorded (append-only).
  - F6 Low: glossary and backlog header point to the roadmap; BL-0037 is `in-progress`; the `M1` capability rows split per ADR-0046.
  - F7 Low: cadences follow ADR-0044 (lens reviews at four points with spot audits, one whole-system review at M11); ADR-0054 is credited to the licence inventory, not S0001's spikes.
- The verifier's repro script exits 0 after the fixes. Two Medium findings: no further round required (PROCESS.md §3 step 5).

## Retro
- What the verifier found: two real gaps (a dropped exception from ADR-0043; an incomplete M0 list) and five wording issues. A cheaper gate: building the "left in M0" list by behaviour (every open M0 backlog row plus every pending-by-M0 invariant) instead of from memory, which is exactly ADR-0057's population rule; I did not apply it to a docs slice, and it missed five items.
- Process cost: drafting about 30 min; verifier about 4 min wall; fixes about 15 min.
