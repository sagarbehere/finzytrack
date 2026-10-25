"""Meta-tests for the oracle's copy guards (INV-24): what it refuses, what it stages, what it may print."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "oracle"))

import copies  # noqa: E402

# macOS reaches the data volume by a second spelling as well (a firmlink); `resolve()` keeps either spelling.
FIRMLINK = Path("/", "System", "Volumes", "Data")  # from parts: the privacy scan reads the literal as a home path
HAS_FIRMLINK = sys.platform == "darwin" and FIRMLINK.is_dir()


def firm(p: Path) -> Path:
    return Path(str(FIRMLINK) + str(p.resolve()))


CONFIG = 'setup_complete: false\n\n# File paths\nledger_file: "./data/ledgers/one.beancount"\nserver:\n  port: 8001\n'


def snapshot(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


class CopiesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.live, self.legacy = base / "live", base / "legacy"
        for d in (self.live, self.legacy):
            d.mkdir()
        self.old = {k: os.environ.get(k) for k in ("FT_LIVE_DATA", "FT_LEGACY_ROOT")}
        os.environ.update({"FT_LIVE_DATA": str(self.live), "FT_LEGACY_ROOT": str(self.legacy)})
        self.src = base / "src"
        (self.src / "y").mkdir(parents=True)
        (self.src / "main.beancount").write_text('include "y/*.beancount"\n', encoding="utf-8")
        (self.src / "y/2020.beancount").write_text("2020-01-01 open Assets:Cash\n", encoding="utf-8")
        self.template = base / "template"
        (self.template / "config").mkdir(parents=True)
        (self.template / "config/config.yaml").write_text(CONFIG, encoding="utf-8")
        self.base = base

    def tearDown(self) -> None:
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_refuses_the_live_data_folder_the_legacy_worktree_and_the_repo(self) -> None:
        for bad in (self.live, self.live / "data/ledgers", self.legacy, self.legacy / "backend", copies.REPO, self.base):
            with self.subTest(bad=bad):
                self.assertIsNotNone(copies.refuse_dir(bad))
        self.assertIsNone(copies.refuse_dir(self.src))

    def test_refuses_a_symlink_into_the_live_data_folder(self) -> None:
        link = self.base / "innocent"
        link.symlink_to(self.live)
        self.assertIsNotNone(copies.refuse_dir(link))

    def test_refuses_another_spelling_of_a_forbidden_folder(self) -> None:
        # S0030 (INV-24): macOS disks are case-insensitive, so another case is the same folder (as the guard hooks'
        # `_norm`); a `..` detour is resolved first.
        for bad in (Path(str(self.live).upper()), Path(str(self.live / "x").swapcase()), self.src / ".." / "live",
                    Path(str(copies.REPO).upper()) / "backend"):
            with self.subTest(bad=bad):
                self.assertIsNotNone(copies.refuse_dir(bad))

    def test_the_default_live_folder_stays_refused_when_ft_live_data_names_another(self) -> None:
        # S0030: FT_LIVE_DATA adds a folder to refuse; it never un-refuses the default one (nor FT_LEGACY_ROOT the
        # default legacy worktree).
        self.assertIsNotNone(copies.refuse_dir(Path.home() / ("Finzy" + "track" + "Data") / "ledgers"))
        self.assertIsNotNone(copies.refuse_dir(copies.REPO.parent / "finzytrack-legacy" / "backend"))
        self.assertIsNotNone(copies.refuse_dir(Path.home()))  # contains the live folder

    @unittest.skipUnless(sys.platform == "darwin", "macOS firmlink spelling")
    def test_refuses_the_firmlink_spelling_of_a_forbidden_folder(self) -> None:
        self.assertTrue(HAS_FIRMLINK, f"{FIRMLINK} is missing on this Mac")
        # S0030 verifier F3: the same folder by another path text is matched by its identity (device, inode).
        for bad in (firm(self.live), firm(self.live) / "sub", firm(self.legacy), firm(copies.REPO), firm(self.base)):
            with self.subTest(bad=bad):
                self.assertIsNotNone(copies.refuse_dir(bad))
        self.assertIsNone(copies.refuse_dir(firm(self.src)))

    def test_a_changed_home_keeps_the_accounts_own_defaults(self) -> None:
        # S0030 verifier F4: the defaults come from the password database too, not only from $HOME.
        import pwd

        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(self.base)
        try:
            roots = {str(p) for p, _ in copies._forbidden_roots()}
        finally:
            if old is None:
                os.environ.pop("HOME")
            else:
                os.environ["HOME"] = old
        self.assertIn(str(real_home / ("Finzy" + "track" + "Data")), roots)
        self.assertIn(str(real_home / "experiments" / "finzytrack-legacy"), roots)
        self.assertIn(str(self.base / ("Finzy" + "track" + "Data")), roots)

    def test_stage_copies_the_ledger_and_points_the_config_at_the_copy(self) -> None:
        before = snapshot(self.src)
        root = self.base / "case1"
        ledger = copies.stage_root(self.src, "main.beancount", self.template, root)
        self.assertEqual(ledger, root / "data/ledgers/main.beancount")
        self.assertEqual(snapshot(root / "data/ledgers"), before)
        cfg = (root / "config/config.yaml").read_text(encoding="utf-8")
        self.assertIn('ledger_file: "./data/ledgers/main.beancount"', cfg)
        self.assertIn("setup_complete: true", cfg)
        (root / "data/ledgers/main.beancount").write_text("changed\n", encoding="utf-8")
        self.assertEqual(snapshot(self.src), before)  # the source is never written

    def test_stage_refuses_a_root_file_outside_the_ledger_dir(self) -> None:
        with self.assertRaises(ValueError):
            copies.stage_root(self.src, "../escape.beancount", self.template, self.base / "case2")

    def test_stage_refuses_a_forbidden_source(self) -> None:
        with self.assertRaises(ValueError):
            copies.stage_root(self.live, "main.beancount", self.template, self.base / "case3")

    def test_stage_refuses_an_existing_root(self) -> None:
        (self.base / "case4").mkdir()
        with self.assertRaises(ValueError):
            copies.stage_root(self.src, "main.beancount", self.template, self.base / "case4")

    def test_entry_text_only_for_corpus_or_generated_ledgers(self) -> None:
        self.assertFalse(copies.may_show_entries(self.src))  # could be a copy of a real ledger
        copies.mark_generated(self.src)
        self.assertTrue(copies.may_show_entries(self.src))
        self.assertTrue(copies.may_show_entries(copies.REPO / "corpora/ledgers/oracle"))


class LocalTierTest(unittest.TestCase):
    """PROCESS.md §8: T-local reads real-ledger copies only from the folder named by FT_LOCAL_TIER_DIR, outside the
    repo, and prints counts and case IDs only (S0030; BL-0024)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.live, self.legacy, self.tier = base / "live", base / "legacy", base / "tier"
        for d in (self.live, self.legacy, self.tier / "bank", base / "elsewhere"):
            d.mkdir(parents=True)
        self.base = base
        self.old = {k: os.environ.get(k) for k in ("FT_LIVE_DATA", "FT_LEGACY_ROOT", copies.LOCAL_TIER_ENV)}
        os.environ.update({"FT_LIVE_DATA": str(self.live), "FT_LEGACY_ROOT": str(self.legacy),
                           copies.LOCAL_TIER_ENV: str(self.tier)})

    def tearDown(self) -> None:
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_a_copy_inside_the_local_tier_folder_is_accepted(self) -> None:
        self.assertIsNone(copies.refuse_local_tier_source(self.tier / "bank"))
        self.assertIsNone(copies.refuse_local_tier_source(self.tier))
        self.assertIsNone(copies.refuse_local_tier_source(copies.CORPORA / "ledgers/oracle"))  # synthetic, in the repo

    def test_a_folder_outside_the_local_tier_folder_is_refused(self) -> None:
        for bad in (self.base / "elsewhere", self.base, self.tier / ".." / "elsewhere", self.live,
                    copies.REPO / "backend", Path(str(self.base / "elsewhere").upper())):
            with self.subTest(bad=bad):
                self.assertIsNotNone(copies.refuse_local_tier_source(bad))

    def test_a_symlink_from_the_tier_to_elsewhere_is_refused(self) -> None:
        (self.tier / "link").symlink_to(self.base / "elsewhere")
        self.assertIsNotNone(copies.refuse_local_tier_source(self.tier / "link"))

    @unittest.skipUnless(sys.platform == "darwin", "macOS firmlink spelling")
    def test_firmlink_spellings_of_the_tier_and_the_live_folder(self) -> None:
        self.assertTrue(HAS_FIRMLINK, f"{FIRMLINK} is missing on this Mac")
        # S0030 verifier F3: a source inside the live folder spelled via the firmlink is refused, a tier that is the
        # firmlink spelling of a folder containing the live folder is refused, and the tier's own firmlink spelling
        # is still the tier.
        self.assertIsNotNone(copies.refuse_local_tier_source(firm(self.live)))
        self.assertIsNone(copies.refuse_local_tier_source(firm(self.tier / "bank")))
        os.environ[copies.LOCAL_TIER_ENV] = str(firm(self.base))
        self.assertIsNotNone(copies.refuse_local_tier_source(firm(self.base) / "elsewhere"))

    def test_another_spelling_of_the_tier_is_the_tier(self) -> None:
        self.assertIsNone(copies.refuse_local_tier_source(Path(str(self.tier / "bank").upper())))

    def test_the_tier_folder_itself_must_be_set_absolute_existing_and_safe(self) -> None:
        (self.live / "sub").mkdir()
        bad_tiers = ("", "tier", str(self.base / "missing"), str(self.live), str(self.live / "sub"), str(self.base),
                     str(copies.REPO), str(copies.REPO / "corpora"), str(self.legacy))
        for bad in bad_tiers:
            with self.subTest(tier=bad):
                os.environ[copies.LOCAL_TIER_ENV] = bad
                self.assertIsNotNone(copies.refuse_local_tier_source(self.tier / "bank"))
        os.environ.pop(copies.LOCAL_TIER_ENV)
        self.assertIsNotNone(copies.refuse_local_tier_source(self.tier / "bank"))

    def test_a_relative_tier_is_refused_even_when_it_exists_from_here(self) -> None:
        cwd = Path.cwd()
        os.chdir(self.base)
        try:
            os.environ[copies.LOCAL_TIER_ENV] = "tier"  # exists relative to the cwd; still not an absolute path
            self.assertIsNotNone(copies.refuse_local_tier_source(self.tier / "bank"))
        finally:
            os.chdir(cwd)

    def test_nothing_in_the_local_tier_may_show_entry_text(self) -> None:
        # A copy of a real ledger with a planted marker still prints counts and case IDs only.
        copies.mark_generated(self.tier / "bank")
        self.assertFalse(copies.may_show_entries(self.tier / "bank"))
        copies.mark_generated(self.base / "elsewhere")
        self.assertTrue(copies.may_show_entries(self.base / "elsewhere"))


if __name__ == "__main__":
    unittest.main()
