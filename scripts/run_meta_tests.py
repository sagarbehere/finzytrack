#!/usr/bin/env python3
"""Run the check-script meta-tests in parallel, one process per test class (S0034).

Runs the tests `python3 -m unittest discover -s scripts/tests` runs: it discovers them the same way (same start
folder, pattern and import path), runs each test class in a process of its own (a whole module when it has module
fixtures or `load_tests`), as many at once as there are CPUs, and then checks that the processes together ran
exactly the discovered tests. Exit 0 only when every test ran
and passed; 1 on a failure, an error, an unexpected success, a module that could not be imported, a module process
that ended without its result or ran other tests than were discovered, or no tests at all.

Usage: python3 scripts/run_meta_tests.py [-v] [-j JOBS] [--start DIR] [--list]
  -v       print every module's output, not only the failing ones'
  -j       processes at once (default: the CPU count)
  --start  the folder to discover in (default scripts/tests; the meta-tests point it at fixtures)
  --list   print the discovered test IDs and exit
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
START = REPO / "scripts/tests"


def _load_gate_timer():
    """scripts/gate_timer.py (its git-directory state helpers: the units' last times order the next run), under a
    private module name: a test that imports `gate_timer` must fail here as it does under `python3 -m unittest`."""
    spec = importlib.util.spec_from_file_location("_run_meta_tests_gate_timer", REPO / "scripts/gate_timer.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate_timer = _load_gate_timer()
PATTERN = "test*.py"  # unittest discover's default
MODULE_WIDE = ("setUpModule", "tearDownModule", "load_tests")  # a module with one of these runs in one process


def as_unittest_main() -> None:
    """Import paths as `python3 -m unittest` has them: the current folder first, not this script's folder."""
    sys.path[0] = os.getcwd()


def flatten(suite: unittest.TestSuite | unittest.TestCase) -> Iterator[unittest.TestCase]:
    if isinstance(suite, unittest.TestSuite):
        for item in suite:
            yield from flatten(item)
    else:
        yield suite


def discover(start: Path, pattern: str = PATTERN) -> tuple[list[unittest.TestCase], list[str]]:
    """The tests `unittest discover -s <start> -p <pattern>` finds, and its import errors."""
    loader = unittest.TestLoader()
    tests = list(flatten(loader.discover(str(start), pattern=pattern)))
    return tests, list(loader.errors)


def unit_of(test: unittest.TestCase) -> str:
    """The process a test runs in: `module:Class`, or `module` for a module with module-wide fixtures."""
    module = type(test).__module__
    loaded = sys.modules.get(module)
    if loaded is None or any(hasattr(loaded, name) for name in MODULE_WIDE):
        return module
    return f"{module}:{type(test).__qualname__}"


def worker(start: Path, unit: str, result_path: Path, verbose: bool) -> int:
    """Run one unit's tests (found by discovering its module's file name) and write what ran to `result_path`."""
    module, _, cls = unit.partition(":")
    tests, errors = discover(start, module.rsplit(".", 1)[-1] + ".py")
    mine = [t for t in tests if type(t).__module__ == module and (not cls or type(t).__qualname__ == cls)]
    ran: list[str] = []

    class Recorder(unittest.TextTestResult):
        def startTest(self, test: unittest.TestCase) -> None:  # noqa: N802 (unittest's name)
            ran.append(test.id())
            super().startTest(test)

    runner = unittest.TextTestRunner(stream=sys.stderr, verbosity=2 if verbose else 0, resultclass=Recorder)
    result = runner.run(unittest.TestSuite(mine))
    ok = result.wasSuccessful() and not errors
    result_path.write_text(json.dumps({
        "ran": ran, "ok": ok, "failures": len(result.failures), "errors": len(result.errors) + len(errors),
        "skipped": len(result.skipped), "unexpected_successes": len(result.unexpectedSuccesses),
    }))
    return 0 if ok else 1


def run_unit(start: Path, unit: str, verbose: bool) -> tuple[dict | None, str, int, float]:
    began = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        result = Path(tmp) / "result.json"
        cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", unit, "--result", str(result),
               "--start", str(start)] + (["-v"] if verbose else [])
        proc = subprocess.run(cmd, cwd=os.getcwd(), stdin=subprocess.DEVNULL, capture_output=True, text=True)
        try:
            data = json.loads(result.read_text())
        except (OSError, ValueError):
            data = None
        seconds = time.monotonic() - began
        output = proc.stdout + proc.stderr
        if verbose:
            output = f"({seconds:.1f} s)\n{output}"
        return data, output, proc.returncode, seconds


