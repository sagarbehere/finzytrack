#!/usr/bin/env python3
"""Spike S12: run the legacy backend's API-level tests against the legacy app as a separate process (review:C DEP-12).

Steps, all inside a work folder (INV-24: nothing is written into the legacy worktree):
1. copy the legacy backend (app, tests, resources, scripts, project files) into <work>/backend, without bytecode;
2. build a disposable venv from the legacy's pinned requirements.txt plus pytest (the legacy venv has no pytest);
3. run the whole suite in process, recording the API-level tests (`legacy_shim`, FT_SHIM_MODE=record);
4. run the API-level tests out of process (FT_SHIM_MODE=process): each client is the legacy app started on the
   test's temp root by `launch_legacy.LegacyApp` (offline), reached over HTTP;
5. compare the two runs and write <work>/s12-report.json.

Usage (from the repo root; standard library only):
    python3 scripts/oracle/legacy_api_tests.py --work DIR [--skip-setup]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from copies import legacy_root, refuse_dir

ORACLE = Path(__file__).resolve().parent
PYTEST = "pytest==8.4.2"
COPIED = ("app", "tests", "resources", "scripts", "pyproject.toml", "requirements.txt")


def copy_backend(work: Path) -> Path:
    """Copy the legacy backend into *work*/backend (no bytecode, no caches); the source is only read."""
    problem = refuse_dir(work)
    if problem:
        raise ValueError(problem)
    src, dst = legacy_root() / "backend", work / "backend"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.egg-info")
    for name in COPIED:
        s = src / name
        if s.is_dir():
            shutil.copytree(s, dst / name, ignore=ignore, symlinks=True)
        elif s.is_file():
            shutil.copy2(s, dst / name)
    shutil.copy2(legacy_root() / "VERSION", work / "VERSION")  # the app reads it from the repo root
    shutil.copy2(ORACLE / "legacy_shim.py", dst / "legacy_shim.py")
    return dst


def build_venv(work: Path, backend: Path) -> Path:
    venv = work / "venv"
    if not (venv / "bin/python").exists():
        subprocess.run(["uv", "venv", "--python", "3.13", str(venv)], check=True, capture_output=True)
    subprocess.run(["uv", "pip", "install", "--python", str(venv / "bin/python"), "-r", str(backend / "requirements.txt"),
                    PYTEST], check=True, capture_output=True)
    return venv / "bin/python"


def run_pytest(python: Path, backend: Path, work: Path, mode: str, junit: Path) -> float:
    env = {**os.environ, "FT_SHIM_MODE": mode, "FT_SHIM_OUT": str(work), "FT_ORACLE_DIR": str(ORACLE),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(backend)}
    t0 = time.monotonic()
    with (work / f"pytest-{mode}.log").open("w") as log:
        rc = subprocess.run([str(python), "-m", "pytest", "-p", "legacy_shim", "-p", "no:cacheprovider", "-q", "-o",
                             "console_output_style=classic", f"--junitxml={junit}"], cwd=backend, env=env,
                            stdout=log, stderr=subprocess.STDOUT).returncode
    if rc not in (0, 1):  # 0 = all passed, 1 = some failed; anything else is an interrupted or broken run
        raise RuntimeError(f"pytest ({mode}) exited {rc}; see {work / f'pytest-{mode}.log'}")
    return time.monotonic() - t0


def parse_junit(path: Path) -> dict[str, tuple[str, str]]:
    """node ID → (outcome, first message) from a pytest JUnit XML file."""
    out: dict[str, tuple[str, str]] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        parts = case.get("classname", "").split(".")
        # classname is the dotted module path, plus a class name when the test is a method
        mod = [p for p in parts if not p[:1].isupper()]
        cls = [p for p in parts if p[:1].isupper()]
        nodeid = "/".join(mod) + ".py::" + "".join(c + "::" for c in cls) + case.get("name", "")
        outcome, msg = "passed", ""
        for tag in ("failure", "error", "skipped"):
            el = case.find(tag)
            if el is not None:
                outcome, msg = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag], el.get("message", "")
                break
        out[nodeid] = (outcome, msg)
    return out


def compare_outcomes(inproc: dict[str, tuple[str, str]], outproc: dict[str, tuple[str, str]], api: set[str],
                     shimmed: set[str]) -> dict:
    base = sorted(t for t in api if inproc.get(t, ("",))[0] == "passed")
    passing = [t for t in base if outproc.get(t, ("",))[0] == "passed" and t in shimmed]
    not_shimmed = [t for t in base if t not in shimmed]
    failing = {t: f"{outproc.get(t, ('missing', ''))[0]}: {outproc.get(t, ('', ''))[1]}".rstrip(": ")
               for t in base if t in shimmed and outproc.get(t, ("",))[0] != "passed"}
    return {"api_tests": len(api), "denominator": len(base), "passing_out_of_process": len(passing),
            "ratio": len(passing) / len(base) if base else 0.0, "not_shimmed": not_shimmed,
            "failing_out_of_process": failing}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--skip-setup", action="store_true", help="reuse the copy and venv in --work")
    args = ap.parse_args(argv)
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    backend = work / "backend"
    if args.skip_setup:
        python = work / "venv/bin/python"
    else:
        backend = copy_backend(work)
        python = build_venv(work, backend)
    t_in = run_pytest(python, backend, work, "record", work / "inprocess.xml")
    t_out = run_pytest(python, backend, work, "process", work / "process.xml")
    api = set((work / "api_tests.txt").read_text(encoding="utf-8").split())
    shimmed = set((work / "shimmed.txt").read_text(encoding="utf-8").split()) if (work / "shimmed.txt").exists() else set()
    inproc = parse_junit(work / "inprocess.xml")
    summary = compare_outcomes(inproc, parse_junit(work / "process.xml"), api, shimmed)
    summary |= {"all_tests": len(inproc), "in_process_passed": sum(o == "passed" for o, _ in inproc.values()),
                "seconds": {"in_process": round(t_in), "out_of_process": round(t_out)}}
    (work / "s12-report.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("not_shimmed", "failing_out_of_process")}, indent=1))
    print(f"not shimmed: {len(summary['not_shimmed'])}, failing out of process: {len(summary['failing_out_of_process'])}")
    return 0 if summary["ratio"] >= 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
