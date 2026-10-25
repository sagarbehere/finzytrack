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
