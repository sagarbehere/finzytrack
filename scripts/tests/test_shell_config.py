"""The desktop shell's configuration holds the decided values (ADR-0007 and its 2026-10-25 amendment, ADR-0040
and its 2026-10-25 amendment, ADR-0056 for the packaged override). Each check sits beside planted variants that
must fail it."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAURI = ROOT / "shell/src-tauri"
CONF = json.loads((TAURI / "tauri.conf.json").read_text())
CAPS = {p.name: json.loads(p.read_text()) for p in sorted((TAURI / "capabilities").glob("*.json"))}
# The packaged build merges this file over tauri.conf.json (`tauri build --config`, S0012).
PACKAGED_NAME = "tauri.packaged.conf.json"
PACKAGED = json.loads((TAURI / PACKAGED_NAME).read_text())

# Permissions the webview may hold. M0 grants none; the M2 shell slice adds the dialog, opener, updater and
# process permissions ADR-0007 lists. fs and shell execution are never granted (ARCHITECTURE.md §5).
ALLOWED_PERMISSIONS: set[str] = set()
NEVER = ("fs:", "shell:allow-execute", "shell:default")


def csp_directives(csp: str) -> dict[str, list[str]]:
    """Directive name (lower case: CSP directive names are case-insensitive) -> its source list."""
    out: dict[str, list[str]] = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            out.setdefault(words[0].lower(), words[1:])
    return out


def csp_problems(csp: str | None) -> list[str]:
    if not csp:
        return ["csp missing"]
    d = csp_directives(csp)
    out = []
    script_lists = [d[k] for k in ("script-src", "script-src-elem", "script-src-attr") if k in d]
    if not script_lists:
        script_lists = [d.get("default-src", ["*"])]
    for sources in script_lists:
        # ADR-0040: script-src 'self' plus the pre-paint script's hash; nothing inline, evaluated or remote.
        if any(s != "'self'" and not s.startswith("'sha256-") for s in sources):
            out.append("csp script source")
    if d.get("object-src") != ["'none'"]:
        out.append("csp object-src")
    if not {"ipc:", "http://ipc.localhost"} <= set(d.get("connect-src", [])):
        out.append("csp ipc")  # ADR-0040 amendment: without it IPC falls back to postMessage
    return out


def problems(conf: dict, caps: dict[str, dict]) -> list[str]:
    out = []
    if conf.get("identifier") != "com.finzytrack.desktop":
        out.append("identifier")  # ADR-0007
    if conf.get("productName") != "Finzytrack":
        out.append("productName")  # ADR-0007
    if conf.get("bundle", {}).get("macOS", {}).get("minimumSystemVersion") != "14.0":
        out.append("macOS minimum")  # ADR-0007 amendment: macOS 14, Safari 17 WebKit
    security = conf.get("app", {}).get("security", {})
    out += csp_problems(security.get("csp"))
    modification = security.get("dangerousDisableAssetCspModification")
    if not (modification is True or (isinstance(modification, list) and "script-src" in modification)):
        out.append("csp rewriting on")  # ADR-0040 amendment: Tauri may not add nonces to inline scripts
    if security.get("capabilities"):
        out.append("inline capability")  # capabilities live in reviewed files only
    for name, cap in caps.items():
        for perm in cap.get("permissions", []):
            ident = perm if isinstance(perm, str) else perm.get("identifier", "")
            if ident.startswith(NEVER) or ident not in ALLOWED_PERMISSIONS:
                out.append(f"permission {ident} in {name}")
    return out


def merge_patch(base, patch):
    """RFC 7396 JSON merge patch, the way the Tauri CLI applies `--config` (null removes a key)."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = merge_patch(out.get(key), value)
    return out


def config_files(names: list[str]) -> list[str]:
    """Config files the Tauri CLI would read or merge on its own: tauri.conf.*, tauri.<platform>.conf.*,
    Tauri.toml and Tauri.<platform>.toml (case-insensitive file systems make the case moot)."""
    return [n for n in names if n.lower().startswith("tauri.") and (".conf." in n.lower() or n.lower().endswith(".toml"))]


def packaged_problems(conf: dict, packaged: dict, caps: dict[str, dict]) -> list[str]:
    out = []
    # The override packages; it decides nothing about the app (identifier, window, CSP, plugins, version).
    extra = set(packaged) - {"$schema", "bundle"}
    if extra:
        out.append(f"override sets {sorted(extra)}")
    merged = merge_patch(conf, packaged)
    out += problems(merged, caps)
    bundle = merged.get("bundle", {})
    if bundle.get("resources") != {"../../build/sidecar/": "sidecar/"}:
        out.append("resources")  # ADR-0056: the sidecar tree ships as resources, at the path smoke.rs probes
    if "externalBin" in bundle:
        out.append("externalBin")  # ADR-0056: not externalBin
    if bundle.get("macOS", {}).get("signingIdentity") != "-":
        out.append("macOS signing")  # ADR-0007: ad-hoc until an Apple Developer ID exists
    return out


def with_csp(value: str):
    return lambda c, k: c["app"]["security"].update(csp=value)


GOOD_CSP = "default-src 'self'; script-src 'self'; connect-src 'self' ipc: http://ipc.localhost; object-src 'none'"


