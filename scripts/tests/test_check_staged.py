"""Meta-tests for the T0 staged-file guard (INV-23, INV-24): what it must refuse and what it must allow."""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_staged as cs  # noqa: E402


def blobs(files: dict[str, bytes]):
    return lambda p: files.get(p)


class Refuses(unittest.TestCase):
    def test_ledger_at_repo_root(self) -> None:
        self.assertTrue(cs.check(["main.beancount"], blobs({"main.beancount": b""})))

    def test_env_file_anywhere(self) -> None:
        self.assertTrue(cs.check(["backend/.env"], blobs({})))
        self.assertTrue(cs.check(["secrets.env"], blobs({})))

    def test_mirror_database(self) -> None:
        self.assertTrue(cs.check(["backend/ledger.db"], blobs({})))
        self.assertTrue(cs.check(["x/ledger.db-wal"], blobs({})))

    def test_app_config(self) -> None:
        self.assertTrue(cs.check(["backend/config.yaml"], blobs({})))

    def test_bank_export_even_in_corpora_without_manifest(self) -> None:
        self.assertTrue(cs.check(["corpora/imports/axis.xls"], blobs({"corpora/imports/axis.xls": b"data"})))

    def test_bank_export_with_wrong_hash(self) -> None:
        manifest = b"corpora/a.csv\t" + b"0" * 64 + b"\t2026-10-24\treviewed\n"
        files = {"corpora/a.csv": b"x,y\n", cs.MANIFEST: manifest}
        self.assertTrue(cs.check(["corpora/a.csv"], blobs(files)))

    def test_uppercase_extension(self) -> None:
        self.assertTrue(cs.check(["Statement.PDF"], blobs({"Statement.PDF": b"%PDF"})))


class RefusesBypassesFoundByReview(unittest.TestCase):
    """review:G G-1, G-8..G-11."""

    def test_renamed_statement(self) -> None:
        rows = b"".join(b"2026-01-%02d,Coffee shop,-4.50,1234.56\n" % d for d in range(1, 8))
        for name in ("notes.txt", "export", "data.json"):
            self.assertTrue(cs.check([name], blobs({name: rows})), name)

    def test_ofx_in_xml(self) -> None:
        self.assertTrue(cs.check(["bank.xml"], blobs({"bank.xml": b"<?xml?><OFX><BANKTRANLIST>"})))

    def test_other_export_and_archive_types(self) -> None:
        for name in ("a.tsv", "a.xlsm", "a.ods", "a.numbers", "a.zip", "a.csv.gz", "a.msg"):
            self.assertTrue(cs.check([name], blobs({name: b"x"})), name)

    def test_binary_blob(self) -> None:
        self.assertTrue(cs.check(["notes.bin"], blobs({"notes.bin": b"\x00\x01\x02"})))

    def test_other_ledger_formats_and_backups(self) -> None:
        for name in ("main.ledger", "x.journal", "main.beancount.bak", "main.beancount.gpg", "mirror.db"):
            self.assertTrue(cs.check([name], blobs({name: b""})), name)

    def test_secret_files_even_in_fixtures(self) -> None:
        for name in ("backend/tests/fixtures/.env", ".envrc", ".netrc", "config/secrets.yaml", "id_rsa"):
            self.assertTrue(cs.check([name], blobs({name: b""})), name)

    def test_symlink_outside_repo(self) -> None:
        self.assertTrue(cs.check(["link"], blobs({}), {"link": "/elsewhere/data/ledger.beancount"}))
        self.assertTrue(cs.check(["link"], blobs({}), {"link": "../../outside"}))
        self.assertEqual(cs.check(["link"], blobs({}), {"link": "design/README.md"}), [])


class Allows(unittest.TestCase):
    def test_images_and_manifest(self) -> None:
        self.assertEqual(cs.check(["icon.png", cs.MANIFEST], blobs({"icon.png": b"\x89PNG\0\0", cs.MANIFEST: b"# h\n"})), [])

    def test_markdown_and_code_with_dated_tables(self) -> None:
        rows = b"".join(b"| 2026-01-%02d | pin | 1.25 |\n" % d for d in range(1, 9))
        self.assertEqual(cs.check(["design/x.md", "backend/tests/test_x.py"], blobs({"design/x.md": rows, "backend/tests/test_x.py": rows})), [])

    def test_shipped_starter_ledger(self) -> None:
        self.assertEqual(cs.check(["backend/src/app/content/starter/one.beancount"], blobs({})), [])

    def test_env_example(self) -> None:
        self.assertEqual(cs.check([".env.example"], blobs({})), [])

    def test_ledgers_in_corpora_and_fixtures(self) -> None:
        self.assertEqual(cs.check(["corpora/nasty/crlf.beancount", "backend/tests/fixtures/one.beancount"], blobs({})), [])

    def test_reviewed_export_in_manifest(self) -> None:
        data = b"date,amount\n2026-01-01,1.00\n"
        manifest = f"corpora/a.csv\t{hashlib.sha256(data).hexdigest()}\t2026-10-24\tanonymized\n".encode()
        files = {"corpora/a.csv": data, cs.MANIFEST: manifest}
        self.assertEqual(cs.check(["corpora/a.csv"], blobs(files)), [])

    def test_source_files(self) -> None:
        self.assertEqual(cs.check(["backend/src/app/main.py", "design/README.md"], blobs({})), [])


if __name__ == "__main__":
    unittest.main()
