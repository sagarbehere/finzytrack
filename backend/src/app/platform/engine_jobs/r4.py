"""R4, the re-check after a save, the sidecar's side (S0066; `design/specs/engine-jobs.md` §2's `r4`, §5.1, §5.3,
§6.1 steps 1 and 11, §6.5, §6.7, §11; ADR-0008's amendments, ADR-0064 decisions 7 and 11).

The sidecar never reads a journal itself (S0062): it runs R4 as engine ops on one record, named by its key folder
and its name (`R4Target`), in the engines of the job it is part of:

1. `r4_check` in the job's engine (steps 1-3: the real load, so that engine ran the ledger's plugins when `loaded`);
2. after `differs`, a fresh engine (§5.1: one that ran user code is ended first), then `r4_confirm` (step 4,
   parse-only: Beancount's parser and the replica's, the fingerprint once more);
3. a **failure to run** (the engine crashed, failed to start or broke the protocol; it refused with an exception; the
   record could not be read) is counted on disk by `r4_attempt` in a fresh engine that runs no user code; the third
   makes the record `.mismatch` ("could not check"). A death of that engine asks for another, until the client's
   bound says no; when no engine can be had, nothing is counted and the record stays `.closed` (the next save
   refuses `check_pending`). **A Cancel counts nothing** (ADR-0008 amendment: "a user's Cancel does not count").

`R4Runner.run` returns `Checked` (an outcome the engine reached and synced: `pass`, `superseded`, `superseded_build`,
`nondeterministic`, `mismatch`, or `gone` when the record is no longer `.closed`), `NotChecked` (a failure to run,
with what was counted) or `Cancelled`. Only `Checked` with an outcome other than `mismatch` lets a save go on.

The jobs: `r4_spec`/`r4_work`, the background job a landed save queues (§6.1 step 11; `save.submit_save` wires it);
`resume_spec`/`resume_work`, **Resume saving** (§6.7: the only way out of `.mismatch`, on the record the user was
shown, `WritesPausedDetails.record`). The save runs `R4Runner.run` itself for a record its step 1 names
(`save._Run`). What the user reads is `save_text` (§11).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from app.platform.engine import EngineFailure, EngineRefusal, R4Answer
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.scheduler import JobContext, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_r4
from app.platform.tla import tla_action

GONE: Final[frozenset[str]] = frozenset({"crashed", "protocol_violation", "start_failed", "closed"})
"""A failed call after which the engine is gone: R4 did not run to an outcome (a failure to run, counted)."""
SUFFIX: Final = (".closed", ".mismatch", ".done")


@dataclass(frozen=True, slots=True)
class R4Target:
    """One record: `<app_local_data>/local/<key>/journal/<name>.<state>`."""

    key: str
    name: str


def target_of(path: str) -> R4Target | None:
    """The record a path names (`…/local/<key>/journal/<name>.<state>`, as the engine reports it), or None."""
    parts = path.split("/")
    if len(parts) < 4 or parts[-2] != "journal" or parts[-4] != "local":
        return None
    file, key = parts[-1], parts[-3]
    for suffix in SUFFIX:
        if file.endswith(suffix):
            name = file.removesuffix(suffix)
            session, dash, job = name.partition("-")
            ok = dash and session and job and all(c.isalnum() or c == "_" for c in session + job)
            return R4Target(key, name) if ok and key not in ("", ".", "..") else None
    return None


type CheckedOutcome = Literal["pass", "superseded", "superseded_build", "nondeterministic", "mismatch", "gone"]


@dataclass(frozen=True, slots=True)
class Checked:
    """R4 reached an outcome (synced on disk before it was answered). `record`: the record's path now; `backup`: the
    save's backup folder."""

    outcome: CheckedOutcome
    record: str | None = None
    backup: str | None = None


@dataclass(frozen=True, slots=True)
class NotChecked:
    """R4 could not run to an outcome. `counted`: the failure was counted (`counted`), it was the third and the record
    is now `.mismatch` (`could_not_check`), or no engine could count it (`not_counted`)."""

    counted: Literal["counted", "could_not_check", "not_counted"]
    record: str | None = None
    backup: str | None = None


@dataclass(frozen=True, slots=True)
class Cancelled:
    """The job was cancelled (§4): nothing was counted; the record stays as it was."""


type R4Result = Checked | NotChecked | Cancelled


def r4_params(app_local_data: Path | str, target: R4Target) -> dict[str, object]:
    return {"app_local_data": str(app_local_data), "key": target.key, "name": target.name}


