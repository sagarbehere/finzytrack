"""Pydantic schemas for the startup-task framework (/api/startup).

A startup task is a detected, user-facing action surfaced at launch — most often
a required asset migration. The frontend gates the app behind any
`action_required` task until the user consents to apply it. See
dev-docs/upgrades.md.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

# Severity values (kept as strings so the wire format is stable).
SEVERITY_ACTION_REQUIRED = "action_required"  # gates the app until applied
SEVERITY_INFO = "info"                        # a dismissible notice


class StartupTaskInfo(BaseModel):
    """A detected pending task. Detection is read-only — nothing is mutated
    until the user applies it."""
    id: str = Field(..., description="Stable task identifier.")
    title: str = Field(..., description="Short title shown in the dialog.")
    summary: str = Field(..., description="What will change and why (plain text/markdown).")
    severity: str = Field(..., description="'action_required' (gates the app) or 'info'.")
    requires_consent: bool = Field(..., description="True if the app should gate until applied.")
    docs_path: Optional[str] = Field(None, description="Relative docs path for 'Learn more' (e.g. 'upgrade-notes/...').")
    details: Dict[str, Any] = Field(default_factory=dict, description="Task-specific detail (e.g. counts).")


class StartupTasksData(BaseModel):
    tasks: List[StartupTaskInfo]


class StartupApplyData(BaseModel):
    id: str
    applied: bool
    message: str
    result: Dict[str, Any] = Field(default_factory=dict)
