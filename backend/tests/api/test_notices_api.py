"""
Notice channel API tests.

Verifies that GET /api/notices surfaces:
- Ledger parse errors (severity=error, code=LEDGER_PARSE_ERROR)
- Multi-file pushtag/pushmeta syntax (severity=warning,
  code=MULTIFILE_PUSHTAG_PUSHMETA)

And that a clean ledger returns no notices.
"""

from __future__ import annotations

from pathlib import Path


def test_clean_ledger_returns_no_notices(test_client):
    """The small_ledger fixture parses cleanly and uses no pushtag/pushmeta,
    so the notices list must be empty."""
    resp = test_client.get("/api/notices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["notices"] == []


def test_parse_errors_surface_as_lederger_parse_error_notice(test_client, tmp_root_with_ledger: Path):
    """Inject a parse error into the ledger; the next /api/notices call must
    surface a LEDGER_PARSE_ERROR notice."""
    ledger_path = tmp_root_with_ledger / "data" / "ledgers" / "main.beancount"
    ledger_path.write_text(ledger_path.read_text() + "\nNOT A VALID DIRECTIVE\n")

    # Re-export so SQLite knows about the errors. The simplest trigger is to
    # call the export endpoint.
    test_client.post("/api/ledger/export", json={"force": True})

    resp = test_client.get("/api/notices")
    assert resp.status_code == 200
    notices = resp.json()["data"]["notices"]
    assert any(n["code"] == "LEDGER_PARSE_ERROR" for n in notices)
    err = next(n for n in notices if n["code"] == "LEDGER_PARSE_ERROR")
    assert err["severity"] == "error"
    assert err["source"] == "ledger"
    # Signature must reflect the count so dismissal-state can re-surface on change
    assert err["signature"] != ""
    assert err["details"] is not None
    assert len(err["details"]) >= 1


def test_pushtag_syntax_surfaces_warning_notice(test_client, tmp_root_with_ledger: Path):
    """A ledger using pushtag block syntax surfaces the
    MULTIFILE_PUSHTAG_PUSHMETA advisory at warning severity."""
    ledger_path = tmp_root_with_ledger / "data" / "ledgers" / "main.beancount"
    original = ledger_path.read_text()
    ledger_path.write_text(
        'pushtag #trip-japan\n'
        + original
        + '\npoptag #trip-japan\n'
    )
    test_client.post("/api/ledger/export", json={"force": True})

    resp = test_client.get("/api/notices")
    assert resp.status_code == 200
    notices = resp.json()["data"]["notices"]
    warns = [n for n in notices if n["code"] == "MULTIFILE_PUSHTAG_PUSHMETA"]
    assert len(warns) == 1
    w = warns[0]
    assert w["severity"] == "warning"
    assert w["source"] == "ledger"
    assert w["details"] is not None
    assert any(str(ledger_path) in d or str(ledger_path.resolve()) in d for d in w["details"])
    # The advisory must point to the ledger-rewrites docs page so the
    # frontend banner can render a "Learn more" link.
    assert w["learn_more_url"] == "https://docs.finzytrack.com/reference/ledger-rewrites/"


def test_parse_error_notice_has_no_learn_more_url(test_client, tmp_root_with_ledger):
    """Parse errors are self-explanatory in their details list — they don't
    need a docs link. Verifying this so a future regression doesn't add one
    accidentally."""
    ledger_path = tmp_root_with_ledger / "data" / "ledgers" / "main.beancount"
    ledger_path.write_text(ledger_path.read_text() + "\nBAD\n")
    test_client.post("/api/ledger/export", json={"force": True})

    resp = test_client.get("/api/notices")
    notices = resp.json()["data"]["notices"]
    err = next(n for n in notices if n["code"] == "LEDGER_PARSE_ERROR")
    assert err["learn_more_url"] is None
