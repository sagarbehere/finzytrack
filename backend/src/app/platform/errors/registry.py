"""The error code registry: the single source for codes (ARCHITECTURE.md §6.1, §6.5; EP-13, EP-27).

Each code declares its class, whether a retry can succeed, and three short parts in plain English for non-native
readers: what happened (`message`), whether the user's data is safe (`data_safe`: say so only when it is true, §6.5)
and what to do next (`next_step`); plus the type of its `details`, a frozen dataclass or none. The edge
(`app.api.errors`) turns the class into an HTTP status and exports the registry into the OpenAPI contract as a
union of per-code error shapes (§6.2), from which the frontend's types are generated.

The registry is built by `specs()` on each call, never held in a module-level container (INV-11; the state scan
accepts only immutable values). A code joins with the slice that first raises it, with a test through the API
tagged `@pytest.mark.error_code` (§6.7, `tests/api/test_error_registry.py`).
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from app.domain.errors import (
    Conflict,
    DomainError,
    ErrorClass,
    Forbidden,
    Internal,
    Invalid,
    Issue,
    NotFound,
    Unavailable,
)

type NotReadyPhase = Literal["WARMING"]
"""Why the open workspace cannot serve a request yet (ADR-0019: the open workspace's phases short of serving; a
workspace in a failed phase is never open, INV-29). Never `RECOVERING`: every unfinished save is recovered before
any workspace opens, and a failed in-session rollback pauses writes instead (ADR-0063 decision 2, ADR-0064; S0047)."""

CODE = re.compile(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*")


class ErrorCode(StrEnum):
    VALIDATION_FAILED = "VALIDATION_FAILED"
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
    WORKSPACE_UNKNOWN = "WORKSPACE_UNKNOWN"
    WORKSPACE_NOT_OPEN = "WORKSPACE_NOT_OPEN"
    WORKSPACE_NOT_READY = "WORKSPACE_NOT_READY"
    ENGINE_QUEUE_FULL = "ENGINE_QUEUE_FULL"
    JOB_CANCELLED = "JOB_CANCELLED"
    ENGINE_STOPPED = "ENGINE_STOPPED"
    ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"
    CHANGE_OUTCOME_UNKNOWN = "CHANGE_OUTCOME_UNKNOWN"
    LEDGER_EXTERNALLY_CHANGED = "LEDGER_EXTERNALLY_CHANGED"
    WRITES_PAUSED = "WRITES_PAUSED"
    LEGACY_APP_SAVING = "LEGACY_APP_SAVING"
    CHANGE_NOT_SAVED = "CHANGE_NOT_SAVED"
    INTERNAL = "INTERNAL"


@dataclass(frozen=True, slots=True)
class WorkspaceNotOpenDetails:
    """The workspace named is registered but not open (ADR-0063). `open_workspace`: the workspace a request may name
    now, or, while a switch runs, the one being opened; none when no workspace is open. `switching`: a switch is
    running."""

    open_workspace: str | None
    switching: bool


@dataclass(frozen=True, slots=True)
class WorkspaceNotReadyDetails:
    """The workspace named is the open one, but it cannot serve yet (ADR-0018)."""

    phase: NotReadyPhase


@dataclass(frozen=True, slots=True)
class JobCancelledDetails:
    """The job ended by a Cancel (engine-jobs §4, §11). `cannot_stop_ahead`: the user's own waiting request was
    cancelled, and the earlier ledger work it waited behind cannot be stopped from here (the UI adds §4's sentence
    and offers Quit anyway)."""

    cannot_stop_ahead: bool


@dataclass(frozen=True, slots=True)
class EngineStoppedDetails:
    """The engine stopped while it worked on this job (engine-jobs §5.3, §11). `killed_by_system` (failure-modes E4)
    needs the end report's cause, which BL-0094 decides; until then every stop is `crashed`."""

    reason: Literal["crashed", "killed_by_system"]


@dataclass(frozen=True, slots=True)
class EngineUnavailableDetails:
    """No engine could be started for this job (engine-jobs §5.3, §11). `stopped_often`: the restart bound is
    reached (failure-modes E1); `incompatible`: the engine speaks another protocol, a damaged install (E6: reinstall);
    `cannot_start`: the wait for the engine lock failed (E6); `closing`: Finzytrack is closing."""

    reason: Literal["stopped_often", "incompatible", "cannot_start", "closing"]


type PausedReason = Literal["unfinished_save", "check_pending", "check_failed"]


@dataclass(frozen=True, slots=True)
class WritesPausedDetails:
    """This workspace's saves are paused (engine-jobs §6.1 steps 1 and 4, §6.4, §6.6, §11; S0063). `unfinished_save`:
    an unfinished save's journal is open (Reopen recovers it); `check_pending`: the check of the last save (R4) is owed
    and could not run first; `check_failed`: that check found a difference, or could not run three times (Resume
    saving). `file`: for `unfinished_save`, the file the save was creating that the undo will ask about (its Reopen
    text then promises no undo: S0061 red team r8 overflow 1), else None. For `check_failed` (S0066): `record`, the
    paused save's record, which Resume saving names; `backup`, that save's backup folder ("Your backup is here"; None
    when its record cannot be read); `could_not_check`, the check could not run three times rather than finding a
    difference."""

    reason: PausedReason
    file: str | None
    record: str | None
    backup: str | None
    could_not_check: bool


