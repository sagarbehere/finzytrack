#!/usr/bin/env python3
"""G1's refusal rate on a ledger copy (S0163; ADR-0046: "under 5% unjustified refusals on copies of the owner's books").

For N generated intents per mutator (update_transaction, rename_account, import_append; the oracle's generators,
`harness.py`, and its intended-change model, `model.py`), each case applies its change through the new write core as
the product would: the engine's preview (the write contract's steps 1-3) and the confirm's save, in a fresh test copy
of the ledger folder per case (`backend/scripts/g1_runner.py`, wired as `measure_write.py` is). **No real mutator
exists yet** (the shipped engine registers no producer until M3/M4), so each intent is sent as the engine's test
producer's primitive edits (`engine/tests/ledger/producer.py`): an update replaces the transaction at its line, a rename
is one account-token edit per line that names the account, an import is one insert into the root file. What is
measured is the write core (the splice, the contract, the save) on those edits, not a mutator's own logic.

How to run it (from the repository's root, with the oracle's environment; `backend/.venv` and `engine/.venv` synced):

  1. Copy the folder that holds your ledger, with everything it includes and its documents folder, into a subfolder of
     a folder outside the repository, as a plain copy that follows links (never your live data folder), e.g.:

       cp -RL ~/MyLedgers ~/ft-local-tier/books

  2. Run, naming that subfolder and the root file's path inside it:

       FT_LOCAL_TIER_DIR=~/ft-local-tier uv run --project scripts/oracle python scripts/oracle/g1_refusals.py \\
           --ledger-dir ~/ft-local-tier/books --root-file ledgers/main.beancount [--per-mutator 20] [--seed 9]

  A repo corpus (`corpora/...`) needs no FT_LOCAL_TIER_DIR.

**Outcomes.** `saved`; `save_anyway`: the preview listed new errors (an accounting error, ADR-0067), the tool
acknowledged them as the user's "Save anyway" would, and the save landed: not a refusal; `refused` with its code, the
contract's step and the engine's reason; `not_expressible`: a rename that would also change a metadata or `custom`
value naming the account, which the test producer cannot do (it does not splice metadata lines; the spliced
whole-entry replace of the ADR-0009 amendment of 2026-11-28 comes with M4's `account.rename`): not run and not counted
in the rate. Each refusal is **justified** only when `JUSTIFIED` names its (code, step, reason) and
that rule's condition holds in the copy (`facts_of`, a check apart from the engine's); every other refusal is
**unjustified**: an unknown code, an internal error, a runner answer that does not decode, a case it did not answer.
The rate is unjustified refusals / cases run; exit 0 when it is under 5%, 1 otherwise, 2 on a refused input, 3 when
the run stopped (the runner failed, or the source folder changed).

**What it prints** (every source; INV-23, PROCESS.md §8): one line per case (its ID, mutator, outcome, and for a refusal
its code/step/reason and verdict), then a JSON summary: counts per mutator and outcome, the model's label per case
(`valid`, or `not_comparable` where the model does not decide the intended change: still run), refusals per key with
their verdict, citation and case IDs, and the rate. Never an account, amount, payee, narration, file name or entry
text; the runner's answer is decoded field by field against fixed vocabularies (`decode`), and its error output is
shown only for a repo corpus (`copies.may_show_entries`).

**What it reads and writes.** The source folder is only read (INV-24): its files are hashed before and after the run,
and a difference stops it (exit 3). A source that is not a repo corpus must be a folder inside FT_LOCAL_TIER_DIR (not
the folder itself), holding no link of any kind, whose includes stay inside it (`copies.refuse_local_tier_source`,
`harness.check_contained`). Every case's copy, its mirror, the app folder and the engine's cache are made in a fresh
work folder (inside FT_LOCAL_TIER_DIR for a copy, so a real ledger's bytes stay there; else the temp folder) and
removed at the end; each save is confined by the engine to its case's copy (a test-copy mark, `writes.confinement`).

**Stated limits.** The test producer, not a mutator (above). A case whose save landed is not compared with the
intended change (the differential oracle does that, `harness.py`); "saved" means the write core accepted and wrote it.
The independent conditions are coarse: "the ledger set holds a file with an encrypted file's name", "the case
edits such a file" and "a ledger file has a column-0 pushtag or pushmeta line". A plugin's own code runs in the engine and can read or write anything, as in every load.
A run killed part way leaves its work folder (inside FT_LOCAL_TIER_DIR for a copy): delete it. The codes are checked
by shape (capitals and `_`), not against the registry. The runner is started with `backend/.venv/bin/python` (macOS and
Linux).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beancount.core import data

import harness
import model
import semantic_diff as sd
from copies import CORPORA, REPO, local_tier_dir, may_show_entries, refuse_local_tier_source, within

BACKEND = REPO / "backend"
RUNNER = BACKEND / "scripts" / "g1_runner.py"
RUNNER_PYTHON = BACKEND / ".venv" / "bin" / "python"
GOAL = 0.05  # ADR-0046: under 5% unjustified refusals

OUTCOMES = ("saved", "save_anyway", "refused")
STEPS = frozenset({"plan", "apply", "parse", "diff", "spans", "load", "errors", "files"})  # registry's ContractStep
CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
REASON = re.compile(r"[a-z][a-z0-9_-]{0,47}")
PUSH = re.compile(r"(?m)^(pushtag|pushmeta)\b")
ENCRYPTED = (".gpg", ".asc")  # ADR-0059, any case


# -- the table --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """What the copy holds, read apart from the engine: the conditions of the justified refusals."""

    encrypted: bool  # a file of the ledger set has an encrypted file's name
    push_region: bool  # a file of the ledger set has a column-0 pushtag or pushmeta line
    edits_encrypted: bool = False  # the case itself edits a file with an encrypted file's name (`case_facts`)


@dataclass(frozen=True)
class Rule:
    citation: str
    holds: Callable[[Facts], bool]


_PUSH = Rule("ADR-0009: an insert never goes inside a pushtag or pushmeta region", lambda f: f.push_region)
JUSTIFIED: dict[tuple[str, str | None, str | None], Rule] = {
    # (code, step, reason); a None step and reason match any (the code alone decides)
    ("ENCRYPTED_FILE_NOT_EDITABLE", None, None): Rule(
        "ADR-0059: encrypted ledger files are not read or edited in v0.4.0", lambda f: f.encrypted),
    ("WRITE_REFUSED_CHECK_FAILED", "plan", "encrypted_file"): Rule(
        "ADR-0010 guardrail 4 and ADR-0059: no write into a file Beancount may decrypt, judged by its name (S0049, "
        "S0115)", lambda f: f.edits_encrypted),  # the producer refused to touch the file the case edits
    ("WRITE_REFUSED_CHECK_FAILED", "plan", "insert_in_push_region"): _PUSH,  # the producer's routing refused it
    ("WRITE_REFUSED_UNINTENDED_CHANGE", "apply", "insert_in_push_region"): _PUSH,  # the splice's re-check refused it
}
"""The decided refusals. Everything else is unjustified: a code here only justifies a refusal whose condition holds."""


def classify(code: str, step: str | None, reason: str | None, facts: Facts) -> tuple[str, str | None]:
    """("justified", its citation) or ("unjustified", None)."""
    rule = JUSTIFIED.get((code, step, reason)) or JUSTIFIED.get((code, None, None))
    if rule is not None and rule.holds(facts):
        return "justified", rule.citation
    return "unjustified", None


def case_facts(facts: Facts, edits: list[dict[str, Any]]) -> Facts:
    """The ledger's facts with the case's own: whether one of its edits names a file with an encrypted file's name."""
    names = [str(e.get("path") or e.get("default_file") or "") for e in edits]
    return Facts(facts.encrypted, facts.push_region, any(n.lower().endswith(ENCRYPTED) for n in names))


