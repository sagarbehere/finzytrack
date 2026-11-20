# The Windows adapter's leftovers (BL-0285): how far should a ledger file's own Windows permissions be kept, and three smaller gaps?
- Asked: 2026-11-20 by the implementer, S0135-windows-adapter-leftovers
- blocks: nothing (answered 2026-11-20; S0135 builds items 2 and 4 and states items 1, 3 and 5)
- Rubric item: a rare setup and a mechanism with fix-round history (PROCESS.md §3 step 2: the policy option goes to the owner first); item 4 is a fail-closed question (INV-22)
- ADRs checked: ADR-0069 (the replace; silent on file security), your rule 1a (S0128 round 1, 2026-11-17: "1a, wording ok"; it says what is kept, not when), ADR-0015's amendment of 2026-11-18 (the precedent "decide by what the volume offers")

## Context
On Windows a file's privacy is its ACL, the list of accounts that may read or change it. A file normally takes its folder's list ("inherited"). A user can also give one file its own, stricter list, for example "only me" on `main.bean` in a folder that other people on the PC can read. Your rule 1a keeps that list on the saved file. The verifier's round 2 on S0128 found what follows. The facts are checked against the code at 08f38970. The facts marked "runner" are probe questions for the CI's Windows machine and are not answered yet.

Who can read what, by default (runner P6 confirms it):
- Your profile folder (with Documents and the app's own folder `AppData\Local`) is readable only by you, SYSTEM and Administrators. Administrators can read any file anyway. So **in the default location, no other ordinary account can read a ledger, whatever its own list says.**
- A drive's root (`D:\`, or `C:\` itself) lets every user read the folders made under it, and so does the shared Public folder. A ledger there is readable by other accounts unless the user tightens the folder or the file.

## Item 1: the new bytes sit under the folder's list for a moment (V2-F1, Medium, privacy)
Example: `D:\Money\main.bean` has its own list "only me", and `D:\Money` lets all users read. A save writes the new text to a hidden file beside it (`.main.bean.ft-….new`). That file gets the folder's list, "all users read", and only after all the bytes are written does it get the "only me" list. The rename then puts it in place. For those milliseconds, or until the next open if the engine dies right then, another account that watches the folder could read the new text. The undo of a failed save (the put-back of the old text) has the same moment. macOS and Linux do not have it: there the file is born with its final permissions.

How rare: it needs all three of a file with its own list stricter than its folder's, a folder other ordinary accounts can read, and one of those accounts watching the folder during a save. In the default location it cannot happen. Note too that every **new** ledger file (one a save adds, or the create job) gets the folder's list anyway, so the folder is already what protects a ledger in practice.

Options, cheapest first:
1. **Policy, stated as a limit:** "On Windows a ledger is as private as its folder. Its own list is kept on the saved file, but during a save its new text is briefly in a file with the folder's permissions. To keep a ledger private, protect its folder (Documents is private by default)." Cost: a doc line in the spec now and in the user docs at BL-0087.
2. **Reorder the carry-over:** make the file empty, open it and its extra streams, give it the old file's list, then write the bytes through the handles already open (Windows checks access only when a file is opened). No new Windows call. Cost: about 30 lines in `create`, a runner test that lists the hidden file with `icacls` before its first byte, and a verifier round. Runner P3 confirms the "checked at open" fact.
3. **Create the file with the old file's list from the start** (`NtCreateFile` with a security descriptor). Cleanest in principle, but how Windows merges the folder's inherited entries into a list given at creation is not known (runner P1), and a list that denies the user write may break the stream copy (runner P2). Cost: as option 2, plus those probes.

## Item 2: three missing tests (V2-F3, Low, tests only)
No test today holds: (a) a file whose list is "inherited plus one entry of its own" keeps that state (a defect that always marks the list "own only" goes unseen), (b) the archive flag is kept, (c) the undo of a failed save keeps the file's own list.
1. **Drop them** (Low): cost none; rule 1a's details stay unguarded.
2. **Write them** (runner tests only): cost a CI run.

## Item 3: the undo's skipped flush trusts a file system's name (V2-F4, Low)
When a save fails and is undone, the undo makes each file durable on disk ("flush"). Since S0128, on NTFS and ReFS it skips a file the save never touched, so that Word or Excel holding the file cannot block the undo. It recognises "never touched" by the file's ID, which NTFS and ReFS never give to another file. It picks those file systems by **name**, `ntfs` or `refs`. macOS's own NTFS driver also says `ntfs`, and there an ID can be given to another file. Writing on NTFS on macOS needs a setting Apple does not support. Even then it takes a failed save, another program putting back the old text as a new file that receives the old ID, and a power cut, before the undo's "unchanged" could turn out false.
1. **State it as a limit** (macOS with NTFS made writable is unsupported). Cost: a doc line.
2. **Key it on Windows too:** the skip applies only when the Windows adapter says so. Cost: a few lines, a fake test, and a verifier round.
3. **Policy: refuse saves on an NTFS drive on macOS and Linux.** Cost: a new refusal, its text and tests.

This mechanism (the undo's flush) has needed two fix rounds before (S0101: two rounds, one High; S0128 round 1 added the skip), so our rule keeps it out of a batch: options 2 and 3 are their own small slice, not S0135.

## Item 4: an error silently skips the list (V2-Q-B)
If Windows refuses to read the old file's list with one of two error codes, the save goes on without it, and the saved file gets the folder's list: a private file becomes as open as its folder, and nobody is told. The code assumes those codes mean "this drive has no lists" (FAT32, exFAT; runner P4 checks what they really answer). The same shortcut is in two neighbours: the copy of the file's extra streams, and the check of the folder's own list before a save (a refused read counts as "nothing denied"). Your rule says "never a silent fallback". How rare: not seen on a normal NTFS or ReFS drive. It is possible only with a third-party driver that presents itself as NTFS. Network shares never reach a save.
1. **State it as a limit.** Cost: a doc line; the silent fallback stays.
2. **Decide by what the drive says it offers,** as you chose for the mirror on 2026-11-18: the drive's "keeps lists" and "keeps streams" flags (runner P5). A drive that says it keeps lists and then refuses: the save stops, "not saved", nothing changed. A drive that says it keeps none: skipped, as today. Applied to all three places. Cost: a few lines each, fake tests, runner tests that FAT32 and exFAT still save.
3. **Decide by name** (FAT32, exFAT skip; anything else stops): as BL-0285 first proposed. Cost about as option 2, but by name, which you moved away from for the mirror.

## Item 5: backups and journals take the app folder's list (note)
On macOS the save's backups and journal files are made "only me" (mode 600). On Windows that mode means nothing, and they take the app folder's list. The app folder is under your profile by default, so it is private, as on macOS.
1. **State it** (spec and the docs for `--app-root`): a backup is as private as the app folder. Cost: a doc line.
2. **Give each backup and journal an explicit "only me" list.** Cost: a new mechanism with runner tests; it only helps an app folder placed outside the profile.

## Recommendation
- Item 1: **option 2.** It completes your rule 1a at little cost and with no new Windows call: macOS gives a file its permissions at birth, and this gives Windows the same. If you prefer the slice smaller, option 1 is honest, because the folder is already what protects new files.
- Item 2: option 2 (they guard your 1a decision; tests only).
- Item 3: **option 1 now**, plus option 2 as a small backlog slice if you want the claim exact. The setup is unsupported and needs three unlikely events at once. Please confirm it leaves S0135.
- Item 4: **option 2**, your own rule from 2026-11-18. It turns a silent privacy loss into a truthful "not saved".
- Item 5: option 1.

The mechanism's review history: the carry-over (items 1, 2, 4) was built as a fix in S0128 round 1 (your 1a); its first review, round 2, found these items. The Windows adapter took 2 rounds in S0128: 0 High, 5 Medium and 22 Low, none in class (a). The undo's flush (item 3): S0101 took 2 rounds and fixed 1 High (the flush must sync the very file it judged); S0128 round 1 added the skip (red team F1); round 2 found V2-F4.

## Answer (owner, verbatim, with date)
Owner, 2026-11-20, in session, choosing among the options as the orchestrator put them:
- Item 1 (the staging file under the folder's permissions; offered "Reorder the write (Recommended)", "State it as a limit", "Create with the permissions"): "State it as a limit". No code: on Windows a ledger is as private as its folder, stated in engine-jobs §6.3, whose sentence "a ledger made private with its own ACL stays private" is corrected to hold after a save, not during it.
- Item 2 (the three runner tests): not asked separately; the orchestrator took the recommendation (write them).
- Item 3 (the rollback's flush skip keyed on `ntfs`): "State it as a limit (Recommended)". A stated-limit line only, no code; it adds no mechanism, so it stays in S0135's docs.
- Item 4 (the ACL and stream copy's silent fallback): "The drive's own flags (Recommended)". Decided by the volume's `FILE_PERSISTENT_ACLS` and `FILE_NAMED_STREAMS` in all three places (`_carry_over`'s DACL read, the stream copy, the pre-save check of the folder's own ACL); a drive that claims the flag and then refuses the call gives "not saved", nothing changed (the owner's rule of 2026-11-18).
- Item 5 (backups and journals take the app folder's ACL): "State it (Recommended)". In the spec and in the docs for `--app-root`; no code.
