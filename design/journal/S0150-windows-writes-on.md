# S0150: Windows writes on (BL-0087's split, last part; BL-0286, BL-0283's hand check, M1b)

## Brief
- **Goal:** the product's engine on Windows selects the Windows adapter (`fs/this_os.py`) whenever its process is in the pinned placeholder mode (ADR-0070), so open, save, rebuild, copy, create and recovery work there as on macOS and Linux, in ADR-0069's best-effort mode on every volume. The test switch goes (`fs/under_test.py`, the `windows_port` fixture and its opt-ins, `switch_uses`: BL-0286 closes). Every save and recovery text is true on every OS (the 11 Mac-only keys of `design/journal/S0148-in-use-text.md` "For S0150"). The user docs for Windows are drafted (the docs repo's `v0.4` branch). The owner gets a hand-check script for their Windows PC with OneDrive (BL-0283's list, RT Q1 and Q2). BL-0087's remaining must-fix lines are checked.
- **Implements:** BL-0087 (its "Split (2026-11-22)" line; owner, 2026-11-22, in session: "Split OK. Do V4-1, let's go with your lean"); BL-0286 (owner, 2026-11-21, in session: "option 1"); the Mac wording (owner, 2026-11-22, in session: "1A and 3B, keep the brief check, fold the Mac wording into S0150"); BL-0301 and the `/` in paths (owner: "keep option 1"); IMP-0078 (owner, 2026-11-23: "Yes, write it up. I'm already accepting it as you have explained"); ADR-0061 and its 2026-11-14 amendment, ADR-0069, ADR-0070; engine-jobs §6.3, §6.8, §11, §14.
- **Capabilities:** none changes status: CAP-DESK-INSTALLERS (the Windows build) stays M11; the engine's write path on Windows is BL-0087's, not a capability row of its own.
- **Invariants touched:** INV-01 and INV-22 (every save on Windows is now a product save: truthful outcome, the disk byte-identical on a refusal), INV-32 (recovery before any open on Windows), INV-10 (unchanged on Windows: the engine holds no ledger), INV-23 and INV-24 (the hand-check script writes only its own scratch folders; no home path, run ID or machine name enters the repository; the report it prints names paths relative to its scratch folder).
- **Review row:** product code turning on a write path on a new OS: **new write path**, the verifier and the data-safety red team, a fresh round after any product-code fix (PROCESS.md §3 review budget). The texts are under IMP-0078 (class (d) causes; an outcome they claim is class (a)). The hand-check script and its test take the tooling row.
- **Risk class / lane:** High / full.
- **The bytes it protects:** every ledger on Windows, since the product now writes there: never torn, lost or overwritten by our save, rollback or recovery beyond ADR-0069's stated best-effort window, and no false "saved", "not saved", "unchanged", "undone", "created" or "copied".
- **Which real setup needs this, and can a policy forbid the rest?** Every Windows user (Windows 10/11 x64 is a decided platform, ADR-0007, ADR-0056). The rare setups are already refused or stated (refuse first, IMP-0075): every reparse point but a link or a Cloud Files placeholder, EFS files, a cache on a share, `\\` in a moved Copy line (BL-0301's stated limit). Nothing new is accommodated here.
- **Has this mechanism or its family needed two or more fix rounds before?** Yes, the Windows family: verifier rounds per journal S0128 2, S0129 2, S0130 1, S0131 3, S0132 2, S0133 1, S0134 1, S0135 1, S0136 4, S0146 1, S0148 1 (plus two extra CI runs and three owner questions), S0149 4 (three fix rounds, two options notes, and IMP-0078 as its outcome). This slice adds no mechanism: it removes a switch, selects an adapter already reviewed, and makes texts generic. The policy options were the owner's already (refuse first, change D, ADR-0069, ADR-0070).
- **Under IMP-0078:** the outcome is exact (class (a)); a cause is best effort (class (d)). The texts changed here keep every outcome sentence they had ("Finzytrack did not save your change", "Then press Retry") and take the generic form for the cause, with no OS-specific how-to.
- **Acceptance tests (fail on the base unless tagged):**
  - `engine/tests/fs/test_this_os.py::test_on_windows_in_the_pinned_mode_the_adapters_are_the_windows_ones` (every OS, simulated: `win32`, the mode pinned, the adapter module stood in): `for_this_os`, `legacy_locks_for_this_os` and `supported()` give Windows'; fails on the base (refused without the switch).
  - `engine/tests/fs/test_this_os.py::test_on_windows_out_of_the_pinned_mode_every_port_is_refused_naming_the_mode` (`@regression-guard`: the mode rule is S0149's): the port and the legacy locks refused, `mode_refusal()` names the mode.
  - `engine/tests/fs/test_windows.py::test_the_product_on_windows_selects_the_adapter_in_a_fresh_pinned_process_and_its_threads` (Windows runner): a fresh engine process that pins as `main` does gets `WindowsFS` and `WindowsLegacyLocks`, also in a thread it starts; turned round from the base's "the product does not select the Windows adapter" (must-not-pass: a product that still refuses).
  - `engine/tests/fs/test_windows.py::test_a_fresh_process_in_another_mode_refuses_every_port` (Windows runner): set to expose, refused.
  - `engine/tests/architecture/test_platform_selection.py::test_the_windows_test_switch_is_gone`: no `fs/under_test.py`, no `windows_port` fixture or opt-in under `engine/tests`, no `under_test` in `this_os`; fails on the base.
  - `backend/tests/platform/test_engine_job_texts_spec.py::test_no_engine_job_text_gives_one_os_how_to`: no rendered text (the generator's `--stdout`) says "Mac", "Finder", "Terminal", "chmod" or "Get Info"; fails on the base (11 keys).
  - `backend/tests/test_windows_hand_check.py`: the hand-check driver's automatic part runs on a scratch folder on every OS (mode, setup, open, save, the save burst, the looks) and prints a report with no absolute path; fails on the base (no script).
  - On the Windows runners (the 2026-11-23 run 17): the whole engine and backend suites green with no skip naming BL-0087 (the backend's end-to-end writes, the scheduler's real-engine saves, the boot's recover-all, the measurement scripts; the engine's `measure_save.py`).
- **Expected doc impact:** `design/ARCHITECTURE.md` (the port table's Windows line, §2's platform note), `design/specs/engine-jobs.md` (§14's Limit (Windows), the test note), `design/specs/roadmap.md` (M1b's Windows paragraph), `engine/CLAUDE.md` and `backend/CLAUDE.md` (the Windows lines), `design/generated/engine-job-texts.md` (regenerated with its generator's write mode), the docstrings of `fs/this_os.py`, `fs/windows.py`, `fs/port.py`, `fs/darwin.py`; BL-0087, BL-0283, BL-0286 rows; the user docs on the docs repo's `v0.4` branch (Windows, OneDrive, `/` in paths, privacy, the in-use text, the share notice); `scripts/windows_hand_check/README.md`.
- **Open questions:** none hits the rubric. Two points are recorded as decisions (below): `folder_identity` keeps no Windows form; the best-effort notice's Windows form is shown by BL-0166 (M2), so until then the user docs tell it.

## Decisions
- **The selection** (agent): `this_os` selects the Windows adapter on `win32` whenever `pin_refused()` is None (`for_this_os`, `legacy_locks_for_this_os`, `supported()`), and `mode_refusal()` names the mode on every Windows process; `PORTED` gains `win32`; the adapter module is imported at `this_os`'s top under `sys.platform == "win32"`, as Linux's is (the base imported it lazily so that "the product never loads it"; that reason is gone). The legacy locks take the mode rule too: they are used only with the port, on ledger files. The engine lock keeps its own path (`engine_lock_folder`), taken in any mode (S0149 round 1, V7). `UnsupportedPlatform`'s and `engine.unsupported_platform`'s messages name Windows among the ported systems.
- **`folder_identity` keeps no Windows form** (agent; the orchestrator's brief listed it among the functions to turn on): it takes a POSIX descriptor of a folder, which Windows does not have (`os.open` refuses a folder there), and its only caller is the POSIX branch of `protocol.server.hold_engine_lock`. On Windows the app folder's identity is read through the adapter's folder handle (`port.folder_id`, `port.same_folder`: the volume serial and the file ID) by `_hold_engine_lock_windows` and by `protocol.app_folder.changed` (through `for_this_os`), and by the sidecar's `windows_files` (S0128, S0129). Its docstring says so.
- **The pin moves to the test session's start** (agent; the brief: "must move somewhere every real-disk Windows test still gets it"): `engine/tests/conftest.py` pins the test process's mode at import on Windows (a `UsageError` if Windows refuses), before any test or fixture, so every test gets it, the fake's included (where it changes nothing). A test that sets another mode puts the pinned one back (`tests/fs/test_windows_cloud.py`'s `pinned` and `set_mode`, unchanged). The switch's other role, the path model of `test_save.py` and `test_copy_ops.py` (real-disk modules that also name the fake: Windows' model, not the fake's POSIX one), is `tests/windows_pending.HOST_MODEL_MODULES`, by file name as `POSIX_PATH_MODULES` is; behaviour unchanged.
- **Inventory of the switch's users** (by behaviour: every reader of `under_test` and every test that asked for `windows_port`, from `git grep` at the base): `fs/this_os.py` (`supported`, `for_this_os`, `legacy_locks_for_this_os`, `mode_refusal`), `engine/scripts/probe_windows_share_save.py` (two `with` blocks; now `_pinned()`), `engine/tests/conftest.py` (the fixture and `_windows_port_first`), and **20** test modules with the opt-in (the brief said 17; S0128 to S0149 added three), two of them also with `windows_port: None` in a fixture (`test_save.py`'s `led`, `test_publish.py`'s `fs`). All gone; `test_the_windows_test_switch_is_gone` scans every file under `engine/tests` for the names.
- **Every other refusal on Windows, found by behaviour** (`UnsupportedPlatform`, `engine.unsupported_platform`, `sys.platform not in ("darwin", "linux")`, `os.name != "posix"`, `windows_pending` reasons, `PORTED_ONLY`): turned on: the engine's `measure_save.py` case (`PORTED_ONLY` removed; the script pins the mode first, as an engine does), the backend's `test_writes_end_to_end.py` (`WRITE_PATH` removed from `tests/windows_pending.py`), the scheduler's two real-engine saves, `test_main.py`'s two boot recover-all tests, `measure_protocol.py` and `measure_write.py` with their three cases (`MAC_ONLY` removed). Deleted: `test_main.py::test_on_windows_the_boots_recover_all_runs_in_an_engine_and_answers_the_platform_unsupported` (its premise, the refusal, is gone; the boot's own test now runs there). Kept, with the reason now in the skip text: `test_main.py`'s `NO_PORT` and `MACOS_ONLY` cases (the app folder's sync by descriptor and `F_FULLFSYNC`: POSIX by design, their Windows form `windows_files` tested in `test_engine_windows.py`); every other `windows_pending` reason names a POSIX-only rule or another row (BL-0288, BL-0296). `engine.unsupported_platform` stays for other OSes and for a Windows process out of its mode. Fixed on the way, since they would have failed there: the venv interpreter's path (`bin/python`) in `real_rig`, `_plan_fingerprint` and `measure_write.py`, now `engine_client.venv_python` (also used by `for_venv` and `app.main`); and the test-copy mark's reader child (`recorded_mark` in `test_writes_end_to_end.py`, `measure_write.py`) pins its mode before it asks the port, since a fresh child's mode is not guaranteed (run 11).
- **The texts** (owner, 2026-11-22: "1A and 3B, keep the brief check, fold the Mac wording into S0150"; IMP-0078, owner, 2026-11-23: "Yes, write it up. I'm already accepting it as you have explained"): the generic form, no OS's how-to, every outcome sentence kept ("Finzytrack did not save your change", "…then try again", "…then press Retry"): `locked` "is locked or read-only. Allow changes to it"; `permission` (file, folder, unknown) and recovery's `permission` (entry, unknown) "Finzytrack has no permission to change <where>. Check its permissions"; recovery's `failed_permission` keeps the system's answer and says "Check its permissions."; the action refusal `permission` "Finzytrack has no permission to change this folder. Check its permissions."; `no_drive_id` and `no_folder_id` "your computer's own disk". Per-OS advice was considered and not built: the reference is rendered by calling the code, so an OS branch would make `design/generated/engine-job-texts.md` differ by the OS that generates it. `msg:save.permission_acl` and `msg:recovery.permission_acl` (an ACL entry the engine read and names) were already true on every OS and are unchanged. Regenerated with the generator's write mode (`backend/.venv/bin/python -I backend/scripts/gen_engine_job_texts.py`, no redirect).
- **The best-effort notice on Windows** (agent; IMP-0078's "best effort is told"): its words exist (`msg:save.best_effort_windows`, S0128; `msg:save.best_effort_share`, S0136) and are shown by the workspace open's notice, BL-0166 (M2), which nothing calls yet, on macOS's drives without the swap either. Until then the user docs tell it (below). `msg:save.best_effort_drive` ("keep your ledger on your computer's own disk") is false on Windows, where every disk is best effort: BL-0166 shows `best_effort_windows` there instead (its row says so already: "The workspace open shows it on Windows").
- **The user docs** (agent): the docs repository's checkout beside this one is not writable from the agents' sandbox (`touch` refused: "Operation not permitted"), so the text is below for the orchestrator to put on its `v0.4` branch: a new page `src/content/docs/reference/finzytrack-on-windows.md` and one paragraph added at the top of `where-to-keep-your-ledger.md`. The network-share notice and the "best effort" notice it describes are shown once BL-0166 lands (M2, before the release the docs branch is for).
- **The hand-check script** (agent): `backend/scripts/windows_hand_check.py` (a backend script, so pyright reads it on three platforms and the backend tests run it), run by `scripts/windows_hand_check/hand_check.ps1`, with `scripts/windows_hand_check/README.md`. Needs: Windows 10 or 11 signed in to OneDrive with Files On-Demand on; `uv` (installed by its official PowerShell one-liner, no administrator rights; it downloads Python 3.13 itself); this repository at the commit given, as GitHub's zip of `next`, unzipped outside OneDrive. It runs `uv sync --locked` in `engine/` and `backend/` (the locked environments, as CI's Windows jobs), then the product's own open, preview, confirm and rebuild jobs through the sidecar's scheduler on the sidecar's engine command (with the test producer, as `measure_write.py`: only a test copy may be written by an unpromoted change), on three tiny ledgers in a new folder in OneDrive; it writes nowhere else but its temp folder, which it removes. It reports the engine's mode (and an installed build's with `-Installed`), what OneDrive does after our rename (attributes in the expose mode and the in-sync state from `CfGetPlaceholderInfo`, sampled every 2 s for 2 minutes), 8 saves back to back, cloud-only files online, then by hand: offline (an open and a save confirmed from an online plan), paused, Q1 (a folder made on onedrive.com that this PC has not listed, opened offline) and Q2 (a cloud-only plugin module, offline). The report is JSON with no absolute path (`Cleaner`: each known root in every spelling replaced by a label). Its automatic part runs on every OS in `backend/tests/test_windows_hand_check.py`; the ctypes looks and `attrib` run only on Windows, so their first real run is the owner's (the CI run 17 runs the automatic part on the runner, without OneDrive).

## User docs (for the docs repository's `v0.4` branch)

### New page `src/content/docs/reference/finzytrack-on-windows.md`

```markdown
---
title: Finzytrack on Windows
description: How Finzytrack saves on Windows, with OneDrive and other sync apps, and what to watch for.
sidebar:
  order: 9
---

Finzytrack runs on Windows 10 and 11. It reads and saves your ledger there as on a Mac, with the differences below.

## Saves on Windows are best effort

Windows cannot replace a file in one step, as macOS can. So on Windows Finzytrack saves the way it saves on a USB stick:

- Before it changes a file, it writes a backup of it on this computer.
- Just before it replaces the file, it reads it again. If another program changed it, Finzytrack does not save, and tells you.
- If the power fails during a save, the next start puts every file back as it was before the save.

One rare case remains: if another program saves the same file at the very moment Finzytrack saves, that program's change can be lost, and nothing tells you. Close other programs that edit your ledger (an editor, a script) while you save in Finzytrack.

## "Another program is using the file"

Windows does not let a program replace a file that another program has open. If an editor, an antivirus scan or OneDrive's upload has your ledger file open when you save, Finzytrack does not save and says:

> Another program is using main.beancount, for example an editor, antivirus or OneDrive. Close that program or wait a moment, then try again. Your ledger is unchanged.

Wait a moment and save again.

## Write paths in your ledger with `/`

Beancount reads a backslash (`\`) inside quotes as the start of a special character, such as `\n`. In your ledger files, write a Windows path with forward slashes:

    include "D:/Ledger/2026.beancount"
    option "documents" "D:/Ledger/receipts"

A path relative to the file, such as `include "years/2026.beancount"`, works too, and is the easiest. When Finzytrack copies a ledger, it cannot move a line that spells its path with `\\`: it does not copy, changes nothing, and tells you. Change the backslashes to `/`, then try again.

## Who can read your ledger

On Windows, your ledger is as private as its folder. The folders in your user folder (Documents, Desktop and the others) cannot be read by other ordinary accounts on the PC. A folder at the top of a drive (such as `D:\Ledger`) or in the shared Public folder can be. Keep your ledger in a folder in your user folder.

Finzytrack's backups and save records are as private as its app folder. The default app folder is in your user folder, so it is private. If you start Finzytrack with another app folder, they are as private as that folder.

A file encrypted with Windows' file encryption (EFS) is not saved: Finzytrack would have to write its contents unencrypted into a backup. It tells you instead.

## OneDrive and other sync apps

Finzytrack works with a ledger in OneDrive, Dropbox, Google Drive, Box or another sync app that uses Windows' cloud files (in OneDrive: Files On-Demand).

- **Files on this PC** (downloaded, or set to **Always keep on this device**) are read and saved as usual. After a save, the sync app uploads the changed file.
- **A file that is only in the cloud** is downloaded by Windows when Finzytrack reads it, if you are online.
- **Offline**, or with the sync app paused, closed or signed out, Windows cannot download such a file. Finzytrack then does not open or save, and says:

  > main.beancount is only in the cloud (OneDrive or another sync app), and Windows could not download it. Check that you are online and that the sync app is running, then try again.

  Before a save it can also say that the workspace is not ready. Both go away once you are online. To work offline, right-click your ledger folder in File Explorer and choose **Always keep on this device** while you are online.
- **Right after a save**, while the sync app uploads the file, saving again can show "Another program is using…". Wait a moment and save again.

**Do not change one ledger on two PCs before they have synced.** The sync app then keeps both versions: one under the file's name, and one as a conflict copy, for example with the PC's name added to the file name. Finzytrack reads only the files your ledger names, so it does not see the conflict copy, and the changes in it are missing. If you find a conflict copy, copy its changes into the main file, then delete it.

## Ledgers on a network drive

Finzytrack saves to a ledger on a network drive (a mapped drive letter or a `\\server\share` path), in the same best-effort way. It also tells you when you open such a ledger:

> Your ledger is on a network drive. If the file server crashes or loses power right after a save, that save, or part of it, can be lost, and a file can even go missing. Finzytrack keeps the previous version in its backup on this computer. For full protection, keep your ledger on your computer's own disk.

Finzytrack's own app folder must be on this computer's disk: it does not open a workspace whose cache folder is on a network drive.
```

### Added at the top of `src/content/docs/reference/where-to-keep-your-ledger.md` (after the front matter)

```markdown
On Windows, every drive saves the best-effort way described below for USB sticks: read [Finzytrack on Windows](../finzytrack-on-windows/). This page is about macOS.
```

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
(The verifier reads only the Brief, so this section does not steer its attacks; it is the implementer's own record.)
- Checks that accept, tag or refuse, each with its must-not-pass test:
  - `this_os.for_this_os` / `legacy_locks_for_this_os` / `supported()` on Windows (selected only in the pinned mode): `tests/fs/test_this_os.py::test_on_windows_in_the_pinned_mode_the_adapters_are_the_windows_ones` (must not pass: still refused), `::test_on_windows_out_of_the_pinned_mode_every_port_is_refused_naming_the_mode` (must not pass: selected in another mode); on the runner `tests/fs/test_windows.py::test_the_product_on_windows_selects_the_adapter_in_a_fresh_pinned_process_and_its_threads` and `::test_a_fresh_process_in_another_mode_refuses_every_port`, and S0149's `tests/fs/test_windows_cloud.py::test_a_refused_pin_refuses_every_file_operation` (the protocol's refusal, unchanged); other OSes `::test_other_operating_systems_are_refused`, `::test_without_a_port_the_engine_answers_its_handshake_and_ping_and_never_loads_an_adapter`, `::test_BL0261_an_op_that_needs_the_port_is_refused_naming_the_platform_not_a_missing_app_folder` (now on `freebsd14`).
  - The switch gone: `tests/architecture/test_platform_selection.py::test_the_windows_test_switch_is_gone`.
  - No one OS's how-to in a text: `backend/tests/platform/test_engine_job_texts_spec.py::test_no_engine_job_text_gives_one_os_how_to` (on the reference, which `check_generated.py` holds equal to the code).
  - The hand-check script's refusals: `backend/tests/test_windows_hand_check.py::test_a_folder_that_is_not_empty_is_refused_and_left_as_it_is`, `::test_without_a_folder_off_windows_it_refuses_before_anything`, `::test_the_report_names_no_absolute_path`.
- Self-mutation (`scripts/mutants.py`, plans in the session scratchpad): the selection 6 of 6 killed (`port-any-mode`, `port-not-windows-checked`, `locks-any-mode`, `supported-any-mode`, `mode-never-named`, `windows-still-refused`); the texts 3 of 3 killed (`mac-locked-back`, `mac-acl-back`, `refused-mac-back`; the command regenerates the reference and compares it, then runs the text tests). Not run through the tool, Windows only: the conftest pin (off Windows `pin_placeholder_mode` is a no-op, so a mutant there is equivalent on macOS; on the runner a missing pin refuses every real-disk test's port, which hundreds of tests would show).
- Population: the switch's users (20 test modules, the selection point, one probe script, conftest; Decisions); the refusals on Windows (every `sys.platform not in ("darwin", "linux")`, `os.name` check and `windows_pending` reason in `engine/` and `backend/`, read one by one; Decisions: 8 turned on, 1 deleted, 2 kept with their reason); the texts (every row of the reference: 11 of its rows had one OS's words, all 11 changed; `ONE_OS_HOW_TO` finds none now).

## Golden changes
- `backend/tests/platform/test_save_job.py::test_each_not_saved_reason_has_its_section_11_text` (4 expected texts: `locked`, `permission` with a file, `permission` at the journal folder, `no_folder_id`), `backend/tests/platform/test_recovery_text.py` (`test_a_permission_stop_on_a_file_says_how_to_remove_its_entry` became `test_a_permission_stop_on_a_file_names_that_file`, `test_failed_with_no_read_permission_gives_the_acl_way_out` became `test_failed_with_no_read_permission_says_to_check_the_permissions`), and 11 rows of `design/generated/engine-job-texts.md`: the old texts gave macOS's how-to ("Finder: Get Info", "Terminal: chmod -N", "Your Mac", "your Mac's own disk"), false for a Windows user now that Windows saves (owner: "fold the Mac wording into S0150"); each outcome sentence is unchanged.
- `engine/tests/fs/test_this_os.py`: `win32` left the "other operating systems" parameters (Windows is ported); `freebsd14` stays.
- `engine/tests/fs/test_windows.py`: the base's must-not-pass "the product does not select the Windows adapter" and "outside the switch this process refuses too" are turned round (the brief).
- `backend/tests/test_main.py`: the Windows-only "answers the platform unsupported" test is deleted (Decisions).
