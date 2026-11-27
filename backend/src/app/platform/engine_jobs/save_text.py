"""What the user reads when a save does not end "saved" (`design/specs/engine-jobs.md` §11's save rows; S0063), and
the preview's tip for a large file (S0120). Plain text for non-native readers; the screens are BL-0092's.

"Your ledger is unchanged" is said only when every file the save would write was hashed again and equals its bytes
before the save (§11's rule; `ChangeNotSavedDetails.unchanged`); otherwise the text says which file changed, or that
Finzytrack could not check. After a rollback stopped at a file the save was creating, the Reopen text does not promise
an undo (`recovery_text.reopen_after_created`; S0061 red team r8 overflow 1).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from app.domain.errors import DomainError
from app.platform.engine_jobs.recovery_text import REOPEN, in_use, not_downloaded, reopen_after_created
from app.platform.engine_paths import last_name
from app.platform.errors import ChangeNotSavedDetails, ErrorCode, WritesPausedDetails, spec_for
from app.platform.plans import LARGE_FILE_BYTES

NOT_SAVED: Final = "Your change was not saved."
UNCHANGED: Final = "Your ledger is unchanged."
LEDGER_CHANGED: Final = (
    "Your ledger changed after this preview, so Finzytrack did not save your change. Review it again."
)
BEST_EFFORT_DRIVE: Final = (
    "This drive (for example a USB stick or network share) can't replace files in one safe step. Finzytrack still "
    "keeps a backup of every save, but if another program changes the same file while Finzytrack is saving it "
    "(usually less than a second, longer for a large file or a slow drive), that program's change can be lost. For "
    "full protection, keep your ledger on your computer's own disk."
)
"""§11's notice when a workspace whose ledger is on a drive without the swap opens (ADR-0068 D2, the owner's words;
§6.8). The workspace open shows it (BL-0166); this is the one place its words live. S0161 (M1b data-safety red team
DS-F1; owner, 2026-11-27: "State it honestly"): the window is the save's durable phase, from the backup's read to the
file's rename (several disk syncs), not "the very moment you save"."""
BEST_EFFORT_WINDOWS: Final = (
    "On Windows, Finzytrack can't replace a file in one safe step. It still keeps a backup of every save, but if "
    "another program changes the same file while Finzytrack is saving it (usually less than a second, longer for a "
    "large file or a slow drive), that program's change can be lost. Close other programs that edit your ledger "
    "while you save in Finzytrack."
)
"""§11's notice on Windows, where every drive takes §6.8's mode (ADR-0069; S0128): the owner's words (2026-11-17, "1a,
wording ok"); `BEST_EFFORT_DRIVE`'s advice to use the computer's own disk does not hold there. The workspace open shows
it on Windows (BL-0166). S0161 (DS-F1; owner, 2026-11-27: "State it honestly"): "at the very last moment of a save"
understated the window, which is the save's durable phase."""
BEST_EFFORT_SHARE: Final = (
    "Your ledger is on a network drive. If the file server crashes or loses power right after a save, that save, or "
    "part of it, can be lost, and a file can even go missing. Finzytrack keeps the previous version in its backup on "
    "this computer. For full protection, keep your ledger on your computer's own disk."
)
"""§11's notice when a workspace whose ledger is on a network share opens (S0136, BL-0219: Windows; the owner's answer
of 2026-11-20, "Best effort + open notice (Recommended)"; the note's example tightened by the owner the same day,
"Tighten the sentence": the backup clause dropped, since the backup holds the version before a save and cannot bring
back a save the server lost; then, after S0136's first review, "Short, plus what you chose (Recommended)": a part
of a save can be lost, a file can go missing, the previous version is in the backup on this computer): the share's
server makes a save durable when it does, so a crash or power loss of the server right after "saved" can bring back
the old file, part of a save, or leave a file missing (§6.8, "Limit (network share durability)"). The engine tells a
share apart (`handles.drive_kind`: `network_share`); the workspace open shows it (BL-0166)."""


def _name(path: str | None) -> str:
    return "A ledger file" if not path else last_name(path)


NOT_CHECKED: Final = "Finzytrack could not check whether your ledger changed."

EARLIER_UNFINISHED: Final = (
    "Your change was not saved. An earlier change is not finished. Reopen the workspace so Finzytrack can check your "
    "files and undo the unfinished change, then try again."
)
"""§11 "an earlier save is unfinished" (`WRITES_PAUSED {earlier_unfinished}`, S0100, BL-0183): this change was refused
before anything was written."""


def _rehash(d: ChangeNotSavedDetails) -> str:
    """What the re-hash found (§11's rule): unchanged, the first file another program changed, or nothing known. In a
    "not saved" outcome no file of the save is left at its new bytes (the rollback put them back), so a file found not
    at its old bytes was written by another program (BL-0182's test)."""
    if d.unchanged:
        return UNCHANGED
    if d.changed is not None:
        return f"{_name(d.changed)} was changed by another program."
    return NOT_CHECKED