def _seconds(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float("inf")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1)
    p.add_argument("--start", type=Path, default=START)
    p.add_argument("--list", action="store_true")
    p.add_argument("--worker", help=argparse.SUPPRESS)
    p.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    as_unittest_main()
    if args.worker:
        return worker(args.start, args.worker, args.result, args.verbose)

    began = time.monotonic()
    tests, errors = discover(args.start)
    if args.list:
        print("\n".join(t.id() for t in tests))
        return 0
    groups: dict[str, list[str]] = {}
    for t in tests:
        if type(t).__module__ != "unittest.loader":  # unittest's _FailedTest: the import errors below
            groups.setdefault(unit_of(t), []).append(t.id())
    problems = [f"could not import a test module:\n{e}" for e in errors]
    if not tests:
        problems.append(f"no tests found in {args.start}")
    # The longest units first, so none starts last (a unit with no time yet counts as longest): the order only
    # changes the wall time, never what runs. Times are kept for the real suite only, in the git directory.
    times_file = None
    if args.start.resolve() == START.resolve():
        folder = gate_timer.state_dir(REPO)
        times_file = folder / "meta-test-units.json" if folder else None
    last = gate_timer.load_json(times_file)
    order = sorted(groups, key=lambda u: (-_seconds(last.get(u)), -len(groups[u]), u))
    jobs = max(1, min(args.jobs, len(order) or 1))
    with ThreadPoolExecutor(jobs) as pool:
        results = dict(zip(order, pool.map(lambda u: run_unit(args.start, u, args.verbose), order), strict=True))
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "unexpected_successes": 0}
    modules = {u.partition(":")[0] for u in order}
    for unit in order:
        data, output, code, took = results[unit]
        last[unit] = round(took, 2)
        if args.verbose and output.strip():
            print(f"--- {unit}\n{output.rstrip()}", file=sys.stderr)
        if not isinstance(data, dict):
            problems.append(f"{unit}: its process ended (exit {code}) without a result:\n{output[-4000:]}")
            continue
        ran = data.get("ran") if isinstance(data.get("ran"), list) else []
        counts["tests"] += len(ran)
        for k in ("failures", "errors", "skipped", "unexpected_successes"):
            counts[k] += int(data.get(k) or 0)
        expected = groups[unit]
        if sorted(ran) != sorted(expected):
            problems.append(f"{unit}: ran {len(ran)} tests, not the {len(expected)} discovered "
                            f"(missing {sorted(set(expected) - set(ran))[:5]}, "
                            f"extra {sorted(set(ran) - set(expected))[:5]})")
        if data.get("ok") is not True or code != 0:
            problems.append(f"{unit}: FAILED (exit {code})\n{output.rstrip()[-8000:]}")
    gate_timer.save_json(times_file, {u: last[u] for u in order})
    seconds = time.monotonic() - began
    summary = (f"{counts['tests']} tests in {len(modules)} modules ({len(order)} processes, {jobs} at once), "
               f"{seconds:.1f} s (failures={counts['failures']}, errors={counts['errors']}, "
               f"skipped={counts['skipped']}, unexpected successes={counts['unexpected_successes']})")
    if problems:
        print("\n\n".join(problems), file=sys.stderr)
        print(f"run_meta_tests: FAILED: {summary}", file=sys.stderr)
        return 1
    print(f"run_meta_tests: OK: {summary}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
