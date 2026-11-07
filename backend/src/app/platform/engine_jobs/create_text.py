"""What the user reads when a create does not end "created" (`design/specs/engine-jobs.md` §8.2, §11's create rows;
S0080). Plain text for non-native readers; the screens are BL-0092's and M3's.

A text is "Not created.", then why (one sentence, when there is one), then what the job left in the folder
(`NotCreated.left`; owner, 2026-11-07, the middle path): the job removes what it made itself, so usually nothing is left
and nothing more is said; entries it kept because it could not be sure they are only its own are named, with the advice
to check them (after an engine death, a folder with nothing of the job's in it is only said to be one Finzytrack may
have made); when the folder was not cleaned up or listed (a Cancel ended the engine, no engine could read it, an
error), the text says the folder may hold files of an unfinished new workspace and to check it first. It never tells the
user to delete anything. Two refusals have whole texts of their own: a folder that is not empty (§11) and a
`.finzytrack` another program put there at the same moment (§8.2 step 7)."""

from __future__ import annotations

from typing import Final

from app.platform.engine_jobs import recovery_text
from app.platform.engine_jobs.create import NotCreated

NOT_CREATED: Final = "Not created."
NOT_EMPTY: Final = "This folder is not empty. Choose an empty folder or a new name."
SHOWN: Final = 5
"""How many kept names a text lists before "and N more"."""
REPORT: Final = "This is a fault in Finzytrack; please report it."


def name_of(path: str | None) -> str:
    return "A file" if not path else path.rpartition("/")[2]


UNFINISHED: Final = "an unfinished new workspace"
"""What a folder a create did not finish may hold (a copy says "an unfinished copy": `copy_text`)."""


def unknown_folder(folder: str, unfinished: str = UNFINISHED) -> str:
    """The folder was not listed: it may hold the job's files, or another program's."""
    return f"The folder {folder} may hold {unfinished}. Check it before you delete anything."


def manifest_taken(folder: str, not_done: str = NOT_CREATED) -> str:
    return (
        f"{not_done} Another program put a Finzytrack workspace file into {folder} at the same moment. Check the "
        "folder before you use it."
    )


def _why(v: NotCreated) -> str | None:
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
            return "Finzytrack could not check the new ledger."
        case "load_errors":
            return f"The new ledger has errors. {REPORT}"
        case "load_outside":
            return f"The new ledger reads a file outside its folder ({v.path}). {REPORT}"
        case "path_moved":
            return "The folder was moved or renamed while Finzytrack was writing it."
        case "io_error":
            answer = f" The system answered: {recovery_text.system_answer(v.errno)}." if v.errno is not None else ""
            return f"Finzytrack could not write {name_of(v.path)}.{answer}"
        case "stopped":
            return "Finzytrack stopped before it finished."
        case "not_empty" | "manifest_taken":  # pragma: no cover (whole texts of their own, `text`)
            return None


TARGET: Final = "."
"""In `NotCreated.left`, the folder itself (`ledger_engine.ledger.create.TARGET`)."""


def maybe_made(folder: str) -> str:
    """After an engine death, only the folder itself is named (nothing at the job's names is in it): the job may not
    have made it (the user's own empty folder), so the text never says Finzytrack left it (BL-0199)."""
    return f"Finzytrack may have made the folder {folder}. Check it, and remove it if you do not need it."


def kept(folder: str, left: tuple[str, ...], *, after_death: bool = False) -> str:
    """The entries the job left because it could not be sure they are only its own, the first `SHOWN` by name; the
    folder itself when it is all that is named (after a death: `maybe_made`)."""
    if left == (TARGET,):
        if after_death:
            return maybe_made(folder)
        return (
            f"Finzytrack left the folder {folder} because it could not be sure everything in it is its own. Check it, "
            "and remove it if you do not need it."
        )
    shown = ", ".join(left[:SHOWN])
    more = f" and {len(left) - SHOWN} more" if len(left) > SHOWN else ""
    return (
        f"Finzytrack left these in {folder} because it could not be sure they are only its own: {shown}{more}. "
        "Check them, and remove any you do not need."
    )


def folder_text(
    folder: str, left: tuple[str, ...] | None, unfinished: str = UNFINISHED, *, after_death: bool = False
) -> str | None:
    """What the job left in the folder (`NotCreated.left`), or None when it left nothing of its own. `after_death`:
    `left` is the outcome read's after an engine death (reason `stopped`, the sidecar's own), not the job's cleanup."""
    if left is None:
        return unknown_folder(folder, unfinished)
    return kept(folder, left, after_death=after_death) if left else None


STOPPED: Final = "stopped"
"""The sidecar's reason for a job whose engine stopped before its answer: its `left` is the outcome read's."""


def _folder(v: NotCreated) -> str | None:
    return folder_text(v.folder, v.left, after_death=v.reason == STOPPED)


def text(v: NotCreated) -> str:
    """The create's §11 text for `v`."""
    if v.reason == "not_empty":
        return NOT_EMPTY
    if v.reason == "manifest_taken":
        left = _folder(v)  # what the job kept is named here too (r3 F2, V4)
        return manifest_taken(v.folder) if left is None else f"{manifest_taken(v.folder)} {left}"
    return " ".join(part for part in (NOT_CREATED, _why(v), _folder(v)) if part)


def cancelled_text(folder: str) -> str:
    """A create the user cancelled after it started: its engine was ended, so nothing listed the folder (§8.2)."""
    return f"{NOT_CREATED} {unknown_folder(folder)}"
