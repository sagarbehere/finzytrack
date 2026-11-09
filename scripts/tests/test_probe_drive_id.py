"""Meta-tests for the owner's drive-ID probe (`scripts/probe_drive_id.py`; S0089 round 2, verifier F2: it told
"YES" for a drive with an ID but no creation time, which the save refuses). Expected verdicts come from the engine's
rules (`design/specs/engine-jobs.md` §6.3 "Drive identity"): no ID, or a version 4 ID on FAT, refuses `no_drive_id`
first; then no creation time, or none at a FAT or exFAT top level, refuses `no_folder_id`."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import probe_drive_id as probe  # noqa: E402

V3 = "4e386abb-6e49-38eb-8ef8-a36ba10a34aa"
V4 = "5b0f3c2e-8d1a-4e6b-9c7f-2a4d6e8b0c1d"


class Verdict(unittest.TestCase):
    def test_refusals_and_their_order(self) -> None:
        cases = [
            (("smbfs", None, None), "no_drive_id"),  # no ID and no creation time: the ID is told first
            (("smbfs", V4, None), "no_folder_id"),  # an ID but no creation time (the verifier's F2)
            (("msdos", V4, (1, 0)), "no_drive_id"),
            (("msdos", V3, (0, 0)), "no_folder_id"),
            (("exfat", V3, (0, 0)), "no_folder_id"),
        ]
        for args, refusal in cases:
            with self.subTest(args=args):
                got = probe.verdict(*args)
                self.assertTrue(got.startswith("NO:"), got)
                self.assertIn(refusal, got)

    def test_what_saves(self) -> None:
        for args in (
            ("apfs", V4, (1, 5)),
            ("exfat", V4, (1, 0)),
            ("msdos", V3, (1_794_000_000, 0)),
            ("hfs", V3, (0, 0)),
        ):
            with self.subTest(args=args):
                self.assertTrue(probe.verdict(*args).startswith("YES"), args)


if __name__ == "__main__":
    unittest.main()
