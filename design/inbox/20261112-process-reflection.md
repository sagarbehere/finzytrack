# Process reflection: is the slowness the price of safe software, or waste? Which process changes do you want?
- Asked: 2026-11-12 by the reflection agent (PROCESS.md §9), at the owner's question of 2026-11-12
- blocks: batch f of M1a (the owner holds it until this is decided)
- Rubric item: PROCESS.md changes and any loosening are the owner's
- ADRs checked: ADR-0058 (risk-scaled verification; its decision 5 re-measurement is in S0106), ADR-0068 D6 (bytes it protects; options note before a second fix round), ADR-0044 (lanes and cadences), ADR-0057 (pilot); IMP-0044, IMP-0045, IMP-0049, IMP-0051, IMP-0052. They set today's rules. None of them measured S0096-S0106 or split findings by what they protect.

## Context (plain language, a concrete example first)

Your question, 2026-11-12: "It's been agonizingly slow and we need to determine if this is merely the price for 'safe and correct' software or if we've been wasting time and tokens unnecessarily."

A concrete example. S0100 fixed eight small write-path leftovers. Its red team then found that our bytecode step could re-create the app's own data folder if another program deleted it while the app runs. That finding led to a whole new slice, S0102 (about 1.07 million tokens, 2 review rounds). In S0102's second round the reviewer found two more cases of the same setup. You then closed them by policy: "unsupported". The setup is rare, and no ledger byte was ever lost in any attack. One question at S0100's brief ("can a policy say the app's own folder must not be replaced while the app runs?") would most likely have ended this family before S0102.

How I measured:
- I read every journal S0000-S0106 (Brief, Verifier, Decisions, Retro). The per-slice table is Appendix A.
- For S0096-S0106 I classified every finding (122 in total) into five classes:
  - (a) loses, tears or overwrites a byte, or gives a false outcome ("saved", "not saved", "unchanged", "created"), in product code;
  - (b) a wrong number or finance result;
  - (c) a gap in our own checks or tests (a gate, scanner or walker that misses a shape);
  - (d) docs or wording;
  - (e) other product behaviour that loses nothing and tells nothing false (a false refusal, a hang, a wrong error kind, an exotic plugin edge).
  Details are in Appendix B.
- Tokens are the orchestrator's records of subagent tokens, not the orchestrator's own. Build and fix minutes are the implementers' own estimates from the retros.

Limits of this data: the token figures cover only S0096-S0106. "Build" time includes in-build ceremony (inventories, self-mutation, the journal) that I could not separate. Earlier slices are classified per slice, not per finding.

## Findings

### 1. Where the tokens went (S0096-S0106, 11 slices, 8.35M subagent tokens)