def facts_of(src: Path, before: sd.LedgerSet) -> Facts:
    files = [src / rel for rel in before.files]
    return Facts(
        encrypted=any(p.name.lower().endswith(ENCRYPTED) for p in files),
        push_region=any(PUSH.search(p.read_text(encoding="utf-8", errors="replace")) for p in files),
    )


# -- the cases --------------------------------------------------------------------------------------------------------

KEYWORDS: dict[type, str] = {data.Open: "open", data.Close: "close", data.Balance: "balance", data.Pad: "pad",
                             data.Note: "note", data.Document: "document", data.Transaction: "txn"}


def entries_of(before: sd.LedgerSet, rel: str) -> list[Any]:
    """The entries of one file of the set, parsed alone (as `semantic_diff.parse_file` does), each instance with its
    lines."""
    path = before.base / rel
    entries, _, _, _ = sd.parse_entries_with_totals(path.read_text(encoding="utf-8"), str(path))
    return entries


def gen_updates_at(before: sd.LedgerSet, n: int, rng: random.Random) -> list[dict[str, Any]]:
    """The harness's update edits (`harness.edited`), on any transaction by its place: the legacy app addresses a
    transaction by its `id`, the write core by its file and line, so no `id` is needed here."""
    pool = sorted(((rel, e) for rel in before.files for e in entries_of(before, rel) if isinstance(e, data.Transaction)),
                  key=lambda x: (x[1].date, sd.canonical_key(x[1]), x[0], x[1].meta["lineno"]))
    cases = []
    for i, (rel, t) in enumerate(rng.sample(pool, min(n, len(pool)))):
        text = sd.canonical_key(harness.edited(t, i))
        try:
            (_,), _ = model.parse_intent(text)
            label = "valid"
        except ValueError:
            label = "not_comparable"
        cases.append({"id": f"upd-{i:03d}", "mutator": "update_transaction", "model": label, "edits": [
            {"kind": "replace", "path": rel, "line": t.meta["lineno"], "date": t.date.isoformat(), "keyword": "txn",
             "text": text}]})
    return cases


