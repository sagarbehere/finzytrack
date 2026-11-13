"""The packaged build's engine self-check (`app.selfcheck`; S0038; ADR-0054 decisions 1 and 5, ADR-0056).

The packaged `--smoke-test` (`shell/src-tauri/src/smoke.rs`) and the sidecar build (`shell/sidecar/build_sidecar.py`)
run it inside the bundled interpreter and judge its report: a real handshake and ping with the packaged engine, the
main side's view (no engine code), and the engine side's path (no main-side site-packages). It reports facts and
never raises on a broken engine, so the judges see the failure. Here the backend's interpreter and the engine venv's
site-packages stand for the bundle's two sides (as in `tests/platform/test_engine_packaged.py`).
"""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import site
import subprocess
import sys
from pathlib import Path

import pytest

from app import selfcheck

REPO = Path(__file__).resolve().parents[1].parent
ENGINE_VENV = REPO / "engine" / ".venv"


def engine_site() -> Path:
    found = [p for p in (ENGINE_VENV / "lib").glob("python3.*/site-packages") if p.is_dir()]
    found += [p for p in [ENGINE_VENV / "Lib" / "site-packages"] if p.is_dir()]
    if len(found) != 1:
        pytest.fail("engine/.venv is missing: run `uv sync --locked` in engine/ (the engine tests need it)")
    return found[0]


def resolved(paths: object) -> set[str]:
    assert isinstance(paths, list)
    return {str(Path(str(p)).resolve()) for p in paths}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def test_a_working_engine_is_reported_with_both_views() -> None:
    report = selfcheck.report(Path(sys.executable), engine_site())
    assert report["error"] is None, report
    handshake = report["handshake"]
    assert isinstance(handshake, dict)
    assert handshake["beancount_version"] == "3.2.0"
    assert handshake["protocol"] == 2
    assert report["pong"] is True
    assert report["site"] == str(engine_site())
    assert report["main_finds"] == {"beancount": False, "beanquery": False, "ledger_engine": False}
    assert report["engine_finds_app"] is False
    assert resolved(report["main_sites"]) == {str(Path(p).resolve()) for p in site.getsitepackages()}
    assert not resolved(report["engine_path"]) & resolved(report["main_sites"])
    assert str(engine_site().resolve()) in resolved(report["engine_path"])
    assert str(engine_site().resolve()) not in resolved(report["main_path"])


def test_a_failed_view_probe_is_named_while_the_engine_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """S0038 verifier F2: a working engine whose path probe fails still reports why (a regression guard: it kills
    the mutant that drops `view_error` from `error`)."""

    def failed_view(_python: Path, _site: Path) -> tuple[list[str] | None, bool | None, str | None]:
        return None, None, "engine view: TimeoutExpired"

    monkeypatch.setattr(selfcheck, "_engine_view", failed_view)
    report = selfcheck.report(Path(sys.executable), engine_site())
    assert report["pong"] is True
    assert report["error"] == "engine view: TimeoutExpired"


def test_a_missing_engine_is_reported_not_raised(tmp_path: Path) -> None:
    report = selfcheck.report(Path(sys.executable), tmp_path / "missing")
    assert report["pong"] is False
    assert report["handshake"] is None
    assert isinstance(report["error"], str)
    assert "start_failed" in report["error"]


def test_the_module_prints_one_json_report_for_the_packaged_layout() -> None:
    """Outside a bundle the packaged engine site does not exist: the report says so on one line."""
    out = subprocess.run(
        [sys.executable, "-I", "-B", "-m", "app.selfcheck"], capture_output=True, text=True, timeout=60, check=True
    )
    lines = out.stdout.splitlines()
    assert len(lines) == 1, out.stdout
    report = json.loads(lines[0])
    assert report["site"] == str(Path(sys.prefix).parent / "engine" / "site-packages")
    assert report["pong"] is False