| | Tokens | Share |
|---|---|---|
| Implementers (build and fix rounds) | 4.63M | 55% |
| of which fixing review findings (by the retros' fix/build time, 808 of 2,224 min) | about 1.68M | 20% |
| Verifier rounds (18 runs) | 2.42M | 29% |
| Red-team runs (9 runs: data-safety and finance) | 1.30M | 16% |
| **Review plus fixing** | **about 5.4M** | **about 65%** |
| Building | about 2.95M | about 35% |

A review run costs 100-180k tokens whatever the size of the diff (S0096's small diff: 112k). Each pre-push run takes 320-420 s. Fix time is 0.57 of build time here; ADR-0058's re-measurement (S0106) gives 0.77 for all of M1a and 0.57 for M0. Process code is 0.76 of product code (22.1k against 29.0k lines). On 2026-10-28 it was 2.5 times product code. So product code has grown much faster than the process since the last reflection.

### 2. What the reviews found (S0096-S0106, 122 findings)

| Class | Findings | Share of findings | Share of review tokens (each run's tokens split evenly over its findings) |
|---|---|---|---|
| (a) bytes or false outcome | 9 | 7% | 8% |
| (b) wrong number | 10 | 8% | 8% |
| (c) gap in our own checks or tests | 39 | 32% | 48% (with (d)) |
| (d) docs or wording | 28 | 23% | |
| (e) other product edge | 36 | 30% | 31% |

- **About a fifth of review effort found an (a) or (b) problem.** Half went to our own checks and to wording.
- **7 of the 9 (a) findings are one rare setup:** another program removing or replacing the app's own data folder while the app runs (S0100, S0102). You closed two of them by policy in S0102's second round. Only two (a) findings sit outside that family: S0100's false "unchanged" and S0101's flush race. The verifier found both of them too.
- **All 10 (b) findings came from S0098**, a warning feature for numbers longer than 28 digits. One was High: a plugin's precision setting leaking into later loads. It was older than the slice and not reachable today. Six of the ten became backlog rows.
- **Red teams found few (a)/(b) problems that the verifier missed:** two (a) findings (both in the app-folder family) and six (b) findings (four went to the backlog). Two red-team runs found nothing in (a) or (b): S0098's data-safety run (158k) and S0099's run (167k).

### 3. Is the ceremony matched to the risk?

| Slice kind (S0096-S0106) | Slices | Tokens | Review runs | (a)/(b) found |
|---|---|---|---|---|
| Data path, hardening existing write paths (S0100, S0101, S0102) | 3 | 3.14M (38%) | 11 | 9 (a), 7 of them in one rare setup |
| Finance meaning (S0098) | 1 | 1.52M (18%) | 6 | 10 (b), 6 to the backlog |
| Gate input, plugin fingerprint (S0099) | 1 | 0.90M (11%) | 4 | 0 |
| Tooling, gates, measurement, docs (S0096, S0097, S0103-S0106) | 6 | 2.80M (33%) | 6 | 0 by construction; 33 (c) and 9 (d) |

- **Tooling slices take a third of the tokens.** Their review findings are almost all of one kind: the verifier plants more shapes than the author thought of, and the check misses some (S0103: 12 of 12 planted blocking shapes passed the walker; S0104: 34 of 57 planted shapes passed the counter). Each miss is then fixed and planted. This is the same pattern that §9 froze for the gates' self-defence (a new check only after a real escape). Here it still runs on the coverage of every new check.
- **Red teams ran outside the letter of PROCESS.md §4.** §4 sends the data-safety red team to slices that add a new way to write, or a new write outcome. S0098 (no write), S0101 and S0102 (hardening of existing paths) and S0099 (a gate input) got one anyway: 0.70M tokens, with one unique (a) finding (S0102 red team F1, Medium, app-folder family).
- **The red team on genuinely new write paths and new finance meanings earns its cost.** Across M1a it found Highs that the verifier did not: S0060 (hard links), S0061, S0071 (exFAT kept copy replaced), S0080 (7 Highs: deleting other programs' files, a false "created"), S0081 (a false "loads the same"), S0086 (tickers such as AMD and BND counted as cash; a USD bank balance vanishing), S0089 (a twin FAT stick written by recovery), S0095 (a false "loads the same"). This is the price of safe software, and I propose keeping all of it.

### 4. Where loops ran long, and the question that would have ended them

The old deep loops are gone. S0061 had 8+8 rounds. Since IMP-0051/0052, no slice has had more than 3 rounds, and the options note ended each long loop at round 2 (S0098, S0099, S0102, S0105). What is left is **breadth**: almost every slice pays two to four review runs, and **rare-setup families** get whole new slices.

| Loop | Rounds or cost | Ended by | The question at the brief |
|---|---|---|---|
| App folder replaced while the app runs (S0100 r1 → S0100 r2 → S0102 r1, r2) | 4 rounds over 2 slices; S0102 alone 1.07M | Policy, S0102 r2 ("Policy + fix F3") | "Can a policy say the app's own folder must not be removed or replaced while the app runs?" |
| Guard reads quoted messages as data, BL-0054 (S0105 B r1, r2) | 2 Highs, then revert | Revert ("Revert BL-0054") | "Has modelling bash's quoting failed before?" Yes: S0014, S0028, S0033, S0054. |
| Plugin import tricks in the fingerprint (S0099 r1, r2) | 4 runs, 0.90M, 0 (a)/(b) | Owner narrowed it; the rest became stated limits | "What does a missed plugin edit cost? Under ADR-0067 the preview's error list is advice. Which import tricks do we state as a limit?" |
| 28-digit warning precision (S0098 r1-r3) | 6 runs, 1.52M | Owner chose which to fix; 6 to the backlog | "What does a missed or false warning cost? The user then sees Beancount's own number, as in bean-check and Fava. Prefer a simple warning that may fire too often." |
| Earlier: S0005 symlinks, S0016 privacy, S0033 hook pins, S0054 app guard, S0061 ACLs, S0065 no-swap, S0066/67 R4 | 3-8 rounds each | Policy, or removal by ADR-0067/0068 | The same two questions every time |

The recommendations matter most. You chose the "(Recommended)" option in every one of about 40 choices I found in S0080-S0106. Two of those recommendations extended a loop instead of ending it: "BL-0244 slice in M1a" (which became S0102) and "Exact shapes only" for BL-0054 (reverted two rounds later).

### 5. Gates, rules and rituals: cost against catch

| Item | Cost | Catch | Verdict |
|---|---|---|---|
| Verifier on every slice | 100-180k per run | Found every (a) of S0096-S0106 except one | Keep |
| Data-safety red team on new write paths | 100-175k per run | The M1a Highs above | Keep |
| Data-safety red team on hardening slices or slices without a write (S0098, S0099, S0101, S0102) | 0.70M | 1 unique (a), Medium, in a rare-setup family | Drop unless the verifier finds an (a) (P3) |
| Finance red team | 0.30M in S0098 | 6 unique (b), 1 High | Keep |
| Verifier shape hunts on tooling, and their fix rounds | 0.88M of review runs and about 0.56M of implementer fix tokens in 6 slices | 0 (a)/(b); 33 (c) | Narrow (P2) |
| Second review round after a fix | 0.22M per round on data slices | Real regressions from the fix itself (S0099 R2-F1: plugins reading package data broke; S0102 R2-F3: our bytecode step wrote .pyc files into the Python install) | Keep, on the fix range only |
| Owner questions about milestone moves (S0097, S0106 twice, the quote-format commit 8fb3002e) | S0097 parked one evening; one extra commit | Each one answered "(Recommended)" | Rule-based (P6) |
| Journal ceremony | Recent journals are 4-5k words (M0: about 2k); ADR-0058 asked for one line per finding | Under 2% of tokens | Tidy (P5), not a big saving |
| Process-checking scripts (invariants, capabilities, retired terms, deferrals, push gate, timer, generated; about 3,300 lines plus their meta-tests) | Not measured per check; repeated small false positives (check_invariants in S0044, S0098; the deferral check's quote format) | Few true catches recorded | Measure, then decide at G1 (P9) |
| Bash guard | Rewording in 23 slices | Guards INV-24 | Keep (a safety guarantee) |

### 6. What a leaner process would have missed (the honest trade-off)

If you take P1-P4 as proposed, these past (a)/(b) findings would have become stated limits or later findings instead of fixes:
- S0102 red team F1 (Medium): a replacement app folder gets the journal and backups, and the save answers "saved". It also covers S0102 verifier F1 and red team F6, and S0100 red team F3 and the shared r2 Medium. All are in the app-folder family. With P1 they would be a stated limit ("unsupported while the app runs"), like the two you already closed by policy.
- S0089 red team r1 H1 (twin FAT sticks, recovery writing on the wrong stick): S0089 hardened recovery and did not add a write path. Under P3 the red team would not have run in round 1. The verifier found a High of the same class in round 2, so the class would most likely still have been caught, one round later.
- S0099 R2-F1 (a fix that broke plugins reading their package data): this is (e), not (a)/(b), and it is kept under every proposal, because re-review after a fix stays.
- S0098 finance F1 High stays caught: the finance red team stays.

The cheaper process would have caught every other (a)/(b) finding of M1a, because the verifier stays on every slice and red teams stay on new write paths and new finance meanings.

### 7. Slice size and batching

- A slice has a high fixed cost: S0097 wrote only a brief and evidence, and still cost 74k tokens. Each review run costs at least about 110k.
- **Batching homogeneous rows helped:** S0100 did 8 rows for 1.11M (about 140k per row); S0103 did 4 rows for 0.44M (about 110k per row). The single-row hardening slices cost 0.95M (S0101) and 1.07M (S0102).
- **Batching hurt once:** S0105 part B put a loosening of the guard (BL-0054) with tooling rows. Both Highs of part B came from BL-0054, and it was reverted. S0105 split into parts A and B, which kept part A clean.
- Slice size is about right. Batch more of the low-risk rows, and keep loosenings and mechanisms with a fix history in slices of their own.

## Options (pick any set; P1-P9 ranked by expected saving per unit of risk)

### Set A: end rare-setup loops at the brief
**P1. The policy question and the history question at the brief.** Tightening (it asks more), but a PROCESS.md change, so yours to approve.
- PROCESS.md §3 step 2, add: "**Every brief that adds or hardens a mechanism answers, in one line each: 'Which real setup needs this, and can a policy forbid the rest?' and 'Has this mechanism or its family needed two or more fix rounds before (journal IDs)?'. When the setup is rare or the family has that history, the policy option goes to the owner before coding. At the first class (a) finding in a rare setup, the orchestrator offers the policy before the first fix round, not the second.**"
- Hypothesis: most long loops are rare setups that a policy removes; asking first removes the loop.
- Metric: review runs per slice on data slices; slices created by a review finding.
- Saving on S0096-S0106: about 0.75M (most of S0102) plus about 0.15M (S0099 r2).
- Risk: the app-folder family findings above become stated limits.

**P7. Better recommendations in options notes.** Tightening; agent-approved.
- Rule (escalation README template and the slice skill): "Each options note lists the cheapest option first and, for the recommended option, the mechanism's review history (slices, rounds, Highs). A note never recommends a new slice for a setup that a policy could forbid without offering that policy first."
- Hypothesis: you take the recommendation almost always, so its quality decides the outcome.
- Metric: recommended options later reverted or extended (two in S0096-S0106).
- Saving: about 0.35M (S0105 part B's BL-0054 rounds and fixes); it also overlaps P1 on S0102.
- Risk: none for (a)/(b).

### Set B: match ceremony to risk (loosenings: yours)
**P2. Stop rule for tooling, gates, measurement and docs.** Loosening.
- PROCESS.md §3 step 7, add: "**Tooling, gates, measurement and docs** (no ledger or user bytes, no finance number): one verifier round. The fix round fixes only (1) a shape that the check's own must-not-pass list or docstring promises to refuse, (2) a false statement of fact, (3) a finding in a guard of real data or privacy (INV-23, INV-24). Every other shape a check misses becomes one line in its stated limits, or a backlog row when a real file in the repository has that shape."
- Hypothesis: shape hunts on new checks cost more than they catch before any real escape.
- Metric: fix minutes per tooling slice; real escapes past a check with a stated limit.
- Saving: about 0.3M tokens and about 2 hours of fix time.
- Risk: no past (a)/(b). A check may miss a shape in future product code (for example S0104's 34 planted shapes). The verifier's [EP-23] and [EP-25] questions still look at real code.

**P3. Data-safety red team only on new write paths.** Loosening.
- PROCESS.md §4, replace the red-team cadence sentence's first part with: "A data-safety red team runs only on a slice that adds a new way to write, delete or move ledger or user data, or a new outcome text for a write. A slice that hardens an existing write path (a new check, a sync, an identity test) gets the verifier only; the red team joins when the verifier reports a class (a) finding. A finance red team runs when a number's meaning or an input changes."
- Hypothesis: on hardening slices the verifier finds what the red team finds.
- Metric: unique (a) findings per red-team run; (a) findings found after landing.
- Saving: about 0.53M (S0098's data-safety run, S0099, S0101; S0102 overlaps P1).
- Risk: S0102 red team F1 (Medium) and, in earlier history, S0089 r1 H1, which the verifier found one round later.

### Set C: consolidate
**P4. One review-budget table instead of scattered conditionals (the consolidation §9 asks for).** Mixed. It carries P2 and P3 only if you choose them.
- Replace §3 step 5's second sentence, the three bold rules of step 7 and §4's red-team cadence sentence with:

  | Slice kind | Reviews | Fresh round on the fix range | Then |
  |---|---|---|---|
  | New write path or write outcome | verifier + data-safety red team | after an (a) fix | only (a)/(b) findings are fixed; the rest become rows; a second fix round on one mechanism needs the owner's options note |
  | New finance meaning or input | verifier + finance red team | after a (b) fix | same |
  | Hardening an existing write path | verifier (red team on a verifier (a)) | after an (a) fix | same |
  | Tooling, gates, measurement | verifier | only when a real-data or privacy guard changed | P2's rule |
  | Docs only | verifier on facts | none | none |

- Hypothesis: today's rule set (about a dozen conditions added by five past fixes) is misapplied: S0098's data-safety run is one example. One table is easier to follow and to check.
- Metric: PROCESS.md §3-§4 words; review runs outside the table found by the verifier or the next reflection.
- Saving: indirect; it prevents misapplications such as the 0.70M of out-of-rule red teams.
- Risk: as for P2 and P3, if included.

**P5. A class letter on every finding; a short Verifier section.** Tightening; agent-approved.
- Rule (verifier prompt and journal template): "Every finding carries a class: (a) bytes or a false outcome, (b) a wrong number, (c) a gap in our own checks or tests, (d) docs or wording, (e) other. The journal's Verifier section is one table row per finding: ID, severity, class, disposition."
- Hypothesis: the share of (a)/(b) per run becomes visible at each slice, and the next reflection becomes a count instead of a reading.
- Metric: (a)/(b) per review run, per slice kind.
- Saving: small (journal words, owner reading time).
- Risk: none.

**P9. Measure the process checks, then retire the idle ones at G1.** Consolidation; measuring is agent work, retiring is yours.
- Rule: "At G1, each process check (invariants, capabilities, retired terms, deferrals, push gate, gate timer, generated) lists its true catches and its false positives since M1a; a check with no true catch and repeated false positives is offered to the owner for retirement."
- Hypothesis: part of the roughly 3,300 lines of process-checking code costs more than it catches.
- Metric: catches and false positives per check.
- Saving: unknown until measured.
- Risk: none for (a)/(b); it never touches the data guards (privacy scan, staged-file guard, INV-24 guards).

### Set D: fewer owner interruptions
**P6. Milestone moves by rule.** Loosening of the deferral check.
- PROCESS.md §5, add: "A backlog row may move to the milestone of its first consumer (the code that will call it) without the owner's words; the row names the consumer, and the move is listed in the daily batch. The deferral check accepts 'moves with <ID>' as its citation."
- Hypothesis: these questions always get the recommended answer and only cost waiting time.
- Metric: owner questions per slice; parked hours.
- Saving: 3 of 13 owner touches in S0096-S0106; S0097's parked evening; one commit.
- Risk: none for (a)/(b).

**P8. Batch rows of one kind; isolate risky rows.** Tightening; agent-approved.
- Rule (slice skill): "Batch backlog rows of one lane and one kind into one slice. Never put a loosening, or a mechanism that needed two fix rounds before, into a batch."
- Hypothesis: batches cut the fixed cost per row; one risky row holds the whole batch's review loop.
- Metric: tokens per backlog row closed.
- Saving: at today's rates, about 0.1-0.15M per row on low-risk rows done in batches.
- Risk: none for (a)/(b).

## Recommendation

Take Set A (P1, P7) and P4 with P2 and P3 included, plus P5. Let agents apply P8 and the measuring part of P9. Decide P6 as you like: it saves waiting more than tokens.

Expected effect on a period like S0096-S0106: about 1.7-2.0M fewer subagent tokens (20-25%) and a similar share of fix time. The safety core is unchanged: the verifier on every slice, and red teams on new write paths and new finance meanings, which is where the M1a Highs came from. The price is a handful of low-likelihood findings in rare setups. They become stated limits, as you already chose for two of them in S0102.

The short answer to your question: **the core reviews are the price of safe software, and they are paying. About a fifth to a quarter of the effort was waste, and it came from three habits: polishing our own checks, red teams where nothing new is written, and rare-setup loops that a policy question would have ended.** Part of the slowness is also the roadmap and not the process: M1a is foundation work, and 0 of the 310 release-blocking capability rows are built yet. That is outside this note.

## Answer (owner, verbatim, with date)


---

## Appendix A: per-slice table, S0000-S0106

Kind: L = ledger or user bytes (write path, recovery); F = finance numbers; P = product infrastructure with no write (engine, API, reads); G = tooling, gates or tests only; D = docs, specs or design; M = formal model. Rounds: V = verifier rounds, RT = red-team runs. Findings are H/M/L per round where the journal gives them. Times are build/fix minutes from the retro ("-" where none is stated). End: how the loop ended (fixed; policy = an owner policy or a removed setup; revert; BL = to the backlog; owner = an owner decision in the loop).

### M0 (S0000-S0034)
| Slice | Class/lane | Kind | Rounds | Findings | Build/fix | End |
|---|---|---|---|---|---|---|
| S0000 setup | High/full | G | 1 V | 7H/37M | - | fixed |
| S0001 spikes | Med | G | 0 | - | - | - |
| S0002 oracle harness | Med→High | G (evidence for writes) | 2 V | r1 5H/3M/2L; r2 0/4/2 | 80/45 | fixed |
| S0003 legacy API tests | Med | G | 1 V | 0/2/3 | 50/25 | fixed |
| S0004 intent table | High/full | D (what is written) | 2 V | r1 0/8/2; r2 0/6/2 | - | fixed; 1 to owner |
| S0005 pilot | Med | G | 4 V | 5, 4, 2 (2H), 1 | 40/50 | fixed (links refused) |
| S0006 backend skeleton | Med | G | 2 V | r1 0/4/4; r2 0/1/2 | 60/60 | fixed |
| S0007 roadmap | Low | D | 1 V | 0/2/5 | 30/15 | fixed |
| S0008 process trim | Low | D | 1 V | 0/1/3 | 30/10 | fixed |
| S0009 frontend skeleton | Med | G | 1 V | 0/2/1 | 50/20 | fixed |
| S0010 shell skeleton | Med | G | 1 V | 0/3/3 | 75/35 | fixed; 1 owner |
| S0011 capability index | Low | G | 1 V | 0/0/3 | 15/10 | fixed |
| S0012 packaged builds | Med | G | 1 V | 0/0/6 | 90/20 | fixed |
| S0013 cargo age check | Med | G | 1 V | 0/3/2 | 45/25 | fixed |
| S0014 guard parser | High | G (INV-24 guard) | 2 V (r2 incomplete) | r1 5H/3M/1L; r2 2H/1M/1L | - | parked |
| S0015 cut rows | Low | D | 1 V | 0/1/3 | 25/10 | owner |
| S0016 detect-secrets | Med→High | G (privacy) | 5 V | 2/4/3, 0/4/2, 0/2/1, 0/2/2, 0/0/3 | about 150 total | fixed (conservative rule) |
| S0017 globals registry | Med | G | 1 V | 0/3/4 | 40/25 | fixed |
| S0018 frontend boundaries | Med | G | 2 V | 1/2/4, 0/1/2 | 75/40 | fixed |
| S0019 close BL-0019-0023 | Low | D | 1 V | 0/2/2 | - | fixed |
| S0020 write corpus | Med | G | 1 V | 0/1/4 | 75/15 | fixed |
| S0021 mirror surface | Med | D | 1 V | 0/2/6 | - | 3 owner |
| S0022 INV-21 goldens | High | G (ids) | 1 V | 0/2/3 | - | fixed |
| S0023 milestone exit check | Low-Med | G | 1 V | 0/3/1 | 40/20 | fixed |
| S0024 Beancount vectors | Med/spec | F | 3 V | 0/3/3, 0/3/1, 0/1/0 | - | fixed |
| S0025 stub ban | Low | G | 1 V | 0/1/3 | 20/10 | fixed |
| S0026 fixture gate gaps | Med | G (privacy) | 1 V | 0/2/4 | 45/25 | fixed |
| S0027 demo generator | Med | G | 1 V | 0/0/6 | - | fixed |
| S0028 guard gaps | Med | G | 1 V | 0/6/2 (4 not fixed) | 50/25 | stopped at 1 round |
| S0029 P1 fixme | Med | G | 1 V | 0/6/3 | - | fixed |
| S0030 acceptance harness | High | G (INV-24) | 1 V | 0/2/8 | - | fixed; 1 owner |
| S0031 gate hardening | High-adj | G | 2 V | 0/4/6, 0/1/6 | 180/150 | fixed |
| S0032 seeded misses | Med | G | 1 V | 0/1/3 | - | fixed |
| S0033 seeded misses 2 | Med | G | 3 V | 1/3/1, 1/2/1, 0/1/2 | -/70 | fixed (byte pin) |
| S0034 gate time | Med | G | 2 V | 0/2/4, 0/1/6 | - | fixed |

### M1a (S0035-S0106)
| Slice | Class/lane | Kind | Rounds | Findings | Build/fix | End |
|---|---|---|---|---|---|---|
| S0035 engine process | Med | P | 2 V | 0/3/1, 0/1/6 | - | fixed |
| S0036 CI on Linux | Med | G | 1 V | 0/2/2 | - | fixed |
| S0037 beanquery | Med | G (licence) | 1 V | 0/1/2 | - | fixed; owner |
| S0038 engine in product | Med | P | 1 V | 0/0/3 | - | fixed |
| S0039 structured log | High/spec | P (privacy) | 1 V | 0/0/3 | - | fixed |
| S0040 engine statechart | Med | P | 1 V | 0/0/3 | - | fixed |
| S0041 failure modes | Med | D/G | 1 V | 0/2/3 | - | fixed |
| S0042 request deadline | Med | P | 1 V | 0/1/3 | - | fixed |
| S0043 review follow-ups | Med | G | 1 V | 0/1/2 | - | fixed |
| S0044 single instance | High care | D | 1 V | 0/2/3 | - | fixed |
| S0045 one workspace | High care | D | 3 V | 1/4/5, 1/8/3, 0/1/3 | - | policy (territory rule, owner) |
| S0046 API skeleton | Med | P | 1 V | 0/2/6 | - | fixed |
| S0047 convergence | Med | G/D | 1 V | 0/3/5 | - | owner |
| (engine-jobs design, 10-27/28) | - | D/M | 7 spec + 5 model reviews | - | - | policy (ADR-0064), IMP-0044 |
| S0048 serve API | Med | P | 1 V | 0/2/1 | - | fixed |
| S0049 Beancount port | High/spec | F/P | 1 V | 0/4/4 | -/30 | fixed; 1 owner |
| S0050 mirror build | High/spec | F | 2 V | 1/2/2, 0/1/1 | 30/40 | fixed ((b) High: abs() rounding) |
| S0051 workspace format | High | L (gate input) | 2 V | 1/3/3, 0/1/2 | - | fixed ((a) High: document files missing from fingerprint) |
| S0052 query runner | High/spec | P (security) | 2 V | 1/4/4, 0/1/3 | 90/- | fixed |
| S0053 engine queue | High/full | P | 2 V | M only | - | fixed |
| S0054 consolidation | Med | G (guard) | 3 V | 1/5/4, 1/4/4, 0/1/1 | 60/140 | policy (app guard removed) |
| S0055 Vue docs | Low | D | 0 | - | 75/0 | - |
| S0056 owner decisions | Med | D | 1 V | 1/2/2 | 45/50 | owner |
| S0057 engine client | High/full | P (commit point) | 3 V | 1/2/5, 0/3/2, 0/1/2 | 300/210 | fixed ((a) High: Cancel past the commit point) |
| S0058 scheduler | High/full | P | 2 V | 1/2/5, 0/0/3 | 330/150 | fixed |
| S0059 conformance traces | Low | G | 1 V | 0/2/3 | 210/30 | fixed |
| S0060 durable order | High/full | L | 2 V + 2 RT | V 0/3/2, RT 1/2/10; V 0/4/4, RT 0/4/4 | 37/63 | owner (hard links refused) |
| S0061 restore | High/full | L | 8 V + 8 RT | about 9 Highs over r1-r7 | 77/700 | 4 owner policies |
| S0062 recover-all | High/full | L | 1 V + 1 RT | 0/1/1, 0/0/4 | 60/53 | fixed |
| S0063 save job | High/full | L | 2 V + 2 RT | V 1/2/1, RT 0/1/4; 0/0/2, 0/0/3 | 92/70 | fixed |
| S0064 save conformance | Med | G | 1 V | 0/3/2 | 90/30 | fixed |
| S0065 no-swap fallback | High/full | L | 3 V + 3 RT | r1 2/1/4 + 1/1/3; r2 0/1/3 + 1/1/1; r3 0/2/2 + 0/1/3 | 51/160 | removed by ADR-0068 D2 |
| S0066 R4 | High/full | L (advisory) | 2 V + 2 RT | r1 1/5/4 + 2/3/1; r2 0/1/2 + 0/2/2 | 71/72 | removed by ADR-0068 D1 |
| S0067 R4 follow-ups | High/full | L (advisory) | 2 V + 2 RT | 1H r1, 1H r2 | - | removed (ADR-0067/0068) |
| S0068 integrity blocks | High/full | L | 1 V + 1 RT | 0/3/7 | - | fixed |
| S0069 step back | High/full | D/L | 1 V + 1 RT | 0/4/6 | 180/60 | fixed |
| S0070 save without load | High/full | L | 1 V + 1 RT | 0/3/3 | - | fixed |
| S0071 best-effort drives | High/full | L | 2 V + 2 RT | 1/4/10 total | - | fixed |
| S0072 model durability | Med | M | 1 V | 0/3/3 | 53/24 | fixed |
| S0073 spec rules | High (part) | M/P | A: 1 V + 1 RT; B: 1 V | A 0/3/1; B 0/3/4 | 46/11 | 2 owner |
| S0074 history trim | High/full | L (deletes) | 1 V + 1 RT | 0/2/5, 0/1/4 | 29/15 | fixed |
| S0075 drain listing | High/full | P | 1 V + 1 RT | 0/2/1, 0/0/1 | 36/20 | fixed |
| S0076 quit drain | Med | P | 1 V | 0/2/1 | 45/10 | fixed |
| S0077 linked journal | High/full | L | 1 V + 1 RT | 0/0/3, 0/1/2 | 50/20 | fixed |
| S0078 start storm flake | Med | P | 1 V | 0/0/2 | 60/30 | fixed |
| S0079 spec structure | Low | D | 1 V | 0/1/2 | 110/60 | fixed |
| S0080 create | High/full | L (new write) | 4 rounds | V 0/2/5, 0/2/3, 0/1/3; RT 2/2/2, 2/1/2, 3/1/1 | 150/210 | owner ("Fix, then land") |
| S0081 copy | High/full | L (new write) | 3 rounds | r1 V 0/2/4, RT 2/2/2; r2 V 1/1/3, RT 0/1/7; r3 V 0/2/3 | 180/230 | 4 owner |
| S0082 create/copy cleanups | High/full | L | 1 V | 0/0/3 | 75/20 | fixed |
| S0083 roadmap | Low | D | 1 V | 0/1/3 | 15/10 | fixed |
| S0084 create/copy conformance | Med | G | 1 V | 0/0/1 | 37/2 | fixed |
| S0085 publish model | High | M | design + fidelity review | 0/2/3, 0/1/3 | 30/25 | 3 owner |
| S0086 commodities rows | High/spec | F | 4 V + 3 RT | RT 2/3/5, 1/1/2, 0/2/3; V 0/1/2 each round | 20/45 | 5 owner ((b) Highs: tickers as cash) |
| S0087 mirror publish | High/full | L | 2 V + 1 RT | V 0/1/4, RT 0/3/3; V 0/2/2 | 55/23 | BL |
| S0088 open and reads | High/full | L/P | 2 V + 2 RT | V 0/1/5, RT 1/1/1; V 0/0/1, RT 1 | 26/9 | BL; 3 owner |
| S0089 recovery volume id | High/full | L | 3 V + 3 RT | r1 V 0/1/3, RT 1/1/1; r2 V 1/1/2, RT 2/1/2; r3 V 0/1/3, RT 2 (within limit)/1 | 32/50 | 5 owner (refusals) |
| S0090 drive identity cleanups | High/full | L | 1 V + 1 RT | 0/0/1, 0/0/3 | 25/2 | fixed |
| S0091 model replays | Med | G | 1 V | 0/2/3 | 45/- | fixed |
| S0092 money and dates | Med | G | 1 V | 0/4/4 | 18/7 | 2 owner |
| S0093 cache registry | Med | G | 1 V | 0/2/3 | 17/13 | 2 owner |
| S0094 parity corpus | Med | G | 1 V | 0/2/2 | 150/35 | fixed |
| S0095 INV-21 through the build | Med→High | L (loads-the-same gate) | 2 V + 2 RT | V 0/2/2, RT 1H; V 0/0/3, RT 4H out of scope | 37/12 | 4 owner |
| S0096 mirror checksums | Med/light | G | 1 V | 0/1/4 (+1 doc) | 40/30 | fixed |
| S0097 mirror indexes | Low | D | 0 | - | 3/0 | owner: move to M1b |
| S0098 numbers > 28 digits | High/spec | F | 3 V + 3 RT | r1 1/7/8; r2 0/4/8; r3 0/0/3 | 38/63 | owner options note r2; 6 BL |
| S0099 plugin fingerprint | High/full | L (gate input) | 2 V + 1 RT | r1 0/4/4 + 1 liveness; r2 0/2/4 | 300/180 | owner ("Narrow the refusal"); limits |
| S0100 write-path leftovers (8 rows) | High/full | L | 2 V + 2 RT | r1 1/1/8; r2 0/1/2 | 110/40 | owner: new slice S0102 |
| S0101 resolve syncs file data | High/full | L | 2 V + 2 RT | r1 1/1/2; r2 0/0/6 | 150/60 | fixed; BL |
| S0102 app folder identity | High/full | L | 2 V + 1 RT | r1 0/2(+1 L-M)/8; r2 0/3/1 | 150/100 | policy ("Policy + fix F3") |
| S0103 architecture items (4 rows) | Med/light | G | 1 V | 0/2/3 | 150/90 | fixed |
| S0104 concept counters | Med/light | G | 1 V | 0/6/3 | 25/35 | fixed |
| S0105 gate tooling (7 rows) | Med/light | G | 3 V (A r1, B r1, B r2) | 0/3/4; 1/2/1; 1/1/2 | 300/150 | revert (BL-0054); BL |
| S0106 measurements | Med/light | G | 1 V | 0/3/3 | 150/60 | 2 owner (moves to M2, M7) |

## Appendix B: S0096-S0106 by slice, with tokens and classes

| Slice | Impl tokens | Review tokens (V / RT) | Findings | (a) | (b) | (c) | (d) | (e) |
|---|---|---|---|---|---|---|---|---|
| S0096 | 308k | 112k / - | 5 | 0 | 0 | 4 | 1 | 0 |
| S0097 | 74k | - | 0 | 0 | 0 | 0 | 0 | 0 |
| S0098 | 628k | 432k / 459k | 26 | 0 | 10 | 2 | 4 | 10 |
| S0099 | 471k | 261k / 167k | 15 | 0 | 0 | 3 | 2 | 10 |
| S0100 | 560k | 258k / 292k | 14 | 3 | 0 | 1 | 5 | 5 |
| S0101 | 448k | 298k / 207k | 11 | 1 | 0 | 3 | 4 | 3 |
| S0102 | 603k | 300k / 171k | 14 | 5 | 0 | 1 | 2 | 6 |
| S0103 | 314k | 127k / - | 6 | 0 | 0 | 4 | 2 | 0 |
| S0104 | 376k | 139k / - | 10 | 0 | 0 | 9 | 1 | 0 |
| S0105 | 459k | 356k / - | 15 | 0 | 0 | 11 | 4 | 0 |
| S0106 | 390k | 141k / - | 6 | 0 | 0 | 1 | 3 | 2 |
| **Total** | **4,631k** | **2,424k / 1,296k** | **122** | **9** | **10** | **39** | **28** | **36** |

Notes on the classification:
- (a) S0100: the gate's false "unchanged" (red team High = verifier Medium); the bytecode step re-creating a removed app folder (red team Low); the same by the compiler child (r2, shared Medium). S0101: the flush synced another object than the one judged (red team High = verifier Medium). S0102: false "not saved" in a replaced folder (verifier r1 F1); journal and backups in a replacement, answered "saved" (red team F1); a fail-open identity read (red team F6); two in-flight cases closed by policy in r2. Seven of the nine are the app-folder family.
- (b) S0098: the precision leak between loads (finance High, older than the slice); a 28-digit amount never judged; double rounding in the detector; a written 28-digit cost not judged; a false warning from a negative long number; a trailing zero unwarned; a false warning from another currency; a plugin's import-time context; a regression warning inside a multi-line narration; the mirror's 64-digit sums (to the owner). Six went to the backlog.
- (e) is dominated by S0098 (wrong error kinds, false refusals, a stuck WARMING state) and S0099 (plugin import tricks: `pkgutil` discovery, `extend_path`, temporary code folders, `sys.meta_path`; most became stated limits).
- Owner touch points in S0096-S0106: 13 decisions, all the recommended option. Loop-ending after two or more rounds: 4 (S0098 r2, S0099 r2, S0102 r2, S0105 r2). Moves to a later milestone: 3 (S0097, S0106 twice). One decision extended a loop: "BL-0244 slice in M1a" (S0100).
- Owner touch points in M1a overall: 28 inbox files answered (2026-10-26 to 2026-11-12), and about 40 recorded in-session choices in S0080-S0106. Every choice I found was the "(Recommended)" option. One earlier answer was later reversed after evidence (numbers beyond 28 digits: option 1 on 2026-10-29, option 3 on 2026-11-10).

## Appendix C: method

- Review tokens split by class: each run's tokens are split evenly over its findings, and a finding shared by two runs counts in both. Result: (a)+(b) 21%, (c)+(d) 48%, (e) 31% of 3.72M.
- Implementer fix tokens are estimated as implementer tokens × fix/(build+fix) minutes, which assumes tokens follow time.
- Savings are per proposal on S0096-S0106 and overlap: P1 and P3 both touch S0102 (counted once in the recommendation's 1.7-2.0M).
- Process code and product code lines are S0106's re-measurement at 8fb3002e. The 2026-10-28 comparison (6.6k product, 16.3k gate tooling) comes from that reflection's summary in IMP-0045; its categories may differ slightly from S0106's.
