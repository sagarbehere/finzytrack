"""The desktop shell's configuration holds the decided values (ADR-0007 and its 2026-10-25 amendment, ADR-0040
and its 2026-10-25 amendment, ADR-0056 for the packaged override). Each check sits beside planted variants that
must fail it."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAURI = ROOT / "shell/src-tauri"
CONF = json.loads((TAURI / "tauri.conf.json").read_text())
CAPS = {p.name: json.loads(p.read_text()) for p in sorted((TAURI / "capabilities").glob("*.json"))}
# The packaged build merges this file over tauri.conf.json (`tauri build --config`, S0012).
PACKAGED_NAME = "tauri.packaged.conf.json"
PACKAGED = json.loads((TAURI / PACKAGED_NAME).read_text())
INDEX_HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")


class _Page(HTMLParser):
    """The inline scripts of a page and every markup shape a strict CSP would block or that runs script outside it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.inline: list[str] = []
        self.other: list[str] = []
        self._in_script = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = {k.lower(): v or "" for k, v in attrs}
        for name, value in names.items():
            if name.startswith("on"):
                self.other.append(f"event handler {name}")  # inline handlers run only under 'unsafe-inline'
            if name == "style":
                self.other.append("style attribute")
            if value.strip().lower().startswith("javascript:"):
                self.other.append(f"javascript: url in {name}")
        if tag == "style":
            self.other.append("style element")
        if tag == "script" and "src" not in names:
            self._in_script, self._buffer = True, []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_script:
            self.inline.append("".join(self._buffer))
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._buffer.append(data)


def page_of(html: str) -> _Page:
    page = _Page()
    page.feed(html)
    page.close()
    return page


def csp_hash(script: str) -> str:
    """The CSP source that allows exactly this inline script: SHA-256 of its text, base64 (CSP Level 2)."""
    return "'sha256-" + base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii") + "'"


def page_problems(html: str) -> list[str]:
    """ADR-0040: the page's one inline script is the pre-paint theme script; nothing else inline."""
    page = page_of(html)
    out = list(page.other)
    if len(page.inline) != 1:
        out.append(f"{len(page.inline)} inline scripts")
    return out


def prepaint_hash(html: str) -> str | None:
    page = page_of(html)
    return csp_hash(page.inline[0]) if len(page.inline) == 1 else None

# Permissions the webview may hold. M0 grants none; the M2 shell slice adds the dialog, opener, updater and
# process permissions ADR-0007 lists. fs and shell execution are never granted (ARCHITECTURE.md §5).
ALLOWED_PERMISSIONS: set[str] = set()
NEVER = ("fs:", "shell:allow-execute", "shell:default")


def csp_directives(csp: str) -> dict[str, list[str]]:
    """Directive name (lower case) -> its source list, from its first copy (a browser's rule). Only meaningful once
    `csp_problems` has refused repeats and names not in lower case (Tauri keeps the last copy, by exact name)."""
    out: dict[str, list[str]] = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            out.setdefault(words[0].lower(), words[1:])
    return out


def reviewed_csp(prepaint: str | None) -> dict[str, set[str]]:
    """The reviewed policy (ADR-0040, its 2026-10-25 amendment; S0169): each directive and the only sources it may
    hold. A directive not listed is refused, so nothing is loosened by adding one (`frame-src *`, `worker-src blob:`)."""
    return {
        "default-src": {"'self'"},
        "script-src": {"'self'", str(prepaint)},  # the pre-paint script's hash; nothing inline, evaluated or remote
        "style-src": {"'self'"},
        "img-src": {"'self'", "data:"},
        "connect-src": {"'self'", "ipc:", "http://ipc.localhost"},
        "object-src": {"'none'"},
        "base-uri": {"'self'"},  # does not fall back to default-src
        "form-action": {"'self'"},  # does not fall back to default-src
    }


# Directives that must be present with exactly their reviewed sources: default-src bounds every fetch directive not
# named; script-src must allow the pre-paint script; base-uri and form-action have no fallback.
EXACT_DIRECTIVES = ("default-src", "script-src", "object-src", "base-uri", "form-action")