@dataclass
class R4Runner:
    """R4 inside one job (the module's docstring). `dirty`: the job's current engine ran user code (or died), so the
    job's next part needs a fresh engine (§5.1)."""

    ctx: JobContext
    app_local_data: str
    dirty: bool = False
    _params: dict[str, object] = field(default_factory=dict[str, object])

    def _call(self, op: str) -> R4Answer | EngineFailure | EngineRefusal | Violation:
        out = self.ctx.call(op, self._params)
        if isinstance(out, EngineFailure | EngineRefusal):
            self.dirty = True  # a refusal may come after the plugins ran; a failure leaves no engine
            return out
        return decode_r4(op, out)

    def fresh(self) -> EngineFailure | None:
        """A fresh engine for the job's next part when the current one ran user code or is gone."""
        if not self.dirty:
            return None
        failure = self.ctx.fresh_engine()
        if failure is None:
            self.dirty = False
        return failure

    @tla_action("R4_Pick")
    def run(self, target: R4Target) -> R4Result:
        """R4 of the record `target` (the module's docstring)."""
        self._params = r4_params(self.app_local_data, target)
        out = self._call("r4_check")
        if isinstance(out, EngineFailure) and out.kind == "cancelled":
            return Cancelled()
        if not isinstance(out, R4Answer) or out.outcome == "unreadable":
            return self._count()
        self.dirty = self.dirty or out.loaded
        if out.outcome != "differs":
            return Checked(out.outcome, out.record, out.backup)  # pyright: ignore[reportArgumentType]
        failure = self.fresh()  # §6.7 step 4: the confirm in a fresh engine that runs no user code
        if failure is not None:
            return Cancelled() if failure.kind == "cancelled" else NotChecked("not_counted")
        out = self._call("r4_confirm")
        if isinstance(out, EngineFailure) and out.kind == "cancelled":
            return Cancelled()
        if not isinstance(out, R4Answer) or out.outcome == "unreadable":
            return self._count()
        return Checked(out.outcome, out.record, out.backup)  # pyright: ignore[reportArgumentType]

    def _count(self) -> R4Result:
        """§6.7: one failure to run counted, in an engine that runs no user code (another after a death, until the
        client's bound says no)."""
        while True:
            failure = self.fresh()
            if failure is not None:
                return Cancelled() if failure.kind == "cancelled" else NotChecked("not_counted")
            out = self._call("r4_attempt")
            if isinstance(out, EngineFailure):
                if out.kind == "cancelled":
                    return Cancelled()
                if out.kind in GONE:
                    continue
                return NotChecked("not_counted")
            if isinstance(out, EngineRefusal | Violation):
                return NotChecked("not_counted")
            match out.outcome:
                case "could_not_check":
                    return NotChecked("could_not_check", out.record, out.backup)
                case "counted":
                    return NotChecked("counted", out.record, out.backup)
                case _:  # `gone`: the record is no longer `.closed` (nothing to count; the save asks again)
                    return Checked("gone")


# -- the jobs ---------------------------------------------------------------------------------------------------------


def r4_spec(workspace: str) -> JobSpec:
    """The background R4 a landed save queues (§2, §6.1 step 11): nobody waits, unconfirmed; another job's Cancel may
    end it (§4)."""
    return JobSpec(kind="r4", workspace=workspace, waited_for=False, confirmed=False, owner="app")


def r4_work(app_local_data: Path, target: R4Target) -> Work:
    def work(ctx: JobContext) -> WorkResult:
        runner = R4Runner(ctx, str(app_local_data))
        result = runner.run(target)
        return WorkResult(None if isinstance(result, Cancelled) else result, ran_user_code=runner.dirty)

    return work


type ResumeOutcome = Literal["done", "gone", "failed"]


def resume_spec(workspace: str) -> JobSpec:
    """Resume saving (§6.7), after the user confirmed it: waited for, so another job's Cancel never ends it (§4)."""
    return JobSpec(kind="r4", workspace=workspace, waited_for=True, confirmed=True, owner="user")


def resume_work(app_local_data: Path, record: str) -> Work:
    """Resume saving of the `.mismatch` record `record` (`WritesPausedDetails.record`): `done`; `gone` (it is not
    `.mismatch`: nothing was lifted); `failed` (no answer: nothing is known to be lifted; the next save tells)."""

    def work(ctx: JobContext) -> WorkResult:
        target = target_of(record)
        if target is None or not record.endswith(".mismatch"):
            return WorkResult("gone", ran_user_code=False)
        out: Mapping[str, object] | EngineFailure | EngineRefusal = ctx.call(
            "r4_resume", r4_params(app_local_data, target)
        )
        if isinstance(out, EngineFailure) and out.kind == "cancelled":
            return WorkResult(None, ran_user_code=False)
        if isinstance(out, EngineFailure | EngineRefusal):
            return WorkResult("failed", ran_user_code=False)
        answer = decode_r4("r4_resume", out)
        if isinstance(answer, Violation):
            return WorkResult("failed", ran_user_code=False)
        return WorkResult(answer.outcome, ran_user_code=False)

    return work
