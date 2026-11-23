# ADR-0070: The engine's placeholder mode (cloud files on Windows)

This record decides how the ledger engine sees OneDrive's Files On-Demand placeholders, and those of every other sync app built on Windows' Cloud Files API, and what it tells when a cloud-only file cannot be downloaded.

- **Status:** Accepted (owner, 2026-11-22, in session: "A"; and on the providers: "Yes let's support every such provider and word the text accordingly")
- **Date:** 2026-11-22
- **Deciders:** owner (2026-11-22); drafted by the agent in S0149.
- **Area:** write path, data safety, platforms

## Context

Windows promotes moving Documents and Desktop into OneDrive, so a ledger in Documents is often a OneDrive file. The owner decided to support the typical OneDrive setups (2026-11-21, in session: "I'm accepting the inbox note as written", change D of `design/inbox/answered/20261121-pace-and-scope.md`; BL-0283): files on this PC are read and saved normally; a cloud-only file is downloaded when the engine reads it while online; one that cannot be downloaded is refused with a named reason; every other reparse point stays refused.

OneDrive's files are placeholders of Windows' cloud filter (`cldflt`). What a process sees of a placeholder depends on its **placeholder compatibility mode**, a per-process setting (`RtlSetProcessPlaceholderCompatibilityMode`). In the exposed mode a placeholder is a reparse point, which the Windows adapter refuses as not a regular file. In the disguised mode the cloud filter hides the reparse point and the placeholder is a plain file or folder.

Facts, each with the process it was seen in (S0144 and S0149; `design/journal/S0144-cloud-files-probe.md`, `design/journal/S0149-onedrive.md`; Windows Server 2022 on the CI runner, with a test provider in its own process):

