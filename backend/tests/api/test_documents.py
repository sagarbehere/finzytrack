"""
API + integration tests for the documents feature.

Binds to dev-docs/documents.md invariants: I1 (naming/storage), I2/I3/I4
(transaction document metadata + count + round-trip), I5/I6 (account documents
+ rename invariant), I7/I8 (orphan sweep), I9 (Fava-plugin tolerance), I10 (path
safety). Outcomes are verified via a subsequent read, never the response alone.
"""

import os
import time
import shutil
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _build_config, FIXTURES_DIR


# A transaction carrying an explicit UUIDv7-shaped id so the update path can
# locate it (hand-authored fixture transactions otherwise have no id meta).
_TXN_ID = "01920000-0000-7000-8000-000000000001"

_DOC_LEDGER = f"""\
option "operating_currency" "USD"

1970-01-01 open Assets:Bank:Checking   USD
1970-01-01 open Assets:Bank:Savings    USD
1970-01-01 open Expenses:Office        USD
1970-01-01 open Equity:Opening-Balances USD

2024-01-01 * "Opening Balance"
  Assets:Bank:Checking     5000.00 USD
  Equity:Opening-Balances

2026-06-15 * "ACME Corp" "Office supplies"
  id: "{_TXN_ID}"
  source_account: "Assets:Bank:Checking"
  Expenses:Office           42.00 USD
  Assets:Bank:Checking
"""


def _make_root(tmp_path: Path, ledger_text: str) -> Path:
    for d in ["config/csv_rules", "config/xls_rules", "config/email_rules",
              "config/recipes", "data/ledgers", "data/backups", "logs"]:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.yaml").write_text(
        "setup_complete: true\n"
        "ledger_file: ./data/ledgers/main.beancount\n"
        "accounts:\n  default_currency: USD\n  default_unknown_account: Expenses:Unknown\n"
    )
    (tmp_path / "data" / "ledgers" / "main.beancount").write_text(ledger_text)
    return tmp_path


@pytest.fixture
def doc_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    from app.main import create_app
    root = _make_root(tmp_path, _DOC_LEDGER)
    with TestClient(create_app(_build_config(root))) as client:
        client._root = root  # type: ignore[attr-defined]
        yield client


def _ledger_text(client: TestClient) -> str:
    return (client._root / "data" / "ledgers" / "main.beancount").read_text()  # type: ignore[attr-defined]


def _upload(client: TestClient, content: bytes, name="receipt.pdf", date="2026-06-15", narration=None):
    data = {"date": date}
    if narration is not None:
        data["narration"] = narration
    resp = client.post(
        "/api/documents/upload",
        files={"file": (name, content, "application/pdf")},
        data=data,
    )
    return resp


def _update_meta(client: TestClient, meta: dict):
    """PUT the fixture transaction with the given meta dict (plus required id)."""
    payload = {"transactions": [{
        "id": _TXN_ID, "date": "2026-06-15", "flag": "*",
        "payee": "ACME Corp", "narration": "Office supplies",
        "tags": [], "links": [], "meta": {"id": _TXN_ID, **meta},
        "postings": [
            {"account": "Expenses:Office", "amount": "42.00", "currency": "USD"},
            {"account": "Assets:Bank:Checking", "amount": "-42.00", "currency": "USD"},
        ],
    }]}
    return client.put("/api/ledger/transactions", json=payload)


def _query(client: TestClient, sql: str):
    resp = client.post("/api/ledger/query", json={"query": sql})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["rows"]


# ── Upload / serve (I1, I10) ────────────────────────────────────────────────────

