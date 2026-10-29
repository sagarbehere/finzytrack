# S0054: process consolidation (periodic reflection 2026-10-28, proposals 1-12)

## Brief
- Goal: apply the reflection's twelve proposals (report in the owner's private archive, `m1a/reflection-2026-10-28/`, section 4): less process around designs, models and the gates' self-defence; shorter records; the bash guard narrowed to acts aimed at the owner's app. PROCESS.md gets shorter.
- Implements: owner approval of 2026-10-29 for the loosenings (1, 2, 3, 4, 7, 8, 10, 12); 5, 6, 9, 11 and 10's template part are agent-approved tightenings or scheduling. EP-19 narrowed (ENGINEERING.md §4: an EP loosening needs the owner first; covered by the quote).
- Capabilities: none (process).
- Invariants touched: INV-24 (its guard narrowed; the rule unchanged), INV-23 (privacy scan rules frozen; the rule unchanged).
- Risk class / lane: Medium / light (process docs and one guard hook; no ledger, finance or user data). The guard accepts and refuses, so it gets must-not-pass tests.
- Acceptance tests: `scripts/tests/test_guards.py` new cases: false positives of S0038/S0040/S0048 (a kill word with an app word in a heredoc body or in other text) pass; `pkill`/`killall` by any name, and any kill beside port 8001, are refused; the dropped anti-bypass forms (BL-0055 expansion, non-8001 `/dev/tcp`) become stated limits.
- Expected doc impact: PROCESS.md §3, §4, §5, §8, §9, §11 and the header; IMPROVEMENTS.md format and IMP-0045; ENGINEERING.md EP-19, EP-30; ARCHITECTURE.md §8.1, §8.3; backlog BL-0042, BL-0055, BL-0062, BL-0109, BL-0110, BL-0113; journal template; implementer.md; slice skill.
- Open questions: none (approvals given in session).

Owner approval (2026-10-29, in session): "Yes to Reflection proposals 1, 2, 3, 4, 7, 8, 10 and 12."

## Decisions
- **BL-0055 set to `removed`, not left `done`** (agent): S0054 takes out what S0028 built for it (the `$(...)`-next-to-git rule and the refusal of network redirects to any port), so `done` would no longer be true; the `.git/config`/`.git/hooks` write refusal it also asked for stays, and the row says so.
- **The guard keeps its whole-text refusals for the live data folder and for the owner's loopback address on port 8001** (agent): they are aimed at the owner's data and app, had no recorded false positive, and also catch an interpreter heredoc that would reach them. Only the kill-word rule (the source of the S0038, S0040, S0048 false positives) and the BL-0055 anti-bypass rules went. Heredoc bodies are no longer split into commands (BL-0042's false positive). Writing this journal hit the kept rule once (a heredoc naming the address); it went through the file tool instead, which is the rule working as meant.
- **Kill rule** (agent): pkill and killall pick by name, so any use is refused; `kill` is refused when the same command also runs a process lookup (`pgrep`, `pidof`, `ps`, `lsof`) or names port 8001, and on `-1` or an app word in its own arguments; a plain `kill <pid>` passes. Judged by the commands that run, not by words in the text.
- **A line continuation now joins a command before splitting** (agent; found while rewriting the round-2 test): a commit whose no-verify flag sits on a continued line was refused at the base only by the `$(...)` rule's joining; once that rule went, joining continuations in the splitter keeps it refused at no cost.
- **The engine-jobs spec §13 is not edited** (agent): it still lists liveness and outcome properties for the model; EP-19 and BL-0110 say only durability holds the code and the rest become tests. `formal/` untouched, as briefed.
- **The mapping table and a seeded mutant per property stay** (agent), in EP-19 rather than PROCESS.md: the one fidelity review needs the mapping, and the mutants are how vacuity is checked.
- **The escalation rubric is not changed** (agent): proposal 12 changes only when confirmations outside the rubric are sent (PROCESS.md §5); rubric questions still go at once through the inbox, and changing the rubric needs an ADR.
- **IMP-0037, 0038, 0039, 0042, 0044 marked superseded and IMP-0043 narrowed** (agent): their Status lines point to IMP-0045; the entries stay (append-only).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept or refuse, each with its must-not-pass test: `guards.bash_problem` kill rule → `test_kills_are_judged_by_what_runs_not_by_words` (refused: pkill/killall by any name, kill beside pgrep/pidof/ps/lsof or port 8001; allowed: the three slices' false positives, `kill $PID`, `kill %1`), plus `test_owner_app` and `test_bypasses_found_by_review` (unchanged, green); heredoc bodies as data → `test_a_heredoc_body_is_data` (body lines pass; the opener's line and any live-data mention are still refused); port-8001 redirects, `.git/config` writes, line continuations → `test_the_owners_port_and_the_git_gates`.
- Fails on base (97ef73dd), seen: `test_kills_are_judged_by_what_runs_not_by_words` (`pkill -f my_worker` allowed at base), `test_a_heredoc_body_is_data` (a body line removing a legacy folder refused at base), `test_the_owners_port_and_the_git_gates` (a continued no-verify commit allowed at base).
- Self-mutation: optional on tooling under proposal 7 (the guard keeps agents off real data but is not product code); not run.
- Population: the false positives come from the reflection's list (S0005, S0038, S0040, S0041, S0048). Their journals name two classes: a kill word beside an app word (S0038, S0040, S0048) and heredoc body lines parsed as commands (S0005, BL-0042); S0041's journal records no guard refusal. Both classes have a test.

## Retro
- Build 60 min, fix 0 min, 0 verifier rounds so far (the orchestrator decides), findings 0H/0M/0L. Cheaper gate: none for this slice; the guard's false positives sat in five retros before a reflection acted, so a false-positive count per check in the retro line would show them sooner.
