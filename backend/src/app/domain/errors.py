"""The error taxonomy (ARCHITECTURE.md §6.1; EP-13).

Core and shell code raise a `DomainError` subclass with a code from the one registry (`app.platform.errors`, which
also builds them: `error(code, details)`). The class says what kind of failure it is; the registry says everything
else about the code (its message, whether the data is safe, the next step, its typed details). No module below
`app.api` knows an HTTP status: `app.api.errors` translates once, at the edge.

Expected outcomes the caller must handle are not errors: they are tagged-union results (EP-13). `ContractRefused`
joins with the write contract (M1b, BL-0106).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar


class ErrorClass(StrEnum):
    INVALID = "invalid"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    FORBIDDEN = "forbidden"
    UNAVAILABLE = "unavailable"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class Issue:
    """One located problem of an invalid request: where (a JSON pointer into the request) and what."""

    path: str
    message: str


class DomainError(Exception):
    """A failure with a registry code. Raise a subclass, built by `app.platform.errors.error`."""

    error_class: ClassVar[ErrorClass | None] = None  # the bare base names no class: the edge refuses it

    def __init__(self, code: str, details: object | None = None, issues: tuple[Issue, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.details = details
        self.issues = issues


class Invalid(DomainError):
    error_class = ErrorClass.INVALID


class NotFound(DomainError):
    error_class = ErrorClass.NOT_FOUND


class Conflict(DomainError):
    error_class = ErrorClass.CONFLICT


class Forbidden(DomainError):
    error_class = ErrorClass.FORBIDDEN


class Unavailable(DomainError):
    error_class = ErrorClass.UNAVAILABLE


class Internal(DomainError):
    error_class = ErrorClass.INTERNAL
