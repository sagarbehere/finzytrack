"""Meta-tests for the Cargo 14-day check (`scripts/check_cargo_age.py`; EP-24, ADR-0050 + 2026-10-25 amendment).

Expected outcomes come from EP-24's text: a version is adopted only once it is at least 14 days old (counted as
14 × 24 h, like pnpm's `minimumReleaseAge: 20160` minutes); the security exception lets a younger release in
only with a journal line naming the advisory. "Adopted" is the commit that first put the version in the lock
(BL-0052). Each passing case sits beside the case that must not pass (EP-29).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_cargo_age as cca  # noqa: E402

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
CRATES = 'source = "registry+https://github.com/rust-lang/crates.io-index"'


def lock(*pkgs: tuple[str, str], extra: str = "") -> str:
    body = "".join(f'[[package]]\nname = "{n}"\nversion = "{v}"\n{CRATES}\n\n' for n, v in pkgs)
    return 'version = 4\n\n[[package]]\nname = "finzytrack-shell"\nversion = "0.4.0-dev.0"\n\n' + body + extra


class Entries(unittest.TestCase):
    def test_crates_io_entries_are_read_and_the_own_package_skipped(self) -> None:
        entries, problems = cca.lock_entries(lock(("serde", "1.0.0"), ("serde", "2.0.0")))
        self.assertEqual(entries, {("serde", "1.0.0"), ("serde", "2.0.0")})
        self.assertEqual(problems, [])

    def test_other_sources_are_refused(self) -> None:
        for source in ('git+https://github.com/x/y?rev=abc#abc', "registry+https://example.com/index",
                       "sparse+https://index.crates.io/"):
            with self.subTest(source=source):
                extra = f'[[package]]\nname = "x"\nversion = "1.0.0"\nsource = "{source}"\n'
                self.assertNotEqual(cca.lock_entries(lock(extra=extra))[1], [])


class Evaluate(unittest.TestCase):
    def setUp(self) -> None:
        self.journal = Path(tempfile.mkdtemp())
        (self.journal / "S0099-fix.md").write_text(
            "- Security exception (EP-24): ring 0.17.99 fixes RUSTSEC-2026-0042, adopted early.\n"
            "- ring is also mentioned here.\n- GHSA-abcd-efgh-jkmp is about something else.\n")
        self.key = ("ring", "0.17.99")

    def run_case(self, age: timedelta, exception: str = "") -> list[str]:
        adopted = T0 + age
        return cca.evaluate({self.key}, {self.key: (T0, exception)}, {self.key: adopted}, self.journal)

    def test_exactly_14_days_passes(self) -> None:
        self.assertEqual(self.run_case(timedelta(days=14)), [])

    def test_one_minute_short_of_14_days_fails(self) -> None:
        # The same calendar day 14 days later is not enough when the hours fall short.
        self.assertNotEqual(self.run_case(timedelta(days=14) - timedelta(minutes=1)), [])

    def test_a_younger_release_with_a_journal_line_naming_the_advisory_passes(self) -> None:
        self.assertEqual(self.run_case(timedelta(days=2), "S0099-fix.md"), [])

    def test_exceptions_that_must_not_pass(self) -> None:
        (self.journal / "S0098-other.md").write_text("- ring was updated.\n")
        (self.journal / "S0097-split.md").write_text("- ring 0.17.99 adopted early.\n- RUSTSEC-2026-0042.\n")
        (self.journal / "S0096-substring.md").write_text("- string-ring fixes RUSTSEC-2026-0042.\n")
        (self.journal / "S0095-prefix.md").write_text("- ring-compat fixes RUSTSEC-2026-0042.\n")
        (self.journal / "S0094-other-version.md").write_text("- ring 0.17.98 fixes RUSTSEC-2026-0042.\n")
        (self.journal / "S0093-longer.md").write_text("- ring 0.17.990 fixes RUSTSEC-2026-0042.\n")
        outside = Path(tempfile.mkdtemp()) / "x.md"
        outside.write_text("- ring fixes RUSTSEC-2026-0042.\n")
        cases = {
            "no exception": "",
            "journal file missing": "S0100-none.md",
            "journal without an advisory": "S0098-other.md",
            "crate and advisory on different lines": "S0097-split.md",
            "crate name only as part of another name": "S0096-substring.md",
            "crate name only as the start of another name": "S0095-prefix.md",
            "file outside the journal": str(outside),
            "path escaping the journal": f"../{outside.parent.name}/x.md",
            "a line written for another version of the crate": "S0094-other-version.md",
            "a line naming a longer version (0.17.990)": "S0093-longer.md",
        }
        for name, exception in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(self.run_case(timedelta(days=2), exception), [])

    def test_ghsa_and_cve_advisories_count(self) -> None:
        (self.journal / "S0092-ghsa.md").write_text("- ring 0.17.99 fixes GHSA-2c2h-xq9m-r3v7 early.\n")
        (self.journal / "S0091-cve.md").write_text("- ring 0.17.99 fixes CVE-2026-12345 early.\n")
        for ref in ("S0092-ghsa.md", "S0091-cve.md"):
            with self.subTest(ref=ref):
                self.assertEqual(self.run_case(timedelta(days=2), ref), [])

    def test_an_escape_marker_covers_only_history(self) -> None:
        # A violation found after the fact is recorded, not undone; the marker is refused for anything adopted
        # once the check exists (CHECK_SINCE), so it cannot let a new young release in.
        (self.journal / "S0013-x.md").write_text("- Escape: ring 0.17.99 was adopted 6 hours early in S0010.\n")
        (self.journal / "S0012-y.md").write_text("- ring 0.17.99 was adopted early.\n")
        before = cca.CHECK_SINCE - timedelta(hours=1)
        key, row = self.key, (before - timedelta(days=13), "escape:S0013-x.md")
        self.assertEqual(cca.evaluate({key}, {key: row}, {key: before}, self.journal), [])
        after = cca.CHECK_SINCE + timedelta(minutes=1)
        row_after = (after - timedelta(days=13), "escape:S0013-x.md")
        self.assertNotEqual(cca.evaluate({key}, {key: row_after}, {key: after}, self.journal), [])
        no_line = (before - timedelta(days=13), "escape:S0012-y.md")
        self.assertNotEqual(cca.evaluate({key}, {key: no_line}, {key: before}, self.journal), [])
        # an advisory exception's journal does not count as an escape record, nor the reverse
        sec = (before - timedelta(days=13), "escape:S0099-fix.md")
        self.assertNotEqual(cca.evaluate({key}, {key: sec}, {key: before}, self.journal), [])

    def test_a_stale_exception_on_an_old_enough_release_fails(self) -> None:
        # The exception is for early adoption only; left on a release that no longer needs it, it is noise.
        self.assertNotEqual(self.run_case(timedelta(days=30), "S0099-fix.md"), [])

    def test_missing_and_stale_rows_fail(self) -> None:
        other = ("serde", "1.0.0")
        self.assertNotEqual(cca.evaluate({self.key, other}, {self.key: (T0, "")}, {self.key: T0 + timedelta(days=20),
                                         other: T0 + timedelta(days=20)}, self.journal), [])
        self.assertNotEqual(cca.evaluate(set(), {self.key: (T0, "")}, {}, self.journal), [])


class Verification(unittest.TestCase):
    """Dates are re-read from crates.io for the entries new since the base (CI: the last green commit), or all;
    a mismatch, a missing answer or a failed request fails (brief items 2 and 8)."""

    def setUp(self) -> None:
        self.old, self.new = ("serde", "1.0.0"), ("evil", "9.9.9")
        self.rows = {self.old: (T0, ""), self.new: (T0, "")}

    def test_only_entries_absent_at_the_base_are_selected(self) -> None:
        got = cca.to_verify({self.old, self.new}, self.rows, lock(self.old), verify_all=False)
        self.assertEqual(got, [self.new])

    def test_everything_is_selected_without_a_base_or_with_verify_all(self) -> None:
        both = sorted([self.old, self.new])
        self.assertEqual(cca.to_verify({self.old, self.new}, self.rows, None, verify_all=False), both)
        self.assertEqual(cca.to_verify({self.old, self.new}, self.rows, lock(self.old, self.new), True), both)

    def test_matching_dates_pass(self) -> None:
        self.assertEqual(cca.mismatches(self.rows, [self.new], lambda keys: {k: T0 for k in keys}), [])

    def test_answers_that_must_not_pass(self) -> None:
        cases = {
            "a different date (a forged row)": lambda keys: {k: T0 - timedelta(days=30) for k in keys},
            "no answer for a key": lambda keys: {},
        }
        for name, fetch in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(cca.mismatches(self.rows, [self.new], fetch), [])

    def test_a_failed_request_raises(self) -> None:
        def down(keys):
            raise OSError("crates.io unreachable")
        with self.assertRaises(OSError):  # main() turns it into exit 1
            cca.mismatches(self.rows, [self.new], down)


class Rows(unittest.TestCase):
    def test_rows_parse(self) -> None:
        rows, problems = cca.read_dates(cca.HEADER + "ring\t0.17.99\t2026-10-01T12:00:00Z\tS0099-fix.md\n")
        self.assertEqual(rows, {("ring", "0.17.99"): (T0, "S0099-fix.md")})
        self.assertEqual(problems, [])

    def test_bad_rows_fail(self) -> None:
        for text in ("ring\t0.17.99\n", "ring\t0.17.99\tyesterday\n",
                     "ring\t0.17.99\t2026-10-01T12:00:00Z\nring\t0.17.99\t2026-10-01T12:00:00Z\n"):
            with self.subTest(text=text):
                self.assertNotEqual(cca.read_dates(text)[1], [])


class Adoption(unittest.TestCase):
    """Adoption is the earliest commit holding the version; re-committing it later does not reset the clock."""

    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp())
        self.old_root = cca.ROOT
        cca.ROOT = self.repo
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        (self.repo / "shell/src-tauri").mkdir(parents=True)

    def tearDown(self) -> None:
        cca.ROOT = self.old_root

    def git(self, *args: str, date: str | None = None) -> None:
        env = {"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else {}
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, env={**os.environ, **env})

    def commit(self, text: str, date: str) -> None:
        (self.repo / cca.LOCK).write_text(text)
        (self.repo / "other.txt").write_text(date)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", date, date=date)

    def test_the_earliest_commit_counts_and_new_working_entries_are_now(self) -> None:
        self.commit(lock(("a", "1.0.0")), "2026-10-01T12:00:00+00:00")
        self.commit(lock(("a", "1.0.0"), ("b", "1.0.0")), "2026-10-05T12:00:00+00:00")
        self.commit(lock(("b", "1.0.0")), "2026-10-07T12:00:00+00:00")  # a removed
        self.commit(lock(("a", "1.0.0"), ("b", "1.0.0")), "2026-10-09T12:00:00+00:00")  # a back
        now = datetime(2026, 10, 20, tzinfo=UTC)
        got = cca.adoption_dates(lock(("a", "1.0.0"), ("b", "1.0.0"), ("c", "1.0.0")), now)
        self.assertEqual(got[("a", "1.0.0")], datetime(2026, 10, 1, 12, tzinfo=UTC))
        self.assertEqual(got[("b", "1.0.0")], datetime(2026, 10, 5, 12, tzinfo=UTC))
        self.assertEqual(got[("c", "1.0.0")], now)

    def test_a_version_first_added_on_a_merged_side_branch_keeps_that_date(self) -> None:
        self.commit(lock(("a", "1.0.0")), "2026-10-01T12:00:00+00:00")
        self.git("checkout", "-q", "-b", "side")
        self.commit(lock(("a", "1.0.0"), ("b", "1.0.0")), "2026-11-02T12:00:00+00:00")
        self.git("checkout", "-q", "-")
        self.commit(lock(("a", "1.0.0"), ("b", "1.0.0")), "2026-11-12T12:00:00+00:00")
        self.git("merge", "-q", "--no-edit", "-X", "ours", "side", date="2026-11-13T12:00:00+00:00")
        self.git("branch", "-q", "-D", "side")
        got = cca.adoption_dates(lock(("a", "1.0.0"), ("b", "1.0.0")), datetime(2026, 12, 1, tzinfo=UTC))
        self.assertEqual(got[("b", "1.0.0")], datetime(2026, 11, 2, 12, tzinfo=UTC))

    def test_a_shallow_clone_fails_closed(self) -> None:
        self.commit(lock(("a", "1.0.0")), "2026-10-01T12:00:00+00:00")
        self.commit(lock(("a", "1.0.0")), "2026-10-02T12:00:00+00:00")
        shallow = Path(tempfile.mkdtemp()) / "s"
        subprocess.run(["git", "clone", "-q", "--depth", "1", self.repo.as_uri(), str(shallow)], check=True,
                       capture_output=True)
        cca.ROOT = shallow
        with self.assertRaises(cca.CheckError):
            cca.adoption_dates(lock(("a", "1.0.0")), datetime.now(UTC))


if __name__ == "__main__":
    unittest.main()
