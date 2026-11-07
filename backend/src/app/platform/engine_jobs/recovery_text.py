"""What the user reads about recover-all and its blocked screen (S0062; `design/specs/engine-jobs.md` §8.1
"Afterwards", §11's blocked-screen rows, §15; ADR-0063's text and its "Keep the files as they are now").

Pure: words from a `recovery.Report`'s parts and the workspace's display name (the registry's; the caller passes it).
The screen that shows them is M2's (BL-0092); the open that raises `RECOVERY_BLOCKED` is BL-0152/BL-0154's. Each
text is short and plain, for readers whose first language is not English. Files are named by their file name; a
folder, a kept copy and an entry the user must act on in Terminal by its full path.

**Actions** are named, not worded, so the screen can place them (`Action`): `retry`; `stay_or_back` ("Stay" or "Back
to <previous workspace>" at a switch, "Open another workspace" at boot, §9.1, §9.2); `keep` ("Keep the files as they
are now"); `show_both` and `delete_kept` (the kept copy; Delete only after Show both); `show_it` and `delete_created`
(a file at a created path; Delete only after Show it). The rule "only after the user saw it" is the screen's; the
engine refuses any delete whose bytes differ from the SHA-256 the report showed.
"""

from __future__ import annotations

import errno as errnos
import os
from dataclasses import dataclass
from typing import Final, Literal

from app.platform.engine import ActOutcome
from app.platform.engine_jobs.recovery import ENGINE_STOPPED, UNREADABLE, Blocking, NotRecovered

type Action = Literal["retry", "stay_or_back", "keep", "show_both", "delete_kept", "show_it", "delete_created"]

BASE: Final[tuple[Action, ...]] = ("retry", "stay_or_back", "keep")


@dataclass(frozen=True, slots=True)
class Screen:
    message: str
    actions: tuple[Action, ...]


def _name(path: str | None) -> str:
    return "A ledger file" if not path else path.rpartition("/")[2]


def _file_of(b: Blocking) -> str | None:
    return b.path or (b.journal.files[0].path if b.journal.files else None)


KEPT_COPY_CAUSES: Final = frozenset({"kept_copy", "displaced", "outside_save_in_temp"})
"""Causes whose screen is the kept-copy row: an outside save Finzytrack moved to a kept name (§6.4)."""
CREATED: Final = "created_path_taken"
PERMISSION: Final = "permission"


def undone_notice(workspace: str) -> str:
    """§11 "recovery done"."""
    return f"Finzytrack undid an interrupted save in {workspace}."


def _kept_names(b: Blocking) -> list[str]:
    path = b.path
    for i in b.inspection or ():
        if path is None or i.path == path:
            names = [k.path for k in i.kept]
            if names:
                return names
    return []


def _missing(b: Blocking) -> tuple[list[str], list[str]]:
    """The replaced files a stopped try found absent, by name: those whose backup the engine found holding the old
    version (`InspectedFile.backup_good`), then the others. On a drive without the swap a power loss can leave a file
    missing (§6.8); Keep leaves it missing, so the screen and the Keep confirmation name it (S0071 red team F2), and
    they promise the backup only where it was checked (round 2, R2-1: a try that stops `backup_bad` there). A file the
    save was creating, or one whose folder is not there or could not be read, is not counted."""
    absent = [i for i in b.inspection or () if not i.created and i.reachable and i.now is None and i.errno is None]
    return [_name(i.path) for i in absent if i.backup_good], [_name(i.path) for i in absent if not i.backup_good]


def _are(names: list[str]) -> str:
    """ "main.bean is", "a.bean and main.bean are", "a.bean, b.bean and main.bean are"."""
    if len(names) == 1:
        return f"{names[0]} is"
    return f"{', '.join(names[:-1])} and {names[-1]} are"


def _missing_now(b: Blocking) -> str:
    """§11's sentences for the stop screen when a replaced file is absent ("" when none is)."""
    good, other = _missing(b)
    text = ""
    if good:
        old = "its old version" if len(good) == 1 else "their old versions"
        text += f" {_are(good)} missing now. Finzytrack has {old} in its backup."
    if other:
        text += f" {_are(other)} missing now."
    return text


def _missing_kept(b: Blocking) -> str:
    """§11's sentences for Keep's confirmation when a replaced file is absent ("" when none is)."""
    good, other = _missing(b)
    text = ""
    if good:
        old = "Its old version is" if len(good) == 1 else "Their old versions are"
        text += f" {_are(good)} missing now and will stay missing. {old} only in Finzytrack's backup."
    if other:
        text += f" {_are(other)} missing now and will stay missing."
    return text


