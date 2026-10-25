"""Meta-tests for the T0 privacy scanner (INV-23). Each rule has a case it must flag and one it must pass.

Sensitive-looking strings are assembled at runtime so this file itself stays clean for the scanner.
Run: python3 -m unittest discover -s scripts/tests
"""

from __future__ import annotations

import base64
import hashlib
import io
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
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "source.py"
            outside.write_bytes(b"caf\xe9\n")
            for path in (str(outside), "../escape.py", "--exclude-files=.*", "./a.py", "a//b.py", "-x.md"):
                with self.subTest(path=path):
                    got = ps.entropy_findings({path: secret, "other.py": secret})
                    self.assertEqual(sorted({f.path for f in got}), sorted({path, "other.py"}))
            self.assertEqual(outside.read_bytes(), b"caf\xe9\n")
            self.assertFalse((Path(tmp).parent / "escape.py").exists())

    def test_every_file_name_is_scanned(self) -> None:
        # F3: detect-secrets' name and extension skips are off; lock files and stylesheets are scanned too.
        for path in ("style.css", "notes.lock", "Cargo.lock", "yarn.lock", "app.js.map", "pic.svg"):
            with self.subTest(path=path):
                self.assertIn("secret-entropy", self.found(f'token = "{self.B64}"\n', path))

    def test_a_mark_applies_to_its_own_line_only(self) -> None:
        # F4: only "\n" ends a line, as in detect-secrets and in a diff; a form feed does not.
        text = "x = 1\f\f\f\f# privacy-scan: allow secret-entropy\n" + f'token = "{self.B64}"\n'
        self.assertIn("secret-entropy", self.found(text))
        text2 = "a = 1\n" * 4 + f'token = "{self.B64}"\n'
        self.assertIn("secret-entropy", self.found(text2))

    def test_pin_keys_are_exact(self) -> None:
        # F5: a key that only ends in sha256 is not a pin; F7: a pin does not excuse a secret on its line.
        for text in (f'not_a_sha256 = "{self.HEX}"\n', f'api_secret_sha256: "{self.HEX}"\n',
                     f'"sha256": "{self.HEX}", "token": "{self.B64}"\n'):
            with self.subTest(text=text[:20]):
                self.assertIn("secret-entropy", self.found(text))
        for text in (f'checksum = "{self.HEX}"\n', f'hash = "sha256:{self.HEX}"\n'):
            with self.subTest(text=text[:20]):
                self.assertEqual(self.found(text), [])

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
