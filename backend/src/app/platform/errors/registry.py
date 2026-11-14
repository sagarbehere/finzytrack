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
    ContractRefused,
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
    LEDGER_CHANGING = "LEDGER_CHANGING"
    WRITES_PAUSED = "WRITES_PAUSED"
    CHANGE_NOT_SAVED = "CHANGE_NOT_SAVED"
    ACK_REQUIRED = "ACK_REQUIRED"
    WRITE_REFUSED_UNINTENDED_CHANGE = "WRITE_REFUSED_UNINTENDED_CHANGE"
    WRITE_REFUSED_CHECK_FAILED = "WRITE_REFUSED_CHECK_FAILED"
    ENCRYPTED_FILE_NOT_EDITABLE = "ENCRYPTED_FILE_NOT_EDITABLE"
    PLAN_EXPIRED = "PLAN_EXPIRED"
    PREVIEW_STALE = "PREVIEW_STALE"
    UNPROMOTED_MUTATOR_ON_REAL_LEDGER = "UNPROMOTED_MUTATOR_ON_REAL_LEDGER"
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
    `cannot_start`: the wait for the engine lock failed (E6); `closing`: Finzytrack is closing; `app_folder_missing`:
    the app's data folder is missing or another folder since the start (S0102, BL-0244: quit and reopen)."""

    reason: Literal["stopped_often", "incompatible", "cannot_start", "closing", "app_folder_missing"]


type PausedReason = Literal[
    "unfinished_save",  # this save's own journal was left open: Reopen recovers it
    "earlier_unfinished",  # an earlier save's journal is open, so this change was not written (S0100, BL-0183)
]


@dataclass(frozen=True, slots=True)
class WritesPausedDetails:
    """This workspace's saves are paused (engine-jobs §6.1 steps 1 and 4, §6.4, §6.6, §11; S0063). `unfinished_save`:
    this save's journal was left open (Reopen recovers it); `earlier_unfinished`: refused before anything was written,
    since an earlier save's journal is open (S0100, BL-0183). Nothing else pauses saving since S0068 (ADR-0067).
    `file`: the file the save was creating that the undo will ask about (its Reopen text then promises no undo: S0061
    red team r8 overflow 1), else None."""

    reason: PausedReason
    file: str | None


type NotSavedReason = Literal[
    "hard_linked",  # a file has a second name on disk (ADR-0013 amendment of 2026-10-29)
    "locked",  # a file is locked (`uchg`)
    "not_a_file",  # a ledger path is a folder, a FIFO, a device
    "permission",  # the system would not allow the save: a mode, an ACL, unreadable extended attributes
    "io_error",  # the disk answered an error (a full disk, an I/O error)
    "read_back",  # a written file did not read back as written (the save was rolled back)
    "name_not_utf8",  # a path is not valid UTF-8
    "linked_folder",  # a link stands in place of the workspace's own journal, backups or key folder (S0077, BL-0193)
    "no_drive_id",  # a drive Finzytrack cannot identify: a save there could never be undone (S0089, BL-0188; S0090)
    "no_folder_id",  # a folder with no creation time: a FAT or exFAT drive's top level, or a drive keeping none (S0089)
    "engine_stopped",  # the save's engine stopped, and the disk shows the change was not saved (§6.6)
    "case_twins",  # two files of the change whose names differ only in capital letters (S0100, BL-0162)
    "folder_appeared",  # a folder stood where the save makes one (S0100, BL-0187)
    "app_folder_missing",  # the app's own data folder is gone (S0100, BL-0163 (a))
]


@dataclass(frozen=True, slots=True)
class ChangeNotSavedDetails:
    """A save that did not happen, and why (engine-jobs §6.1 step 8's refusals, §6.4, §6.6, §11; S0063). `path`: the
    file it is about (an I/O error's file); `folder` and `acl_entry`: for `permission`, the folder and the ACL entry
    (in `chmod`'s words, no subject) that stop it, for `linked_folder` the link in place of the save's own folder, for
    `folder_appeared` the folder that appeared, for `app_folder_missing` the app's folder; `unchanged`: every file
    the save would write was hashed again and equals its bytes before the save (only then may the user read "Your
    ledger is unchanged"); `changed`: the first file that re-hash found not at those bytes (None when none was found,
    or when it could not read a file; S0100, BL-0163 (b)). The UI words each reason
    (`app.platform.engine_jobs.save_text.not_saved_text`)."""

    reason: NotSavedReason
    path: str | None
    folder: str | None
    acl_entry: str | None
    unchanged: bool
    changed: str | None


type ContractStep = Literal["plan", "apply", "parse", "diff", "spans", "load", "errors", "files"]
"""Where the write contract refused a change (S0117): the engine's preview steps (ADR-0008 steps 1-3:
`ledger_engine.ledger.contract`), and `files`, the confirm's own check that the plan writes only files of its ledger
set at the bytes the preview read (`app.workspaces.writes.plan_problems`)."""


@dataclass(frozen=True, slots=True)
class ContractStepDetails:
    """The step of the write contract that refused the change (S0117, BL-0106)."""

    step: ContractStep


@dataclass(frozen=True, slots=True)
class EncryptedFileDetails:
    """The encrypted file the change would edit (ADR-0059; S0117), when the refusal names one."""

    path: str | None


