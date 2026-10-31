"""R4, the re-check after a save, the sidecar's side (S0066, S0068; `design/specs/engine-jobs.md` §2's `r4`, §5.1,
§6.1 step 11, §6.7, §11; ADR-0067 decision 3).

R4 compares and shows; it **gates nothing** (owner, 2026-10-31: "Yes, draft that ADR. I'm approving it already, so go
ahead and implement it."). A landed save queues it as background work (`save.submit_save`), naming the save's closed
record (`R4Target`). The sidecar never reads a journal itself (S0062): the work is one `r4_check` in the job's engine
(Beancount's real load, so that engine ran the ledger's plugins when `loaded`), which renames and writes nothing.

`run_r4` returns `Checked` (`pass`; `differs`, which carries §11's notice, `notice_of`; `superseded`, the disk moved or
another build; `gone` or `unreadable`, nothing to compare), `NotChecked` (R4 did not run to an outcome: its engine
crashed, failed to start or broke the protocol, or the engine refused, an encrypted file met included) or `Cancelled`.
Only `differs` is shown; the others are the job's value only. Nothing is counted, nothing is retried, and nothing
pauses saving: a quit, a crash or another job's Cancel before R4 ran loses only its notice. The job's value is the
record of the comparison until the decision log writes it (TODO(BL-0154)); adopting Beancount's result for the
screens is the mirror publish (TODO(BL-0113)).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from app.platform.engine import EngineFailure, EngineRefusal
from app.platform.engine_jobs import save_text
from app.platform.engine_jobs.queue import JobSpec
from app.platform.engine_jobs.scheduler import JobContext, Work, WorkResult
from app.platform.engine_protocol import Violation, decode_r4
from app.platform.tla import tla_action


@dataclass(frozen=True, slots=True)
class R4Target:
    """One closed record: `<app_local_data>/local/<key>/journal/<name>.closed`."""

    key: str
    name: str


type CheckedOutcome = Literal["pass", "differs", "superseded", "gone", "unreadable"]


@dataclass(frozen=True, slots=True)
class Checked:
    """R4 reached an outcome (`differs`: the notice)."""

    outcome: CheckedOutcome


type NotCheckedWhy = Literal["engine_failed", "refused", "violation"]


@dataclass(frozen=True, slots=True)
class NotChecked:
    """R4 did not run to an outcome: logged as the job's value, nothing shown, nothing gated."""

    why: NotCheckedWhy


@dataclass(frozen=True, slots=True)
class Cancelled:
    """The job was cancelled (§4): nothing was compared; nothing is owed."""


type R4Result = Checked | NotChecked | Cancelled

NOTICE: Final = save_text.R4_DIFFERS


def notice_of(result: R4Result) -> str | None:
    """§11: the short notice for a difference; None for every other result (nothing to show)."""
    return NOTICE if isinstance(result, Checked) and result.outcome == "differs" else None


def r4_params(app_local_data: Path | str, target: R4Target) -> dict[str, object]:
    return {"app_local_data": str(app_local_data), "key": target.key, "name": target.name}


@tla_action("R4_Start")
def run_r4(ctx: JobContext, app_local_data: Path | str, target: R4Target) -> tuple[R4Result, bool]:
    """R4 of the record `target` in the job's engine: the result, and whether that engine ran user code (a refusal or
    a failure may come after the plugins ran: the engine is not reused, §5.1)."""
    out = ctx.call("r4_check", r4_params(app_local_data, target))
    if isinstance(out, EngineFailure):
        return (Cancelled() if out.kind == "cancelled" else NotChecked("engine_failed")), True
    if isinstance(out, EngineRefusal):
        return NotChecked("refused"), True
    answer = decode_r4(out)
    if isinstance(answer, Violation):
        return NotChecked("violation"), True
    return Checked(answer.outcome), answer.loaded


def r4_spec(workspace: str) -> JobSpec:
    """The background R4 a landed save queues (§2, §6.1 step 11): nobody waits, unconfirmed; another job's Cancel may
    end it (§4)."""
    return JobSpec(kind="r4", workspace=workspace, waited_for=False, confirmed=False, owner="app")


def r4_work(app_local_data: Path, target: R4Target) -> Work:
    def work(ctx: JobContext) -> WorkResult:
        result, ran_user_code = run_r4(ctx, app_local_data, target)
        return WorkResult(None if isinstance(result, Cancelled) else result, ran_user_code=ran_user_code)

    return work
