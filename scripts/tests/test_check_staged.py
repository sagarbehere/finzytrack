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


STATEMENT = b"".join(b"2024-01-%02d,ACME STORE,-12.%02d\n" % (d, d) for d in range(1, 8))


class Bl0023(unittest.TestCase):
    """BL-0023 (S0019 verifier F1, F2): the statement sniff covers corpora/ and fixtures/; MANIFEST rows are validated."""

    def test_statement_like_text_in_corpora_or_fixtures_is_refused(self) -> None:
        for path in ("corpora/imports/statement.txt", "backend/tests/fixtures/rows.dat", "corpora/x/export"):
            with self.subTest(path=path):
                self.assertTrue(cs.check([path], blobs({path: STATEMENT})))

    def test_the_named_synthetic_assets_pass(self) -> None:
        import json
        as_json = json.dumps({"lines": STATEMENT.decode().splitlines()}, indent=1).encode()
        for path in cs.SYNTHETIC_ASSETS:
            with self.subTest(path=path):
                self.assertEqual(cs.check([path], blobs({path: as_json})), [])
                # S0026 verifier F5: the path alone does not vouch: a CSV written over it is refused
                self.assertTrue(cs.check([path], blobs({path: STATEMENT})))
        for other in ("corpora/ledgers/inv21/goldens2.json", "corpora/ledgers/inv21/goldens.json.txt"):
            with self.subTest(near=other):  # a near or longer name is not exempt
                self.assertTrue(cs.check([other], blobs({other: STATEMENT})))

    def test_a_reviewed_statement_in_corpora_passes(self) -> None:
        path = "corpora/imports/statement.txt"
        row = f"{path}\t{hashlib.sha256(STATEMENT).hexdigest()}\t2026-10-24\treviewed\n".encode()
        self.assertEqual(cs.check([path], blobs({path: STATEMENT, cs.MANIFEST: row})), [])

    def test_plain_text_in_corpora_passes(self) -> None:
        path = "corpora/ledgers/write/documents/docs/a.txt"
        self.assertEqual(cs.check([path], blobs({path: b"synthetic statement placeholder\n"})), [])

    def test_malformed_manifest_rows_are_refused_when_the_manifest_is_staged(self) -> None:
        good = "a\t" + "0" * 64 + "\t2026-10-24\tok\n"
        for bad in ("a\t" + "0" * 63 + "\t2026-10-24\tx", "a\t" + "g" * 64 + "\t2026-10-24\tx",
                    "a\t" + "0" * 64 + "\t2026-02-30\tx", "a\t" + "0" * 64 + "\t24-10-2026\tx",
                    "a\t" + "0" * 64 + "\t2999-01-01\tx", "a\t" + "0" * 64, "\t" + "0" * 64 + "\t2026-10-24\tx"):
            with self.subTest(row=bad):
                manifest = (cs.MANIFEST, (good + bad + "\n").encode())
                self.assertTrue(cs.check([cs.MANIFEST], blobs(dict([manifest]))))
        self.assertEqual(cs.check([cs.MANIFEST], blobs({cs.MANIFEST: ("# header\n" + good).encode()})), [])

    def test_a_future_or_bad_row_does_not_vouch_for_a_file(self) -> None:
        path = "corpora/a.csv"
        row = f"{path}\t{hashlib.sha256(b'x,y').hexdigest()}\t2999-01-01\treviewed\n".encode()
        self.assertTrue(cs.check([path], blobs({path: b"x,y", cs.MANIFEST: row})))



class Bl0023Round2(unittest.TestCase):
    """S0026 verifier F1 (UTF-16/32 text) and F4 (the review date boundary; a bad committed row)."""

    def test_unicode_text_exports_are_sniffed(self) -> None:
        ofx = b"OFXHEADER:100\n<OFX><BANKTRANLIST><STMTTRN>\n"
        for codec, bom in (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"), ("utf-32-le", b"\xff\xfe\x00\x00"),
                           ("utf-32-be", b"\x00\x00\xfe\xff")):
            for body in (STATEMENT, ofx):
                for path in ("corpora/imports/s.txt", "notes/s.txt"):
                    with self.subTest(codec=codec, path=path, ofx=body is ofx):
                        blob = bom + body.decode().encode(codec)
                        self.assertTrue(cs.check([path], blobs({path: blob})))

    def test_a_row_dated_today_vouches(self) -> None:
        from datetime import date
        row = "a\t" + "0" * 64 + "\t2026-10-25\tok\n"
        entries, problems = cs.parse_manifest(row, today=date(2026, 10, 25))
        self.assertEqual((list(entries), problems), (["a"], []))
        self.assertTrue(cs.parse_manifest(row, today=date(2026, 10, 24))[1])

    def test_a_bad_committed_row_does_not_block_an_unrelated_commit(self) -> None:
        bad = ("a\t" + "0" * 64 + "\t2999-01-01\tx\n").encode()
        self.assertEqual(cs.check(["design/notes.md"], blobs({cs.MANIFEST: bad, "design/notes.md": b"hello\n"})), [])
        self.assertTrue(cs.check([cs.MANIFEST], blobs({cs.MANIFEST: bad})))


if __name__ == "__main__":
    unittest.main()
