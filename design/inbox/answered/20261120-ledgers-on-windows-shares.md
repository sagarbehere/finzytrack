# BL-0219 (Windows): how should a save on a Windows network share work, and what may "saved" mean there?
- Asked: 2026-11-20 by the orchestrator (facts from the 2026-11-20 runs 2 and 3, the BL-0219 probe step at heads 08f38970 and 558629a8)
- blocks: the BL-0219 Windows slice; BL-0087 should not turn Windows writes on before this is answered
- Rubric item: a change to a decided rule (the save's durable order: entries are durable after the folder's sync, engine-jobs §6; ADR-0008, ADR-0068 D2) and a new write outcome's meaning
- ADRs checked: ADR-0008, ADR-0012 (drive identity), ADR-0013 and its amendment of 2026-10-29 (best effort), ADR-0068 D2, ADR-0069 (Windows' plain rename); BL-0160, BL-0166

## Your direction
Owner, 2026-11-19: "For bl-0219, I think we need to ensure that Finzytrack works for ledgers on windows shares, possibly with reduced guarantees".

## What the runner showed (Windows Server 2022, its own SMB 3.1.1 server over loopback; the share as `Z:`, `\\localhost\ftshare` and `\\<machine>\ftshare`)
The probe ran the engine's real save, rollback and recovery on the share, with the drive-ID refusal bypassed inside the probe only.
- **Today, every save on a Windows share is refused before writing** (`no_drive_id`): the adapter gives no drive ID for any network volume.
- **With a drive ID but no other change, no save on a share can ever finish.** The server refuses a folder's flush (`ERROR_INVALID_FUNCTION`). So every save ends "not finished" with the change in the file, the journal stays open, recovery fails on the same flush every time, and the workspace's saves stay paused for good. Nothing is lost, but it is unusable.
- **With exactly that refusal treated as done (on a network volume only), everything works on all three spellings:**
  - two saves in a row, with the mirror rebuilt between them;
  - an injected failure rolled back to the previous bytes, "unchanged" truthful;
  - recovery of a reopened journal putting the older bytes back.
  - The ledger was whole at its before or after bytes in every case.
- **A drive identity is available.** It is the server volume's 64-bit serial plus the folder's file ID and creation time. It was stable across every spelling, a drive remap and a restart of the file server (which really dropped every session), in two runs on different machines. A mapped drive is recorded by its UNC path. The server's own local path to the same folder gives the same identity.
- **Exclusive creates are truly exclusive on the share**, in every case probed; an earlier "ok (unexpected)" was a probe artefact. No replace ever left the file missing (0 in about 18,000 looks). Every refused replace changed nothing.
- **Permissions and streams are copied there:** the share claims both flags and every call works (your BL-0285 item 4 rule leaves share saves working on this server).

## What "reduced guarantees" would mean (facts, as the stated limits)
1. **"Saved" would mean "the file server accepted the change", not "it survives a crash".** Because the folder flush is refused, a crash or power loss of the server soon after "saved" may bring back the old file, whole. The change would then be lost, and "saved" would have been false. The same holds for an undo: the file may come back at the newer bytes. This is weaker than today's best effort on a USB stick, where the folder flush works.
2. **More "not saved, try again":** another program merely looking at the file made 143 to 183 of 300 replaces fail on the share (0 locally). A refused replace changed nothing. BL-0284's retry question applies here too.
3. **A network drop during a save:** writes and flushes through open handles fail ("unexpected network error"), while reads may still be served from the PC's cache. Not yet probed: what a save cut mid-way gives, and whether the read-back after the rename reads the server's bytes or the PC's cache.
4. The rename is Windows' plain rename (as everywhere on Windows, ADR-0069). A removed file another program holds keeps its name until that program closes it.
5. The app's own cache stays on a local disk (BL-0298, S0134).
6. **Permissions and streams follow the drive's own flags** (BL-0285 item 4, S0135): a server that claims to keep ACLs or streams and then refuses the call makes every save there "not saved", nothing changed. Windows' own server claims both and honours them.
7. Tested on one server only. Samba, a NAS, ReFS behind a share and older SMB versions are untested (M11's real machines).

## Options (cheapest first)
1. **Keep refusing saves on Windows shares (today).** Reading works; saving says why it is refused. Cost: none. It does not meet your direction.
2. **Best effort on a share, with its own notice (recommended).**
   - A drive identity for shares: the server volume's serial plus the folder's identity, as the probe found it.
   - Exactly the refused folder flush (`ERROR_INVALID_FUNCTION`, on a network volume only) treated as done, in the save, the rollback, recovery, create and copy. Any other error still stops the save.
   - The open's notice for a ledger on a share says what limit 1 means, in plain words, with "Show me how" and "Don't show again for this ledger" as BL-0166 has them. For example: "Your ledger is on a network share. Finzytrack keeps a backup of every save, but if the file server crashes or loses power right after a save, that save can be undone. For full protection, keep your ledger on your computer's own disk."
   - The durability models state the mode on a network share as a limit (as they do for the no-swap mode).
   - Before it lands, the slice probes limit 3's two open questions (a save cut by a network drop; where the read-back reads).
   - Cost: one product slice on the write path, with the data-safety red team (a new write outcome's meaning). This changes a decided rule: a save's durable order on a share no longer includes the folder's flush.
3. **Option 2, and "saved" on a share worded as "saved to the file server" in the outcome text itself,** not only in the open's notice. Cost: option 2's, plus a new outcome text (§11) and its tests.

## Recommendation
Option 2. It meets your direction with one narrow tolerance (one error code, network volumes only). It keeps every other check, and it tells the user the one real difference once, when the ledger is opened, as the best-effort drives already do. Option 3 is worth it if you want the weaker meaning visible at every save, not only once.

Review history of the mechanism option 2 extends (the best-effort write mode): S0065 (round 1 found a High: a put-back's plain rename could replace another program's new file; fixed by an exclusive create), S0071 (2 rounds), S0128 (the Windows adapter, 2 rounds, no High). The share's folder-flush tolerance was built once before for the mirror only (S0132) and removed in S0134 when the mirror's cache was refused on shares.

## Answer (owner, verbatim, with date)
Owner, 2026-11-20, in session, asked "BL-0219: how should a save on a Windows network share work?" with the options "Best effort + open notice (Recommended)", "Also say it at each save" and "Keep refusing for now": "Best effort + open notice (Recommended)".

**Decision: option 2.** A drive identity for shares (the server volume's serial and the folder's identity); exactly the refused folder flush (`ERROR_INVALID_FUNCTION`, on a network volume only) treated as done in the save, rollback, recovery, create and copy, every other error still stopping the save; the open's notice for a ledger on a share stating that a file-server crash right after a save can undo it, with "Show me how" and "Don't show again for this ledger" (BL-0166); the durability models stating the mode on a network share as a limit; and before landing, the two open probe questions (a save cut by a network drop; where the read-back reads). Review row: "New write path or write outcome" (verifier + data-safety red team).

## Later answers (owner, verbatim, 2026-11-20, in session, after S0136's first review; asked before its first fix round)
- "Which kinds of network drive should get best-effort saves?" (options "SMB shares only (Recommended)" and "Any network drive"): **"Any network drive"**. No protocol check; WebDAV, `\\tsclient`, WinFsp or Dokan mounts and Offline Files, where "saved" may mean "in this PC's cache", are a stated limit (engine-jobs §6.8).
- A share whose server keeps the file on exFAT, FAT or USB, where a server power loss can leave the file missing (red team RT1): **"State it, in limit and notice (Recommended)"**.
- Saves that change several files, which a server crash can undo in part (verifier F2): **"Allow, state it"**.
- Whether to keep the notice short: **"Short, plus what you chose (Recommended)"**. The notice: "Your ledger is on a network drive. If the file server crashes or loses power right after a save, that save, or part of it, can be lost, and a file can even go missing. Finzytrack keeps the previous version in its backup on this computer. For full protection, keep your ledger on your computer's own disk."
- Earlier the same day, on the first draft's wording: **"Tighten the sentence"**.
