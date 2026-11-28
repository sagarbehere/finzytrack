"""Write plans and the `PlanStore` (S0117; ARCHITECTURE.md §2.5's state table, §4.2, §9.2 steps 3-4; ADR-0008,
ADR-0033, ADR-0063; INV-10, INV-11; BL-0106).

A **write plan** is what the engine's `preview` prepared and the user may confirm: the files it writes (each path, the
SHA-256 of its bytes when the preview read them, the SHA-256 of its new text, and that text), the plan's fingerprint
(the ledger set the preview's load read, with each written file at its pre bytes: the save's gate checks the disk
still holds it, engine-jobs §6.1 step 4), the mirror generation it was made on, the mutator that made it, and the keys
of the new errors the preview listed (ADR-0067). It holds texts and hashes, never entries (INV-10).

The **preview hash** is the SHA-256 of everything the user was shown about the plan, in one canonical encoding: the
workspace, the root, the mutator, the generation, the fingerprint, each file's path and both hashes in order, and the
new error keys in order. The text is covered by its `post` hash (the confirm checks the text against it). The plan id
is not in it. The client sends it back with the confirm, which must name this hash (owner's rule 3, S0117).

The **store** holds the plans of this user container (the owner's rule 1: a confirm writes only a plan this app's
preview prepared, held here, never sent back by the client). Bounded (INV-10, `PlanPolicy`): a plan lives at most
`ttl_seconds` after it was made (by the injected `Clock`), at most `max_plans` are kept, and their texts hold at most
`max_text_bytes` of UTF-8 together, the oldest let go first; **the newest plan is always kept**, even alone over the
byte budget, so a file of any size can be previewed and confirmed (S0120, ADR-0008 amendment of 2026-11-15). So the
store holds at most `max_text_bytes` plus one plan's texts, and one plan's texts fit one engine answer (the protocol's
256 MiB guard, `engine_protocol.MAX_LINE_BYTES`).
A plan is found only in its own workspace; `take` removes it, so it is used once; a switch drops plans (ADR-0063
decision 1 step 3): `workspace_opened(ws)` drops every other workspace's, `workspace_closed()` drops them all (the
switch calls them, BL-0154). Every method takes one lock, so two confirms of one plan take it once. The ids of taken
plans are remembered (`used`, at most `max_used`, kept across a switch), so a second confirm of a confirmed plan is
told so (S0117 round 1, red team RT-F2).

Limits (S0117 round 1): a plan whose confirm waits in the queue can be let go by newer previews (`max_plans`, or since
S0120 the byte budget) or by its TTL before its job starts; that confirm then writes nothing and answers `PLAN_EXPIRED`
(red team RT-F3). The TTL follows the injected wall clock: a clock set back before a plan was made expires it; under the
test-only `--frozen-clock` no plan expires by age, only by count (red team RT-F5, verifier V1). A used id let go after
`max_used` later confirms, or from an earlier run of the app, is `unknown` to `fate` and answered `PLAN_UNKNOWN`, whose
words ask the user to check the ledger first (S0117 round 3; BL-0268).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal, NewType

from app.platform.clock import Clock

LARGE_FILE_BYTES: Final = 15_000_000
"""A file larger than this (its bytes now, or its new text in UTF-8) is written more slowly than ADR-0008's 5 s at 54k
entries, and its preview carries the tip to split it with `include` (S0120; owner, 2026-11-15; ADR-0008 amendment of
that date; INV-20)."""

PlanId = NewType("PlanId", str)
"""A plan's id in the store (ARCHITECTURE §2.6)."""

PreviewHash = NewType("PreviewHash", str)
"""The SHA-256 (hex) of what the preview showed (the module's docstring; ARCHITECTURE §2.6)."""

type Fate = Literal["used", "gone", "unknown"]
"""What became of a plan the store no longer gives (`PlanStore.fate`; S0117 round 3)."""


@dataclass(frozen=True, slots=True)
class PlannedFile:
    """A file the plan writes: its absolute path as the ledger names it, the SHA-256 of its bytes when the preview
    read them (`pre`), the SHA-256 of its new text (`post`), and the text."""

    path: str
    pre: str
    post: str
    text: str


