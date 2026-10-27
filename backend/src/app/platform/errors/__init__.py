"""Error codes: the registry, the single source for codes (ARCHITECTURE.md §6.1; EP-13)."""

from app.platform.errors.registry import (
    ErrorCode,
    ErrorSpec,
    NotReadyPhase,
    WorkspaceNotOpenDetails,
    WorkspaceNotReadyDetails,
    details_fit,
    error,
    registry_problems,
    spec_for,
    specs,
)

__all__ = [
    "ErrorCode",
    "ErrorSpec",
    "NotReadyPhase",
    "WorkspaceNotOpenDetails",
    "WorkspaceNotReadyDetails",
    "details_fit",
    "error",
    "registry_problems",
    "spec_for",
    "specs",
]
