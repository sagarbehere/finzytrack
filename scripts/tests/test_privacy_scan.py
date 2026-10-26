"""Meta-tests for the T0 privacy scanner (INV-23). Each rule has a case it must flag and one it must pass.

Sensitive-looking strings are assembled at runtime so this file itself stays clean for the scanner.
Run: python3 -m unittest discover -s scripts/tests
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import privacy_scan as ps  # noqa: E402


def j(*parts: str) -> str:
    return "".join(parts)


def rules(text: str, denylist: list[str] | None = None) -> list[str]:
    return [f.rule for f in ps.scan_text("x.md", text, denylist or [])]


class MustFlag(unittest.TestCase):
    def test_card_number_with_spaces(self) -> None:
        # a Luhn-valid number that is not on the test-card allowlist
        self.assertIn("card-number", rules(j("card ", "4539", " 1488", " 0343", " 6467")))

    def test_long_account_number(self) -> None:
        self.assertIn("account-number", rules(j("savings ", "5010", "0234", "5678", "91")))

    def test_short_account_number_near_account_word(self) -> None:
        self.assertIn("account-number", rules(j("account no ", "123", "456", "789")))

    def test_pan(self) -> None:
        self.assertIn("pan", rules(j("pan: ", "ABCPE", "1234", "F")))

    def test_iban(self) -> None:
        self.assertIn("iban", rules(j("GB82", " WEST", " 1234", " 5698", " 7654", " 32")))

    def test_email(self) -> None:
        self.assertIn("email", rules(j("mail ", "someone", "@", "gmail.com")))

    def test_openai_style_key(self) -> None:
        self.assertIn("secret", rules(j("key = ", "sk-", "proj-", "A1b2C3d4E5f6G7h8I9j0K1l2")))

    def test_github_token(self) -> None:
        self.assertIn("secret", rules(j("ghp", "_", "a" * 20, "B" * 16)))

    def test_private_key_block(self) -> None:
        self.assertIn("secret", rules(j("-----BEGIN ", "OPENSSH PRIVATE", " KEY-----")))

    def test_tauri_updater_secret_key(self) -> None:
        self.assertIn("secret", rules(j("untrusted comment: ", "rsign encrypted ", "secret key")))

    def test_assigned_credential(self) -> None:
        self.assertIn("secret", rules(j('password = "', "hunter2hunter2xx", '"')))

    def test_home_path(self) -> None:
        self.assertIn("home-path", rules(j("see ", "/Us", "ers/", "alice", "/notes.txt")))

    def test_windows_home_path(self) -> None:
        self.assertIn("home-path", rules(j("C:", "\\", "Users", "\\", "bob", "\\file")))

    def test_denylist_literal_case_insensitive(self) -> None:
        self.assertIn("denylist", rules("Paid 1,234.56 to ACME", ["acme"]))

    def test_allow_mark_only_suppresses_named_rule(self) -> None:
        line = j("mail ", "someone", "@", "gmail.com", " /Us", "ers/", "alice", "/x  privacy-scan: allow email")
        self.assertEqual(rules(line), ["home-path"])


class BypassesFoundByReview(unittest.TestCase):
    """Bypass attempts from the adversarial review of the seed (review:G, G-2..G-7)."""

    def test_account_number_after_separators(self) -> None:
        n = j("5010", "0987", "6543", "22")
        for text in (f"acct:{n}", f"UPI/P2A/{n}/JOHN", f'"accounts": {n}', f"ref_{n}"):
            self.assertIn("account-number", rules(text), text)

    def test_grouped_and_masked_account_numbers(self) -> None:
        self.assertIn("account-number", rules(j("account ", "5010 ", "0987 ", "6543 ", "22")))
        self.assertIn("account-number", rules(j("a/c ", "5010-", "0987-", "6543-", "22")))
        self.assertIn("account-number", rules(j("card ", "XXXXXXXX", "4321")))

    def test_card_with_odd_separators(self) -> None:
        for sep in (".", "/", "_", "  ", "\u00a0"):
            self.assertIn("card-number", rules(sep.join(["4539", "1488", "0343", "6467"])), repr(sep))

    def test_lowercase_or_dashed_iban_and_labelled_pan(self) -> None:
        self.assertIn("iban", rules(j("gb82", "west", "1234", "5698", "7654", "32")))
        self.assertIn("iban", rules(j("GB82-", "WEST-", "1234-", "5698-", "7654-", "32")))
        self.assertIn("pan", rules(j("pan: ", "abcpe", "1234", "f")))

    def test_credential_shapes(self) -> None:
        val = j("Zq9", "xW2", "pL7", "mN4", "vB8")
        for text in (f"OPENAI_API_KEY={val}", f'"apiKey": "{val}"', f"api_key: {val}",
                     j("Authorization: Bearer ", val, val), j("https://bob:", "s3cr3tpass", "@db.example.org"),
                     j("sk_", "live_", val, val), j("hf_", val, val, val), j("eyJ", "hbGciOi", "JIUzI1N.", "eyJ", "zdWIiOi", "IxMjM0.", "abcdefghij")):
            self.assertIn("secret", rules(text), text)

    def test_home_paths_in_other_forms(self) -> None:
        self.assertIn("home-path", rules(j("~", "alice", "/notes")))
        self.assertIn("home-path", rules(j("/Vol", "umes/", "Backup/x")))
        self.assertIn("live-data-path", rules(j("~/", "Finzytrack", "Data/config")))

    def test_utf16_text_is_scanned(self) -> None:
        data = j("mail ", "someone", "@", "gmail.com").encode("utf-16")
        text = ps.decode(data)
        self.assertIsNotNone(text)
        self.assertIn("email", rules(text or ""))
        self.assertIsNone(ps.decode(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\x01\x02\x03"))


class MustPass(unittest.TestCase):
    def test_allowlisted_fake_numbers(self) -> None:
        self.assertEqual(rules(j("account ", "0000", "1234", "5678", " and test card ", "4111", "1111", "1111", "1111")), [])

    def test_luhn_invalid_digits_short(self) -> None:
        self.assertEqual(rules("order 1234567890 shipped"), [])

    def test_placeholder_digit_runs(self) -> None:
        self.assertEqual(rules("account 000000000000"), [])

    def test_iso_dates_amounts_and_versions(self) -> None:
        self.assertEqual(rules("2026-10-24 * \"Coffee\"\n  Expenses:Food  4.50 USD\npin 1.1.414 and 3.13.16"), [])

    def test_sha256_hex(self) -> None:
        self.assertEqual(rules("sha256 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"), [])

    def test_example_emails(self) -> None:
        self.assertEqual(rules(j("a", "@", "example.com ", "noreply", "@", "anthropic.com")), [])

    def test_placeholder_home_paths(self) -> None:
        self.assertEqual(rules(j("/Us", "ers/", "<user>", "/x and /home/", "runner", "/work")), [])

    def test_placeholder_credentials(self) -> None:
        self.assertEqual(rules('api_key = "${OPENAI_API_KEY}"\ntoken: "xxxxxxxxxxxxxxxx"'), [])

    def test_retina_asset_names_are_not_emails(self) -> None:
        self.assertEqual(rules('"icons/128x128@2x.png", "logo@3x.webp"'), [])

    def test_package_versions_are_not_emails(self) -> None:
        self.assertEqual(rules("  react@19.3.0:\n    '@types/node@24.1.0': {}"), [])

    def test_sizes_and_epoch_millis_are_not_account_numbers(self) -> None:
        self.assertEqual(rules('"quota": 20615843021,\n"at": 1792862321002\nsize_bytes = 123456789012'), [])

    def test_account_number_still_caught_in_json(self) -> None:
        self.assertIn("account-number", rules(j('"number": "', "5010", "0234", "5678", '91"')))

    def test_shared_folder_and_config_home(self) -> None:
        self.assertEqual(rules("/Users/Shared/x and ~/.config/finzytrack/denylist.txt"), [])

    def test_env_placeholders_and_code_references(self) -> None:
        self.assertEqual(rules('OPENAI_API_KEY=${OPENAI_API_KEY}\napi_key = os.environ["OPENAI_API_KEY"]\ntoken: settings.ai.token_value'), [])

    def test_dates_near_bank_words(self) -> None:
        self.assertEqual(rules("bank statement 2026-10-24 to 2026-11-23, 31 days"), [])

    def test_uppercase_words_are_not_pan(self) -> None:
        self.assertEqual(rules("CHECKING ACCOUNT EXPENSES"), [])


def rules_in(path: str, text: str) -> list[str]:
    return [f.rule for f in ps.scan_text(path, text, [])]


class SeededMissesS0033(unittest.TestCase):
    """Seeded-defect run 1 A05, A06, A11 (S0033): a UPI address alone, 6-10 digits next to an account word, and a
    statement excerpt in prose. Every value here is invented and assembled at runtime."""

    def test_upi_handles_are_flagged(self) -> None:
        for text in (j("narration sample: UPI/", "rahul.verma42", "@", "okaxis", "/Swiggy"),  # plant A11
                     j("paid to ", "asha.k", "@", "ybl"), j("VPA: ", "9876501234", "@", "paytm", "."),
                     j("| ", "Kiran_77", "@", "OKSBI", " |"), j("upi://pay?pa=", "store.owner", "@", "icici", "&am=1"),
                     j("(", "m-rao", "@", "axl", ")")):
            with self.subTest(text=text):
                self.assertIn("upi-handle", rules(text))

    def test_emails_placeholders_and_unknown_handles_are_not_upi_handles(self) -> None:
        for text in (j("mail ", "a.b", "@", "icici.com"), j("see ", "name", "@", "okaxis", " for the shape"),
                     j("user", "@", "ybl"), j("x", "@", "ybl", ".example.com"), "@ybl alone", "react@19.3.0",
                     j("someone", "@", "mybank"), j("pkg", "@", "upi-tools")):
            with self.subTest(text=text):
                self.assertNotIn("upi-handle", rules(text))

    def test_a_marked_upi_line_passes(self) -> None:
        self.assertEqual(rules(j("asha.k", "@", "ybl", "  privacy-scan: allow upi-handle")), [])

    def test_short_account_numbers_next_to_an_account_word(self) -> None:
        for text in (j("Seen in the wild: `Acct #: ", "7392", "0481", "` on an export."),  # plant A06
                     j("A/C No. ", "1234", "567"), j("account number: ", "9988", "7766"), j("acc ", "7654", "321"),
                     j("ACCOUNT NO-", "4821", "3756"), j("acct#", "583920"), j("Account ", "58392", "01746"),
                     j("Acct No: ", "7392", " 0481"), j("a/c ", "7392", "-0481"), j("account no ", "739-204", "-81"),
                     # S0033 verifier F3: sentence punctuation after the number
                     j("Account no. ", "7392", "0481", "."), j("Acct #: ", "7392", "0481", ", branch Pune"),
                     j("A/C No. ", "7392", "0481", "."), j("acct ", "7392", "0481", ";"), j("(acct ", "7392", "0481", ")"),
                     j("account ", "7392", "0481", ". Next"),
                     # ten digits after the words the line-wide account context does not know (`acc`, `ac no`)
                     j("acc ", "76543", "21098"), j("ac no ", "76543", "21098")):
            with self.subTest(text=text):
                self.assertIn("account-number", rules(text))

    def test_numbers_that_are_not_next_to_an_account_word(self) -> None:
        for text in ("account 2026", "accounts: 12345", "account_id 1234567", "Acct 2026-03-01", "account 1234567.89",
                     "sha ac35103371ab9f", "transaction 1234567", "card 1234567", "account 111111", "accounting 7654321",
                     "3 accounts, 1234567 rows", "account 2026 03 01", "acct 12 34", "account 1234567,89",
                     "account 1234567.5", "account 1,234,567.00"):
            with self.subTest(text=text):
                self.assertNotIn("account-number", rules(text))

    def test_one_finding_for_a_long_number_next_to_an_account_word(self) -> None:
        self.assertEqual(rules(j("account no ", "5839", "2017", "46")), ["account-number"])

    EXCERPT = j("Repro excerpt from the bank PDF:\n\n",  # plant A05, synthetic
                "    01 Mar 2026  SALARY ACME SOFTWARE PVT LTD     1,84,250.00 Cr\n",
                "    03 Mar 2026  RENT TO R K SHARMA                 42,000.00 Dr\n",
                "    05 Mar 2026  SCHOOL FEES DPS NOIDA              18,600.00 Dr\n")

    def test_a_statement_excerpt_in_prose_or_a_message(self) -> None:
        csv = "2026-03-01,Coffee Corner,-4.50,1020.25\n2026-03-02,Book Shop,-12.00,1008.25\n2026-03-04,Salary,900.00,1908.25\n"
        table = ("| Date | Payee | Amount |\n|---|---|---|\n| 02/03/2026 | Corner Store | 140.00 |\n"
                 "| 03/03/2026 | Fuel Station | 2,300.50 |\n| 07/03/2026 | Pharmacy | 89.90 |\n")
        for path, text in (("design/journal/S0030-acceptance-harness.md", self.EXCERPT), ("notes.txt", csv),
                           ("docs/x.md", table), ("commit 0123456789ab message", self.EXCERPT),
                           ("tag 0123456789ab message", csv), ("commit message", table), ("README.rst", csv),
                           ("notes.md", self.EXCERPT.replace("Cr\n", "Cr\n\n\n").replace("42,000.00 Dr\n", "42,000.00 Dr\n\n"))):
            with self.subTest(path=path):
                self.assertEqual(rules_in(path, text).count("statement-excerpt"), 1)

    def test_the_six_line_window(self) -> None:  # S0033 verifier F5: "three or more within six lines"
        line = "    {:02d} Mar 2026  RENT TO SOMEONE   42,000.00 Dr"
        def text(*at: int) -> str:
            return "\n".join(line.format(n) if n in at else "" for n in range(1, max(at) + 1)) + "\n"
        self.assertEqual(rules_in("notes.md", text(1, 3, 6)).count("statement-excerpt"), 1)  # six lines
        self.assertNotIn("statement-excerpt", rules_in("notes.md", text(1, 4, 7)))  # seven lines
        self.assertNotIn("statement-excerpt", rules_in("notes.md", text(1, 4, 8)))
        # a long excerpt is one finding; a second one more than six lines on is another
        self.assertEqual(rules_in("notes.md", text(1, 2, 3, 4, 5, 6, 7, 8)).count("statement-excerpt"), 1)
        self.assertEqual(rules_in("notes.md", text(1, 2, 3, 9, 10, 11)).count("statement-excerpt"), 2)
        # the second needs three lines of its own: 1-3, then 9 and 10 alone are not one
        self.assertEqual(rules_in("notes.md", text(1, 2, 3, 9, 10)).count("statement-excerpt"), 1)

    def test_beancount_shaped_lines_are_not_statement_lines(self) -> None:  # S0033 verifier F4
        register = ("```\ndate        account                  position\n"
                    "2026-03-01  Expenses:Food:Groceries  45.20 USD\n2026-03-02  Expenses:Transport       12.00 USD\n"
                    "2026-03-05  Expenses:Food:Dining     30.50 USD\n```\n")
        prices = ("| date | commodity | price |\n|---|---|---|\n| 2026-03-01 | INFY | 1520.35 |\n"
                  "| 2026-03-02 | INFY | 1525.10 |\n| 2026-03-03 | RELIANCE.NS | 2519.80 |\n")
        balances = ("2026-01-31  Assets:Bank:Checking  1000.00 USD\n2026-02-28  Assets:Bank:Checking  1250.00 USD\n"
                    "2026-03-31  Liabilities:CreditCard  -1100.00 USD\n")
        priced = ("| 2026-03-01 | Corner Store | 140.00 INR |\n| 2026-03-02 | Fuel Station | 2,300.50 INR |\n"
                  "| 2026-03-03 | Pharmacy | 89.90 INR |\n")
        narrated = ("2026-03-01  Coffee  Expenses:Food  4.50 USD\n2026-03-02  Tea  Expenses:Food  2.50 USD\n"
                    "2026-03-03  Cake  Expenses:Food  3.50 USD\n")
        accounts = ("| date | account | amount |\n|---|---|---|\n| 2026-03-01 | Expenses:Food:Groceries | 45.20 |\n"
                    "| 2026-03-02 | Income:Salary | -900.00 |\n| 2026-03-03 | Assets:Bank:Checking | 1,020.25 |\n")
        for name, text in (("register", register), ("prices", prices), ("balances", balances), ("priced", priced),
                           ("narrated", narrated), ("accounts without a commodity", accounts)):
            with self.subTest(name):
                self.assertNotIn("statement-excerpt", rules_in("design/specs/x.md", text))

    def test_statement_lines_that_only_look_beancount_shaped_still_count(self) -> None:
        for name, text in (
                ("a word after the amount", "2026-03-01  RENT TO SOMEONE  42,000.00 Debit\n"
                 "2026-03-02  SALARY ACME  1,84,250.00 Credit\n2026-03-03  SCHOOL FEES  18,600.00 debit\n"),
                ("Dr and Cr after the amount", "2026-03-01  RENT TO SOMEONE  42,000.00 DR\n"
                 "2026-03-02  SALARY ACME  1,84,250.00 CR\n2026-03-03  SCHOOL FEES  18,600.00 Dr\n"),
                ("a one-word narration with Dr or Cr", "| 2026-03-01 | SALARY | 184250.00 Cr |\n"
                 "| 2026-03-02 | RENT | 42000.00 Dr |\n| 2026-03-03 | INTEREST | 120.00 CR |\n"),
                ("upper-case account words", "2026-03-01  EXPENSES:FOOD  45.20\n2026-03-02  INCOME:SALARY  900.00\n"
                 "2026-03-03  UPI:SWIGGY  12.00\n"),
                ("a ticker-shaped word on a non-ISO date", "| 01/03/2026 | RENT | 42000.00 |\n"
                 "| 02/03/2026 | FUEL | 2300.50 |\n| 03/03/2026 | ATM | 500.00 |\n"),
                ("a currency code on a non-ISO date", "01 Mar 2026  RENT TO SOMEONE  42,000.00 INR\n"
                 "02 Mar 2026  FUEL STATION  2,300.50 INR\n03 Mar 2026  PHARMACY  89.90 INR\n"),
                ("an account name on a non-ISO date", "01/03/2026  Expenses:Food  45.20\n"
                 "02/03/2026  Expenses:Food  12.00\n03/03/2026  Expenses:Food  30.50\n")):
            with self.subTest(name):
                self.assertEqual(rules_in("notes.md", text).count("statement-excerpt"), 1)
        self.assertEqual(rules_in("notes.md", self.EXCERPT).count("statement-excerpt"), 1)  # plant A05

    def test_what_is_not_a_statement_excerpt(self) -> None:
        two = "\n".join(self.EXCERPT.splitlines()[:4]) + "\n"
        spread = self.EXCERPT.replace("Cr\n", "Cr\n\n\n\n\n\n")
        beancount = ('2026-03-01 * "Corner Store" "Milk"\n  Assets:Bank  -4.50 USD\n'
                     "2026-03-01 balance Assets:Bank  1,020.25 USD\n2026-03-02 price ACME  140.00 USD\n"
                     "2026-03-03 balance Assets:Bank  1,008.25 USD\n")
        no_text = "2026-03-01  42.00\n2026-03-02  43.00\n2026-03-03  44.00\n"
        marked = "".join(line + "  privacy-scan: allow statement-excerpt\n" for line in self.EXCERPT.splitlines()[2:])
        for path, text in (("backend/tests/test_csv.py", self.EXCERPT), ("frontend/src/x.ts", self.EXCERPT),
                           ("data.json", self.EXCERPT), ("notes.md", two), ("notes.md", spread),
                           ("docs/ledger.md", beancount), ("notes.md", no_text), ("notes.md", marked)):
            with self.subTest(path=path, text=text[:40]):
                self.assertNotIn("statement-excerpt", rules_in(path, text))


class Plumbing(unittest.TestCase):
    def test_binary_detection(self) -> None:
        self.assertTrue(ps.is_binary(b"\x89PNG\0\0"))
        self.assertFalse(ps.is_binary(b"plain text"))

    def test_redaction_never_prints_the_secret(self) -> None:
        secret = j("sk-", "proj-", "A1b2C3d4E5f6G7h8I9j0K1l2")
        findings = ps.scan_text("x", secret, [])
        self.assertTrue(findings)
        self.assertNotIn(secret, str(findings[0]))

    def test_luhn_and_iban_checksums(self) -> None:
        self.assertTrue(ps.luhn_ok(j("4539", "1488", "0343", "6467")))
        self.assertFalse(ps.luhn_ok(j("4539", "1488", "0343", "6468")))
        self.assertTrue(ps.iban_ok(j("GB82", "WEST", "1234", "5698", "7654", "32")))
        self.assertFalse(ps.iban_ok(j("GB82", "WEST", "1234", "5698", "7654", "33")))



class SecretEntropy(unittest.TestCase):
    """BL-0035: detect-secrets 1.5.0 from its pinned environment, network verification off, fail closed;
    a 64-hex value under a `sha256` key is a hash pin. Planted values are assembled at runtime."""

    HEX = hashlib.sha256(b"finzytrack test value").hexdigest()
    B64 = base64.b64encode(hashlib.sha256(b"another test value").digest()).decode()

    def found(self, text: str, path: str = "x.py") -> list[str]:
        return [f.rule for f in ps.entropy_findings({path: text})]

    def test_must_flag(self) -> None:
        cases = {
            "aws key id": j("aws_key = \"AKIA", "Q3EGRIUIUHWS", "J3XY\"\n"),
            "high-entropy base64": f'token = "{self.B64}"\n',
            "a 64-hex value under another key": f'"token": "{self.HEX}"\n',
            "a hash-looking value one character short": f'"sha256": "{self.HEX[:63]}"\n',
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                self.assertIn("secret-entropy", self.found(text))

    def test_must_pass(self) -> None:
        cases = {
            "a sha256 pin": f'"sha256": "{self.HEX}",\n',
            "a marked line": f'token = "{self.B64}"  # privacy-scan: allow secret-entropy\n',
            "plain prose": "The scanner reads every staged file.\n",
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.found(text), [])

    def test_the_scan_of_staged_or_tracked_files_runs_the_rule(self) -> None:
        text = f'token = "{self.B64}"\n'
        findings, _ = ps.run(["a.py"], lambda p: text.encode(), [])
        self.assertIn("secret-entropy", [f.rule for f in findings])

    def test_network_verification_is_off(self) -> None:
        seen: list[list[str]] = []

        def fake_run(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout='{"results": {}}', stderr="")

        with mock.patch.object(ps.subprocess, "run", fake_run):
            ps.detect_secrets({"x.py": "x = 1\n"})
        self.assertIn("--no-verify", seen[0])

    def test_the_tool_failing_is_an_error_never_a_pass(self) -> None:
        failures = {
            "missing environment": lambda cmd, **kw: (_ for _ in ()).throw(OSError("uv not found")),
            "non-zero exit": lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"),
            "unreadable output": lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr=""),
        }
        for name, fake in failures.items():
            with self.subTest(case=name), mock.patch.object(ps.subprocess, "run", fake):
                with self.assertRaises(ps.ToolError):
                    ps.detect_secrets({"x.py": "x = 1\n"})

    def test_main_exits_2_when_the_tool_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.md"
            path.write_text("hello\n")
            with mock.patch.object(ps, "detect_secrets", side_effect=ps.ToolError("down")), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(ps.main([str(path), "--quiet"]), 2)

    def test_names_never_steer_the_tool(self) -> None:
        # S0016 verifier F1, F2, F6: blobs are written under generated names, so a path cannot leave the temp
        # folder, overwrite its source or be read as an option, and odd spellings map back.
        secret = f'token = "{self.B64}"\n'
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(tempfile, "tempdir", tmp):
            outside = Path(tmp) / "source.py"
            outside.write_bytes(b"caf\xe9\n")
            for path in (str(outside), "../escape.py", "--exclude-files=.*", "./a.py", "a//b.py", "-x.md"):
                with self.subTest(path=path):
                    got = ps.entropy_findings({path: secret, "other.py": secret})
                    self.assertEqual(sorted({f.path for f in got}), sorted({path, "other.py"}))
            self.assertEqual(outside.read_bytes(), b"caf\xe9\n")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["source.py"])  # nothing left behind

    def test_every_file_name_is_scanned(self) -> None:
        # F3: detect-secrets' name and extension skips are off; lock files and stylesheets are scanned too.
        for path in ("style.css", "notes.lock", "Cargo.lock", "yarn.lock", "app.js.map", "pic.svg"):
            with self.subTest(path=path):
                self.assertIn("secret-entropy", self.found(f'token = "{self.B64}"\n', path))

    def test_a_mark_applies_to_its_own_line_only(self) -> None:
        # F4: only "\n" ends a line, as in detect-secrets and in a diff; a form feed does not.
        mark = "# privacy-scan: allow secret-entropy"
        secret = f'token = "{self.B64}"'
        for name, text in {
            "a form feed before the mark": f"a\f{mark}\n{secret}\n",
            "a lone CR before the secret": f"a = 1\r{secret}\n{mark}\n",
            "CRLF line ends": f"a = 1\r\n{secret}\r\n{mark}\r\n",
        }.items():
            with self.subTest(case=name):
                self.assertIn("secret-entropy", self.found(text))

    def test_pin_keys_are_exact(self) -> None:
        # F5: a key that only ends in sha256 is not a pin; F7: a pin does not excuse a secret on its line.
        for text in (f'not_a_sha256 = "{self.HEX}"\n', f'api_secret_sha256: "{self.HEX}"\n',
                     f'"sha256": "{self.HEX}", "token": "{self.B64}"\n'):
            with self.subTest(text=text[:20]):
                self.assertIn("secret-entropy", self.found(text))
        for text in (f'checksum = "{self.HEX}"\n',):
            with self.subTest(text=text[:20]):
                self.assertEqual(self.found(text), [])

    def test_file_type_rules_still_apply(self) -> None:
        # S0016 round-2 F2: the suffix is kept, so detect-secrets' per-language keyword rules run.
        word = "pass" + "word"
        cases = {"main.go": f'{word} := "hunter22x"\n', "app.c": f'char {word}[] = "hunter22x";\n'}
        for path, text in cases.items():
            with self.subTest(path=path):
                self.assertIn("secret-entropy", self.found(text, path))

    def test_detection_does_not_depend_on_the_file_name(self) -> None:
        # S0016 round-3 F1: each text is scanned as `.txt` and under its own suffix, and the findings united.
        word = "pass" + "word"
        cases = {
            "yaml block scalar": ("c.yaml", f"key: |\n  {self.B64}\n"),
            "yaml folded scalar": ("c.yml", f"key: >\n  text\n  {self.B64}\n"),
            "yaml multi-line plain scalar": ("c.yaml", f"key: aaa\n  {self.B64}\n"),
            "python unquoted value": ("a.py", f"x = {self.B64}\n"),
            "python unquoted keyword": ("a.py", f"db_{word} = hunter2abc\n"),
            "properties": ("a.properties", f"b={self.B64}\n"),
        }
        for name, (path, text) in cases.items():
            with self.subTest(case=name):
                self.assertIn("secret-entropy", self.found(text, path))

    def test_yaml_line_breaks_do_not_move_a_mark(self) -> None:
        # R3-F2, R4-F1, R4-F2: a mark or pin excuses a hit only when every numbering detect-secrets may have used
        # shows it on that line. The unquoted value after the separator is found by the YAML scan only.
        mark = "# privacy-scan: allow secret-entropy"
        for sep in ("\u2028", "\x85", "\u2029"):
            with self.subTest(sep=repr(sep), case="mark on the line below"):
                self.assertIn("secret-entropy", self.found(f"a: 1{sep}k: {self.B64}\n{mark}\n", "c.yaml"))
            with self.subTest(sep=repr(sep), case="a marked line with the same number elsewhere"):
                text = f'a: 1{sep}k: {self.B64}\nold: "{self.B64}"  {mark}\n'
                self.assertIn("secret-entropy", self.found(text, "c.yaml"))
            with self.subTest(sep=repr(sep), case="a pin with the same number elsewhere"):
                text = f'a: 1{sep}token: {self.HEX}\nsha256: "{self.HEX}"\n'
                self.assertIn("secret-entropy", self.found(text, "c.yaml"))
        word = "pass" + "word"
        with self.subTest(case="unparsable YAML numbered by universal newlines"):
            self.assertIn("secret-entropy", self.found(f"bad: [\u2028{mark}\n  {word}: hunter2abc\n", "c.yaml"))

    def test_too_old_a_python_refuses(self) -> None:
        with mock.patch.object(ps.sys, "version_info", (3, 9, 6)), redirect_stderr(io.StringIO()):
            self.assertEqual(ps.main(["--quiet", "x.md"]), 2)

    def test_a_supported_python_scans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.md"
            path.write_text("hello\n")
            for version in ((3, 12, 0), (3, 13, 5)):
                with self.subTest(version=version), mock.patch.object(ps.sys, "version_info", version), \
                        redirect_stderr(io.StringIO()):
                    self.assertEqual(ps.main(["--quiet", str(path)]), 0)

    def test_a_crlf_mark_above_a_secret_does_not_cover_it(self) -> None:
        # F3: CRLF is one line end, not two.
        text = "a = 1\r\n# privacy-scan: allow secret-entropy\r\n" + f'token = "{self.B64}"\r\n'
        self.assertIn("secret-entropy", self.found(text, "x.py"))

    def test_a_non_utf8_locale_does_not_skip_files(self) -> None:
        # F3: the tool runs in UTF-8 mode whatever the locale, so a file with non-ASCII text is still read.
        text = "# caf\u00e9 \u00c1\u00e9\u00ff\u20ac\n" + f'token = "{self.B64}"\n'  # not decodable as Shift JIS
        with mock.patch.dict(os.environ, {"LC_ALL": "ja_JP.SJIS", "LANG": "ja_JP.SJIS", "PYTHONUTF8": "0"}):
            self.assertIn("secret-entropy", self.found(text))

    def test_suffixes(self) -> None:
        self.assertEqual(ps.suffix("a/b/main.go"), ".go")
        self.assertEqual(ps.suffix("x/../--exclude-files=.*"), ".txt")
        self.assertEqual(ps.suffix("weird.$(x)"), ".txt")
        self.assertEqual(ps.suffix("noext"), ".txt")

    def test_one_finding_per_value_and_line(self) -> None:
        # F9: several plugins may report the same value; it is shown once.
        got = ps.entropy_findings({"x.py": j("password = \"AKIA", "Q3EGRIUIUHWS", "J3XY\"\n")})
        self.assertEqual(len(got), len({(f.path, f.line) for f in got}))

    def test_the_lockfile_is_used(self) -> None:
        seen: list[list[str]] = []

        def fake_run(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout='{"results": {}}', stderr="")

        with mock.patch.object(ps.subprocess, "run", fake_run):
            ps.detect_secrets({"x.py": "x = 1\n"})
        self.assertIn("--locked", seen[0])
        self.assertLess(seen[0].index("--no-verify"), seen[0].index("--"))


if __name__ == "__main__":
    unittest.main()