@dataclass(frozen=True, slots=True)
class WritePlan:
    plan_id: PlanId
    workspace: str
    root: str
    mutator: str
    generation: int
    fingerprint: str
    files: tuple[PlannedFile, ...]
    new_error_keys: tuple[str, ...]
    preview_hash: PreviewHash
    made_at: datetime


def preview_hash(
    workspace: str,
    root: str,
    mutator: str,
    generation: int,
    fingerprint: str,
    files: Iterable[PlannedFile],
    new_error_keys: Iterable[str],
) -> PreviewHash:
    value = {
        "workspace": workspace,
        "root": root,
        "mutator": mutator,
        "generation": generation,
        "fingerprint": fingerprint,
        "files": [[f.path, f.pre, f.post] for f in files],
        "new_error_keys": list(new_error_keys),
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return PreviewHash(hashlib.sha256(encoded).hexdigest())


def new_plan(
    plan_id: PlanId,
    workspace: str,
    root: str,
    mutator: str,
    generation: int,
    fingerprint: str,
    files: tuple[PlannedFile, ...],
    new_error_keys: tuple[str, ...],
    made_at: datetime,
) -> WritePlan:
    """A plan with its own preview hash."""
    digest = preview_hash(workspace, root, mutator, generation, fingerprint, files, new_error_keys)
    return WritePlan(plan_id, workspace, root, mutator, generation, fingerprint, files, new_error_keys, digest, made_at)


def utf8_bytes(text: str) -> int:
    """The length of `text` in UTF-8 (a lone surrogate, which a JSON escape can carry, counts as its 3 bytes)."""
    return len(text) if text.isascii() else len(text.encode("utf-8", "surrogatepass"))


def is_large(pre_size: int, text: str) -> bool:
    """A file is large when its bytes now (`pre_size`) or its new text pass `LARGE_FILE_BYTES` (S0120)."""
    return pre_size > LARGE_FILE_BYTES or utf8_bytes(text) > LARGE_FILE_BYTES


def text_bytes(plan: WritePlan) -> int:
    """What a plan's texts weigh against the store's byte budget: their UTF-8 bytes, over all its files."""
    return sum(utf8_bytes(f.text) for f in plan.files)


@dataclass(frozen=True, slots=True)
class PlanPolicy:
    """The store's bounds (INV-10: a setting, with desktop defaults). `max_text_bytes` bounds the plans' texts
    together, the newest plan excepted (the module's docstring; S0120: the bound was `max_plans` x the line limit,
    which a 256 MiB guard would make about 1 GB)."""

    ttl_seconds: int = 15 * 60
    max_plans: int = 4
    max_text_bytes: int = 128_000_000
    max_used: int = 256
    """How many used plan ids, and how many plan ids let go unconfirmed, the store remembers (ids only, about 100 bytes
    each), so a confirm of either is told what became of it (S0117 rounds 1 and 3, red team RT-F2, verifier V-R3-5)."""

    @property
    def ttl(self) -> timedelta:
        return timedelta(seconds=self.ttl_seconds)

    def __post_init__(self) -> None:
        if self.ttl_seconds < 1 or self.max_plans < 1 or self.max_used < 1 or self.max_text_bytes < 1:
            msg = "a plan store needs a positive ttl and room for one plan"
            raise ValueError(msg)


class PlanStore:
    """The module's docstring."""

    def __init__(self, clock: Clock, policy: PlanPolicy | None = None) -> None:
        self._clock = clock
        self._policy = policy or PlanPolicy()
        self._lock = threading.Lock()
        self._plans: dict[PlanId, WritePlan] = {}  # in the order they were added: the oldest first
        self._bytes: dict[PlanId, int] = {}  # each held plan's `text_bytes`
        self._used: dict[PlanId, str] = {}  # taken plan ids and their workspaces, the oldest first
        self._gone: dict[
            PlanId, str
        ] = {}  # plan ids let go unconfirmed (expired, pushed out, dropped), the oldest first

    def __len__(self) -> int:
        with self._lock:
            return len(self._plans)

    def add(self, plan: WritePlan) -> None:
        """Keep `plan`; the expired ones are let go, then the oldest until there is room by count and by bytes; `plan`
        itself is always kept (the newest). An id is never used twice."""
        size = text_bytes(plan)
        with self._lock:
            if plan.plan_id in self._plans:
                msg = f"a plan id is used once: {plan.plan_id}"
                raise ValueError(msg)
            self._drop_expired()
            while len(self._plans) >= self._policy.max_plans:
                self._let_go(next(iter(self._plans)))
            while self._plans and sum(self._bytes.values()) + size > self._policy.max_text_bytes:
                self._let_go(next(iter(self._plans)))
            self._plans[plan.plan_id] = plan
            self._bytes[plan.plan_id] = size

    def find(self, workspace: str, plan_id: str) -> WritePlan | None:
        """The plan, if it is held, has not expired and is this workspace's; it stays held."""
        with self._lock:
            self._drop_expired()
            plan = self._plans.get(PlanId(plan_id))
            return plan if plan is not None and plan.workspace == workspace else None

    def take(self, workspace: str, plan_id: str) -> WritePlan | None:
        """As `find`, and the plan is no longer held: it is used once."""
        with self._lock:
            self._drop_expired()
            plan = self._plans.get(PlanId(plan_id))
            if plan is None or plan.workspace != workspace:
                return None
            del self._plans[plan.plan_id]
            del self._bytes[plan.plan_id]
            self._remember(self._used, plan.plan_id, workspace)
            return plan

    def used(self, workspace: str, plan_id: str) -> bool:
        """This workspace's plan of that id was taken (confirmed) in this session, as far as the store remembers
        (`PlanPolicy.max_used` ids; kept across a switch, since a retry may come after one)."""
        with self._lock:
            return self._used.get(PlanId(plan_id)) == workspace

    def fate(self, workspace: str, plan_id: str) -> Fate:
        """What became of a plan `find` did not give (S0117 rounds 1 and 3, red team RT-F2, verifier V-R3-5): `used`,
        taken by a confirm of this workspace in this session; `gone`, held for another workspace or let go unconfirmed
        (expired, pushed out by newer previews, dropped by a switch); `unknown`, an id the store does not remember (an
        earlier run of the app, or one let go after `max_used` later ones): only then may an earlier confirm have saved
        it without this store knowing."""
        with self._lock:
            pid = PlanId(plan_id)
            if self._used.get(pid) == workspace:
                return "used"
            if pid in self._plans or pid in self._gone or pid in self._used:
                return "gone"
            return "unknown"

    def workspace_opened(self, workspace: str) -> None:
        """A switch opened `workspace`: every other workspace's plans are dropped (ADR-0063)."""
        with self._lock:
            for k in [k for k, p in self._plans.items() if p.workspace != workspace]:
                self._let_go(k)

    def workspace_closed(self) -> None:
        """The open workspace closed: every plan is dropped (ADR-0063)."""
        with self._lock:
            for k in list(self._plans):
                self._let_go(k)

    def _let_go(self, plan_id: PlanId) -> None:
        plan = self._plans.pop(plan_id)
        del self._bytes[plan_id]
        self._remember(self._gone, plan_id, plan.workspace)

    def _remember(self, memory: dict[PlanId, str], plan_id: PlanId, workspace: str) -> None:
        while len(memory) >= self._policy.max_used:
            del memory[next(iter(memory))]
        memory[plan_id] = workspace

    def _drop_expired(self) -> None:
        """A plan is held while its age is at least zero and under the TTL: a clock set back before the plan was made
        expires it, rather than lengthening its life (S0117 round 1, red team RT-F5, verifier V1)."""
        now = self._clock.now()
        ttl = self._policy.ttl
        for k in [k for k, p in self._plans.items() if not timedelta(0) <= now - p.made_at < ttl]:
            self._let_go(k)
