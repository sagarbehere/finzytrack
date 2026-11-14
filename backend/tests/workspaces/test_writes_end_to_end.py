"""The write contract end to end on a test copy: the real engine, a real disk, the sidecar's preview and confirm
(`app.workspaces.writes`; S0117; ADR-0008 steps 1-7, ADR-0014, ADR-0067, ADR-0068 D1; `design/specs/engine-jobs.md`
§2, §6.1, §6.7; BL-0106).

**The producer** (the brief's constraint: no test-only producer reachable in the shipped engine). The engine's
`preview.PRODUCERS` stays empty in the product; here the engine is started by a test-only entry, the sidecar's own
bootstrap (`bootstrap_argv`, the venv's site) running a few lines that put `engine/` on the path, register the test
producer of `engine/tests/ledger/producer.py` (S0116) and then run the engine as `ENGINE_RUN` does. `engine/tests` is
not part of the built package (`[tool.uv.build-backend] module-name = "ledger_engine"`) and is not on the engine's
path under the product's bootstrap; `test_the_shipped_engine_has_no_producer` holds both.

Each must-not-pass case compares the ledger folder's bytes before and after (the brief: "each with the disk
byte-identical afterwards").
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.platform.adapters.app_folder import AppFolder, prepare
from app.platform.adapters.engine_client import (
    ENGINE_RUN,
    EngineClient,
    EngineCommand,
    bootstrap_argv,
    engine_env,
    venv_site,
)
from app.platform.adapters.engine_lock import engine_lock_path, wait_engine_lock
from app.platform.clock import FrozenClock
from app.platform.engine_jobs.opening import Opened, submit_open
from app.platform.engine_jobs.opening import OpenTarget as OpenJobTarget
from app.platform.engine_jobs.save import NotDone, Saved
from app.platform.engine_jobs.scheduler import Done, EngineJobs, Failed, JobTicket
from app.platform.engine_supervision import RestartPolicy
from app.platform.errors import ErrorCode
from app.platform.plans import PlannedFile, PlanStore, new_plan
from app.workspaces import writes
from app.workspaces.writes import CopyMark, Previewed, WriteServices, WriteTarget, mirror_generation

REPO = Path(__file__).resolve().parents[3]
ENGINE = REPO / "engine"
ENGINE_VENV = ENGINE / ".venv"
DEADLINE = 120.0
KEY = "k1"
MUTATOR = "test.edits"  # `engine/tests/ledger/producer.TEST_MUTATOR`
CLOCK = FrozenClock(datetime(2026, 11, 14, 10, 0, tzinfo=UTC))
BEFORE = b'option "operating_currency" "USD"\n2026-01-01 open Assets:Cash USD\n'
NOTE = '2026-01-02 note Assets:Cash "s0117"\n'

WITH_TEST_PRODUCER = (
    f"import sys; sys.path.insert(0, {str(ENGINE)!r}); "
    "from tests.ledger import producer; from ledger_engine.ledger import preview; "
    "preview.PRODUCERS[producer.TEST_MUTATOR] = producer.produce_json; " + ENGINE_RUN
)


def python() -> Path:
    found = ENGINE_VENV / "bin" / "python"
    if not found.exists():
        pytest.fail("engine/.venv is missing: run `uv sync --locked` in engine/ (the engine tests need it)")
    return found


def snapshot(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


class Rig:
    """A test copy (its folder holds the ledger), the app's folders, a scheduler over a real engine that has the test
    producer, the open's published mirror, and the write services as the product wires them (the generation read
    from the published mirror: `mirror_generation`)."""

    def __init__(self, tmp: Path) -> None:
        tmp = tmp.resolve()
        self.folder = tmp / "copy"
        self.folder.mkdir()
        self.main = self.folder / "main.bean"
        self.main.write_bytes(BEFORE)
        app = prepare(tmp / "app")
        assert isinstance(app, AppFolder), app
        self.app: AppFolder = app
        self.cache = tmp / "cache"
        self.cache.mkdir()
        command = EngineCommand(
            argv=bootstrap_argv(python(), venv_site(ENGINE_VENV), WITH_TEST_PRODUCER),
            env=engine_env(os.environ),
            engine_lock=engine_lock_path(app.path),
            app_folder=app,
            app_cache=self.cache,
        )
        self.client = EngineClient(
            command, RestartPolicy(), on_engine_spawned=self._spawned, on_engine_ended=self._ended
        )
        self.jobs = EngineJobs(self.client, wait_engine_lock=lambda: wait_engine_lock(app))
        boot = self.jobs.start()
        assert isinstance(boot, JobTicket)
        assert boot.wait(DEADLINE) is not None
        self.jobs.exclusive_began()
        opening = submit_open(self.jobs, KEY, OpenJobTarget(KEY, str(self.main), "lin1", app.path), clock=CLOCK)
        assert isinstance(opening, JobTicket)
        opened = opening.wait(DEADLINE)
        assert isinstance(opened, Done), opened
        assert isinstance(opened.value, Opened), opened.value
        assert opened.value.mirror_ready, opened.value
        self.jobs.exclusive_ended()
        self.jobs.workspace_opened(KEY)
        self.target = WriteTarget(
            workspace=KEY,
            root=str(self.main),
            lineage="lin1",
            app_local_data=app.path,
            app_cache=self.cache,
            copy=CopyMark(self.folder),
        )
        self.plans = PlanStore(CLOCK)
        self.services = WriteServices(jobs=self.jobs, plans=self.plans, clock=CLOCK, ids=_Ids(), session="s1")

    def _spawned(self, serial: int) -> None:
        self.jobs.engine_spawned(serial)

    def _ended(self, end: object) -> None:
        self.jobs.engine_ended(end)  # pyright: ignore[reportArgumentType] (the client's own report)

    def close(self) -> None:
        self.jobs.close()
        self.client.close()

    def generation(self) -> int | None:
        return mirror_generation(self.target)

    def preview(self, target: WriteTarget | None = None) -> Previewed:
        request = {"edits": [{"kind": "insert", "text": NOTE, "default_file": str(self.main)}]}
        generation = self.generation()
        assert generation is not None
        ticket = writes.submit_preview(self.services, target or self.target, generation, MUTATOR, request)
        assert isinstance(ticket, JobTicket), ticket
        out = ticket.wait(DEADLINE)
        assert isinstance(out, Done), out
        assert isinstance(out.value, Previewed), out.value
        return out.value

    def confirm(self, p: Previewed, target: WriteTarget | None = None) -> Saved | NotDone | Failed:
        out = writes.submit_confirm(
            self.services, target or self.target, p.plan_id, p.preview_hash, acknowledged=frozenset()
        )
        if isinstance(out, NotDone | Failed):
            return out
        done = out.wait(DEADLINE)
        assert isinstance(done, Done | Failed), done
        if isinstance(done, Failed):
            return done
        assert isinstance(done.value, Saved | NotDone), done.value
        return done.value


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self) -> str:
        self.n += 1
        return f"plan-{self.n}"


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    yield r
    r.close()


def code_of(value: object) -> str:
    assert isinstance(value, NotDone | Failed), value
    return value.error.code


def wait_for(cond: object) -> bool:
    import threading

    for _ in range(int(DEADLINE / 0.05)):
        if cond():  # pyright: ignore[reportCallIssue]
            return True
        threading.Event().wait(0.05)
    return False


def test_a_preview_confirmed_on_a_test_copy_is_saved_byte_for_byte_and_rebuilt(rig: Rig) -> None:
    p = rig.preview()
    assert len(p.files) == 1
    planned = p.files[0]
    assert planned.path == str(rig.main)
    assert planned.text.startswith(BEFORE.decode())
    assert NOTE in planned.text
    assert rig.main.read_bytes() == BEFORE  # the preview wrote nothing
    assert rig.confirm(p) == Saved(published=False)
    assert rig.main.read_bytes() == planned.text.encode()  # exactly the plan's text
    assert wait_for(lambda: rig.generation() == 2)  # the background rebuild ran and published (§6.7)


def test_a_file_changed_after_the_preview_is_ledger_externally_changed_and_kept(rig: Rig) -> None:
    p = rig.preview()
    rig.main.write_bytes(BEFORE + b"; another program\n")
    before = snapshot(rig.folder)
    assert code_of(rig.confirm(p)) == ErrorCode.LEDGER_EXTERNALLY_CHANGED
    assert snapshot(rig.folder) == before


def test_a_real_workspace_is_refused_by_the_guards_default(rig: Rig) -> None:
    real = dataclasses.replace(rig.target, copy=None)
    p = rig.preview(real)
    before = snapshot(rig.folder)
    assert code_of(rig.confirm(p, real)) == ErrorCode.UNPROMOTED_MUTATOR_ON_REAL_LEDGER
    assert snapshot(rig.folder) == before


def _planted(rig: Rig, p: Previewed, extra: PlannedFile) -> Previewed:
    """The previewed plan with one more file, put back in the store under its own hash (as if a bug had stored it)."""
    stored = rig.plans.take(KEY, p.plan_id)
    assert stored is not None
    f = dataclasses.asdict(stored)
    f.pop("preview_hash")
    f["files"] = (*stored.files, extra)
    bad = new_plan(**f)
    rig.plans.add(bad)
    return dataclasses.replace(p, preview_hash=bad.preview_hash)


def test_a_plan_with_a_file_outside_the_ledger_set_writes_nothing(rig: Rig) -> None:
    """Rule 5: the save refuses a change that is not in the plan's fingerprint at its `pre` bytes before anything is
    written (`ledger_engine.ledger.save`, `pre_not_the_plans`); only our own preview makes plans, so it is a bug:
    `INTERNAL`, never "saved". The file is inside the test copy, so the promotion guard lets it through to the save."""
    other = rig.folder / "other.bean"
    other.write_bytes(b"; not in the ledger\n")
    p = rig.preview()
    old = hashlib.sha256(other.read_bytes()).hexdigest()
    new = "; written\n"
    p = _planted(rig, p, PlannedFile(str(other), old, hashlib.sha256(new.encode()).hexdigest(), new))
    before = snapshot(rig.folder)
    assert code_of(rig.confirm(p)) == ErrorCode.INTERNAL
    assert snapshot(rig.folder) == before


def test_a_plan_with_an_encrypted_file_writes_nothing(rig: Rig) -> None:
    secret = rig.folder / "secret.bean.gpg"
    secret.write_bytes(b"\x85\x01not really encrypted\n")
    p = rig.preview()
    old = hashlib.sha256(secret.read_bytes()).hexdigest()
    p = _planted(rig, p, PlannedFile(str(secret), old, hashlib.sha256(b"x\n").hexdigest(), "x\n"))
    before = snapshot(rig.folder)
    assert code_of(rig.confirm(p)) == ErrorCode.ENCRYPTED_FILE_NOT_EDITABLE
    assert snapshot(rig.folder) == before


def test_the_shipped_engine_has_no_producer(tmp_path: Path) -> None:
    """Under the product's bootstrap (`EngineCommand.for_venv` / `for_packaged`: `ENGINE_RUN` after the path setup
    with the engine's site only), `preview.PRODUCERS` is empty and the engine's tests cannot be imported; no module
    of the engine's source registers a producer."""
    code = (
        "import importlib.util as u; from ledger_engine.ledger import preview; "
        "print(len(preview.PRODUCERS), u.find_spec('tests') is None)"
    )
    out = subprocess.run(  # noqa: S603 (fixed argv: the engine's interpreter under the sidecar's bootstrap)
        bootstrap_argv(python(), venv_site(ENGINE_VENV), code),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        cwd=tmp_path,
        env=engine_env(os.environ),
    )
    assert out.stdout.split() == ["0", "True"], out
    product = EngineCommand.for_venv(ENGINE_VENV).argv
    assert "producer" not in " ".join(product)
    writers = [
        str(p.relative_to(ENGINE))
        for p in sorted((ENGINE / "src").rglob("*.py"))
        if "PRODUCERS[" in p.read_text(encoding="utf-8") or "PRODUCERS.update" in p.read_text(encoding="utf-8")
    ]
    assert writers == []