class TestUploadServe:
    def test_upload_returns_relative_path_and_hash(self, doc_client):
        import hashlib
        content = b"%PDF fake receipt content"
        resp = _upload(doc_client, content, narration="ACME office")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["path"] == f"../documents/main/2026/2026-06-15-acme-office-{hashlib.sha256(content).hexdigest()[:8]}.pdf"
        assert data["full_hash"] == hashlib.sha256(content).hexdigest()
        assert data["size"] == len(content)
        # file exists on disk under the documents root
        abs_path = (doc_client._root / "data" / "ledgers" / data["path"]).resolve()
        assert abs_path.is_file() and abs_path.read_bytes() == content

    def test_serve_streams_identical_bytes(self, doc_client):
        content = b"%PDF roundtrip bytes"
        path = _upload(doc_client, content).json()["data"]["path"]
        resp = doc_client.get("/api/documents/file", params={"path": path})
        assert resp.status_code == 200
        assert resp.content == content

    def test_empty_upload_rejected(self, doc_client):
        resp = _upload(doc_client, b"")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "EMPTY_FILE"

    def test_oversize_upload_rejected(self, doc_client, monkeypatch):
        import app.api.routers.documents as docs_router
        monkeypatch.setattr(docs_router, "_MAX_FILE_SIZE", 10)
        resp = _upload(doc_client, b"this is more than ten bytes")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "FILE_TOO_LARGE"

    def test_serve_missing_path_404(self, doc_client):
        resp = doc_client.get("/api/documents/file", params={"path": "../documents/main/2026/nope.pdf"})
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

    def test_serve_path_traversal_rejected(self, doc_client):
        resp = doc_client.get("/api/documents/file", params={"path": "../../../../etc/passwd"})
        assert resp.status_code == 403

    def test_serve_absolute_path_rejected(self, doc_client):
        resp = doc_client.get("/api/documents/file", params={"path": "/etc/passwd"})
        assert resp.status_code == 403

    def test_upload_autowrites_documents_option(self, doc_client):
        assert 'option "documents"' not in _ledger_text(doc_client)
        _upload(doc_client, b"%PDF x")
        text = _ledger_text(doc_client)
        assert 'option "documents" "../documents/main"' in text

    def test_option_survives_subsequent_rewrite(self, doc_client):
        """The auto-written option must round-trip, not be stripped on rewrite."""
        _upload(doc_client, b"%PDF x")
        # Trigger another ledger write (account create) and re-check.
        resp = doc_client.post("/api/accounts", json={
            "name": "Expenses:Travel", "open_date": "2024-01-01", "currencies": ["USD"],
        })
        assert resp.status_code in (200, 201), resp.text
        assert 'option "documents" "../documents/main"' in _ledger_text(doc_client)


# ── Transaction document metadata (I2, I3, I4) ──────────────────────────────────

