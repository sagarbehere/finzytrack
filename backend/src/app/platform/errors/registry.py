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