type NotSavedReason = Literal[
    "hard_linked",  # a file has a second name on disk (ADR-0013 amendment of 2026-10-29)
    "locked",  # a file is locked (`uchg`)
    "not_a_file",  # a ledger path is a folder, a FIFO, a device
    "permission",  # the system would not allow the save: a mode, an ACL, unreadable extended attributes
    "io_error",  # the disk answered an error (a full disk, an I/O error)
    "read_back",  # a written file did not read back as written (the save was rolled back)
    "name_not_utf8",  # a path is not valid UTF-8
    "engine_stopped",  # the save's engine stopped, and the disk shows the change was not saved (§6.6)
]


@dataclass(frozen=True, slots=True)
class ChangeNotSavedDetails:
    """A save that did not happen, and why (engine-jobs §6.1 step 8's refusals, §6.4, §6.6, §11; S0063). `path`: the
    file it is about; `folder` and `acl_entry`: for `permission`, the folder and the ACL entry (in `chmod`'s words, no
    subject) that stop it; `unchanged`: every file the save would write was hashed again and equals its bytes before
    the save (only then may the user read "Your ledger is unchanged"). The UI words each reason
    (`app.platform.engine_jobs.save_text.not_saved_text`)."""

    reason: NotSavedReason
    path: str | None
    folder: str | None
    acl_entry: str | None
    unchanged: bool


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    code: str
    error_class: ErrorClass
    retryable: bool
    message: str
    data_safe: bool
    next_step: str
    details: type | None


def specs() -> tuple[ErrorSpec, ...]:
    return (
        ErrorSpec(
            ErrorCode.VALIDATION_FAILED,
            ErrorClass.INVALID,
            retryable=False,
            message="Finzytrack could not accept this request because some values are not valid.",
            data_safe=True,
            next_step="Correct the values shown and try again.",
            details=None,
        ),
        ErrorSpec(
            ErrorCode.ROUTE_NOT_FOUND,
            ErrorClass.NOT_FOUND,
            retryable=False,
            message="This address does not exist in this version of Finzytrack.",
            data_safe=True,
            next_step="Reload the window. If it happens again, report a problem.",
            details=None,
        ),
        ErrorSpec(
            ErrorCode.WORKSPACE_UNKNOWN,
            ErrorClass.NOT_FOUND,
            retryable=False,
            message="Finzytrack does not know this workspace.",
            data_safe=True,
            next_step="Open a workspace from the workspace list.",
            details=None,
        ),
        ErrorSpec(
            ErrorCode.WORKSPACE_NOT_OPEN,
            ErrorClass.CONFLICT,
            retryable=False,
            message="This workspace is not open. Finzytrack may have switched to another workspace in another window.",
            data_safe=True,
            next_step="Switch back to this workspace, or continue in the open one.",
            details=WorkspaceNotOpenDetails,
        ),
        ErrorSpec(
            ErrorCode.WORKSPACE_NOT_READY,
            ErrorClass.CONFLICT,
            retryable=True,
            message="This workspace is still getting ready.",
            data_safe=True,
            next_step="Wait a moment and try again.",
            details=WorkspaceNotReadyDetails,
        ),
        ErrorSpec(
            ErrorCode.ENGINE_QUEUE_FULL,
            ErrorClass.UNAVAILABLE,
            retryable=True,
            message="Finzytrack is busy with other ledger work.",
            data_safe=True,
            next_step="Please try again in a moment.",
            details=None,
        ),
        ErrorSpec(
            ErrorCode.JOB_CANCELLED,
            ErrorClass.CONFLICT,
            retryable=False,
            message="Your request is cancelled.",
            data_safe=False,  # a cancelled save says "unchanged" only after its files are hashed again (§11)
            next_step="Start it again when you need it.",
            details=JobCancelledDetails,
        ),
        ErrorSpec(
            ErrorCode.ENGINE_STOPPED,
            ErrorClass.UNAVAILABLE,
            retryable=True,
            message="The ledger engine stopped while working on this.",
            data_safe=True,  # nothing was written: a read job, or a save whose engine stopped before its commit point
            # with no engine left to read its outcome (§4; S0063); past the gate that is CHANGE_OUTCOME_UNKNOWN
            next_step="Try again. If it happens again, copy the diagnostics and report a problem.",
            details=EngineStoppedDetails,
        ),
        ErrorSpec(
            ErrorCode.ENGINE_UNAVAILABLE,
            ErrorClass.UNAVAILABLE,
            retryable=True,
            message="The ledger engine is not working right now, so Finzytrack did not do this.",
            data_safe=True,  # nothing ran; the UI words each reason (`app.platform.engine_jobs.text.unavailable_text`)
            next_step="Try again in a moment. If it happens again, copy the diagnostics and report a problem.",
            details=EngineUnavailableDetails,
        ),
        ErrorSpec(
            ErrorCode.CHANGE_OUTCOME_UNKNOWN,
            ErrorClass.UNAVAILABLE,
            retryable=False,  # the change may have landed: a retry could make it twice
            message="The ledger engine stopped while it was saving your change, so Finzytrack does not know yet "
            "whether it was saved.",
            data_safe=False,
            next_step="Do not make the change again until you have checked whether it is in your ledger.",
            details=None,  # only when no engine could read the outcome from disk (§6.6; S0063)
        ),
        ErrorSpec(
            ErrorCode.LEDGER_EXTERNALLY_CHANGED,
            ErrorClass.CONFLICT,
            retryable=False,  # the change must be reviewed against the ledger as it is now (ADR-0026: re-plan)
            message="Your ledger changed after this preview, so Finzytrack did not save your change.",
            data_safe=True,  # nothing of this change was written; the other change is the other program's
            next_step="Review it again.",
            details=None,
        ),
        ErrorSpec(
            ErrorCode.WRITES_PAUSED,
            ErrorClass.CONFLICT,
            retryable=False,
            message="Saving is paused in this workspace, so Finzytrack did not save your change.",
            data_safe=False,  # an unfinished save may be half-applied until Reopen undoes it (§6.6)
            next_step="Follow the steps shown to resume saving.",
            details=WritesPausedDetails,
        ),
        ErrorSpec(
            ErrorCode.LEGACY_APP_SAVING,
            ErrorClass.CONFLICT,
            retryable=True,
            message="The old Finzytrack app is saving.",
            data_safe=True,  # refused before anything was written (ADR-0062)
            next_step="Quit the old Finzytrack first, then try again.",
            details=None,
        ),
        ErrorSpec(
            ErrorCode.CHANGE_NOT_SAVED,
            ErrorClass.CONFLICT,
            retryable=False,  # most reasons need the user to change something first; the text says what
            message="Finzytrack did not save your change.",
            data_safe=False,  # "unchanged" is said only after a re-hash (`details.unchanged`, §11)
            next_step="Follow the steps shown, then try again.",
            details=ChangeNotSavedDetails,
        ),
        ErrorSpec(
            ErrorCode.INTERNAL,
            ErrorClass.INTERNAL,
            retryable=False,
            message="Something went wrong inside Finzytrack.",
            data_safe=False,  # the edge cannot know where it happened (§6.5)
            next_step="Copy the diagnostics and report a problem.",
            details=None,
        ),
    )


