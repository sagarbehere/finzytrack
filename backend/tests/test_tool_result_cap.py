"""Tool-result size-cap tests.

Spec under test: ``build_tool_result_message`` caps the serialised payload
before it's appended to the conversation history that goes back to the
LLM. Tools that legitimately produce big payloads (unfiltered queries,
large recipes) used to balloon the context window across the 12-round
agent loop. The cap is a defensive backstop — individual tools may still
truncate semantically (e.g. ``execute_query``'s 500-row cap).
"""
import json

from app.ai.client import _MAX_TOOL_RESULT_CHARS, build_tool_result_message


def test_small_result_passes_through_unchanged():
    """Below the cap, the payload is the verbatim json.dumps of the dict."""
    result = {"success": True, "row_count": 3, "rows": [{"a": 1}, {"a": 2}, {"a": 3}]}
    msg = build_tool_result_message("tool_call_abc", result)
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "tool_call_abc"
    assert json.loads(msg["content"]) == result


def test_large_result_is_replaced_with_truncation_notice():
    """Above the cap, the payload becomes a structured notice the model
    can read and react to.
    """
    big_blob = "x" * (_MAX_TOOL_RESULT_CHARS + 100)
    result = {"success": True, "row_count": 9999, "rows": [{"blob": big_blob}]}
    msg = build_tool_result_message("tool_call_abc", result)
    notice = json.loads(msg["content"])

    assert notice["_truncated"] is True
    assert notice["success"] is True  # preserved so branching still works
    assert notice["_original_size_chars"] > _MAX_TOOL_RESULT_CHARS
    assert "Refine your query" in notice["_note"]
    # The model needs a hint at what kind of tool ran to recover gracefully.
    assert set(notice["_keys"]) == {"success", "row_count", "rows"}


def test_truncation_notice_is_valid_json_and_under_the_cap():
    """The replacement must itself fit comfortably under the cap so we
    can't accidentally truncate the truncation notice on enormous keys.
    """
    huge_result = {f"k{i}": "v" * 1000 for i in range(100)}
    huge_result["success"] = False
    msg = build_tool_result_message("tool_call_xyz", huge_result)
    assert len(msg["content"]) < _MAX_TOOL_RESULT_CHARS
    notice = json.loads(msg["content"])
    assert notice["_truncated"] is True
    assert notice["success"] is False  # error branch preserved


def test_failure_result_preserves_success_false():
    """A failed tool result that's also too large must still surface
    ``success: false`` so the LLM can see the tool failed.
    """
    result = {
        "success": False,
        "error": "x" * (_MAX_TOOL_RESULT_CHARS + 100),
    }
    msg = build_tool_result_message("tool_call_err", result)
    notice = json.loads(msg["content"])
    assert notice["success"] is False
    assert notice["_truncated"] is True