def _kept_copy(b: Blocking, workspace: str) -> Screen:
    """§11's kept-copy row (owner, 2026-10-29: "Offer to delete it (Recommended)"). Which of the two moved the outside
    save to its kept name is known only for a copy this try made (`displaced`, `outside_save_in_temp`: while undoing);
    one found when the try began may come from the save or from an earlier try, so it says both (S0062)."""
    file = _name(_file_of(b))
    when = (
        "undoing an unfinished change"
        if b.cause in ("displaced", "outside_save_in_temp")
        else "saving or undoing a change"
    )
    kept = _kept_names(b)
    copy = " and ".join(kept) if kept else f"a file next to {file} whose name contains .kept-"
    message = (
        f"Another program changed {file} while Finzytrack was {when} in {workspace}. "
        f"Finzytrack kept the other program's version as {copy}. Compare the two files. "
        "To undo Finzytrack's unfinished change, delete the kept copy and press Retry. "
        "To keep the files as they are now, choose Keep the files as they are now, "
        "then delete or move the kept copy before you use the ledger."
    )
    return Screen(message, (*BASE, "show_both", "delete_kept"))


def _created(b: Blocking) -> Screen:
    """§11's created-path row (owner, 2026-10-30: "Never remove it (Recommended)"; the owner's text)."""
    message = (
        f"Finzytrack was creating {_name(b.path)} when the save was interrupted. It cannot tell whether this file "
        "holds that unfinished change or another program's file, so it left it alone. Look at it; if you don't want "
        "it, delete it and press Retry, or choose Keep the files as they are now."
    )
    return Screen(message, ("show_it", "delete_created", *BASE))


def _permission(b: Blocking, workspace: str) -> Screen:
    """§11's ACL row for an undo (owner, 2026-10-30: "Allowlist (Recommended)", "Any subject (Recommended)") and §15's
    entry added during a save: the folder's entry while it is there; then the file that inherited it."""
    head = f"Finzytrack could not undo an unfinished save in {workspace}."
    where = b.entry or b.path
    if b.detail is not None and where is not None:
        return Screen(
            f"{head} The folder {where} has a permission entry (ACL) that stops Finzytrack from saving safely: "
            f"{b.detail}. Remove that entry, then press Retry.",
            BASE,
        )
    target = where or "the file"
    return Screen(
        f"{head} Your Mac does not allow Finzytrack to change {target}. If it has a permission entry (ACL), "
        f'remove it (Terminal: chmod -N "{target}"), then press Retry.',
        BASE,
    )


def _stopped(b: Blocking, workspace: str) -> Screen:
    """ADR-0063's text for a journal that could not be undone."""
    if b.cause == UNREADABLE:
        return Screen(
            "Finzytrack cannot read the record of an unfinished save, so it cannot tell which files it changed. "
            "No workspace opens until you press Retry or choose Keep the files as they are now.",
            BASE,
        )
    return Screen(
        f"{_name(_file_of(b))} has an unfinished save from {workspace} that could not be undone.",
        BASE,
    )


def system_answer(code: int) -> str:
    """The system's answer in its own words (`strerror`), as §11 asks: "says what the system answered"."""
    return os.strerror(code)


def _not_undone(b: Blocking, workspace: str) -> Screen:
    """§11's row for `cancelled`, `failed` and `not_tried`; for `failed` with an error, the file, the entry that
    failed when it is not the file, and the system's answer (S0061 rounds 5-7), with the read-only-drive wording for
    `EROFS` (red team r8 F1) and the ACL way out for a permission error (§15)."""
    file = _name(_file_of(b))
    message = f"{file} has an unfinished save from {workspace} that Finzytrack has not undone yet."
    if b.reason == "failed" and b.cause == ENGINE_STOPPED:
        message += " The ledger engine stopped while it was undoing it. Press Retry to try again."
    elif b.reason == "failed" and b.errno is not None:
        where = b.entry or b.path or file
        if b.errno == errnos.EROFS:
            message += (
                f" The drive with {where} is read-only, so Finzytrack cannot undo the change there. "
                "Make the drive writable and press Retry, or choose Keep the files as they are now."
            )
        else:
            message += f" Finzytrack could not read or change {where}: {system_answer(b.errno)}."
            if b.errno in (errnos.EACCES, errnos.EPERM):
                message += f' If it has a permission entry (ACL), remove it (Terminal: chmod -N "{where}").'
            message += " Then press Retry."
    elif b.reason == "not_tried":
        message += " The ledger engine is not working. Press Retry when it works again."
    elif b.reason == "cancelled":
        message += " You cancelled it. Press Retry to undo it."
    elif b.reason == "failed":
        message += " Press Retry to try again."  # a step the engine refused (red team r1 O3)
    return Screen(message, BASE)