def spec_for(code: str) -> ErrorSpec | None:
    return next((s for s in specs() if s.code == code), None)


def _class_of(error_class: ErrorClass) -> type[DomainError]:
    match error_class:
        case ErrorClass.INVALID:
            return Invalid
        case ErrorClass.NOT_FOUND:
            return NotFound
        case ErrorClass.CONFLICT:
            return Conflict
        case ErrorClass.FORBIDDEN:
            return Forbidden
        case ErrorClass.UNAVAILABLE:
            return Unavailable
        case ErrorClass.INTERNAL:
            return Internal


def error(code: ErrorCode, details: object | None = None, issues: tuple[Issue, ...] = ()) -> DomainError:
    """The `DomainError` of a registry code, of the class the registry gives it, with details of its declared type.

    A mismatch is a programming error and raises `TypeError` here, at the raise site, not at the edge."""
    spec = spec_for(code)
    if spec is None:  # ErrorCode and the registry are held equal by a test
        raise TypeError(code)
    if not details_fit(spec, details):
        msg = f"{code}: details must be {spec.details.__name__ if spec.details else 'None'}"
        raise TypeError(msg)
    return _class_of(spec.error_class)(spec.code, details, issues)


def details_fit(spec: ErrorSpec, details: object | None) -> bool:
    if spec.details is None:
        return details is None
    return type(details) is spec.details


def registry_problems(registry: tuple[ErrorSpec, ...]) -> list[str]:
    """Every way a registry can break §6.1 and §6.5, as messages; empty when it holds."""
    out: list[str] = []
    seen: set[str] = set()
    for s in registry:
        if s.code in seen:
            out.append(f"{s.code}: listed twice")
        seen.add(s.code)
        if not CODE.fullmatch(s.code):
            out.append(f"{s.code!r}: a code is upper-case words joined by underscores")
        if not isinstance(s.error_class, ErrorClass):  # pyright: ignore[reportUnnecessaryIsInstance] (a registry is data)
            out.append(f"{s.code}: unknown class {s.error_class!r}")
        for part in ("message", "next_step"):
            text: str = getattr(s, part)
            if not text.strip() or not text.rstrip().endswith("."):
                out.append(f"{s.code}: {part} must be a full sentence")
        if s.details is not None and not dataclasses.is_dataclass(s.details):
            out.append(f"{s.code}: details must be a dataclass type or None")
    return out