class ShellConfig(unittest.TestCase):
    def test_the_committed_config_holds(self) -> None:
        self.assertEqual(problems(CONF, CAPS), [])

    def test_a_pre_paint_hash_is_allowed(self) -> None:
        c = copy.deepcopy(CONF)
        c["app"]["security"]["csp"] = GOOD_CSP.replace("script-src 'self'", "script-src 'self' 'sha256-abc='")
        self.assertEqual(problems(c, CAPS), [])

    def test_a_repeated_directive_is_ignored_as_browsers_do(self) -> None:
        # CSP: only the first occurrence of a directive counts, so a later looser copy changes nothing.
        c = copy.deepcopy(CONF)
        c["app"]["security"]["csp"] = GOOD_CSP + "; script-src 'self' 'unsafe-inline'"
        self.assertEqual(problems(c, CAPS), [])

    def test_planted_variants_fail(self) -> None:
        variants = {
            "identifier": lambda c, k: c.update(identifier="com.example.app"),
            "productName": lambda c, k: c.update(productName="Other"),
            "macOS minimum": lambda c, k: c["bundle"]["macOS"].update(minimumSystemVersion="13.0"),
            "unsafe-inline": with_csp(GOOD_CSP.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")),
            "upper-case directive": with_csp(GOOD_CSP.replace("script-src 'self'", "SCRIPT-SRC 'self' 'unsafe-inline'")),
            "script-src-elem": with_csp(GOOD_CSP + "; script-src-elem 'self' 'unsafe-inline'"),
            "data: script": with_csp(GOOD_CSP.replace("script-src 'self'", "script-src 'self' data:")),
            "https: script": with_csp(GOOD_CSP.replace("script-src 'self'", "script-src 'self' https:")),
            "default-src only, wide": with_csp("default-src *; object-src 'none'; connect-src ipc: http://ipc.localhost"),
            "object-src": with_csp(GOOD_CSP.replace("object-src 'none'", "object-src *")),
            "no ipc": with_csp(GOOD_CSP.replace(" ipc: http://ipc.localhost", "")),
            "csp missing": with_csp(""),
            "csp rewriting on": lambda c, k: c["app"]["security"].pop("dangerousDisableAssetCspModification"),
            "inline capability": lambda c, k: c["app"]["security"].update(
                capabilities=[{"identifier": "x", "windows": ["main"], "permissions": ["fs:default"]}]),
            "fs permission": lambda c, k: k["default.json"].update(permissions=["fs:default"]),
            "a second capability file": lambda c, k: k.update(
                {"extra.json": {"identifier": "y", "permissions": ["shell:allow-execute"]}}),
            "object permission": lambda c, k: k["default.json"].update(
                permissions=[{"identifier": "opener:allow-open-path", "allow": [{"path": "**"}]}]),
        }
        for name, plant in variants.items():
            with self.subTest(variant=name):
                c, k = copy.deepcopy(CONF), copy.deepcopy(CAPS)
                plant(c, k)
                self.assertNotEqual(problems(c, k), [], f"{name} passed the check")


class PackagedOverride(unittest.TestCase):
    def test_the_committed_override_holds(self) -> None:
        self.assertEqual(packaged_problems(CONF, PACKAGED, CAPS), [])

    def test_the_merge_follows_rfc_7396(self) -> None:
        # null removes a key (so an override could delete a decided value); arrays are replaced, objects merged.
        base = {"a": {"b": 1, "c": 2}, "l": [1, 2]}
        self.assertEqual(merge_patch(base, {"a": {"b": None, "d": 3}, "l": [3]}), {"a": {"c": 2, "d": 3}, "l": [3]})

    def test_no_other_config_file_is_merged(self) -> None:
        # Tauri merges tauri.<platform>.conf.json (and .json5, Tauri.toml) by itself; only the two reviewed
        # files may exist.
        self.assertEqual(sorted(config_files([p.name for p in TAURI.iterdir()])),
                         sorted(["tauri.conf.json", PACKAGED_NAME]))

    def test_config_file_detection_must_not_miss(self) -> None:
        names = ["tauri.macos.conf.json", "tauri.windows.conf.json5", "Tauri.toml", "Tauri.linux.toml",
                 "TAURI.CONF.JSON"]
        self.assertEqual(config_files(names + ["Cargo.toml", "build.rs"]), names)

    def test_planted_variants_fail(self) -> None:
        variants = {
            "csp through the override": lambda p: p.update(app={"security": {"csp": "default-src *"}}),
            "csp removed with null": lambda p: p.update(app={"security": {"csp": None}}),
            "identifier": lambda p: p.update(identifier="com.example.app"),
            "plugins": lambda p: p.update(plugins={"updater": {"endpoints": ["https://example.com"]}}),
            "macOS minimum lowered": lambda p: p["bundle"]["macOS"].update(minimumSystemVersion="13.0"),
            "macOS block removed with null": lambda p: p["bundle"].update(macOS=None),
            "extra resource": lambda p: p["bundle"]["resources"].update({"../../": "repo/"}),
            "resources moved": lambda p: p["bundle"].update(resources={"../../build/sidecar/": "python/"}),
            "externalBin": lambda p: p["bundle"].update(externalBin=["binaries/x"]),
            "signing identity": lambda p: p["bundle"]["macOS"].update(signingIdentity="Developer ID"),
        }
        for name, plant in variants.items():
            with self.subTest(variant=name):
                p = copy.deepcopy(PACKAGED)
                plant(p)
                self.assertNotEqual(packaged_problems(CONF, p, CAPS), [], f"{name} passed the check")


if __name__ == "__main__":
    unittest.main()