def _unchanged_or_not(d: ChangeNotSavedDetails) -> str:
    """After an engine death (§6.6), and for an I/O error with no file known."""
    if d.unchanged:
        return f"{NOT_SAVED} {UNCHANGED}"
    if d.changed is not None:  # the re-hash: the first file not at its pre bytes
        return f"Your change was not saved, and {_name(d.changed)} was changed by another program."
    return f"{NOT_SAVED} {NOT_CHECKED}"


def not_saved_text(d: ChangeNotSavedDetails) -> str:
    """`CHANGE_NOT_SAVED`, by its reason (§11's rows for the writer's refusals and for an engine death)."""
    did_not = "Finzytrack did not save your change."
    match d.reason:
        case "hard_linked":
            return (
                f"{did_not} {_name(d.path)} has a second name on disk (a hard link). Remove the extra link, or "
                "replace the file with a copy, then try again."
            )
        case "locked":
            return f"{did_not} {_name(d.path)} is locked or read-only. Allow changes to it, then try again."
        case "not_a_file":
            return f"{did_not} {_name(d.path)} is not a normal file."
        case "permission":
            if d.folder is not None and d.acl_entry is not None:
                return (
                    f"{did_not} The folder {d.folder} has a permission entry (ACL) that stops Finzytrack from saving "
                    f"safely: {d.acl_entry}. Remove that entry, then try again."
                )
            where = _name(d.path) if d.path is not None else (d.folder or "your ledger")
            return f"{did_not} Finzytrack has no permission to change {where}. Check its permissions, then try again."
        case "linked_folder":  # S0077 (BL-0193): recovery never reads a save record through such a link
            return (
                f"{did_not} The folder {d.folder or 'of its save records'} is a link to another place. Finzytrack "
                "keeps its save records and backups only in a real folder. Put the real folder back in its place, "
                "or remove the link, then try again."
            )
        case "no_drive_id":  # S0089 (BL-0188): its journal could never be undone (owner, 2026-11-08)
            return (
                f"{did_not} Finzytrack cannot identify the drive that holds {_name(d.path)}, so it could not undo "
                "the change if saving were interrupted. Keep your ledger on a drive Finzytrack can identify, such as "
                "your computer's own disk or a USB drive."
            )
        case "no_folder_id":  # S0089 rounds 1, 2: the top level of a FAT or exFAT drive, or a drive that keeps no
            # folder creation time at all (S0090, BL-0220 V2-1: the way out names both)
            return (
                f"{did_not} The folder that holds {_name(d.path)} has no creation time that Finzytrack can read, so "
                "Finzytrack cannot tell it from a folder at the same place on another drive. It could not undo the "
                f"change safely if saving were interrupted. If {_name(d.path)} is at the top level of a USB drive, "
                "make a folder on the drive, move your ledger into it, and open it from there. Otherwise keep your "
                "ledger on another drive, such as your computer's own disk."
            )
        case "case_twins":  # S0100 (BL-0162): the journal compares names under the widest fold (case, `ß`/`ss`, the
            # Kelvin sign, HFS+'s own fold), so "only in capital letters" is not always true (round 1, red team F5)
            return (
                f"{did_not} {_name(d.path)} and another file in this change have names this drive may treat as the "
                f"same (for example, they differ only in capital letters). Finzytrack cannot save both in one change. "
                f"Rename one of them, then try again. {_rehash(d)}"
            )
        case "folder_appeared":  # S0100 (BL-0187): maybe our own retried `mkdir` on a network share
            return (
                f"{did_not} A folder {d.folder or 'of this change'} appeared where Finzytrack was about to make one. "
                f"Try again. {_rehash(d)}"
            )
        case "app_folder_missing":  # S0100 (BL-0163 (a)), S0102 (BL-0244): the start makes it, or takes the new one
            folder = "Its data folder" if d.folder is None else f"Its data folder {d.folder}"
            return (
                f"{did_not} {folder} is missing or was replaced. Quit Finzytrack and open it again, then try again. "
                f"{_rehash(d)}"
            )
        case "unsafe_app_folder":  # S0125 round 1 (owner, 2026-11-17: "C1"): ecryptfs or an NFS home on Linux,
            # any volume macOS says lacks it, a Windows volume that refuses it. BL-0278 (owner, 2026-11-26:
            # "Generic text (Recommended)"): one cause for every OS, the folder is the journal folder (not "data
            # folder"), and no setting moves it, so no move instruction
            where = "in its own folder" if d.folder is None else f"in the folder {d.folder}"
            return (
                f"{did_not} Finzytrack keeps a record of each save {where}. That folder is on a drive where Finzytrack "
                "cannot rename a file without the risk of replacing another one, so it cannot save safely. Copy the "
                f"diagnostics and report a problem. {_rehash(d)}"
            )
        case "outside_folder" if d.path is None:  # S0117: a mark with the root or a relative path (a bug)
            return f"{did_not} The test copy's folder is not recorded correctly. Make the test copy again."
        case "outside_folder":  # S0117 round 1 (red team RT-F1): a file of the change outside the test copy
            return (
                f"{did_not} {_name(d.path)} is outside the test copy's folder, and Finzytrack saves this kind of "
                f"change only inside a test copy. {_rehash(d)}"
            )
        case "link_in_copy":  # S0117 round 3 (owner, 2026-11-14: "2"): a test copy's save follows no link
            return (
                f"{did_not} {_name(d.path)} in the test copy is a link to another place. Finzytrack saves this kind "
                f"of change only in a test copy without links. Make the test copy again. {_rehash(d)}"
            )
        case "copy_folder_moved":  # S0117 rounds 2 and 3: a link at the folder or above it, or another folder there
            folder = d.folder or "The test copy's folder"
            return (
                f"{did_not} {folder} was moved or replaced since the test copy was made, and Finzytrack saves this "
                f"kind of change only in that test copy. {_rehash(d)}"
            )
        case "copy_folder_missing":
            folder = d.folder or "The test copy's folder"
            return f"{did_not} {folder}, the test copy's folder, is missing. {_rehash(d)}"
        case "io_error" if d.path is not None:  # S0100 (BL-0163 (b)): the file named, the re-hash told apart
            return f"{NOT_SAVED} Finzytrack could not read or write {_name(d.path)}. {_rehash(d)}"
        case "in_use":  # S0148 (BL-0284): another program holds the file; the engine names it (`path`)
            return f"{NOT_SAVED} {in_use(_name(d.path))} {_rehash(d)}"
        case "cloud_unavailable":  # S0149 (BL-0283): only in the cloud, not downloaded; the engine names it (`path`)
            return f"{NOT_SAVED} {not_downloaded(_name(d.path))} {_rehash(d)}"
        case "io_error" if d.folder is not None:  # S0148 (BL-0306): a network share's folder refused its sync
            return (
                f"{NOT_SAVED} Finzytrack could not write to the folder {d.folder}. Check that its drive is connected "
                f"and that you can change files there, then try again. {_rehash(d)}"
            )
        case "read_back" if d.path is not None:
            return f"{NOT_SAVED} {_name(d.path)} did not read back as Finzytrack wrote it. {_rehash(d)}"
        case "name_not_utf8" if d.path is not None:
            return (
                f"{NOT_SAVED} The name of {_name(d.path)} is not valid text (UTF-8), so Finzytrack cannot record it. "
                f"{_rehash(d)}"
            )
        case "io_error" | "read_back" | "name_not_utf8" | "engine_stopped":
            return _unchanged_or_not(d)