class TestTransactionDocuments:
    def test_single_document_round_trips_and_counts(self, doc_client):
        path = _upload(doc_client, b"%PDF one").json()["data"]["path"]
        assert _update_meta(doc_client, {"document": path}).status_code == 200
        # round-trip in the ledger file
        assert f'document: "{path}"' in _ledger_text(doc_client)
        # exported to transaction_metadata_json + document_count
        rows = _query(doc_client, f"SELECT DISTINCT document_count, transaction_metadata_json FROM postings WHERE transaction_id='{_TXN_ID}'")
        assert rows[0]["document_count"] == 1
        import json
        assert json.loads(rows[0]["transaction_metadata_json"])["document"] == path

    def test_three_documents_ordered_and_counted(self, doc_client):
        p1 = _upload(doc_client, b"%PDF a").json()["data"]["path"]
        p2 = _upload(doc_client, b"%PDF b").json()["data"]["path"]
        p3 = _upload(doc_client, b"%PDF c").json()["data"]["path"]
        assert _update_meta(doc_client, {"document": p1, "document2": p2, "document3": p3}).status_code == 200
        text = _ledger_text(doc_client)
        assert f'document: "{p1}"' in text
        assert f'document2: "{p2}"' in text
        assert f'document3: "{p3}"' in text
        rows = _query(doc_client, f"SELECT DISTINCT document_count FROM postings WHERE transaction_id='{_TXN_ID}'")
        assert rows[0]["document_count"] == 3

    def test_removing_middle_recompacts(self, doc_client):
        """Frontend renumbers on removal; backend must round-trip the gapless
        result (document, document2) with count 2 and no document3."""
        p1 = _upload(doc_client, b"%PDF a").json()["data"]["path"]
        p2 = _upload(doc_client, b"%PDF b").json()["data"]["path"]
        p3 = _upload(doc_client, b"%PDF c").json()["data"]["path"]
        _update_meta(doc_client, {"document": p1, "document2": p2, "document3": p3})
        # remove middle -> renumbered to document, document2 (== old p1, p3)
        assert _update_meta(doc_client, {"document": p1, "document2": p3}).status_code == 200
        text = _ledger_text(doc_client)
        assert "document3:" not in text
        rows = _query(doc_client, f"SELECT DISTINCT document_count FROM postings WHERE transaction_id='{_TXN_ID}'")
        assert rows[0]["document_count"] == 2

    def test_zero_when_none(self, doc_client):
        rows = _query(doc_client, f"SELECT DISTINCT document_count FROM postings WHERE transaction_id='{_TXN_ID}'")
        assert rows[0]["document_count"] == 0

    def test_has_documents_filter(self, doc_client):
        path = _upload(doc_client, b"%PDF x").json()["data"]["path"]
        _update_meta(doc_client, {"document": path})
        rows = _query(doc_client, "SELECT DISTINCT transaction_id FROM postings WHERE document_count > 0")
        assert {r["transaction_id"] for r in rows} == {_TXN_ID}

    def test_dangling_reference_round_trips(self, doc_client):
        """A document key pointing at a missing file still round-trips — a
        dangling reference is a display concern, not a parse error."""
        missing = "../documents/2026/2026-06-15-ghost-deadbeef.pdf"
        assert _update_meta(doc_client, {"document": missing}).status_code == 200
        assert f'document: "{missing}"' in _ledger_text(doc_client)


# ── Account documents + rename invariant (I5, I6) ───────────────────────────────

class TestAccountDocuments:
    def test_create_emits_document_directive(self, doc_client):
        path = _upload(doc_client, b"%PDF statement").json()["data"]["path"]
        resp = doc_client.post("/api/documents/account", json={
            "account": "Assets:Bank:Checking", "date": "2026-06-15", "path": path,
        })
        assert resp.status_code == 200, resp.text
        # round-trips as a real Document directive with relative filename
        assert f'2026-06-15 document Assets:Bank:Checking "{path}"' in _ledger_text(doc_client)
        # readable from the documents table
        docs = doc_client.get("/api/documents/account", params={"account": "Assets:Bank:Checking"}).json()["data"]["documents"]
        assert len(docs) == 1
        assert docs[0]["path"] == path and docs[0]["account"] == "Assets:Bank:Checking"

    def test_create_for_missing_account_404(self, doc_client):
        path = _upload(doc_client, b"%PDF x").json()["data"]["path"]
        resp = doc_client.post("/api/documents/account", json={
            "account": "Assets:DoesNotExist", "date": "2026-06-15", "path": path,
        })
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"

    def test_delete_removes_directive(self, doc_client):
        path = _upload(doc_client, b"%PDF x").json()["data"]["path"]
        doc_client.post("/api/documents/account", json={
            "account": "Assets:Bank:Checking", "date": "2026-06-15", "path": path})
        resp = doc_client.request("DELETE", "/api/documents/account", json={
            "account": "Assets:Bank:Checking", "path": path})
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted_count"] == 1
        assert "document Assets:Bank:Checking" not in _ledger_text(doc_client)

    def test_rename_invariant(self, doc_client):
        """I6: renaming an account rewrites the directive's account, leaves the
        filename byte-identical, and moves no files on disk."""
        path = _upload(doc_client, b"%PDF statement", narration="statement").json()["data"]["path"]
        doc_client.post("/api/documents/account", json={
            "account": "Assets:Bank:Checking", "date": "2026-06-15", "path": path})

        docs_root = doc_client._root / "data" / "documents"
        before_files = sorted(p.relative_to(docs_root).as_posix() for p in docs_root.rglob("*") if p.is_file())
        line_before = [l for l in _ledger_text(doc_client).splitlines() if "document Assets" in l][0]
        filename_before = line_before.split('"')[1]

        resp = doc_client.request("PUT", "/api/accounts/Assets:Bank:Checking",
                                  json={"new_name": "Assets:Bank:Current"})
        assert resp.status_code == 200, resp.text

        line_after = [l for l in _ledger_text(doc_client).splitlines() if "document Assets" in l][0]
        after_files = sorted(p.relative_to(docs_root).as_posix() for p in docs_root.rglob("*") if p.is_file())

        assert "Assets:Bank:Current" in line_after        # account field rewritten
        assert line_after.split('"')[1] == filename_before  # filename byte-identical
        assert after_files == before_files                 # nothing moved on disk
        # file still resolves
        assert doc_client.get("/api/documents/file", params={"path": filename_before}).status_code == 200

    def test_rename_account_without_documents_is_noop_for_docs(self, doc_client):
        resp = doc_client.request("PUT", "/api/accounts/Assets:Bank:Savings",
                                  json={"new_name": "Assets:Bank:Reserve"})
        assert resp.status_code == 200
        assert "document " not in _ledger_text(doc_client).lower()