def test_main_finds_reports_what_this_process_finds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Must not pass: a main side that can import Beancount is reported as such, for the judges to refuse."""
    # the venv installs ledger_engine through a .pth, so its source folder is added too
    monkeypatch.setattr(sys, "path", [str(engine_site()), str(REPO / "engine" / "src"), *sys.path])
    report = selfcheck.report(Path(sys.executable), tmp_path / "missing")
    assert report["main_finds"] == {"beancount": True, "beanquery": True, "ledger_engine": True}


def test_engine_finds_app_reports_an_engine_site_that_holds_the_main_sidecar() -> None:
    """Must not pass: an engine whose site holds `app` is reported as such (here: this venv's own site-packages)."""
    own = Path(site.getsitepackages()[0])
    report = selfcheck.report(Path(sys.executable), own)
    assert report["engine_finds_app"] is True
    assert str(own.resolve()) in resolved(report["engine_path"])


# S0114 (owner, 2026-11-13: "Fix the check now (Recommended)"): `main_sites` are the main side's real site-packages
# folders. Windows' `site.getsitepackages()` lists the interpreter's prefix too, and every standard-library folder
# lies inside it, so the judges refused every Windows build (S0112 verifier F1).
WIN_PREFIX = "C:\\Apps\\Finzytrack\\sidecar\\python"
UNIX_PREFIX = "/opt/finzytrack/lib/finzytrack/sidecar/python"


def windows(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path))


def unix(path: str) -> str:
    return posixpath.normcase(posixpath.normpath(path))


def test_windows_main_sites_drop_the_prefix_and_keep_the_real_site_packages() -> None:
    sites = [WIN_PREFIX, ntpath.join(WIN_PREFIX, "Lib", "site-packages")]
    assert selfcheck.main_site_packages(sites, [WIN_PREFIX, WIN_PREFIX], windows) == [sites[1]]


def test_unix_main_sites_keep_every_real_site_packages() -> None:
    sites = [posixpath.join(UNIX_PREFIX, d, "python3.13", "site-packages") for d in ("lib64", "lib")]
    assert selfcheck.main_site_packages(sites, [UNIX_PREFIX], unix) == sites


def test_main_sites_must_not_drop_a_real_site_packages() -> None:
    """Must not pass: only the prefix itself goes, compared as the OS compares paths; a folder inside it (the real
    site-packages, in any case or spelling), and another folder, stay, so the judges still refuse an engine path that
    holds one of them."""
    kept = [
        ntpath.join(WIN_PREFIX, "Lib", "site-packages"),
        ntpath.join(WIN_PREFIX.upper(), "LIB", "SITE-PACKAGES"),
        WIN_PREFIX + "\\Lib\\..\\Lib\\site-packages",
        "D:\\elsewhere\\site-packages",
    ]
    assert selfcheck.main_site_packages([WIN_PREFIX, *kept], [WIN_PREFIX], windows) == kept
    # the prefix in another spelling (case, separator, trailing separator) is still the prefix
    for spelling in (WIN_PREFIX.lower(), WIN_PREFIX.replace("\\", "/"), WIN_PREFIX + "\\"):
        assert selfcheck.main_site_packages([spelling, kept[0]], [WIN_PREFIX], windows) == [kept[0]], spelling
    # a prefix is never a site-packages folder; with nothing else listed nothing is left, which the judges refuse
    assert selfcheck.main_site_packages([WIN_PREFIX], [WIN_PREFIX], windows) == []


def test_the_report_names_the_real_site_packages_on_a_windows_shaped_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The report itself (what both judges read): `getsitepackages()` shaped as on Windows, here with this OS's
    separators."""
    real = os.path.join(sys.prefix, "Lib", "site-packages")
    monkeypatch.setattr(site, "getsitepackages", lambda: [sys.prefix, real])
    report = selfcheck.report(Path(sys.executable), tmp_path / "missing")
    assert report["main_sites"] == [real]