def paused_text(d: WritesPausedDetails) -> str:
    """`WRITES_PAUSED`, by its reason (§11)."""
    match d.reason:
        case "unfinished_save":
            return REOPEN if d.file is None else reopen_after_created(d.file)
        case "earlier_unfinished":
            return EARLIER_UNFINISHED


def text(err: DomainError) -> str:
    """The full text of a save's error (§11), for every code a save job ends with."""
    match err.code:
        case ErrorCode.CHANGE_NOT_SAVED if isinstance(err.details, ChangeNotSavedDetails):
            return not_saved_text(err.details)
        case ErrorCode.WRITES_PAUSED if isinstance(err.details, WritesPausedDetails):
            return paused_text(err.details)
        case ErrorCode.LEDGER_EXTERNALLY_CHANGED:
            return LEDGER_CHANGED
        case _:
            spec = spec_for(err.code)
            return "Something went wrong inside Finzytrack." if spec is None else f"{spec.message} {spec.next_step}"


def large_file_tip(paths: Sequence[str]) -> str | None:
    """The preview's tip when the plan changes a file larger than 15 MB (`Previewed.large_files`; S0120, ADR-0008
    amendment of 2026-11-15): such a file is saved more slowly; splitting it keeps Finzytrack fast. None without one."""
    mb = f"{LARGE_FILE_BYTES // 1_000_000} MB"
    split = "into smaller files with Beancount's include directive"
    if not paths:
        return None
    if len(paths) == 1:
        return (
            f"The file {paths[0]} is larger than {mb}, so saving changes to it takes longer. "
            f"To keep Finzytrack fast, split it {split}."
        )
    return (
        f"These files are larger than {mb}, so saving changes to them takes longer: {', '.join(paths)}. "
        f"To keep Finzytrack fast, split them {split}."
    )