def blocked_screen(b: Blocking, workspace: str) -> Screen:
    """The blocked screen for one blocking journal (§11), `workspace` the display name of the journal's own
    workspace. A stopped try that found a replaced file absent says so after its row's text (`_missing_now`)."""
    if b.reason != "stopped":
        return _not_undone(b, workspace)
    if b.cause in KEPT_COPY_CAUSES:
        screen = _kept_copy(b, workspace)
    elif b.cause == CREATED:
        screen = _created(b)
    elif b.cause == PERMISSION:
        screen = _permission(b, workspace)
    else:
        screen = _stopped(b, workspace)
    return Screen(screen.message + _missing_now(b), screen.actions)


def not_recovered_screen(n: NotRecovered) -> Screen:
    """Every open is blocked: a journal folder could not be listed, or recover-all never listed (fail closed)."""
    if n.folder is not None and n.linked:  # S0077 (BL-0193): the way out is the user's, so the screen names it
        answer = f" ({system_answer(n.errno)})" if n.errno is not None else ""
        return Screen(
            f"{n.folder} is a link to another place{answer}. Finzytrack does not use its save records through a link, "
            "and it may hold an unfinished save. No workspace opens until it is a real folder again. "
            "Put the real folder back in its place, then press Retry.",
            ("retry",),
        )
    if n.folder is not None:
        answer = f": {system_answer(n.errno)}" if n.errno is not None else ""
        return Screen(
            f"Finzytrack could not read its list of unfinished saves in {n.folder}{answer}. "
            "No workspace opens until it can. Press Retry.",
            ("retry",),
        )
    return Screen(
        "Finzytrack could not check for unfinished saves, so no workspace opens yet. Press Retry.",
        ("retry",),
    )


# -- confirmations ------------------------------------------------------------------------------------------------


def keep_confirmation(b: Blocking, workspace: str) -> str:
    """§8.1 "Afterwards", ADR-0063: it lists the files it releases; for `cancelled`, `failed` and `not_tried` it says
    Retry may still undo the save (review 7 L6); a kept copy stays and must be deleted or moved first (owner,
    2026-10-29); a replaced file a stopped try found absent stays missing, and its old version is only in the backup
    when the engine found it there (`_missing_kept`; S0071 red team F2, round 2 R2-1)."""
    files = ", ".join(_name(f.path) for f in b.journal.files) or "the files of this save"
    text = (
        f"Keep the files as they are now? Finzytrack will stop trying to undo the unfinished save in {workspace} "
        f"and will not change these files: {files}."
    )
    if b.reason in ("cancelled", "failed", "not_tried"):
        text += " Finzytrack has not tried, or not finished, undoing this save. Retry may still undo it."
    text += _missing_kept(b)
    kept = _kept_names(b)
    if kept:
        text += f" The kept copy {' and '.join(kept)} stays. Delete or move it before you use the ledger."
    return text


def delete_kept_confirmation(file: str, kept: str) -> str:
    return f"Delete {kept}? It holds the other program's version of {_name(file)}. This cannot be undone."


def delete_created_confirmation(file: str) -> str:
    return f"Delete {file}? This cannot be undone."


ACTION_REFUSED: Final[tuple[tuple[str, str], ...]] = (
    ("changed", "The file changed after you looked at it, so Finzytrack did not delete it. Look at it again."),
    ("absent", "The file is no longer there."),
    ("not_open", "This unfinished save was already resolved."),
    ("unlisted", "Finzytrack could not read the folder of this unfinished save, so it did not do this. Press Retry."),
    ("path_moved", "The folder moved. Press Retry."),
    ("permission", "Your Mac does not allow Finzytrack to change this folder."),
)
"""What a refused action tells (`ActOutcome.reason`); any other reason gets `ACTION_REFUSED_OTHER`."""
ACTION_REFUSED_OTHER: Final = "Finzytrack did not do this, and changed nothing. Press Retry."
ACTION_FAILED: Final = "Finzytrack could not do this. Press Retry."


def action_text(outcome: ActOutcome) -> str | None:
    """None for an action that was done."""
    if outcome.outcome == "done":
        return None
    if outcome.outcome == "failed":
        return ACTION_FAILED
    return next((t for r, t in ACTION_REFUSED if r == outcome.reason), ACTION_REFUSED_OTHER)


# -- for the save (BL-0151 slice 4) -----------------------------------------------------------------------------

REOPEN: Final = (
    "Finzytrack could not finish your change. Reopen the workspace so Finzytrack can check your files and undo the "
    "unfinished change."
)
"""§11 "unfinished save in this session" (`WRITES_PAUSED {unfinished_save}`)."""


def reopen_after_created(file: str) -> str:
    """§11's Reopen text when the save's rollback stopped at a path it was creating (`created_path_taken`): Reopen
    will ask about that file, so the text does not promise to undo the change (red team r8 overflow 1)."""
    return (
        "Finzytrack could not finish your change. Reopen the workspace so Finzytrack can check your files. "
        f"It will ask you about {_name(file)}, which it was creating."
    )
