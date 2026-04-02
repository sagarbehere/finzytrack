"""Request/response schemas for rule file CRUD endpoints."""

from pydantic import BaseModel


class RuleContentResponse(BaseModel):
    """Raw YAML content of a single rule file."""
    filename: str
    content: str


class RuleWriteRequest(BaseModel):
    """Body for updating an existing rule file."""
    content: str


class RuleCreateRequest(BaseModel):
    """Body for creating a new rule file."""
    filename: str
    content: str


class RuleWriteResponse(BaseModel):
    """Response after writing a rule file."""
    filename: str
    path: str
    backup_created: bool


class RuleDeleteResponse(BaseModel):
    """Response after deleting a rule file."""
    filename: str
