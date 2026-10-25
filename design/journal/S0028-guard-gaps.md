# S0028: Guard gaps found by the S0014 verifier (BL-0055)

## Brief
- Goal: close BL-0055's four gaps in the agent guard hooks (INV-24), each by refusing the class (EP-30) rather than modelling the shell: (1) flags smuggled into a gated git command through word splitting (`git commit -m x $(echo <skip-hooks flag>)`): refuse any unquoted `$`-expansion or backtick in the same simple command as git commit/push/add/clean/stash/tag/config/worktree (quoted forms stay allowed: they give one argument and cannot add a flag; heredoc bodies are data); (2) bash network redirects: refuse `/dev/tcp/` and `/dev/udp/` anywhere outside heredoc bodies, quoted or not (bash opens a quoted redirect target too); (3) writes to `.git/config` and `.git/hooks/` (file tools and parsed shell writes) are refused; `.githooks/` stays editable; (4) redirect targets of git commands are now checked like every other command's.
- Implements: BL-0055; INV-24; PROCESS.md §8 and §11 (a safety net, not a sandbox). BL-0042 (the parser's false positives) stays parked (owner).
- Capabilities: none. Invariants touched: INV-24 (its guard tightened).
- Risk class / lane: Medium (security tooling; no ledger or user data): one verifier round, a fresh one only after a High.
- Stated limits: commands run through `sh -c`, `eval` or an interpreter are not parsed.
- Acceptance tests: `scripts/tests/test_guards.py` `test_bl_0055_gaps` (14 refusals, 10 must-pass forms, path rules).
- Expected doc impact: `guards.py` comments; backlog BL-0055 done; roadmap §1; this journal.
- Open questions: none. Base SHA: 7188a882.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Ways around it, each tested: drop either new rule; one of the two device names only; backticks instead of `$(`; `$VAR`/`${VAR}` instead of a substitution; a quoted network redirect target; quoted text counted as unquoted (would refuse normal commit messages); heredoc bodies counted as commands (would refuse writing docs); any git subcommand gated (would refuse `git log $(...)`); push left ungated; `.git/config` or `.git/hooks` writable; `.githooks/` blocked; git redirects unchecked.
- Self-mutation: 12 tried, 11 killed; the survivor (heredoc bodies) got a must-pass case, then 12 of 12. Found while writing this journal: the network rule refused a heredoc that only mentioned the device path; it now skips heredoc bodies (must-pass case added).

## Verifier
- Round 1, head 9ddb134: PASS WITH FINDINGS (net tightening, no regression, tests fail on base). Attacks: 12 (base failure, regressions, quote blanking, fake and real heredocs, git word recognition, flags without `$`, network path in parts, `.git` spellings, unknown redirect operators, 22 daily forms, mutants, real shell semantics checked with printf).
- F1 Medium: an apostrophe inside double quotes and a heredoc opener's rest-of-line hid an unquoted expansion. Fixed: quotes blanked in one left-to-right pass; only the heredoc body is blanked.
- F2, F3, F5, F6 Medium: an escaped/quoted `git` word, nested or `&`-joined git, a network path spelled in parts, unknown redirect operators, brace and zsh glob expansions. Not fixed: each needs a deliberately built command and the regex parser cannot model the shell (the S0014 trap). Recorded as BL-0062, parked with BL-0042; BL-0055 says "closed for the forms it names".
- F4 Medium: `.git` rule on unresolved, case-sensitive paths; already fixed in S0026's round (5421f83), with a `.GIT/Config` test.
- F7 Low: tests for every gated subcommand and the continuation join added. F8 Low: `-m "user's fix" -m 'costs $5'` no longer refused; a quoted mention of the network device path is still refused (accepted in the brief).
- Fix-round mutants: 4 tried, 4 killed. No High: no further round (ADR-0058). Review loop stopped deliberately at one round (owner, session brief: "Tell me if a slice's review loop is running long").

## Retro
- The verifier's attacks on quoting reproduced S0014's lesson: a shell guard built on regexes has unbounded variants. Fixing the cheap ones and parking the class with BL-0042 was the right stop; the next step there is a real lexer, which the owner has parked.
- Incident: a fix-round edit left `guards.py` with a syntax error for one tool call. The hooks import it and fail closed, so a broken guard can block every tool. Edit live guard files in a copy, import-check, then swap.
- Process cost: about 50 min building; verifier 4 min; fixes about 25 min.