@dataclass(frozen=True, slots=True)
class AckRequiredDetails:
    """The new errors the preview listed, by key (ADR-0008's error identity, as the engine's SHA-256 of it), each once:
    the user's Save anyway acknowledges exactly these (ADR-0067; S0117)."""

    error_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnpromotedDetails:
    """The mutator that is not promoted yet (ADR-0014): its changes are saved only in a test copy."""

    mutator: str


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
            ErrorCode.LEDGER_CHANGING,
            ErrorClass.UNAVAILABLE,
            retryable=True,  # engine-jobs §7, §11: Try again once the other program has stopped
            # owner, 2026-11-08 ("Reword + state limit"): it blames no one program, since a ledger's own plugin that
            # writes into its files or documents on every load gives it too (engine-jobs §15 "own plugin writes")
            message="Your ledger kept changing while Finzytrack read it (another program, or one of the ledger's own "
            "plugins).",
            data_safe=True,  # a read: nothing was written to the ledger (only the app's cache, never half a mirror)
            next_step="Wait until it stops, then try again.",
            details=None,
        ),
        ErrorSpec(
            ErrorCode.WRITES_PAUSED,
            ErrorClass.CONFLICT,
            retryable=False,
            # never "did not save": the change may be on disk until Reopen undoes it (step-back area review F3)
            message="Finzytrack could not finish saving a change, so saving is paused in this workspace.",
            data_safe=False,  # an unfinished save may be half-applied until Reopen undoes it (§6.6)
            # both causes of §11 (S0068 verifier r1 F5): no undo is promised when `file` is set (`created_path_taken`)
            next_step=(
                "Reopen the workspace so Finzytrack can check your files. It undoes the unfinished change, "
                "or asks you about a file it was creating."
            ),
            details=WritesPausedDetails,
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
        # S0117 (BL-0106; ARCHITECTURE §6.1): the write contract. Every refusal below happens before the save writes
        # anything, so each says the change was not saved, and none says "unchanged": that is said only after a
        # re-hash (engine-jobs §11), and another program may have changed the ledger meanwhile.
        ErrorSpec(
            ErrorCode.ACK_REQUIRED,
            ErrorClass.CONTRACT_REFUSED,
            retryable=False,  # the user decides: Save anyway or Cancel (ADR-0067)
            message="This change adds new errors to your ledger, so Finzytrack did not save it yet.",
            data_safe=True,  # nothing of this change was written
            next_step="Check the errors listed, then choose Save anyway or Cancel.",
            details=AckRequiredDetails,
        ),
        ErrorSpec(
            ErrorCode.WRITE_REFUSED_UNINTENDED_CHANGE,
            ErrorClass.CONTRACT_REFUSED,
            retryable=False,  # the same request makes the same text
            message="Finzytrack did not save this change, because the text it would write is not exactly the change "
            "you asked for.",
            data_safe=True,  # refused at the preview: nothing was written
            next_step="Copy the diagnostics and report a problem.",
            details=ContractStepDetails,
        ),
        ErrorSpec(
            ErrorCode.WRITE_REFUSED_CHECK_FAILED,
            ErrorClass.CONTRACT_REFUSED,
            retryable=False,  # a check that could not run on this ledger fails the same way again (INV-22)
            message="Finzytrack could not check this change, so it did not save it.",
            data_safe=True,  # refused before the save: nothing was written
            next_step="Copy the diagnostics and report a problem.",
            details=ContractStepDetails,
        ),
        ErrorSpec(
            ErrorCode.ENCRYPTED_FILE_NOT_EDITABLE,
            ErrorClass.CONTRACT_REFUSED,
            retryable=False,
            message="This change would edit an encrypted file. Finzytrack never changes encrypted files, so it did "
            "not save the change.",
            data_safe=True,  # refused at the preview, the confirm or the save's step 0, each before any write
            next_step="Make this change with the tools you use for that encrypted file.",
            details=EncryptedFileDetails,
        ),
        ErrorSpec(
            ErrorCode.PLAN_EXPIRED,
            ErrorClass.NOT_FOUND,
            retryable=False,  # the preview is gone: a new one is needed
            message="This preview is no longer available, so Finzytrack did not save the change.",
            data_safe=True,  # nothing of this change was written
            next_step="Preview the change again.",
            details=None,  # expired, used, dropped by a switch, or another workspace's: one answer for all
        ),
        ErrorSpec(
            ErrorCode.PREVIEW_STALE,
            ErrorClass.CONFLICT,
            retryable=False,
            message="This preview is out of date, so Finzytrack did not save the change.",
            data_safe=True,  # nothing of this change was written
            next_step="Preview the change again.",
            details=None,
        ),
        ErrorSpec(
            ErrorCode.UNPROMOTED_MUTATOR_ON_REAL_LEDGER,
            ErrorClass.FORBIDDEN,
            retryable=False,
            message="Finzytrack is still testing this kind of change, so it saves it only in a test copy of a "
            "workspace.",
            data_safe=True,  # refused before the save: nothing was written
            next_step="Make the change in a test copy of your workspace.",
            details=UnpromotedDetails,
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
        case ErrorClass.CONTRACT_REFUSED:
            return ContractRefused
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
