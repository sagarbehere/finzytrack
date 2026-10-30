# The ACL allowlist misjudges whom an entry applies to: stop asking, and clear our own leftovers' ACLs before removing them?
- Asked: 2026-10-30 by the orchestrator (S0061 round 7: red team F1 High, F2 Medium, F3 Low; verifier F1 Medium; the same mechanism produced r5 F2 and the r6 High)
- blocks: S0061-restore (BL-0151 slice 2) fix round, and so slices 3-5
- Rubric item: a change to the owner's allowlist rule ("Allowlist (Recommended)", 2026-10-30) and to which folders are refused
- ADRs checked: ADR-0013 amendments of 2026-10-29 ("Keep xattrs, drop ACLs (Recommended)") and 2026-10-30 (the allowlist); decision record 20261030-acl-allowlist.md; ADR-0012 amendments of 2026-10-30; INV-22, INV-32 (temporary names derived from the recorded paths). No ADR decides how "applies to us" is judged.

## Context
Your allowlist refuses a folder when it carries a deny entry that applies to us. To decide "applies to us", the code asks macOS whether we are a member of the entry's subject. Round 7 found, on real APFS:
1. **High.** macOS has two special subjects, `owner` (whoever owns the file) and `group` (the file's group). Membership lookup answers "no" for both, but the kernel applies them to us: we own our files, and every folder under your home folder has group staff. So `chmod +a "group:owner deny delete,file_inherit"` gets past the check. The save then breaks half-way, and recovery stays blocked on our own temp file, which inherited the entry, even after you remove the entry from the folder.
2. **Medium.** An entry added during the save, after the check and before we create our files, has the same effect. A check can only narrow that window.
3. **Low** (the verifier also found it). The folders holding the journal and the backups are not checked at all, though your decision says "any folder they write in".

## Options
1. **Stop judging who an entry is for, and clean our own leftovers (recommended).**
   - Any deny entry refuses the folder, whoever it names. The exception stays as before: a non-inherited entry that protects only the folder itself (delete, writeextattr, writeattr, writesecurity, chown), which covers macOS's own. No membership lookups remain to get wrong.
   - The undo removes the ACL from a file of ours just before deleting it. That is safe because the file is ours and about to go. An entry added at any moment then never leaves Retry stuck once it is gone from the folder.
   - The journal and backup folders are checked too, per your "any folder they write in".
   - Cost: a folder with a deny entry for some other user (a shared Mac) is refused as well. On this Mac every deny entry I found is macOS's own `group:everyone` kind, which is allowed.
   - One narrow case stays open, written into §15: an entry added in the instant during a save can still leave the saved ledger file unreadable. You remove it from that file and press Retry.
2. **Patch the subject check.** Treat macOS's special subjects as ours, and add the leftover clean-up and the two folders. The membership logic stays, and so does the chance of the next subject it misreads.

## Answer (owner, verbatim, with date)
Owner (2026-10-30, in session): "Any subject (Recommended)" (option 1 above).
