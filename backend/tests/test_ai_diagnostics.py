"""Tests for the AI validator audit log."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.ai import diagnostics as diag


def test_records_one_jsonl_line_per_failure(tmp_path: Path):
    log_path = tmp_path / "ai_diagnostics.jsonl"
    with patch.object(diag, "DIAGNOSTICS_PATH", log_path):
        diag.record_validation_failure(
            "preview_recipe",
            ["viz.chartType: must be one of [...], got 'piechart'",
             "viz.options: must be an object, got string"],
            recipe_id="test-recipe",
        )
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["tool"] == "preview_recipe"
    assert record["recipe_id"] == "test-recipe"
    assert record["error_count"] == 2
    assert record["first_fields"][0] == "viz.chartType"
    assert "ts" in record


def test_extracts_hints_from_messages(tmp_path: Path):
    log_path = tmp_path / "x.jsonl"
    with patch.object(diag, "DIAGNOSTICS_PATH", log_path):
        diag.record_validation_failure(
            "write_recipe.sql",
            ["widgets[0] ('w'): SQL error — no such column: foo. Hint: column 'foo' is not in the postings table"],
        )
    record = json.loads(log_path.read_text().splitlines()[0])
    assert any("postings table" in h for h in record["hints"])


def test_empty_errors_writes_nothing(tmp_path: Path):
    log_path = tmp_path / "x.jsonl"
    with patch.object(diag, "DIAGNOSTICS_PATH", log_path):
        diag.record_validation_failure("preview_recipe", [])
    assert not log_path.exists()


def test_rotates_when_file_exceeds_size_limit(tmp_path: Path):
    """Once the active file crosses MAX_BYTES, it's rolled to .1 and a fresh
    file is started. Older backups shift down: .1 -> .2, .2 -> .3."""
    log_path = tmp_path / "ai_diagnostics.jsonl"
    with patch.object(diag, "DIAGNOSTICS_PATH", log_path), \
         patch.object(diag, "DIAGNOSTICS_MAX_BYTES", 200):
        # First write — small, no rotation expected
        diag.record_validation_failure("preview_recipe", ["a: b"])
        assert log_path.is_file()
        assert not log_path.with_suffix(".jsonl.1").exists()

        # Pad the file past the threshold then write again
        log_path.write_text("X" * 250)
        diag.record_validation_failure("preview_recipe", ["c: d"])

        # Active file should be small (just the new record); .1 holds the old contents
        assert log_path.is_file() and log_path.stat().st_size < 200
        rotated = log_path.with_suffix(".jsonl.1")
        assert rotated.is_file()
        assert rotated.read_text() == "X" * 250


def test_rotated_paths_returns_active_plus_backups(tmp_path: Path):
    log_path = tmp_path / "ai_diagnostics.jsonl"
    log_path.write_text("active\n")
    log_path.with_suffix(".jsonl.1").write_text("backup1\n")
    log_path.with_suffix(".jsonl.2").write_text("backup2\n")
    with patch.object(diag, "DIAGNOSTICS_PATH", log_path):
        paths = diag.rotated_paths()
    assert [p.name for p in paths] == [
        "ai_diagnostics.jsonl",
        "ai_diagnostics.jsonl.1",
        "ai_diagnostics.jsonl.2",
    ]


def test_swallows_io_errors(tmp_path: Path, caplog):
    """A diagnostics-write failure must not break the calling tool."""
    bad_path = tmp_path / "this/dir/cannot/be/created/x.jsonl"
    # Make the parent path collide with a file so mkdir fails
    (tmp_path / "this").write_text("not a directory")
    with patch.object(diag, "DIAGNOSTICS_PATH", bad_path):
        caplog.set_level("WARNING", logger="app.ai.diagnostics")
        # Must not raise
        diag.record_validation_failure("preview_recipe", ["some: error"])
    assert "Failed to record AI diagnostics" in caplog.text
