# May R4's check load skip asking the load policy, since it only runs on files the save's checked load already read?
- Asked: 2026-10-31 by the implementer of S0066-r4
- blocks: nothing (built as option 1; with today's desktop policy, which allows every file, plugin and folder, the options behave the same; corrected 2026-10-31 after S0066 verifier r1 F2, see "Correction")
- Rubric item: 4 (the built code differs from an accepted ADR's words)
- ADRs checked: ADR-0010 (its 2026-10-27 amendment says R4 compares "the real `load_file` (through the `LoadPolicy`)"); ADR-0040 (on desktop every ledger loads in full, no trust prompt); ADR-0041 (hosted mode: don't build); ADR-0064 decision 7; ADR-0059 (an encrypted file is not read in v0.4.0: met in R4 since round 1). None says how Beancount's own loader can be put "through" the policy, which asks before each file and plugin.

## Context (plain language, a concrete example first)
After a save, R4 loads your ledger once more with Beancount's own loader and checks it gives what Finzytrack predicted. Finzytrack's own load (the "overlay") asks a **load policy** before it reads each file or runs each plugin: "may I read `/Users/you/ledger/prices.bean`?", "may I run plugin `my_plugin`?". On the desktop the policy says yes to everything (ADR-0040). A policy that says no would matter only in a hosted, many-user build, which is not planned (ADR-0041).

Beancount's own loader cannot be asked per file: it reads and runs everything in one go. So S0066 does this instead: R4 loads only when the disk is byte-for-byte the state the save left (its fingerprint, checked right before and right after the load). The save's own load asked the policy about exactly those files and plugins, so R4 meets nothing the policy has not already allowed. The gap: a file that changes after the first check is still met by the load, and the load has run by the time the second check sees the change (R4 then ignores the result as "superseded"). That window is not an instant: Beancount reads each included file when its parser reaches it, so the window is the whole parse (about 0.5 s on a 54k-entry ledger). Example: another program adds `include "secret.gpg"` to your main file while R4's load is parsing it; the load reaches `secret.gpg` before the second check.

## Correction (2026-10-31, S0066 round 1)
The first version said the options "behave the same" with the desktop policy. That was wrong in one point: the refusal of encrypted files (ADR-0059: v0.4.0 reads no encrypted file) is the overlay's own, not the policy's, so R4's load did not have it; in the example above Beancount would have run `gpg --decrypt` (perhaps with a password dialog). Round 1 gave R4's load and parse that refusal: an encrypted file met is never decrypted (`beancount_port.EncryptedFileMet`); R4 then says `superseded` when the disk moved, or counts a failure to run. What stays is this question: the policy itself (files, plugins, `pythonpath` folders, documents roots) is not asked during the whole parse window, which matters only for a policy that refuses something.

## Options
1. **Keep it as built**: no policy question in R4's load (the encrypted refusal kept, as above); the fingerprint equality stands in for the rest; spec §15 names the gap (the whole parse) for a refusing policy. Cost: nothing. ADR-0010's amendment would get a note that "through the `LoadPolicy`" is met this way.
2. **Add a parse-only pre-check**: before R4's load, parse the ledger with Finzytrack's own reader and ask the policy about every file, plugin and folder. Cost: about 0.4 s more per R4 at 54k entries (R4 is in the background, but a save right after a save waits for it). It does not close the gap either: the files can still change between the pre-check and Beancount's load.
3. **Defer**: build option 2 only if a hosted mode is ever planned (ADR-0041 already says its plan must revisit the load rules).

## Recommendation
Option 1, with option 3's note: with the encrypted refusal added it gives the same protection as option 2 today, costs nothing, and the only case it would differ (a policy that refuses something) does not exist in any planned build.

## Answer (owner, verbatim, with date)
