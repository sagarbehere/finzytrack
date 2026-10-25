#!/usr/bin/env python3
"""Differential-oracle harness (ADR-0014): apply an intent with the legacy app on a copy, compare its change with
the intended change, and tag every divergence.

Each case runs in a fresh data root (a copy of --ledger-dir plus a seeded legacy config) with its own legacy
process, so no state leaks between cases. The source folder is only read (INV-24). Reports carry entry text only
for repo corpora and generated ledgers; for anything else (a copy of a real ledger, T-local) they carry counts,
classes and case IDs only.

Usage (from the repo root):
    uv run --project scripts/oracle python scripts/oracle/harness.py --demo [--per-mutator 5]
    uv run --project scripts/oracle python scripts/oracle/harness.py --ledger-dir DIR --root-file main.beancount \\
        [--cases cases.json | --per-mutator N] [--report out.json] [--port 18960]

A case is {"id", "mutator": "update_transaction" | "rename_account" | "import_append", "params": {...}}:
update_transaction {"txn_id", "entry"}, rename_account {"old", "new"}, import_append {"entries": [...]}.
Exit status: 0 when no case is UNEXPLAINED, 1 otherwise, 2 on a refused input.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from beancount.core import data
from beancount.core.number import MISSING

import adapters
import classify
import model
import semantic_diff as sd
from copies import legacy_root, mark_generated, may_show_entries, refuse_dir, stage_root
from launch_legacy import LegacyApp

MUTATORS = ("update_transaction", "rename_account", "import_append")
DEMO_ANCHOR = "2026-04"  # a fixed calendar, so the demo ledger is byte-identical on every run


def expected_change(before: sd.LedgerSet, case: dict[str, Any]) -> model.Expected:
    p = case["params"]
    match case["mutator"]:
        case "update_transaction":
            return model.update_transaction(before, p["txn_id"], p["entry"])
        case "rename_account":
            return model.rename_account(before, p["old"], p["new"])
        case "import_append":
            return model.import_append(before, p["entries"])
    raise ValueError(f"unknown mutator {case['mutator']!r}")


def seed_template(work: Path, port: int) -> Path:
    """A legacy config/ seeded by one first start on an empty root; copied into every case root."""
    template = work / "template"
    if not (template / "config/config.yaml").is_file():
        with LegacyApp(template, port):
            pass
    return template


def ledger_files_bytes(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


@dataclass(frozen=True)
class Run:
    """What every case of one run shares."""
    src: Path
    root_file: str
    before: sd.LedgerSet
    generated: frozenset[str]
    booked: dict[str, Any]
    template: Path
    work: Path
    port: int


def stray_files(run: Run, ledgers: Path, after: sd.LedgerSet) -> list[classify.Divergence]:
    """Files the legacy app created or changed that the semantic diff cannot see: outside the ledger set, or new and
    holding no entries (I2: a file literally named after a glob pattern)."""
    old, new = ledger_files_bytes(run.src), ledger_files_bytes(ledgers)
    out = []
    for f in sorted(set(old) | set(new)):
        if old.get(f) == new.get(f) or f in run.before.files or (f in after.files and after.files[f].keys):
            continue
        if f not in old and sd.GLOB_CHARS & set(Path(f).name) and not new.get(f, b"").strip():
            out.append(classify.Divergence("I2", f, None, None, "created an empty file named after a glob pattern"))
        else:
            out.append(classify.Divergence("UNEXPLAINED", f, None, None, "a file outside the ledger set changed"))
    return out


def run_case(case: dict[str, Any], run: Run) -> dict[str, Any]:
    t0 = time.monotonic()
    out: dict[str, Any] = {"id": case["id"], "mutator": case["mutator"]}
    try:
        expected = expected_change(run.before, case)
    except model.NonComparable as err:
        return {**out, "verdict": "NON_COMPARABLE", "note": str(err)}
    req = adapters.ADAPTERS[case["mutator"]](case["params"], run.booked)
    root = run.work / "cases" / case["id"]
    stage_root(run.src, run.root_file, run.template, root)
    ledgers = root / "data/ledgers"
    t_staged = time.monotonic()
    with LegacyApp(root, run.port):
        t_up = time.monotonic()
        status, body = adapters.send(run.port, req)
    t_done = time.monotonic()
    after = sd.parse_set(ledgers, run.root_file)
    error = body.get("error") or {}
    error = error if isinstance(error, dict) else {}
    if status >= 400:
        report = classify.refused(run.before, after, expected, run.generated, case["mutator"], error.get("code"))
    else:
        report = classify.compare(run.before, after, expected, run.generated)
    divergences = report.divergences + stray_files(run, ledgers, after)
    verdict = classify.Report(divergences).verdict
    out |= {"verdict": verdict, "legacy_http": status, "legacy_code": error.get("code"),
            "legacy_message": error.get("message"), "legacy_details": error.get("details"),
            "classes": dict(classify.Report(divergences).class_counts()), "divergences": [asdict(d) for d in divergences],
            "seconds": {"stage": round(t_staged - t0, 2), "start": round(t_up - t_staged, 2), "write": round(t_done - t_up, 2),
                        "compare": round(time.monotonic() - t_done, 2), "total": round(time.monotonic() - t0, 2)}}
    return out


def redact(result: dict[str, Any]) -> dict[str, Any]:
    """Counts, classes and case IDs only: no entry text, file names, notes or legacy messages."""
    return {k: v for k, v in result.items() if k not in ("divergences", "note", "legacy_message", "legacy_details")}


# --- generated cases (deterministic per seed) ---------------------------------------------------------------------

def _transactions(before: sd.LedgerSet) -> list[Any]:
    return sorted((e for f in before.files.values() for e in f.by_key.values() if isinstance(e, data.Transaction)),
                  key=lambda e: (e.date, sd.canonical_key(e)))


def _render(t: Any) -> str:
    return sd.canonical_key(t)


def gen_updates(before: sd.LedgerSet, n: int, rng: random.Random) -> list[dict[str, Any]]:
    by_id: dict[str, list[Any]] = {}
    for t in _transactions(before):
        if "id" in t.meta:
            by_id.setdefault(t.meta["id"], []).append(t)
    pool = [ts[0] for ts in by_id.values() if len(ts) == 1]
    cases = []
    for i, t in enumerate(rng.sample(pool, min(n, len(pool)))):
        edit = i % 3
        if edit == 0:
            new = t._replace(narration=(t.narration or "") + " (edited)")
        elif edit == 1:
            new = t._replace(payee="Oracle Payee", tags=frozenset(t.tags) | {"oracle"})
        else:
            new = t._replace(date=t.date.replace(day=min(t.date.day, 27) + 1))
        cases.append({"id": f"upd-{i:03d}", "mutator": "update_transaction",
                      "params": {"txn_id": t.meta["id"], "entry": _render(new)}})
    return cases


def gen_renames(before: sd.LedgerSet, n: int, rng: random.Random) -> list[dict[str, Any]]:
    opened = sorted({e.account for f in before.files.values() for e in f.by_key.values() if isinstance(e, data.Open)})
    leaves = [a for a in opened if not any(b.startswith(a + ":") for b in opened)]
    cases = []
    for i, acct in enumerate(rng.sample(leaves, min(n, len(leaves)))):
        new = f"{acct}Oracle"
        cases.append({"id": f"ren-{i:03d}", "mutator": "rename_account", "params": {"old": acct, "new": new}})
    return cases


def gen_imports(before: sd.LedgerSet, n: int, rng: random.Random) -> list[dict[str, Any]]:
    """New two-leg transactions between accounts that are open on the chosen date; the anchor gets `import-key`."""
    txns = [t for t in _transactions(before) if len(t.postings) == 2
            and all(p.units is not MISSING and p.units.number is not MISSING and p.cost is None and p.price is None
                    for p in t.postings) and any(p.account.startswith(adapters.SOURCE_ROOTS) for p in t.postings)]
    cases = []
    for i, t in enumerate(rng.sample(txns, min(n, len(txns)))):
        anchor = next(p for p in t.postings if p.account.startswith(adapters.SOURCE_ROOTS))
        other = next(p for p in t.postings if p is not anchor)
        amount = anchor.units.number
        text = (f'{t.date.isoformat()} * "Oracle Import {i}" "imported line {i}"\n'
                f"  {anchor.account}  {format(amount, 'f')} {anchor.units.currency}\n"
                f'    import-key: "v1:h:oracle{i:03d}"\n'
                f"  {other.account}  {format(-amount, 'f')} {anchor.units.currency}\n")
        cases.append({"id": f"imp-{i:03d}", "mutator": "import_append", "params": {"entries": [text]}})
    return cases


def generate_cases(before: sd.LedgerSet, per_mutator: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    return gen_updates(before, per_mutator, rng) + gen_renames(before, per_mutator, rng) + gen_imports(before, per_mutator, rng)


def generate_demo(work: Path) -> tuple[Path, str]:
    """The legacy demo ledger (synthetic), generated on a fixed calendar into *work*/demo."""
    out = work / "demo"
    out.mkdir(parents=True, exist_ok=True)
    legacy = legacy_root()
    env = {**os.environ, "PYTHONPATH": str(legacy / "backend"), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([str(legacy / "venv/bin/python"), str(legacy / "backend/scripts/generate_fake.py"),
                    "--anchor", DEMO_ANCHOR, "--buffer", "0", "--out", str(out / "fake.beancount")],
                   cwd=work, env=env, check=True, capture_output=True)
    mark_generated(out)
    return out, "fake.beancount"


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [r["seconds"]["total"] for r in results if "seconds" in r]
    verdicts: dict[str, int] = {}
    classes: dict[str, int] = {}
    for r in results:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        for c, n in r.get("classes", {}).items():
            classes[c] = classes.get(c, 0) + n
    timing = {}
    if totals:
        q = statistics.quantiles(totals, n=20, method="inclusive") if len(totals) > 1 else totals * 19
        timing = {"p50": round(statistics.median(totals), 2), "p95": round(q[18], 2), "max": max(totals)}
    return {"cases": len(results), "verdicts": verdicts, "classes": classes, "seconds_per_case": timing}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src_group = ap.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--demo", action="store_true", help="generate the legacy demo ledger and use it")
    src_group.add_argument("--ledger-dir", type=Path, help="folder holding the ledger set (only read)")
    ap.add_argument("--root-file", help="the root file, relative to --ledger-dir")
    ap.add_argument("--cases", type=Path, help="JSON list of cases; default: generated")
    ap.add_argument("--per-mutator", type=int, default=5)
    ap.add_argument("--seed", type=int, default=9)
    ap.add_argument("--port", type=int, default=18960)
    ap.add_argument("--work", type=Path, help="work folder (default: a new temp dir)")
    ap.add_argument("--report", type=Path, help="write the JSON report here")
    args = ap.parse_args(argv)

    work = (args.work or Path(tempfile.mkdtemp(prefix="ft-oracle-"))).resolve()
    for folder in [work] + ([args.ledger_dir] if args.ledger_dir else []):
        problem = refuse_dir(folder, allow_repo_corpora=folder is args.ledger_dir)
        if problem:
            print(f"harness: refused: {problem}", file=sys.stderr)
            return 2
    work.mkdir(parents=True, exist_ok=True)
    if args.demo:
        src, root_file = generate_demo(work)
    elif not args.root_file:
        print("harness: --root-file is required with --ledger-dir", file=sys.stderr)
        return 2
    else:
        src, root_file = args.ledger_dir.resolve(), args.root_file
    show = may_show_entries(src)

    before = sd.parse_set(src, root_file)
    generated = sd.generated_keys(src, root_file)
    cases = json.loads(args.cases.read_text(encoding="utf-8")) if args.cases else \
        generate_cases(before, args.per_mutator, args.seed)
    run = Run(src, root_file, before, generated, sd.booked_by_legacy_id(src, root_file), seed_template(work, args.port), work, args.port)
    results = []
    for case in cases:
        r = run_case(case, run)
        results.append(r if show else redact(r))
        print(f"{r['id']}: {r['verdict']} {r.get('classes', '')} {r.get('seconds', {}).get('total', '')}s", flush=True)
    report = {"summary": summarize(results), "entries_shown": show, "results": results}
    if args.report:
        args.report.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps(report["summary"], indent=1))
    return 1 if any(r["verdict"] == "UNEXPLAINED" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
