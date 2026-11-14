"""The write-safety contract's prepare: an edit plan checked into a write plan, writing nothing (ADR-0008 steps 1, 2,
2b and 3, as amended by ADR-0067 and ADR-0068; ADR-0010; ARCHITECTURE.md §9.2 step 3; S0116).

`prepare(current, produced, root, baseline, policy)` takes the bytes of the ledger's files now (`current`, by
absolute, normalized path; or the `source_edit.Snapshot` of them the producer planned on, whose parses of those bytes
are then reused: one parse of the edited file for the producer, the splice and step 1), an edit producer's plan
and intended change (`Produced`), the ledger's root, the baseline's error keys (`entry_keys.error_keys` of the
`ledger_errors` of the published mirror generation the plan was made on; the `preview` job reads it,
`ledger.preview`) and the workspace's `LoadPolicy`. In order:
- `apply`: the paths absolute and normalized (the load names files so; else `path_not_normal`), then
  `source_edit.apply_edits` (its refusals by their own names: a stale plan, a file outside the plan, overlaps, ...);
- `parse` (step 1), `diff` (step 2), `spans` (step 2b): `semantic_diff`. Each of these refuses: they are integrity
  checks, which always block (ADR-0067 decision 1);
- `load` (step 3): the overlay load of the ledger with the candidate texts in place of their files (`overlay.load`,
  ADR-0010). A load that cannot run (`LoadRefused`: an encrypted file, the policy, Beancount's own load raising)
  refuses with the load's reason;
- `errors` (step 3): the candidate's errors keyed as the baseline is (`rows.error_rows` of `Loaded.reported`), and the
  ones the baseline lacks (as a multiset: a second copy of an old error is new) are the preview's list. **They never
  refuse** (ADR-0067 decision 2: integrity blocks, accounting advises): a failing balance assertion, a booking error,
  a plugin's error are listed, and the user may save anyway.

**Every step fails closed** (INV-22): an exception in a step, or a load result that is neither `Loaded` nor
`LoadRefused`, is `Refused` naming the step (`fault`, `undecidable`); nothing here writes, so a refusal leaves the disk
as it was. The result is `Prepared`: per file its path, the SHA-256 of its bytes now and of the candidate's, and the
candidate text; the plan's fingerprint, `ledger_set.as_before` of the load's (`Loaded.fingerprint`: the bytes the
load parsed, each candidate at its bytes now, which the save's gate compares with the disk, engine-jobs §6.1 step 4);
the new errors; and the count of Beancount's errors in the candidate. No entry outlives the call (INV-10).
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, get_args

from ledger_engine.ledger import overlay
from ledger_engine.ledger.core import entry_keys, ledger_set
from ledger_engine.ledger.core import semantic_diff as sd
from ledger_engine.ledger.core import source_edit as se
from ledger_engine.ledger.core.ledger_set import Fingerprint
from ledger_engine.ledger.policy import LoadPolicy
from ledger_engine.mirror.core import rows

type Step = Literal["plan", "apply", "parse", "diff", "spans", "load", "errors"]
"""Where a preview stopped: `plan` is the edit producer's refusal (the `preview` job's, before prepare); the rest are
prepare's steps, in order."""
STEPS: Final = frozenset(get_args(Step.__value__))

type Load = Callable[[str, Mapping[str, bytes], LoadPolicy], overlay.LoadResult]


@dataclass(frozen=True, slots=True)
class Refused:
    """Not prepared: `step` and its `reason` (`fault`: the step raised, `detail` the exception's class; `undecidable`:
    no result it could read), the file it is about when there is one, and a detail without ledger content."""

    step: Step
    reason: str
    path: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class PlannedFile:
    path: str
    pre: str
    post: str
    text: str


@dataclass(frozen=True, slots=True)
class NewError:
    """An error the candidate has and the baseline lacks: its key's digest (`entry_keys.digest`), Beancount's type
    name and message, and where Beancount puts it."""

    key: str
    type: str
    message: str
    path: str | None
    line: int | None


