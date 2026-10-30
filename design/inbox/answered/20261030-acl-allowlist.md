# Folder permission entries (ACLs) keep slipping past the save's checks: refuse every deny entry except macOS's own?
- Asked: 2026-10-30 by the orchestrator (S0061 round 6: red team F1 High, F2 Medium/High, F3 Medium; verifier F1, F2 Medium; the same mechanism produced red team r5 F2)
- blocks: S0061-restore (BL-0151 slice 2) fix round, and so slices 3-5
- Rubric item: a new refusal users can see (a save refused on a folder they can write in), and a change to what the owner approved in the batched r5 point
- ADRs checked: ADR-0013 amendment of 2026-10-29 ("Keep xattrs, drop ACLs (Recommended)": the swap does not keep a file's ACL); the 2026-10-30 decision record 20261030-created-path-never-removed.md (batched point: "a folder whose permissions make new files undeletable or unreadable ... will be refused before saving", recorded as an agent fix); ADR-0012 amendments of 2026-10-30; INV-22, INV-32. No ADR lists which ACL entries a ledger folder may carry.

## Context
A macOS folder can carry permission entries (ACL entries) beyond the plain owner/group/other bits. Some of them stop us from renaming or deleting the temporary files a save makes, or from looking inside a folder the save creates. The save then gets halfway, the undo cannot remove its own leftovers, and the workspace stays blocked for ever. Removing the folder's entry afterwards does not help, because our leftovers inherited it. No bytes are lost, but writes stay paused and the ledger can be left half-saved.

Round 5 added a check for two kinds of entry ("deny read" and "deny delete" that new files inherit). Round 6 found more that get past it, on real APFS:
- `deny delete_child` on the folder (High);
- an inherited `deny search` into a folder the save creates;
- inherited `deny readattr` and `deny readsecurity`;
- the same entries appearing after a crash, before recovery.

Each fix so far has added a bit to a list.

macOS itself puts deny entries on some home folders. On this Mac (checked today), your home folder, Desktop, Documents and Downloads carry `group:everyone deny delete`, and Desktop and Documents also `deny writeextattr`. These protect the folder itself from being deleted, are not inherited by new files, and do not affect saving inside. A rule must allow them.

## Options
1. **An allowlist (recommended).** The save, and recovery before it writes anything, refuse with `permission` any folder they write in, or make a folder in, that carries a deny entry applying to us. There is one exception: a deny entry that new entries do not inherit and that protects only the folder itself (delete, writeextattr, writeattr, writesecurity, chown) is allowed. That covers macOS's own entries.
   - Unknown or new kinds of entry are refused, so this fails closed and the list stops growing.
   - The plain "can I remove entries here?" system check (`_RMFILE_OK`) is added beside it.
   - Recovery stops with `permission` and writes nothing, so after you remove the entry, Retry works: no leftovers inherit it.
   - Cost: a ledger in a folder with an unusual deny entry cannot be saved until that entry is removed. The refusal message names the folder.
2. **Extend the deny list.** Add `delete_child`, `search`, `list`, `add_file`, `readattr` and `readsecurity`, plus the recovery check. This passes more setups, but the next unlisted bit is the next finding.

## Also (agent fixes, no question; recorded)
These are the verifier's r6 Lows:
- F3: a `failed` try names the entry that failed (for example the hidden temp), not only the ledger file.
- F4: recovery no longer reads the bytes of a file at a created path. The verdict never depends on them, and an unreadable or huge outside file there blocked the created-path screen.
- F5: a created-path file deleted between its `stat` and its read now resolves.

The red team's overflow 3 (a lock flag on a folder the save made) joins the step-0 check.

## Answer (owner, verbatim, with date)
Owner (2026-10-30, in session), first: "I like option 1, but will it affect usability in the sense that users will need to do something more, in order to write to the typical folders they might use with Finzytrack?". The orchestrator checked this Mac: all 15 ~/Documents subfolders carry no ACL, iCloud Drive none, Google Drive's root only the same non-inherited `deny writeextattr` / `deny delete` as Documents (allowed); Dropbox and OneDrive not present here, not checked. Then, to "Go ahead with the allowlist?": "Allowlist (Recommended)".
