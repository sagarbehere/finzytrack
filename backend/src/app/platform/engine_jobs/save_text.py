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


def _unchanged_or_not(d: ChangeNotSavedDetails) -> str:
    if d.unchanged:
        return f"{NOT_SAVED} {UNCHANGED}"
    if d.reason == "engine_stopped" and d.path is not None:  # §6.6's re-hash: the first file not at its pre bytes
        return f"Your change was not saved, and {_name(d.path)} was changed by another program."
    return f"{NOT_SAVED} Finzytrack could not check whether your ledger changed."


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
        case "io_error" | "read_back" | "name_not_utf8" | "engine_stopped":
            return _unchanged_or_not(d)


def paused_text(d: WritesPausedDetails) -> str:
    """`WRITES_PAUSED`, by its reason (§11)."""
    match d.reason:
        case "unfinished_save":
            return REOPEN if d.file is None else reopen_after_created(d.file)


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