- **The mode is not the same for every start.** The engine started as the sidecar starts it (`EngineCommand.for_venv`: `-s -S -B -P`, the bootstrap, its Job Object) got 0 (`application_default`). The test process, and every child it started (plain or with the engine's flags), got 1 (`disguise_placeholder`). A child did not inherit a mode its parent had set (the parent read 2, the child 1). The 2026-11-22 run 11.
- **In the disguised mode** the open, the save and the rebuild worked on a downloaded placeholder and on a cloud-only one (downloaded by the engine's own read). After the save a plain file stood at the name, with no reparse point. The adapter's `sha256`, as a first read, downloaded the file once. Symbolic links and junctions were still links. Run 11.
- **In the exposed mode** the save refused the placeholder (`not_a_file`) while the open's and the rebuild's loads (Python's `open()`) read it. Run 11.
- **Offline** (the provider disconnected): a cloud-only file's read failed with `ERROR_CLOUD_FILE_PROVIDER_NOT_RUNNING` (362) through the adapter, and with a bare `EINVAL` through the C runtime (Python's `open()`, Beancount's read). The save was "not saved" `io_error`; the open and the rebuild failed `engine.internal`. S0144 run 1 and S0149 run 11.

## Decision

1. **Every engine process pins `disguise_placeholder` at its start** (option A; `ledger_engine.fs.this_os.pin_placeholder_mode`): the engine's `main` and the copy check's child (`ledger/copy_check.py`), which is another process and does not inherit the pin. The pin is read back. If Windows refuses it, every file operation of that process is refused (`engine.unsupported_platform`, its message naming the cause): a missing primitive refuses, never a run in another mode.
2. **Every Cloud Files provider is treated alike** (owner: "Yes let's support every such provider and word the text accordingly"): OneDrive, Dropbox, Google Drive, Box. The engine does not ask which provider owns a file.
3. **A cloud-only file Windows could not download has its own reason, `cloud_unavailable`**, keyed only on the cloud filter's download errors (`fs.port.CLOUD_DOWNLOAD_ERRORS`: 362 and its kin, checked against Windows' own message table on the runner), never on `EINVAL` or `EIO` in general. The adapter raises `fs.port.CloudUnavailable` for them (`_win_error`, `_nt_error`). After a read through the C runtime fails, `fingerprint.chunks` asks Windows again with `ReadFile` (`this_os.cloud_download_failed`). It is told by the save ("not saved", naming the file; "unchanged" only after the re-hash), the open and the rebuild (refused, naming the file), and Copy (refused, naming the file). Its text names no single provider and no cause Windows did not give (`msg:save.cloud_unavailable`, `msg:open.cloud_unavailable`, `msg:copy.cloud_unavailable`).

## Options considered

Cheapest first, as they were put to the owner:

- **(C) Inherit whatever Windows gives.** No code. Rejected: run 11 showed the product's engine at 0 and a test child at 1. A ledger could be saved from one start and refused from another, and the packaged interpreter is another executable again.
- **(A) Pin disguise.** One call at each engine process's start and a read-back. Chosen. Placeholders are read and saved as plain files. Other reparse points stay visible and refused (run 11: links and junctions are links in every mode).
- **(B) Pin expose and teach the adapter a cloud kind.** The adapter would accept the cloud reparse tags as files and folders: `_kind`, `_a_link`, `create(like=)`, `sync_file` and the reads would all change, each with must-not-pass tests. That would widen what an adapter with several fix rounds accepts (S0128 2, S0131 3, S0132 2, S0136 4 verifier rounds). Its one gain, telling a downloaded placeholder from a plain file, serves no decided behaviour. Rejected.

## Consequences

- `design/specs/engine-jobs.md` §6.3 ("Cloud placeholders", "Offline") and its §11 rows. **Limit (cloud files)** in §6.3 states what is not covered: a downloaded placeholder looks like a plain file; every provider is alike; conflict copies from two PCs (S0150's user docs); OneDrive's own sync engine, which the runner's simulation is not (the owner's check by hand, S0150); recovery's try meeting a cloud-only file offline gives its general text; an error at a file's open (not its read) keeps the C runtime's error.
- The runner's simulation (`engine/tests/fs/test_windows_cloud.py`, `tests/fs/cloud_provider.py`) holds the pin, the saves, the plain file after a save, the links, each offline outcome and the error codes.
- The packaged engine runs the same `main`, so it pins the same mode. S0150's hand check on the owner's PC sees the packaged app.

## Note (2026-11-22, S0149 round 1; the verifier's and the data-safety red team's findings, owner: "Yes")

Three statements above are narrowed to what holds:
- Decision 1: a process in any other mode refuses every op that reads or writes ledger, journal or mirror files, not "every file operation": the engine lock in the app folder is still taken (a guard, not a ledger file). No pin state is kept: `this_os.supported()` and `for_this_os()` ask the process's mode each time.
- Decision 3: the adapter raises `fs.port.CloudUnavailable` only at a read of a file's data (`ReadFile`, `_read_error`); `_win_error` and `_nt_error` no longer tag any code, so a write, a rename, a flush or a folder's sync meeting a cloud code is a plain I/O error. "Checked against Windows' own message table" means: on the runner each code's own message holds the words of its meaning, and the near codes for in use (391), pinned (392), access denied (395) and not in sync (377) are not in the set.
- Decision 3, the save: "unchanged" is said only of a file the save writes and the re-hash read; a cloud-only file the save only reads gives "could not check whether your ledger changed". The preview tells the reason too (`LEDGER_FILE_NOT_DOWNLOADED`), and every freshness look asks for a cloud-only file before answering "changed" or "not fresh".

## Note (2026-11-23, S0149 round 2; owner, 2026-11-23, in session: "Remove the three early cloud checks")

The 2026-11-22 note's last point is withdrawn in part: "every freshness look asks for a cloud-only file before answering changed or not fresh" no longer holds, and the preview no longer tells the reason (`LEDGER_FILE_NOT_DOWNLOADED` is removed, its text never approved). The round-2 verifier (V2-1) showed the three early checks (the preview's look at the published generation, the save's check of it, the rebuild's look at its hint before the load) observe the set a generation or hint recorded, not the ledger as it is: after a relocation they named the old place's file, and they named a file the ledger no longer includes. Option 1 of `design/inbox/answered/20261122-s0149-freshness-cloud-checks.md` removed them. What names a cloud-only file now: the save's gate on the plan's own files, the writer's reads, the open's load, the rebuild's warnings' read and its look after a failed load, and Copy. Offline, the preview and the save's mirror check answer "not fresh" (`WORKSPACE_NOT_READY`), which is true (the mirror cannot be shown fresh) but says "wait a moment"; naming the file there is BL-0310, with the first mutator.

## Note (2026-11-23, S0149 round 3; owner, 2026-11-23, in session: "Option 2.", `design/inbox/answered/20261123-s0149-rebuild-offline-cause.md`)

The 2026-11-23 note's "the rebuild's … look after a failed load" is narrowed. Offline the cloud filter fails the read, not the open, and Beancount's C parser turns that read failure into its own `SystemError`, which `load_failure` tells as `beancount_failed` (the 2026-11-23 run 15 showed it on the runner; round 3, V3-1). And that look observed the hint's set, so it could name a file the ledger no longer includes (V3-2). Now, when Beancount's own load fails, as an `OSError` or as its own failure, the rebuild asks about cloud files only among the files this load opened (`parsed`, each recorded before Beancount reads it) and names the last one opened that is cloud-only; if none is, the refusal is as before.