# ── Orphan sweep (I7, I8) ────────────────────────────────────────────────────────

class TestOrphanSweep:
    def _backdate(self, client, rel_path):
        p = (client._root / "data" / "ledgers" / rel_path).resolve()
        old = time.time() - 200000
        os.utime(p, (old, old))
        return p

    def test_scan_finds_only_unreferenced_old_files(self, doc_client):
        # referenced via transaction metadata
        ref = _upload(doc_client, b"%PDF referenced").json()["data"]["path"]
        _update_meta(doc_client, {"document": ref})
        self._backdate(doc_client, ref)
        # referenced via account document directive
        acct = _upload(doc_client, b"%PDF acct").json()["data"]["path"]
        doc_client.post("/api/documents/account", json={
            "account": "Assets:Bank:Checking", "date": "2026-06-15", "path": acct})
        self._backdate(doc_client, acct)
        # genuine orphan
        orphan = _upload(doc_client, b"%PDF orphan").json()["data"]["path"]
        self._backdate(doc_client, orphan)

        found = doc_client.post("/api/documents/orphans/scan").json()["data"]["orphans"]
        assert [o["path"] for o in found] == [orphan]
        assert found[0]["size"] == len(b"%PDF orphan")

    def test_no_orphans_returns_empty(self, doc_client):
        data = doc_client.post("/api/documents/orphans/scan").json()["data"]
        assert data["orphans"] == []

    def test_fresh_unreferenced_upload_is_returned(self, doc_client):
        # The scan no longer hard-excludes recent files; a just-uploaded,
        # unreferenced file is returned (the UI labels it "recent" using the
        # mtime + grace_seconds the response carries). grace_seconds is reported.
        up = _upload(doc_client, b"%PDF fresh").json()["data"]["path"]
        data = doc_client.post("/api/documents/orphans/scan").json()["data"]
        assert up in [o["path"] for o in data["orphans"]]
        assert data["grace_seconds"] == 24 * 60 * 60

    def test_delete_subset_only(self, doc_client):
        a = _upload(doc_client, b"%PDF a").json()["data"]["path"]
        b = _upload(doc_client, b"%PDF b").json()["data"]["path"]
        self._backdate(doc_client, a)
        self._backdate(doc_client, b)
        resp = doc_client.post("/api/documents/orphans/delete", json={"paths": [a]})
        assert resp.status_code == 200
        out = resp.json()["data"]
        assert out["deleted"] == [a]
        assert (doc_client._root / "data" / "ledgers" / b).resolve().exists()
        assert not (doc_client._root / "data" / "ledgers" / a).resolve().exists()

    def test_delete_revalidates_now_referenced(self, doc_client):
        """Between scan and delete the file becomes referenced -> skipped, kept."""
        path = _upload(doc_client, b"%PDF willref").json()["data"]["path"]
        self._backdate(doc_client, path)
        # it scans as orphan now
        assert [o["path"] for o in doc_client.post("/api/documents/orphans/scan").json()["data"]["orphans"]] == [path]
        # attach it to the transaction
        _update_meta(doc_client, {"document": path})
        # delete attempt re-validates -> skipped
        out = doc_client.post("/api/documents/orphans/delete", json={"paths": [path]}).json()["data"]
        assert out["deleted"] == []
        assert out["skipped"] == [path]
        assert (doc_client._root / "data" / "ledgers" / path).resolve().exists()

    def test_delete_outside_root_is_skipped(self, doc_client):
        out = doc_client.post("/api/documents/orphans/delete",
                              json={"paths": ["../../../../etc/passwd"]}).json()["data"]
        assert out["deleted"] == []
        assert out["skipped"] == ["../../../../etc/passwd"]


