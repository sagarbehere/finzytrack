"""Shared validation helpers for rule file CRUD endpoints."""

import io
from pathlib import Path
from typing import Type

from pydantic import BaseModel
from ruamel.yaml import YAML

from app.exceptions import APIError


def parse_yaml(content: str) -> dict:
    """Parse a YAML string, raising APIError on failure."""
    try:
        yaml = YAML(typ="safe")
        data = yaml.load(io.StringIO(content))
    except Exception as e:
        raise APIError(
            message=f"YAML parse error: {e}",
            code="YAML_PARSE_ERROR",
            status_code=400,
        )
    if not isinstance(data, dict):
        raise APIError(
            message="YAML content must be a mapping (key-value pairs), not a scalar or list.",
            code="YAML_PARSE_ERROR",
            status_code=400,
        )
    return data


def validate_rule_schema(data: dict, schema_class: Type[BaseModel], rule_type: str) -> None:
    """Validate parsed YAML data against a Pydantic model, raising APIError on failure."""
    try:
        schema_class.model_validate(data)
    except Exception as e:
        raise APIError(
            message=f"{rule_type} rule validation failed: {e}",
            code="VALIDATION_ERROR",
            status_code=400,
        )


def resolve_rule_path(rules_dir: Path, filename: str) -> Path:
    """Resolve filename against rules_dir with path traversal protection.

    Ensures the filename has a .yaml/.yml extension and resolves within
    the target directory.
    """
    if not filename.endswith((".yaml", ".yml")):
        filename += ".yaml"

    target = (rules_dir / filename).resolve()
    if not target.is_relative_to(rules_dir.resolve()):
        raise APIError(
            message="Invalid filename — path traversal not allowed.",
            code="INVALID_PATH",
            status_code=400,
            details={"filename": filename},
        )
    return target