def _tokens(entry: Any, old: str, new: str) -> tuple[Any, list[tuple[int, str, str]]]:
    """The entry with only its account tokens renamed (A and `A:…` to B), and each token changed: (line, old, new)."""
    def rn(name: str) -> str:
        return new + name[len(old):] if name == old or name.startswith(old + ":") else name

    line = entry.meta["lineno"]
    if isinstance(entry, data.Transaction):
        changed = [(p.meta["lineno"], p.account, rn(p.account)) for p in entry.postings if rn(p.account) != p.account]
        return entry._replace(postings=[p._replace(account=rn(p.account)) for p in entry.postings]), changed
    if isinstance(entry, data.Pad):
        changed = [(line, a, rn(a)) for a in (entry.account, entry.source_account) if rn(a) != a]
        return entry._replace(account=rn(entry.account), source_account=rn(entry.source_account)), changed
    return entry._replace(account=rn(entry.account)), [(line, entry.account, rn(entry.account))]


def rename_edits(before: sd.LedgerSet, old: str, new: str) -> list[dict[str, Any]] | None:
    """The rename as token edits, one per line naming A or `A:…`; None when the intended change (`model.renamed`) is
    more than its account tokens (a metadata or `custom` value naming the account): not expressible."""
    edits = []
    for rel in sorted(before.files):
        for e in entries_of(before, rel):
            want = sd.canonical_key(model.renamed(e, old, new))
            if want == sd.canonical_key(e):
                continue
            keyword = KEYWORDS.get(type(e))
            if keyword is None:
                return None
            got, changed = _tokens(e, old, new)
            if sd.canonical_key(got) != want:
                return None
            edits += [{"kind": "token", "path": rel, "line": e.meta["lineno"], "date": e.date.isoformat(),
                       "keyword": keyword, "lineno": lineno, "old": a, "new": b} for lineno, a, b in changed]
    return edits


def _label(before: sd.LedgerSet, case: dict[str, Any]) -> str:
    try:
        harness.expected_change(before, case)
    except (model.NonComparable, ValueError):
        return "not_comparable"
    return "valid"