# ── Plugin tolerance (I9) ────────────────────────────────────────────────────────

class TestPluginTolerance:
    @pytest.fixture
    def plugin_client(self, tmp_path):
        from app.main import create_app
        root = _make_root(tmp_path, 'plugin "fava.plugins.link_documents"\n' + _DOC_LEDGER)
        with TestClient(create_app(_build_config(root))) as client:
            client._root = root
            yield client

    def test_uninstalled_plugin_loads_and_surfaces_neutral_notice(self, plugin_client):
        # entries are present despite the missing plugin (queries not blocked)
        rows = _query(plugin_client, "SELECT COUNT(*) AS n FROM postings")
        assert rows[0]["n"] > 0
        notices = plugin_client.get("/api/notices").json()["data"]["notices"]
        codes = {n["code"]: n for n in notices}
        # Neutral, generalized notice — not Fava-specific, not a scary error.
        assert "PLUGIN_NOT_LOADED" in codes
        assert codes["PLUGIN_NOT_LOADED"]["severity"] == "info"
        assert "LEDGER_PARSE_ERROR" not in codes
        # names the offending plugin in the details, no value judgement in copy
        assert "fava.plugins.link_documents" in (codes["PLUGIN_NOT_LOADED"]["details"] or [])
        assert "Fava" not in codes["PLUGIN_NOT_LOADED"]["title"]
        assert "Fava" not in codes["PLUGIN_NOT_LOADED"]["message"]

    def test_non_fava_plugin_also_tolerated(self, tmp_path):
        from app.main import create_app
        root = _make_root(tmp_path, 'plugin "some.random.uninstalled_plugin"\n' + _DOC_LEDGER)
        with TestClient(create_app(_build_config(root))) as client:
            client._root = root
            rows = _query(client, "SELECT COUNT(*) AS n FROM postings")  # not blocked
            assert rows[0]["n"] > 0
            codes = {n["code"]: n for n in client.get("/api/notices").json()["data"]["notices"]}
            assert "PLUGIN_NOT_LOADED" in codes
            assert "LEDGER_PARSE_ERROR" not in codes

    def test_directive_left_in_file_after_write(self, plugin_client):
        # trigger a write
        plugin_client.post("/api/accounts", json={
            "name": "Expenses:Travel", "open_date": "2024-01-01", "currencies": ["USD"]})
        text = (plugin_client._root / "data" / "ledgers" / "main.beancount").read_text()
        assert 'plugin "fava.plugins.link_documents"' in text
