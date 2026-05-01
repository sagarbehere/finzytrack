"""Tests for AI assistant readiness reporting.

The readiness API exists to make missed sync steps visible. These tests
verify that:
  - get_readiness reports OK in a healthy install
  - it correctly flags a missing reference file as DEGRADED
  - the read_reference tool's enum reflects actually-present files
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.ai import reference as ref_mod
from app.ai.tools.read_reference import ReadReferenceTool


def test_readiness_ok_in_healthy_install():
    state = ref_mod.get_readiness()
    assert state["ok"] is True, state
    assert "recipes.ts" in state["references_present"]
    assert "generators.ts" in state["references_present"]
    assert "recipe.schema.json" in state["schemas_present"]
    assert state["remediation"] == ""


def test_readiness_flags_missing_reference(tmp_path: Path):
    """Point REFERENCE_DIR at an empty dir and confirm readiness goes degraded."""
    empty = tmp_path / "ai_reference"
    empty.mkdir()
    with patch.object(ref_mod, "REFERENCE_DIR", empty):
        state = ref_mod.get_readiness()
    assert state["ok"] is False
    assert set(state["references_missing"]) == set(ref_mod.ALLOWED_REFERENCES.keys())
    assert "sync_ai_reference" in state["remediation"]


def test_readiness_flags_missing_schema(tmp_path: Path):
    empty = tmp_path / "schemas"
    empty.mkdir()
    with patch.object(ref_mod, "SCHEMA_DIR", empty):
        state = ref_mod.get_readiness()
    assert state["ok"] is False
    assert "recipe.schema.json" in state["schemas_missing"]


def test_read_reference_enum_only_lists_present_files(tmp_path: Path):
    """If only one allowlisted file is on disk, the tool's enum is restricted."""
    partial = tmp_path / "ai_reference"
    partial.mkdir()
    (partial / "recipes.ts").write_text("export type X = string\n")
    # generators.ts intentionally missing
    with patch.object(ref_mod, "REFERENCE_DIR", partial):
        tool = ReadReferenceTool()
        enum = tool.parameters_schema["properties"]["name"]["enum"]
        assert enum == ["recipes.ts"]
        assert "generators.ts" not in tool.description


def test_read_reference_logs_warning_when_file_missing(tmp_path, caplog):
    """If a file is allowlisted but absent, calling read_reference must log
    a WARNING — that's the safety net against silent failure."""
    import asyncio
    empty = tmp_path / "ai_reference"
    empty.mkdir()
    caplog.set_level("WARNING", logger="app.ai.tools.read_reference")
    with patch.object(ref_mod, "REFERENCE_DIR", empty):
        tool = ReadReferenceTool()
        result = asyncio.run(tool.execute(name="recipes.ts"))
    assert result["success"] is False
    assert "missing on disk" in caplog.text
    assert "sync_ai_reference" in caplog.text
