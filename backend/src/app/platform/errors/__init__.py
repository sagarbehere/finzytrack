"""Error codes: the registry, the single source for codes (ARCHITECTURE.md §6.1; EP-13)."""

from app.platform.errors.registry import (
    EngineStoppedDetails,
    EngineUnavailableDetails,
    ErrorCode,
    ErrorSpec,
    JobCancelledDetails,
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
    "EngineStoppedDetails",
    "EngineUnavailableDetails",
    "ErrorCode",
    "ErrorSpec",
    "JobCancelledDetails",
    "NotReadyPhase",
    "WorkspaceNotOpenDetails",
    "WorkspaceNotReadyDetails",
    "details_fit",
    "error",
    "registry_problems",
    "spec_for",
    "specs",
]
