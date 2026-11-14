"""What the user reads when a save does not end "saved" (`design/specs/engine-jobs.md` §11's save rows; S0063). Plain
text for non-native readers; the screens are BL-0092's.

"Your ledger is unchanged" is said only when every file the save would write was hashed again and equals its bytes
before the save (§11's rule; `ChangeNotSavedDetails.unchanged`); otherwise the text says which file changed, or that
Finzytrack could not check. After a rollback stopped at a file the save was creating, the Reopen text does not promise
an undo (`recovery_text.reopen_after_created`; S0061 red team r8 overflow 1).
"""

from __future__ import annotations

from typing import Final

from app.domain.errors import DomainError
from app.platform.engine_jobs.recovery_text import REOPEN, reopen_after_created
from app.platform.errors import ChangeNotSavedDetails, ErrorCode, WritesPausedDetails, spec_for

NOT_SAVED: Final = "Your change was not saved."
UNCHANGED: Final = "Your ledger is unchanged."
LEDGER_CHANGED: Final = (
    "Your ledger changed after this preview, so Finzytrack did not save your change. Review it again."
)
BEST_EFFORT_DRIVE: Final = (
    "This drive (for example a USB stick or network share) can't replace files in one safe step. Finzytrack still "
    "keeps a backup of every save, but if another program edits the same file at the very moment you save, its "
    "change can be lost. For full protection, keep your ledger on your computer's own disk."
)
"""§11's notice when a workspace whose ledger is on a drive without the swap opens (ADR-0068 D2, the owner's words;
§6.8). The workspace open shows it (BL-0166); this is the one place its words live."""


def _name(path: str | None) -> str:
    return "A ledger file" if not path else path.rpartition("/")[2]


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
            return f"{did_not} {_name(d.path)} is locked. Unlock it (Finder: Get Info), then try again."
        case "not_a_file":
            return f"{did_not} {_name(d.path)} is not a normal file."
        case "permission":
            if d.folder is not None and d.acl_entry is not None:
                return (
                    f"{did_not} The folder {d.folder} has a permission entry (ACL) that stops Finzytrack from saving "
                    f"safely: {d.acl_entry}. Remove that entry, then try again."
                )
            where = _name(d.path) if d.path is not None else (d.folder or "your ledger")
            return (
                f"{did_not} Your Mac does not allow changes to {where}. Check its permissions (Finder: Get Info), "
                "then try again."
            )
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
                "your Mac's own disk or a USB drive."
            )
        case "no_folder_id":  # S0089 rounds 1, 2: the top level of a FAT or exFAT drive, or a drive that keeps no
            # folder creation time at all (S0090, BL-0220 V2-1: the way out names both)
            return (
                f"{did_not} The folder that holds {_name(d.path)} has no creation time that Finzytrack can read, so "
                "Finzytrack cannot tell it from a folder at the same place on another drive. It could not undo the "
                f"change safely if saving were interrupted. If {_name(d.path)} is at the top level of a USB drive, "
                "make a folder on the drive, move your ledger into it, and open it from there. Otherwise keep your "
                "ledger on another drive, such as your Mac's own disk."
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
        case "outside_folder":  # S0117 round 1 (red team RT-F1): a link led a file of the test copy out of it
            folder = d.folder or "the test copy's folder"
            return (
                f"{did_not} {_name(d.path)} now leads to a place outside {folder}, and Finzytrack saves this kind "
                f"of change only inside a test copy. Check the test copy for links to other folders. {_rehash(d)}"
            )
        case "io_error" if d.path is not None:  # S0100 (BL-0163 (b)): the file named, the re-hash told apart
            return f"{NOT_SAVED} Finzytrack could not read or write {_name(d.path)}. {_rehash(d)}"
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
