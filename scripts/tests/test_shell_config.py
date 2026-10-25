"""The desktop shell's configuration holds the decided values (ADR-0007 and its 2026-10-25 amendment, ADR-0040,
ADR-0053). Each check sits beside a planted variant that must fail it."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONF = json.loads((ROOT / "shell/src-tauri/tauri.conf.json").read_text())
CAPS = json.loads((ROOT / "shell/src-tauri/capabilities/default.json").read_text())


def problems(conf: dict, caps: dict) -> list[str]:
    out = []
    if conf.get("identifier") != "com.finzytrack.desktop":
        out.append("identifier")  # ADR-0007
    if conf.get("productName") != "Finzytrack":
        out.append("productName")
    if conf.get("bundle", {}).get("macOS", {}).get("minimumSystemVersion") != "14.0":
        out.append("macOS minimum")  # ADR-0007 amendment: macOS 14, Safari 17 WebKit
    csp = conf.get("app", {}).get("security", {}).get("csp") or ""
    directives = {d.split()[0]: d.split()[1:] for d in csp.split(";") if d.strip()}
    script = directives.get("script-src", directives.get("default-src"))
    if not script or any(s in script for s in ("'unsafe-inline'", "'unsafe-eval'", "*")):
        out.append("csp")  # ADR-0040: no inline or evaluated script
    if caps.get("permissions"):
        out.append("permissions")  # M0: the window gets no plugin permissions
    return out


class ShellConfig(unittest.TestCase):
    def test_the_committed_config_holds(self) -> None:
        self.assertEqual(problems(CONF, CAPS), [])

    def test_planted_variants_fail(self) -> None:
        variants = {
            "identifier": lambda c, k: c.update(identifier="com.example.app"),
            "macOS minimum": lambda c, k: c["bundle"]["macOS"].update(minimumSystemVersion="13.0"),
            "csp": lambda c, k: c["app"]["security"].update(csp="default-src 'self'; script-src 'self' 'unsafe-inline'"),
            "permissions": lambda c, k: k.update(permissions=["fs:default"]),
        }
        for name, plant in variants.items():
            with self.subTest(variant=name):
                c, k = copy.deepcopy(CONF), copy.deepcopy(CAPS)
                plant(c, k)
                self.assertIn(name, problems(c, k))

    def test_missing_csp_fails(self) -> None:
        c = copy.deepcopy(CONF)
        c["app"]["security"]["csp"] = None
        self.assertIn("csp", problems(c, CAPS))


if __name__ == "__main__":
    unittest.main()
