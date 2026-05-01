"""Shared validation helpers for rule file CRUD endpoints."""

import io
from pathlib import Path
from typing import Type

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML

from app.exceptions import APIError


def format_pydantic_errors(exc: ValidationError) -> list[str]:
    """Convert a ValidationError into one human-readable string per problem.

    Each line names the offending field path, the problem, and (when available)
    the input value the user supplied — enough information for an AI assistant
    to identify and fix the mistake without re-reading the schema.
    """
    lines: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) if err.get("loc") else "(root)"
        msg = err.get("msg", "invalid")
        input_value = err.get("input", None)
        if input_value is None or (isinstance(input_value, str) and not input_value):
            lines.append(f"{loc}: {msg}")
        else:
            # Truncate long inputs so the message stays readable
            shown = input_value
            if isinstance(shown, str) and len(shown) > 60:
                shown = shown[:57] + "..."
            lines.append(f"{loc}: {msg} (got {shown!r})")
    return lines


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