def generate(before: sd.LedgerSet, per_mutator: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    cases = gen_updates_at(before, per_mutator, rng)
    for c in harness.gen_renames(before, per_mutator, rng):
        edits = rename_edits(before, c["params"]["old"], c["params"]["new"])
        cases.append({"id": c["id"], "mutator": c["mutator"], "model": _label(before, c), "edits": edits})
    for c in harness.gen_imports(before, per_mutator, rng):
        edits = [{"kind": "insert", "text": text, "default_file": before.root} for text in c["params"]["entries"]]
        cases.append({"id": c["id"], "mutator": c["mutator"], "model": _label(before, c), "edits": edits})
    return cases


# -- the source and the runner ----------------------------------------------------------------------------------------


def refuse_source(src: Path, root_file: str) -> str | None:
    """Why *src* (with *root_file* inside it) may not be run, or None (the module's docstring)."""
    problem = refuse_local_tier_source(src)
    if problem:
        return problem
    if not src.is_dir():
        return f"--ledger-dir {src} is not a folder"
    if not within(src, CORPORA):
        tier, _ = local_tier_dir()
        if tier is None or within(tier, src):
            return "--ledger-dir must be a folder inside FT_LOCAL_TIER_DIR, not that folder itself"
    rel = Path(root_file)
    if rel.is_absolute() or ".." in rel.parts or not (src / rel).is_file():
        return "--root-file must name a file inside --ledger-dir, by its path relative to it"

    def unreadable(err: OSError) -> None:
        raise err

    try:
        for top, dirs, files in os.walk(src, onerror=unreadable):
            if any(os.path.islink(os.path.join(top, name)) for name in dirs + files):
                return "the ledger folder holds a link; make a plain copy with cp -RL"
    except OSError:
        return "a folder in the ledger folder cannot be listed"
    return None


def digest(src: Path) -> dict[str, str]:
    """Every file below *src*, by relative path: its SHA-256."""
    out = {}
    for top, _, files in os.walk(src):
        for name in files:
            p = Path(top) / name
            h = hashlib.sha256()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            out[p.relative_to(src).as_posix()] = h.hexdigest()
    return out


@dataclass(frozen=True)
class RunnerResult:
    returncode: int
    lines: list[str]
    stderr: str


Runner = Callable[[Path, str, Path, list[dict[str, Any]]], RunnerResult]


def run_runner(src: Path, root_file: str, work: Path, cases: list[dict[str, Any]]) -> RunnerResult:
    done = subprocess.run(  # noqa: S603 (fixed argv: the backend's interpreter and a script of this repository)
        [str(RUNNER_PYTHON), "-I", str(RUNNER), "--source", str(src), "--root-file", root_file, "--work", str(work)],
        cwd=BACKEND, input=json.dumps(cases), capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return RunnerResult(done.returncode, done.stdout.splitlines(), done.stderr)


def decode(lines: list[str], ids: set[str]) -> dict[str, dict[str, Any]]:
    """The runner's answers, field by field: only a known case ID, an outcome of `OUTCOMES`, a code of capitals, a step
    of `STEPS` and a reason of lower-case words pass; any other answer for a known case is the refusal `MALFORMED`, so
    nothing from the runner but those fixed shapes can be printed."""
    out: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            a = json.loads(line)
        except ValueError:
            continue
        if not isinstance(a, dict) or a.get("id") not in ids or a["id"] in out:
            continue
        outcome, code, step, reason = (a.get(k) for k in ("outcome", "code", "step", "reason"))
        ok = (outcome in ("saved", "save_anyway") and code is None and step is None and reason is None) or (
            outcome == "refused" and isinstance(code, str) and CODE.fullmatch(code) is not None
            and (step is None or step in STEPS) and (reason is None or (isinstance(reason, str)
                                                                        and REASON.fullmatch(reason) is not None)))
        out[a["id"]] = ({"outcome": outcome, "code": code, "step": step, "reason": reason} if ok else
                        {"outcome": "refused", "code": "MALFORMED", "step": None, "reason": None})
    return out


# -- the report -------------------------------------------------------------------------------------------------------


def report_of(cases: list[dict[str, Any]], answers: dict[str, dict[str, Any]], facts: Facts) -> tuple[list[str], dict[str, Any]]:
    lines, outcomes, per_mutator, labels, refusals = [], dict.fromkeys(OUTCOMES, 0), {}, {}, {}
    justified = unjustified = not_expressible = 0
    for c in cases:
        labels[c["model"]] = labels.get(c["model"], 0) + 1
        mine = per_mutator.setdefault(c["mutator"], {})
        if c["edits"] is None:
            not_expressible += 1
            mine["not_expressible"] = mine.get("not_expressible", 0) + 1
            lines.append(f"{c['id']} {c['mutator']} not_expressible")
            continue
        a = answers.get(c["id"]) or {"outcome": "refused", "code": "NO_ANSWER", "step": None, "reason": None}
        outcomes[a["outcome"]] += 1
        mine[a["outcome"]] = mine.get(a["outcome"], 0) + 1
        if a["outcome"] != "refused":
            lines.append(f"{c['id']} {c['mutator']} {a['outcome']}")
            continue
        verdict, cite = classify(a["code"], a["step"], a["reason"], case_facts(facts, c["edits"]))
        key = f"{a['code']}/{a['step'] or '-'}/{a['reason'] or '-'}"
        r = refusals.setdefault(key, {"verdict": verdict, "citation": cite, "cases": []})
        r["cases"].append(c["id"])
        justified += verdict == "justified"
        unjustified += verdict == "unjustified"
        lines.append(f"{c['id']} {c['mutator']} refused {key} {verdict}")
    run = sum(outcomes.values())
    rate = round(unjustified / run, 4) if run else 0.0
    summary = {
        "producer": "the engine's test producer (no mutator exists yet): primitive edits through the preview and save",
        "cases_generated": len(cases), "cases_run": run, "not_expressible": not_expressible, "outcomes": outcomes,
        "per_mutator": per_mutator, "model": labels, "refusals": refusals, "refused": outcomes["refused"],
        "justified": justified, "unjustified": unjustified, "unjustified_rate": rate, "goal": f"under {GOAL:.0%}",
    }
    return lines, summary


def main(argv: list[str] | None = None, runner: Runner = run_runner) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--ledger-dir", type=Path, required=True, help="the copy's folder (only read)")
    ap.add_argument("--root-file", required=True, help="the root file, relative to --ledger-dir")
    ap.add_argument("--per-mutator", type=int, default=20)
    ap.add_argument("--seed", type=int, default=9)
    args = ap.parse_args(argv)

    def refuse(why: str) -> int:
        print(f"g1-refusals: refused: {why}", file=sys.stderr)
        return 2

    src: Path = args.ledger_dir
    problem = refuse_source(src, args.root_file)
    if problem:
        return refuse(problem)
    src = src.resolve()
    show = may_show_entries(src)
    try:
        before = harness.check_contained(src, args.root_file)
        facts = facts_of(src, before)
        cases = generate(before, args.per_mutator, args.seed)
    except harness.Unsafe as err:
        return refuse(str(err) if show else "an include of the ledger resolves outside --ledger-dir")
    except Exception as err:  # a ledger that does not parse as UTF-8 Beancount text: never its text on a copy
        return refuse(f"the ledger could not be read ({type(err).__name__})" + (f": {err}" if show else ""))
    runnable = [{"id": c["id"], "edits": c["edits"]} for c in cases if c["edits"] is not None]
    before_bytes = digest(src)
    tier, _ = local_tier_dir()
    work = Path(tempfile.mkdtemp(prefix=".g1-work-", dir=None if show or tier is None else tier))
    try:
        result = runner(src, args.root_file, work, runnable)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if digest(src) != before_bytes:
        print("g1-refusals: STOPPED: the source folder changed during the run", file=sys.stderr)
        return 3
    if result.returncode != 0:
        print(f"g1-refusals: STOPPED: the runner failed (exit {result.returncode})", file=sys.stderr)
        if show:
            print(result.stderr[-4000:], file=sys.stderr)
        return 3
    lines, summary = report_of(cases, decode(result.lines, {c["id"] for c in runnable}), facts)
    for line in lines:
        print(line)
    print(json.dumps(summary, indent=1))
    return 0 if summary["unjustified_rate"] < GOAL else 1


if __name__ == "__main__":
    sys.exit(main())
