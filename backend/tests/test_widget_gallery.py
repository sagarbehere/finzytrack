"""Tests for the Widget Gallery and the get_example_widget tool.

The gallery is the AI assistant's go-to source for working widget templates,
so a regression here directly degrades the assistant's first-shot success
rate. These tests verify:

  - The gallery dashboard validates against recipe.schema.json
  - Every widget in it validates individually
  - Every widget's SQL parses against the postings schema (no typos in column
    names or string literals)
  - get_example_widget exposes the right widget for each enumerated type
  - The tool reads from seed_config/, not the user's editable config/
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from app.ai.tools.get_example_widget import (
    GALLERY_PATH,
    GetExampleWidgetTool,
    _load_gallery,
    list_supported_types,
)
from app.helpers.recipe_validation import validate_dashboard, validate_widget


def _supported_viz_types_from_schema() -> set[str]:
    """Derive the canonical set of supported viz types directly from the
    schema. Hardcoding here is the wrong move because adding a new type
    becomes a two-step change (schema + test) instead of one (schema +
    gallery). With this helper, adding a type to recipe.schema.json plus
    a gallery widget is sufficient — and forgetting either is caught."""
    from app.helpers.recipe_validation import _load_schema, VALID_VIZ_TYPES
    schema = _load_schema()
    chart_types = set(schema["$defs"]["ChartType"]["enum"])
    # Non-chart top-level visualization.types: kpi/table/pivot. Chart types are
    # already enumerated under ChartType. The gallery exposes them under a
    # single flat key per type, so we union them here.
    return chart_types | (VALID_VIZ_TYPES - {"chart"})


SUPPORTED_VIZ_TYPES = _supported_viz_types_from_schema()


@pytest.fixture(autouse=True)
def _clear_gallery_cache():
    """Drop the lru_cache on _load_gallery so per-test patches take effect."""
    _load_gallery.cache_clear()
    yield
    _load_gallery.cache_clear()


# ── Schema and SQL validity ──────────────────────────────────────────────────


def _gallery() -> dict:
    return json.loads(GALLERY_PATH.read_text(encoding="utf-8"))


def test_gallery_validates_as_dashboard():
    errors = validate_dashboard(_gallery())
    assert errors == [], errors


def test_every_gallery_widget_validates_individually():
    failures = []
    for w in _gallery()["widgets"]:
        errs = validate_widget(w, w["id"])
        if errs:
            failures.append((w["id"], errs))
    assert not failures, failures


def _postings_schema_conn() -> sqlite3.Connection:
    """In-memory SQLite with the postings table schema (rows empty)."""
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE postings (
            transaction_id TEXT, transaction_date TEXT, transaction_payee TEXT,
            transaction_narration TEXT, transaction_flag TEXT, transaction_tags TEXT,
            transaction_links TEXT, account TEXT, account_type TEXT, amount REAL,
            currency TEXT, year INTEGER, year_month TEXT, quarter INTEGER
        );
        """
    )
    return con


def test_every_gallery_widget_sql_parses_against_postings_schema():
    """Catches typos in column names, syntax errors, and any drift between
    the postings schema documented in prompts and the queries we ship."""
    con = _postings_schema_conn()
    failures = []
    for w in _gallery()["widgets"]:
        q = w.get("query", "")
        param_names = set(re.findall(r":(\w+)", q))
        params = {n: "__test__" for n in param_names}
        try:
            con.execute(f"SELECT * FROM ({q}) LIMIT 0", params)
        except Exception as e:
            failures.append((w["id"], str(e)))
    assert not failures, failures


def test_gallery_covers_every_supported_viz_type():
    """If a new chart type is added to SUPPORTED_CHART_TYPES but the gallery
    doesn't include an example, the AI loses its safety net for that type."""
    types = set(list_supported_types())
    missing = SUPPORTED_VIZ_TYPES - types
    assert not missing, f"Gallery is missing examples for: {missing}"


# ── Tool behavior ────────────────────────────────────────────────────────────


def test_tool_enum_matches_gallery_contents():
    tool = GetExampleWidgetTool()
    enum = tool.parameters_schema["properties"]["chart_type"]["enum"]
    assert set(enum) == set(list_supported_types())


def test_tool_returns_widget_with_key_points_for_each_type():
    tool = GetExampleWidgetTool()
    for chart_type in list_supported_types():
        result = asyncio.run(tool.execute(chart_type=chart_type))
        assert result["success"], (chart_type, result)
        assert result["chart_type"] == chart_type
        assert "id" in result["widget"]
        assert "visualization" in result["widget"]
        assert isinstance(result.get("key_points"), str) and len(result["key_points"]) > 10


def test_tool_rejects_unknown_type_with_helpful_error():
    tool = GetExampleWidgetTool()
    result = asyncio.run(tool.execute(chart_type="hologram"))
    assert result["success"] is False
    assert "Available types:" in result["error"]


def test_tool_reads_from_seed_config_not_user_config(tmp_path: Path):
    """Editing or removing widgets in the user's config copy must NOT change
    the AI's reference templates. The tool should always read from seed_config."""
    # Sanity: the resolved path lives under seed_config/, never under config/
    assert "seed_config" in str(GALLERY_PATH)
    assert "/config/" not in str(GALLERY_PATH).replace("seed_config", "")
