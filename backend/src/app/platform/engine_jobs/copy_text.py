"""What the user reads when a copy does not end "copied" (`design/specs/engine-jobs.md` §8.2, §11's copy rows; S0081).
Plain text for non-native readers; the screens are BL-0092's and M3's.

The create's rules and texts, with "copy" for "new workspace" (owner, 2026-11-07: "`msg:create.left` style, "copy"
for "new workspace""): "Not copied.", then why, then what the job left in the folder (`create_text.folder_text`, the
same words: the cleanup is the create's). The copy's own reasons have texts of their own; a held legacy lock and a
folder that is not empty have whole texts."""

from __future__ import annotations

from typing import Final

from app.platform.engine_jobs import create_text, recovery_text
from app.platform.engine_jobs.copy import NotCopied
from app.platform.engine_jobs.create_text import REPORT, name_of

NOT_COPIED: Final = "Not copied."
UNFINISHED: Final = "an unfinished copy"
LEGACY_SAVING: Final = "The old Finzytrack app is saving. Quit the old Finzytrack first, then try again."
"""ADR-0062 decision 2's words."""
CHANGED: Final = "Your ledger changed while Finzytrack was copying it. Try again."
"""The owner's words (2026-11-07, decision 2)."""


def _system(errno: int | None) -> str:
    return f" The system answered: {recovery_text.system_answer(errno)}." if errno is not None else ""


def _differs(v: NotCopied) -> str:
    where = {
        "options": " (its options differ)",
        "error": " (its Beancount errors differ)",
        "entry": "",
    }.get(v.detail or "", f" (first at {name_of(v.path)}, line {v.detail})" if v.path else "")
    return f"The copy did not load the same as your ledger{where}."


def _blocked(v: NotCopied) -> str:
    if v.detail in {"unreadable", "unlisted"}:
        return "Finzytrack could not read its record of an unfinished save. Restart Finzytrack, then try again."
    return (
        f"A save to {name_of(v.path)} did not finish, and Finzytrack has not undone it yet. Restart Finzytrack so it "
        "can finish it, then try again."
    )


def _why(v: NotCopied) -> str | None:
    match v.reason:
        case "not_a_folder":
            return f"{v.folder} is a file, not a folder. Choose an empty folder or a new name."
        case "no_parent":
            return f"The folder to put {name_of(v.folder)} in does not exist."
        case "permission":
            return f"The system does not let Finzytrack write in {v.path or v.folder}."
        case "taken":
            return f"Another program put {name_of(v.path)} into the folder at the same moment."
        case "read_back":
            return f"Another program changed {name_of(v.path)} while Finzytrack was writing it."
        case "load_failed":
            return "Finzytrack could not check the copy."
        case "load_outside":
            return f"The copy reads a file outside its folder ({v.path}). {REPORT}"
        case "path_moved":
            return "The folder was moved or renamed while Finzytrack was writing it."
        case "io_error":
            return f"Finzytrack could not write {name_of(v.path)}.{_system(v.errno)}"
        case "stopped":
            return "Finzytrack stopped before it finished."
        case "recovery_blocked":
            return _blocked(v)
        case "source_missing":
            return f"{name_of(v.path)} does not exist."
        case "source_unreadable":
            return f"Finzytrack could not read {name_of(v.path)}.{_system(v.errno)}"
        case "source_failed":
            return "Finzytrack could not load your ledger."
        case "encrypted":
            return f"{name_of(v.path)} is encrypted. This version of Finzytrack does not read encrypted ledger files."
        case "source_changed":
            return CHANGED
        case "cannot_rewrite":
            line = f" (line {v.detail})" if v.detail else ""
            return f"Finzytrack could not change a line that names another file in {name_of(v.path)}{line}."
        case "cannot_place":
            if v.detail == "reserved_name":
                return "Your ledger uses a folder named .finzytrack, a name Finzytrack keeps for itself."
            if v.detail == "inside_documents":
                return "The new folder is inside your ledger's documents folder. Choose another place."
            if v.detail == "inside_legacy":
                return "The new folder is inside the old Finzytrack app's folder. Choose another place."
            return f"{name_of(v.path)} and another of your files would get the same name in the new folder."
        case "load_differs":
            return _differs(v)
        case "plugin_modules":
            return (
                f"Your ledger uses a plugin of its own ({name_of(v.path)}). This version of Finzytrack cannot copy a "
                "ledger's own plugins."
            )
        case "not_empty" | "manifest_taken" | "legacy_saving":  # pragma: no cover (whole texts of their own, `text`)
            return None


def text(v: NotCopied) -> str:
    """The copy's §11 text for `v`."""
    left = create_text.folder_text(v.folder, v.left, UNFINISHED)
    match v.reason:
        case "not_empty":
            return create_text.NOT_EMPTY
        case "manifest_taken":
            head = create_text.manifest_taken(v.folder, NOT_COPIED)
            return head if left is None else f"{head} {left}"
        case "legacy_saving":
            return " ".join(part for part in (NOT_COPIED, LEGACY_SAVING, left) if part)
        case _:
            return " ".join(part for part in (NOT_COPIED, _why(v), left) if part)


def cancelled_text(folder: str) -> str:
    """A copy the user cancelled after it started: its engine was ended, so nothing listed the folder (§8.2)."""
    return f"{NOT_COPIED} {create_text.unknown_folder(folder, UNFINISHED)}"
