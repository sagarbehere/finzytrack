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
