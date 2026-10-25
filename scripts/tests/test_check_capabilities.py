"""Meta-tests for the capability-map check (`scripts/check_capabilities.py`), for BL-0039: a cut or deferred row
must cite the ADR that decided it, and the ADR must exist. PROCESS.md §7: cut and deferred rows stay in the map
"with their ADR". Each passing case sits beside the case that must not pass (EP-29)."""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_capabilities as cc  # noqa: E402

ADRS = {"ADR-0030"}


def row(status: str, adrs: list[str], **extra: str) -> dict:
    base = {"id": "CAP-X-Y", "area": "Test", "capability": "a fictional row (a real ID here would count as its tagged test)", "v04": "cut", "status": status,
            "release_blocking": "no", "adrs": adrs}
    return {**base, **extra}


class CutAndDeferredRows(unittest.TestCase):
    def test_a_cut_or_deferred_row_citing_an_existing_adr_passes(self) -> None:
        for status in ("cut", "deferred"):
            with self.subTest(status=status):
                self.assertEqual(cc.check([row(status, ["ADR-0030"])], ADRS, None)[0], [])

    def test_rows_that_must_not_pass(self) -> None:
        cases = {
            "cut without an ADR": row("cut", []),
            "deferred without an ADR": row("deferred", []),
            "cut without the field": {k: v for k, v in row("cut", []).items() if k != "adrs"},
            "cut citing a missing ADR": row("cut", ["ADR-9999"]),
        }
        for name, r in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(cc.check([r], ADRS, None)[0], [])

    def test_a_planned_row_needs_no_adr(self) -> None:
        self.assertEqual(cc.check([row("planned", [], milestone="M8", release_blocking="yes")], ADRS, None)[0], [])

    def test_the_committed_map_passes(self) -> None:
        caps = tomllib.loads((cc.ROOT / "design/capabilities.toml").read_text())["capability"]
        adr_ids = {p.name[:8] for p in (cc.ROOT / "design/decisions").glob("ADR-*.md")}
        self.assertEqual(cc.check(caps, adr_ids, None)[0], [])


if __name__ == "__main__":
    unittest.main()
