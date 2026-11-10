"""The first derivation of the INV-21 goldens, run again (S0022, S0095; spec design/specs/mirror/inv21-goldens.md).

The goldens and the v0.2.2 install fixture are committed as data; this reruns the released v0.2.2 code from the
`v0.2.2` tag (`gen_inv21_goldens.py`, `gen_v022_install.py`) and compares. It needs the tag in the clone, so it skips
without it; the checks that must always run are in `test_inv21_goldens.py`.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inv21  # noqa: E402


def have_tag(tag: str) -> bool:
    return subprocess.run(["git", "-C", str(inv21.REPO), "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                          capture_output=True).returncode == 0


@unittest.skipUnless(have_tag(inv21.LEGACY_TAG), "the v0.2.2 tag is not in this clone")
class FirstDerivation(unittest.TestCase):
    def test_the_released_code_still_gives_the_committed_goldens(self) -> None:
        import gen_inv21_goldens
        self.assertEqual(gen_inv21_goldens.generate(), (inv21.CORPUS / "goldens.json").read_text(encoding="utf-8"))

    def test_the_released_code_still_gives_the_committed_install(self) -> None:
        import gen_v022_install
        self.assertEqual(gen_v022_install.generate(), gen_v022_install.on_disk())


if __name__ == "__main__":
    unittest.main()
