"""
Unit tests for the document storage backend (app/core/document_store.py).

These bind to the spec in dev-docs/documents.md — invariants I1 (naming),
I7 (orphan definition), I8 (sweep safety), I10 (path safety). Expected values
are hand-derived from the spec, not computed by re-running the code under test.
"""

import hashlib
import os
from datetime import date
from pathlib import Path

import pytest

from app.core.document_store import (
    DocumentStore,
    build_document_name,
    collect_referenced_paths,
    resolve_documents_root,
    documents_option_value,
)
from app.exceptions import APIError


def _short(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


# ── I1: Naming ────────────────────────────────────────────────────────────────

class TestNaming:
    def test_nominal_name_shape(self):
        data = b"%PDF fake receipt"
        name = build_document_name(
            date_obj=date(2026, 6, 15),
            slug_source="ACME Corp office supplies",
            original_filename="receipt.pdf",
            full_hash=hashlib.sha256(data).hexdigest(),
        )
        assert name == f"2026-06-15-acme-corp-office-supplies-{_short(data)}.pdf"

    def test_extension_preserved_and_lowercased(self):
        name = build_document_name(
            date_obj=date(2026, 1, 2), slug_source="x",
            original_filename="SCAN.PNG", full_hash="0" * 64,
        )
        assert name.endswith(".png")

    def test_slug_falls_back_to_filename_then_document(self):
        # empty narration -> original filename stem
        n1 = build_document_name(
            date_obj=date(2026, 1, 2), slug_source="",
            original_filename="My Receipt.pdf", full_hash="a" * 64,
        )
        assert n1 == "2026-01-02-my-receipt-aaaaaaaa.pdf"
        # empty narration + empty filename -> literal "document"
        n2 = build_document_name(
            date_obj=date(2026, 1, 2), slug_source="",
            original_filename="", full_hash="b" * 64,
        )
        assert n2 == "2026-01-02-document-bbbbbbbb"

    def test_unicode_and_punctuation_slugified(self):
        n = build_document_name(
            date_obj=date(2026, 1, 2), slug_source="Café Münch — n°5 (déjà vu)!",
            original_filename="x.pdf", full_hash="c" * 64,
        )
        # NFKD-folded ASCII, dash-joined, no punctuation
        assert n == "2026-01-02-cafe-munch-n5-deja-vu-cccccccc.pdf"

    def test_long_slug_is_capped(self):
        n = build_document_name(
            date_obj=date(2026, 1, 2), slug_source="word " * 40,
            original_filename="x.pdf", full_hash="d" * 64,
        )
        slug = n[len("2026-01-02-"):-len("-dddddddd.pdf")]
        assert len(slug) <= 60

    def test_source_filename_with_date_prefix_not_double_prefixed(self):
        n = build_document_name(
            date_obj=date(2026, 1, 2), slug_source="",
            original_filename="2026-01-02-statement.pdf", full_hash="e" * 64,
        )
        assert n == "2026-01-02-statement-eeeeeeee.pdf"
        assert not n.startswith("2026-01-02-2026-01-02")


# ── I1 + idempotency + collision via store() ──────────────────────────────────

class TestStore:
    @pytest.fixture
    def store(self, tmp_path):
        root = tmp_path / "data" / "documents"
        ledger_dir = tmp_path / "data" / "ledgers"
        ledger_dir.mkdir(parents=True)
        return DocumentStore(root, ledger_dir)

    def test_stores_under_year_shard_with_relative_path(self, store):
        data = b"hello world"
        stored = store.store(
            file_bytes=data, original_filename="r.pdf",
            date_obj=date(2026, 6, 15), slug_source="acme",
        )
        assert stored.path == f"../documents/2026/2026-06-15-acme-{_short(data)}.pdf"
        assert stored.absolute_path.is_file()
        assert stored.absolute_path.read_bytes() == data
        assert stored.absolute_path.parent.name == "2026"
        assert stored.full_hash == hashlib.sha256(data).hexdigest()
        assert stored.size == len(data)

    def test_identical_content_same_name_idempotent(self, store):
        data = b"same"
        a = store.store(file_bytes=data, original_filename="r.pdf", date_obj=date(2026, 6, 15), slug_source="x")
        b = store.store(file_bytes=data, original_filename="r.pdf", date_obj=date(2026, 6, 15), slug_source="x")
        assert a.path == b.path
        # No duplicate-with-suffix file created.
        files = list((store.documents_root / "2026").iterdir())
        assert len(files) == 1

    def test_different_content_same_slug_distinct_names(self, store):
        a = store.store(file_bytes=b"one", original_filename="r.pdf", date_obj=date(2026, 6, 15), slug_source="acme")
        b = store.store(file_bytes=b"two", original_filename="r.pdf", date_obj=date(2026, 6, 15), slug_source="acme")
        assert a.path != b.path
        assert len(list((store.documents_root / "2026").iterdir())) == 2

    def test_zero_byte_and_missing_extension(self, store):
        stored = store.store(file_bytes=b"", original_filename="noext", date_obj=date(2026, 6, 15), slug_source="")
        assert stored.absolute_path.is_file()
        assert stored.path.endswith("-noext-" + _short(b"") + "")  # no extension appended


# ── I10: path safety on resolve ────────────────────────────────────────────────

class TestPathSafety:
    @pytest.fixture
    def store(self, tmp_path):
        root = tmp_path / "data" / "documents"
        (root / "2026").mkdir(parents=True)
        ledger_dir = tmp_path / "data" / "ledgers"
        ledger_dir.mkdir(parents=True)
        return DocumentStore(root, ledger_dir)

    def test_legitimate_relative_path_resolves(self, store):
        (store.documents_root / "2026" / "f.pdf").write_bytes(b"x")
        resolved = store.resolve("../documents/2026/f.pdf")
        assert resolved == (store.documents_root / "2026" / "f.pdf").resolve()

    def test_traversal_escape_rejected(self, store):
        with pytest.raises(APIError) as exc:
            store.resolve("../../../../etc/passwd")
        assert exc.value.status_code == 403

    def test_absolute_path_rejected(self, store):
        with pytest.raises(APIError) as exc:
            store.resolve("/etc/passwd")
        assert exc.value.status_code == 403

    def test_symlink_escape_rejected(self, store, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret")
        link = store.documents_root / "2026" / "link.pdf"
        os.symlink(secret, link)
        # The stored relative path points inside the root, but resolves out.
        with pytest.raises(APIError) as exc:
            store.resolve("../documents/2026/link.pdf")
        assert exc.value.status_code == 403


# ── I7/I8: orphan scan + delete ─────────────────────────────────────────────────

class TestOrphanScan:
    @pytest.fixture
    def store(self, tmp_path):
        root = tmp_path / "data" / "documents" / "2026"
        root.mkdir(parents=True)
        ledger_dir = tmp_path / "data" / "ledgers"
        ledger_dir.mkdir(parents=True)
        return DocumentStore(tmp_path / "data" / "documents", ledger_dir)

    def _file(self, store, name, data=b"x"):
        p = store.documents_root / "2026" / name
        p.write_bytes(data)
        return p

    def test_unreferenced_old_file_is_orphan(self, store):
        p = self._file(store, "orphan.pdf")
        orphans = store.scan_orphans(referenced=set())
        assert [o.path for o in orphans] == ["../documents/2026/orphan.pdf"]
        assert orphans[0].size == 1

    def test_referenced_file_not_flagged(self, store):
        p = self._file(store, "kept.pdf")
        orphans = store.scan_orphans(referenced={p.resolve()})
        assert orphans == []

    def test_recent_file_is_returned_with_mtime(self, store):
        """Recently-modified files are no longer hard-excluded — the scan
        returns them (the UI separates 'recent' using mtime + grace_seconds).
        The mtime is reported so the UI can make that distinction."""
        p = store.documents_root / "2026" / "fresh.pdf"
        p.write_bytes(b"x")  # mtime = now
        orphans = store.scan_orphans(referenced=set())
        assert [o.path for o in orphans] == ["../documents/2026/fresh.pdf"]
        assert orphans[0].mtime == pytest.approx(p.stat().st_mtime)

    def test_empty_root_returns_empty(self, store):
        assert store.scan_orphans(referenced=set()) == []


class TestOrphanDelete:
    @pytest.fixture
    def store(self, tmp_path):
        root = tmp_path / "data" / "documents" / "2026"
        root.mkdir(parents=True)
        ledger_dir = tmp_path / "data" / "ledgers"
        ledger_dir.mkdir(parents=True)
        return DocumentStore(tmp_path / "data" / "documents", ledger_dir)

    def _file(self, store, name):
        p = store.documents_root / "2026" / name
        p.write_bytes(b"x")
        return p

    def test_deletes_selected_only(self, store):
        a = self._file(store, "a.pdf")
        b = self._file(store, "b.pdf")
        out = store.delete(paths=["../documents/2026/a.pdf"], referenced=set())
        assert out.deleted == ["../documents/2026/a.pdf"]
        assert not a.exists() and b.exists()

    def test_revalidation_skips_now_referenced(self, store):
        a = self._file(store, "a.pdf")
        out = store.delete(paths=["../documents/2026/a.pdf"], referenced={a.resolve()})
        assert out.deleted == []
        assert out.skipped == ["../documents/2026/a.pdf"]
        assert a.exists()  # not deleted

    def test_deleting_already_deleted_is_noop(self, store):
        out = store.delete(paths=["../documents/2026/gone.pdf"], referenced=set())
        assert out.deleted == ["../documents/2026/gone.pdf"]  # missing_ok unlink

    def test_escape_attempt_skipped_not_deleted(self, store):
        out = store.delete(paths=["../../../../etc/passwd"], referenced=set())
        assert out.deleted == []
        assert out.skipped == ["../../../../etc/passwd"]


# ── I7: referenced-set collection ───────────────────────────────────────────────

class TestReferencedSet:
    def test_collects_metadata_and_directive_paths(self, tmp_path):
        from beancount.core import data as bd
        from beancount.core.amount import Amount
        from decimal import Decimal
        from datetime import date as d

        ledger = tmp_path / "data" / "ledgers" / "main.beancount"
        ledger.parent.mkdir(parents=True)
        docs = tmp_path / "data" / "documents" / "2026"
        docs.mkdir(parents=True)
        meta_file = {"filename": str(ledger), "lineno": 1}

        txn = bd.Transaction(
            {**meta_file, "document": "../documents/2026/a.pdf",
             "document2": "../documents/2026/b.pdf"},
            d(2026, 1, 1), "*", "P", "n", frozenset(), frozenset(),
            [bd.Posting("Expenses:X", Amount(Decimal("1"), "USD"), None, None, None, None)],
        )
        # Document directive: filename absolute (as Beancount would store post-load)
        doc = bd.Document(
            meta_file, d(2026, 1, 2), "Assets:Bank",
            str((docs / "c.pdf").resolve()), frozenset(), frozenset(),
        )
        refs = collect_referenced_paths([txn, doc])
        assert refs == {
            (docs / "a.pdf").resolve(),
            (docs / "b.pdf").resolve(),
            (docs / "c.pdf").resolve(),
        }

    def test_collects_from_multiple_source_files(self, tmp_path):
        """I7: a file referenced only from a non-root included file is still in
        the referenced set (entries carry their own source-file dir)."""
        from beancount.core import data as bd
        from datetime import date as d

        root_dir = tmp_path / "data" / "ledgers"
        sub_dir = root_dir / "2026"
        sub_dir.mkdir(parents=True)
        docs = tmp_path / "data" / "documents"
        docs.mkdir(parents=True)

        root_txn = bd.Transaction(
            {"filename": str(root_dir / "main.beancount"), "lineno": 1,
             "document": "../documents/root.pdf"},
            d(2026, 1, 1), "*", "P", "n", frozenset(), frozenset(), [],
        )
        # included child file sits one level deeper -> different relative base
        child_txn = bd.Transaction(
            {"filename": str(sub_dir / "jan.beancount"), "lineno": 1,
             "document": "../../documents/child.pdf"},
            d(2026, 1, 2), "*", "P", "n", frozenset(), frozenset(), [],
        )
        refs = collect_referenced_paths([root_txn, child_txn])
        assert refs == {(docs / "root.pdf").resolve(), (docs / "child.pdf").resolve()}

    def test_non_document_meta_ignored(self, tmp_path):
        from beancount.core import data as bd
        from datetime import date as d
        ledger = tmp_path / "main.beancount"
        txn = bd.Transaction(
            {"filename": str(ledger), "lineno": 1, "id": "abc", "source_account": "Assets:X"},
            d(2026, 1, 1), "*", "P", "n", frozenset(), frozenset(), [],
        )
        assert collect_referenced_paths([txn]) == set()


# ── I4: document_count helper (incl. tolerance of non-gapless external edits) ──

class TestDocumentCount:
    def test_counts_document_keys(self):
        from app.services.sqlite_exporter import SQLiteExporter
        assert SQLiteExporter._count_documents({"document": "a"}) == 1
        assert SQLiteExporter._count_documents(
            {"document": "a", "document2": "b", "document3": "c"}) == 3

    def test_non_gapless_external_edit_still_counted(self):
        from app.services.sqlite_exporter import SQLiteExporter
        # hand-authored ledger with a gap (document, document3) — we tolerate it
        assert SQLiteExporter._count_documents({"document": "a", "document3": "c"}) == 2

    def test_zero_for_none_and_unrelated_keys(self):
        from app.services.sqlite_exporter import SQLiteExporter
        assert SQLiteExporter._count_documents(None) == 0
        assert SQLiteExporter._count_documents(
            {"id": "x", "source_account": "Assets:Y", "memo": "m"}) == 0


# ── I6: relativize-on-write (the rename-invariant cornerstone) ─────────────────

class TestRelativizeDocumentPath:
    def _doc(self, filename, src="/root/data/ledgers/main.beancount"):
        from beancount.core import data as bd
        from datetime import date as d
        return bd.Document(
            {"filename": src, "lineno": 1}, d(2026, 1, 1),
            "Assets:Bank", filename, frozenset(), frozenset(),
        )

    def test_absolute_filename_relativized_to_target_dir(self, tmp_path):
        from app.core.beancount_engine import BeancountEngine
        docs = tmp_path / "data" / "documents" / "2026"
        docs.mkdir(parents=True)
        ledger_dir = tmp_path / "data" / "ledgers"
        ledger_dir.mkdir(parents=True)
        f = docs / "x.pdf"
        f.write_bytes(b"x")
        doc = self._doc(str(f.resolve()))
        out = BeancountEngine.relativize_document_path(doc, ledger_dir)
        assert out.filename == "../documents/2026/x.pdf"

    def test_relative_filename_left_untouched(self, tmp_path):
        from app.core.beancount_engine import BeancountEngine
        ledger_dir = tmp_path / "data" / "ledgers"
        ledger_dir.mkdir(parents=True)
        doc = self._doc("../documents/2026/x.pdf")
        out = BeancountEngine.relativize_document_path(doc, ledger_dir)
        assert out.filename == "../documents/2026/x.pdf"

    def test_non_document_passes_through(self):
        from app.core.beancount_engine import BeancountEngine
        from beancount.core import data as bd
        from datetime import date as d
        note = bd.Note({"filename": "/x", "lineno": 1}, d(2026, 1, 1), "Assets:Bank", "hi", frozenset(), frozenset())
        assert BeancountEngine.relativize_document_path(note, Path("/x")) is note


# ── Documents-root resolution ────────────────────────────────────────────────────

class TestRootResolution:
    def test_default_is_per_ledger_subfolder(self, tmp_path):
        # No option -> a subfolder keyed by the ledger stem, so two ledgers
        # never share a documents root.
        default = str(tmp_path / "data" / "documents")
        a = resolve_documents_root(
            ledger_file=str(tmp_path / "data" / "ledgers" / "personal.beancount"),
            options={}, default_dir=default,
        )
        b = resolve_documents_root(
            ledger_file=str(tmp_path / "data" / "ledgers" / "business.beancount"),
            options={}, default_dir=default,
        )
        assert a == (Path(default) / "personal").resolve()
        assert b == (Path(default) / "business").resolve()
        assert a != b

    def test_respects_relative_option(self, tmp_path):
        ledger = tmp_path / "data" / "ledgers" / "main.beancount"
        ledger.parent.mkdir(parents=True)
        root = resolve_documents_root(
            ledger_file=str(ledger), options={"documents": ["../docs"]},
            default_dir=str(tmp_path / "data" / "documents"),
        )
        assert root == (ledger.parent / "../docs").resolve()

    def test_option_value_is_relative_to_ledger_dir(self, tmp_path):
        ledger = tmp_path / "data" / "ledgers" / "main.beancount"
        ledger.parent.mkdir(parents=True)
        docroot = (tmp_path / "data" / "documents")
        docroot.mkdir(parents=True)
        value = documents_option_value(documents_root=docroot, ledger_file=str(ledger))
        assert value == "../documents"