@dataclass(frozen=True, slots=True)
class Prepared:
    files: tuple[PlannedFile, ...]
    fingerprint: Fingerprint
    new_errors: tuple[NewError, ...]
    errors: int


class _Stop(Exception):
    def __init__(self, refused: Refused) -> None:
        super().__init__(refused.reason)
        self.refused = refused


def _step[T](step: Step, run: Callable[[], T]) -> T:
    """Run one step; any exception in it is a refusal naming the step (INV-22)."""
    try:
        return run()
    except _Stop:
        raise
    except Exception as e:
        raise _Stop(Refused(step, "fault", None, type(e).__name__)) from e


def _refuse(step: Step, refusal: se.Refusal | sd.Refusal) -> _Stop:
    return _Stop(Refused(step, str(refusal.reason), refusal.path, refusal.detail))


def prepare(
    current: Mapping[str, bytes] | se.Snapshot,
    produced: sd.Produced,
    root: str,
    baseline: Counter[str],
    policy: LoadPolicy,
    *,
    load: Load = overlay.load,
) -> Prepared | Refused:
    """The module's docstring: steps `apply` to `errors`, each failing closed."""
    try:
        snapshot = current if isinstance(current, se.Snapshot) else se.Snapshot(current)
        return _prepare(snapshot, produced, root, baseline, policy, load)
    except _Stop as stop:
        return stop.refused


def _normal(path: str) -> bool:
    return os.path.isabs(path) and os.path.normpath(path) == path


def _apply(current: se.Snapshot, plan: se.EditPlan) -> se.Applied:
    odd = sorted(p for p in [*plan.snapshot, *plan.files] if not _normal(p))
    if odd:
        raise _Stop(Refused("apply", "path_not_normal", odd[0], "a ledger path must be absolute and normalized"))
    applied = se.apply_edits(current, plan)
    if isinstance(applied, se.Refusal):
        raise _refuse("apply", applied)
    return applied


def _prepare(
    current: se.Snapshot,
    produced: sd.Produced,
    root: str,
    baseline: Counter[str],
    policy: LoadPolicy,
    load: Load,
) -> Prepared:
    plan, intent = produced.plan, produced.intent
    applied = _step("apply", lambda: _apply(current, plan))
    old = current

    parses = _step("parse", lambda: sd.parse(old, applied.files))
    if isinstance(parses, sd.Refusal):
        raise _refuse("parse", parses)
    refusal = _step("diff", lambda: sd.diff(parses, plan, intent))
    if refusal is not None:
        raise _refuse("diff", refusal)
    refusal = _step("spans", lambda: sd.outside_spans(old, applied, plan))
    if refusal is not None:
        raise _refuse("spans", refusal)

    loaded = _step("load", lambda: load(root, dict(applied.files), policy))
    if isinstance(loaded, overlay.LoadRefused):
        raise _Stop(Refused("load", loaded.reason, loaded.subject, "the load of the candidate cannot run"))
    if not isinstance(loaded, overlay.Loaded):  # pyright: ignore[reportUnnecessaryIsInstance] (INV-22: a load
        # that answers anything else is undecidable, never taken for a pass)
        raise _Stop(Refused("load", "undecidable", None, type(loaded).__name__))
    found = loaded

    def errors() -> tuple[tuple[NewError, ...], Fingerprint, int]:
        new = tuple(
            NewError(
                entry_keys.digest(key),
                str(kind),
                str(message),
                None if path is None else str(path),
                line if isinstance(line, int) else None,
            )
            for key, (_, path, line, message, _, kind) in entry_keys.new_errors(
                rows.error_rows(found.reported), baseline
            )
        )
        pre = {p: current.hashes[p] for p in applied.files}
        return new, ledger_set.as_before(found.fingerprint, pre), len(found.errors)

    new_errors, fingerprint, count = _step("errors", errors)
    files = tuple(
        PlannedFile(p, current.hashes[p], applied.after[p], applied.files[p].decode("utf-8"))
        for p in sorted(applied.files)
    )
    return Prepared(files, fingerprint, new_errors, count)