def csp_problems(csp: str | None, prepaint: str | None = None) -> list[str]:
    """Every way `csp` is looser than the reviewed policy, or misses what the app needs (verifier round 1 F2).
    `prepaint`: the hash source of the page's pre-paint script."""
    if prepaint is None:
        prepaint = prepaint_hash(INDEX_HTML)
    if not csp:
        return ["csp missing"]
    d = csp_directives(csp)
    allowed = reviewed_csp(prepaint)
    out = []
    # Verifier round 2 F7: browsers keep a directive's first copy, Tauri's header its last (by exact name), so a
    # repeat is refused outright, and a name must be written as the reviewed ones are (lower case).
    names = [part.split()[0] for part in csp.split(";") if part.split()]
    if len({n.lower() for n in names}) != len(names):
        out.append("csp directive repeated")
    out += [f"csp directive name {n}" for n in names if not re.fullmatch(r"[a-z-]+", n)]
    for name, sources in d.items():
        if name not in allowed:
            out.append(f"csp directive {name}")
        elif not set(sources) <= allowed[name]:
            out.append(f"csp {name} source")
    for name in EXACT_DIRECTIVES:
        if name in d and set(d[name]) != allowed[name]:
            out.append(f"csp {name} not exact")
        elif name not in d:
            out.append(f"csp {name} missing")
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


PREPAINT = prepaint_hash(INDEX_HTML)
GOOD_CSP = (f"default-src 'self'; script-src 'self' {PREPAINT}; connect-src 'self' ipc: http://ipc.localhost; "
            "object-src 'none'; base-uri 'self'; form-action 'self'")


