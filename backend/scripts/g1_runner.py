#!/usr/bin/env python3
"""The G1 refusal tool's runner (S0163; ADR-0046): each case's edits through the new write core, as the product runs a
write: the engine's preview (the contract's steps 1-3) and the confirm's save, through the sidecar's scheduler and write
use cases (`app.workspaces.writes`), in a fresh test copy of the source folder per case.

Started only by `scripts/oracle/g1_refusals.py`, which generates the cases, checks the source, classifies the answers
and reports (its docstring has the command and the stated limits). usage (cwd = backend/):
    .venv/bin/python -I scripts/g1_runner.py --source DIR --root-file REL --work DIR [--deadline SECONDS] < cases.json

stdin: a JSON list of cases, `{"id", "edits": [...]}`, each edit the engine's test producer's request
(`engine/tests/ledger/producer.py`: `insert {text, default_file}`, `replace {path, line, date, keyword, text}`,
`token {path, line, date, keyword, lineno, old, new}`) with every path relative to the source folder.

The wiring is `measure_write.py`'s (`wire`, `open_copy`): the product's engine command with one change, the engine's
test producer registered as "test.edits" in the engine that runs a preview (the shipped engine has none). Per case: the
source copied into `WORK/c<n>/copy`, opened as its own workspace (its mirror published), the preview of its edits, then
the confirm, with the new errors the preview listed acknowledged as a user's "Save anyway" would; the rebuild the save
queues is awaited, then the copy is removed. Each save is confined by the engine to the case's copy (the test-copy
mark, `writes.confinement`); the source is only read.

stdout: one JSON line per case, exactly `{"id", "outcome", "code", "step", "reason"}`: `saved` or `save_anyway` with
the other three null, or `refused` with the registry code, the contract step (`ContractStepDetails`) and the engine's
reason (the preview's, read from its answer; a save's `ChangeNotSavedDetails.reason`), each null when there is none,
the reason null too when it is not one lower-case word (`REASON`). A failure of the tool inside a case is `refused`
with the code `TOOL_ERROR`. Nothing else is printed on stdout; the engine's events are not logged.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))  # `measure_write`, beside this script (run with -I)

from measure_write import (
    CLOCK,
    JOB_DEADLINE,
    MUTATOR,
    Jobs,
    Measured,
    Wired,
    _Ids,  # pyright: ignore[reportPrivateUsage] (the plan ids of measure_write's runs)
    open_copy,
    waited,
    wire,
)

from app.platform.engine import PreviewAnswer
from app.platform.engine_jobs import preview as preview_job
from app.platform.engine_jobs.preview import NotPrepared
from app.platform.engine_jobs.save import NotDone, Saved
from app.platform.engine_jobs.scheduler import Failed
from app.platform.errors import ChangeNotSavedDetails, ContractStepDetails
from app.platform.plans import PlanStore
from app.workspaces import writes
from app.workspaces.writes import Previewed, WriteServices

REASON = re.compile(r"[a-z][a-z0-9_-]{0,47}")
PATH_FIELDS = ("path", "default_file")


class EngineReason:
    """The engine's reason of the last refused preview: the sidecar folds it into a code and a step
    (`preview.refusal_of`), so this runner reads it on the way (its own wrapper of that function, in this process)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reason: str | None = None
        original = preview_job.refusal_of

        def recorded(a: PreviewAnswer) -> NotPrepared:
            with self._lock:
                self.reason = a.reason
            return original(a)

        preview_job.refusal_of = recorded

    def take(self) -> str | None:
        with self._lock:
            reason, self.reason = self.reason, None
        return reason


def answer(case_id: str, outcome: str, code: str | None = None, step: str | None = None, reason: object = None) -> str:
    fine = isinstance(reason, str) and REASON.fullmatch(reason) is not None
    row = {"id": case_id, "outcome": outcome, "code": code, "step": step, "reason": reason if fine else None}
    return json.dumps(row)


def refused(case_id: str, code: str, details: object, reason: object = None) -> str:
    step = details.step if isinstance(details, ContractStepDetails) else None
    if isinstance(details, ChangeNotSavedDetails):
        reason = details.reason
    return answer(case_id, "refused", code, step, reason)


def absolute(edits: list[Mapping[str, object]], copy: Path) -> dict[str, object]:
    """The producer's request with every path field made absolute inside the case's copy."""
    out: list[dict[str, object]] = []
    for e in edits:
        out.append({k: str(copy / str(v)) if k in PATH_FIELDS else v for k, v in e.items()})
    return {"edits": out}


def one_case(
    wired: Wired, reasons: EngineReason, source: Path, root_file: str, work: Path, n: int, case: Mapping[str, object]
) -> str:
    case_id = str(case["id"])
    folder = work / f"c{n}"
    copy = folder / "copy"
    try:
        shutil.copytree(source, copy, symlinks=True)  # the tool refused a source holding any link
        copy = copy.resolve()
        jobs: Jobs = wired.jobs
        jobs.tickets = []
        target, generation = open_copy(jobs, wired.folder, wired.cache, f"g{n}", copy, root_file, JOB_DEADLINE)[:2]
        services = WriteServices(jobs=jobs, plans=PlanStore(CLOCK), clock=CLOCK, ids=_Ids(), session="g1")
        request = absolute(cast("list[Mapping[str, object]]", case["edits"]), copy)
        reasons.take()
        previewed = waited(writes.submit_preview(services, target, generation, MUTATOR, request), "preview")
        if isinstance(previewed, Failed):
            return refused(case_id, previewed.error.code, previewed.error.details)
        value = previewed.value
        if isinstance(value, NotPrepared):
            return refused(case_id, value.error.code, value.error.details, reasons.take())
        if not isinstance(value, Previewed):
            return answer(case_id, "refused", "TOOL_ERROR")
        acks = frozenset(e.key for e in value.new_errors)
        confirmed = writes.submit_confirm(services, target, value.plan_id, value.preview_hash, acknowledged=acks)
        if isinstance(confirmed, NotDone):
            return refused(case_id, confirmed.error.code, confirmed.error.details)
        saved = waited(confirmed, "save")
        if isinstance(saved, Failed):
            return refused(case_id, saved.error.code, saved.error.details)
        if isinstance(saved.value, NotDone):
            return refused(case_id, saved.value.error.code, saved.value.error.details)
        if not isinstance(saved.value, Saved):
            return answer(case_id, "refused", "TOOL_ERROR")
        for kind, ticket in jobs.tickets:
            if kind == "rebuild":
                ticket.wait(JOB_DEADLINE)  # the background rebuild the save queued, before the copy goes
        return answer(case_id, "save_anyway" if acks else "saved")
    except (Measured, OSError, ValueError, KeyError, TypeError):
        return answer(case_id, "refused", "TOOL_ERROR")
    finally:
        wired.jobs.workspace_closed()
        shutil.rmtree(folder, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--root-file", required=True)
    ap.add_argument("--work", type=Path, required=True)
    args = ap.parse_args(argv)
    cases = cast("list[Mapping[str, object]]", json.loads(sys.stdin.read()))
    source: Path = args.source.resolve()
    work: Path = args.work.resolve()
    reasons = EngineReason()
    stops: list[str] = []
    (work / "app-root").mkdir()
    wired = wire(work / "app-root", stops)  # its app folder and cache, beside the cases' copies
    try:
        for n, case in enumerate(cases):
            line = one_case(wired, reasons, source, args.root_file, work, n, case)
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    finally:
        wired.close()
        shutil.rmtree(work / "app-root", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
