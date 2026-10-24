"""Meta-tests for the T0 privacy scanner (INV-23). Each rule has a case it must flag and one it must pass.

Sensitive-looking strings are assembled at runtime so this file itself stays clean for the scanner.
Run: python3 -m unittest discover -s scripts/tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

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

    def test_uppercase_words_are_not_pan(self) -> None:
        self.assertEqual(rules("CHECKING ACCOUNT EXPENSES"), [])


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


if __name__ == "__main__":
    unittest.main()