class ShellConfig(unittest.TestCase):
    def test_the_committed_config_holds(self) -> None:
        self.assertEqual(problems(CONF, CAPS), [])

    def test_the_good_csp_holds(self) -> None:
        c = copy.deepcopy(CONF)
        c["app"]["security"]["csp"] = GOOD_CSP
        self.assertEqual(problems(c, CAPS), [])

    def test_a_repeated_directive_is_refused(self) -> None:
        # S0169 verifier round 2 F7: a browser keeps a directive's first copy, but Tauri builds the desktop header
        # from a map filled in order (tauri-utils' Csp to HashMap, then Display), so the last copy wins there, and
        # it compares names case-sensitively. A repeat, in any letter case, is refused whichever copy is looser.
        for extra in ("; script-src 'self' 'unsafe-inline'", "; Script-Src 'self'", f"; script-src 'self' {PREPAINT}"):
            with self.subTest(extra=extra):
                self.assertIn("csp directive repeated", csp_problems(GOOD_CSP + extra))

    def test_planted_variants_fail(self) -> None:
        variants = {
            "identifier": lambda c, k: c.update(identifier="com.example.app"),
            "productName": lambda c, k: c.update(productName="Other"),
            "macOS minimum": lambda c, k: c["bundle"]["macOS"].update(minimumSystemVersion="13.0"),
            "unsafe-inline": with_csp(GOOD_CSP.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")),
            # S0169 verifier round 1 F2, F3: nothing looser than the reviewed policy, anywhere.
            "style-src 'unsafe-inline'": with_csp(GOOD_CSP + "; style-src 'self' 'unsafe-inline'"),
            "default-src *": with_csp(GOOD_CSP.replace("default-src 'self'", "default-src *")),
            "default-src removed": with_csp(GOOD_CSP.replace("default-src 'self'; ", "")),
            "img-src *": with_csp(GOOD_CSP + "; img-src *"),
            "connect-src *": with_csp(GOOD_CSP.replace("connect-src 'self'", "connect-src *")),
            "connect-src to a host": with_csp(GOOD_CSP.replace("connect-src 'self'", "connect-src 'self' https://example.com")),
            "worker-src blob:": with_csp(GOOD_CSP + "; worker-src * blob: data:"),
            "frame-src *": with_csp(GOOD_CSP + "; frame-src *"),
            "base-uri *": with_csp(GOOD_CSP.replace("base-uri 'self'", "base-uri *")),
            "base-uri missing": with_csp(GOOD_CSP.replace("; base-uri 'self'", "")),
            "form-action missing": with_csp(GOOD_CSP.replace("; form-action 'self'", "")),
            "form-action *": with_csp(GOOD_CSP.replace("form-action 'self'", "form-action *")),
            "object-src removed": with_csp(GOOD_CSP.replace("; object-src 'none'", "")),
            "script-src removed": with_csp(GOOD_CSP.replace(f"script-src 'self' {PREPAINT}; ", "")),
            "report-uri": with_csp(GOOD_CSP + "; report-uri https://example.com/r"),
            # S0169: the hash must be the pre-paint script's, and the only one.
            "a stale hash": with_csp(GOOD_CSP.replace(str(PREPAINT), csp_hash("old script"))),
            "a second hash": with_csp(GOOD_CSP.replace(str(PREPAINT), f"{PREPAINT} {csp_hash('other()')}")),
            "no hash": with_csp(GOOD_CSP.replace(f" {PREPAINT}", "")),
            "unsafe-hashes": with_csp(GOOD_CSP.replace("script-src 'self'", "script-src 'self' 'unsafe-hashes'")),
            "strict-dynamic": with_csp(GOOD_CSP.replace("script-src 'self'", "script-src 'self' 'strict-dynamic'")),
            "upper-case directive": with_csp(GOOD_CSP.replace("script-src 'self'", "SCRIPT-SRC 'self' 'unsafe-inline'")),
            # S0169 verifier round 2 F7: Tauri keeps a directive's last copy, by its exact name.
            "a repeated script-src": with_csp(GOOD_CSP + "; script-src 'self' 'unsafe-inline' 'unsafe-eval'"),
            "a repeated base-uri": with_csp(GOOD_CSP + "; base-uri *"),
            "a repeated connect-src": with_csp(GOOD_CSP + "; connect-src *"),
            "an upper-case SCRIPT-SRC beside script-src": with_csp(GOOD_CSP + "; SCRIPT-SRC *"),
            "an upper-case name alone": with_csp(GOOD_CSP.replace("object-src", "OBJECT-SRC")),
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


class PrePaintPage(unittest.TestCase):
    """ADR-0040 and its 2026-10-25 amendment (S0169): the CSP's one hash is the shipped pre-paint script's."""

    def test_the_committed_page_holds(self) -> None:
        self.assertEqual(page_problems(INDEX_HTML), [])
        self.assertIsNotNone(PREPAINT)
        self.assertIn(str(PREPAINT), csp_directives(CONF["app"]["security"]["csp"])["script-src"])

    def test_the_hash_is_the_csp_level_2_one(self) -> None:
        # The example of the CSP Level 2 spec: <script>alert('Hello, world.');</script>
        spec = "'sha256-qznLcsROx4GACP2dm0UCKCzCG+HiZ1guq6ZZDob/Tng='"  # a public hash  privacy-scan: allow secret-entropy
        self.assertEqual(csp_hash("alert('Hello, world.');"), spec)

    def test_an_edited_script_no_longer_matches(self) -> None:
        edited = INDEX_HTML.replace("</script>", " </script>", 1)
        self.assertNotEqual(prepaint_hash(edited), PREPAINT)
        self.assertNotEqual(csp_problems(CONF["app"]["security"]["csp"], prepaint_hash(edited)), [])

    def test_planted_pages_fail(self) -> None:
        head = "<head>"
        variants = {
            "a second inline script": INDEX_HTML.replace(head, head + "<script>x()</script>", 1),
            "no inline script": "<!doctype html><html><head></head><body></body></html>",
            "an inline handler": INDEX_HTML.replace("<body>", '<body onload="x()">', 1),
            "an upper-case handler": INDEX_HTML.replace("<body>", '<body ONLOAD="x()">', 1),
            "a style element": INDEX_HTML.replace(head, head + "<style>a{}</style>", 1),
            "a style attribute": INDEX_HTML.replace("<body>", '<body style="color:red">', 1),
            "a javascript: url": INDEX_HTML.replace("<body>", '<body><a href="javascript:x()">x</a>', 1),
        }
        for name, html in variants.items():
            with self.subTest(variant=name):
                self.assertNotEqual(page_problems(html), [], f"{name} passed the check")


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
