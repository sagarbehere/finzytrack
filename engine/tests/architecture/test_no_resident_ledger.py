"""No resident parsed ledger (INV-10's first sentence; ADR-0011; S0093, BL-0010).

An engine serves job after job until it ran user code (`design/specs/engine-jobs.md` §5.2), so anything a job's load
leaves reachable stays in memory for the engine's life: a resident ledger by another name. The probe runs the jobs
that load a ledger today, the open (the overlay load) and the rebuild after a save (Beancount's own loader), through
one `Engine`, then collects garbage and counts the Beancount directives still alive that those jobs made. The count
must be 0. The planted retentions (a load result kept by the test, one directive kept in an engine module) show the
probe sees one. The `Engine` object itself, the one long-lived object of the engine process besides the server loop's
channel (its two pipes), must hold after the jobs exactly what it held before them: a cache on it, at construction or
added later, changes that.

Today the sidecar ends every engine that ran a load (each loading job reports `ran_user_code`, engine-jobs §5.1: the
open, the rebuild, the save and the create, whose load check is the create's `real_load`), so this is a second line:
it holds if a later change lets an engine serve on after a load (a ledger with no plugin, say).

Stated limits: the probe sees directive objects only (an entry's text kept in a string is not a parsed ledger, and is
the registry's concern); it covers the open, the save and the rebuild (the create's load check is the same
`real_load` as the rebuild's); the `bql` job joins it when it lands (BL-0227).
"""

from __future__ import annotations

import gc
import json
import sys
import unittest
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from beancount.core import data

from ledger_engine.ledger import beancount_port
from ledger_engine.protocol.server import Engine
from tests.test_save_ops import Channel, engine, request, save_setup

pytestmark = pytest.mark.inv("INV-10")
# The jobs need the real file-system port. The tests make their ledger in the body (`save_setup`), not in a fixture:
# the skip below fires only when the test is called, after its fixtures (S0113 round 2: the Linux CI run of
# 2026-11-13 errored in the `setup` fixture).
MAC_ONLY = "the write path's port is macOS-only until BL-0087"

DIRECTIVES = tuple(data.ALL_DIRECTIVES)


def alive() -> set[int]:
    """The ids of every Beancount directive the garbage collector can reach now."""
    gc.collect()
    return {id(o) for o in gc.get_objects() if isinstance(o, DIRECTIVES)}


def jobs(e: Engine, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The open of the setup's ledger, its save, then the rebuild after the save: every job that loads a ledger."""
    target = {
        "root": params["root"],
        "app_local_data": params["app_local_data"],
        "workspace_key": params["workspace_key"],
        "lineage": params["lineage"],
        "floor": 0,
        "today": "2026-11-08",
        "built_at": "2026-11-08T10:00:00+05:30",
    }
    yield json.loads(e.handle(request("open", target, 2), Channel(2, proceed=False)))
    yield json.loads(e.handle(request("save", params, 3), Channel(3, proceed=True)))
    rebuild = {**target, "fingerprint": params["fingerprint"]}
    yield json.loads(e.handle(request("rebuild", rebuild, 4), Channel(4, proceed=False)))


def held(e: Engine) -> dict[str, object]:
    """What the engine object holds: its attributes, with a container's contents copied."""
    return {k: dict(v) if isinstance(v, dict) else v for k, v in vars(e).items()}  # pyright: ignore[reportUnknownArgumentType]


def run_jobs(
    setup: tuple[Path, Path, dict[str, Any]], prepare: Callable[[Engine], None] | None = None
) -> tuple[set[int], list[dict[str, Any]], bool]:
    """The jobs through one engine: the directives they left alive, their replies, and whether the engine object
    holds after them exactly what it held before."""
    _, ald, params = setup
    e = engine(ald)  # one engine for every job, as in the product
    if prepare is not None:
        prepare(e)
    before, kept_before = alive(), held(e)
    replies = list(jobs(e, params))
    left = alive() - before
    assert e.ready  # the engine is still in use: what it holds now is what a resident ledger would be
    return left, replies, held(e) == kept_before


@unittest.skipUnless(sys.platform == "darwin", MAC_ONLY)
def test_no_directive_outlives_its_job(tmp_path: Path) -> None:
    """@regression-guard (it holds on 321be18d: the engine keeps no load result; its must-not-pass twin is below)."""
    setup = save_setup(tmp_path)  # before any plant, as the fixture was
    left, replies, same = run_jobs(setup)
    outcomes = [r["result"]["outcome"] for r in replies]
    assert outcomes == ["loaded", "saved", "loaded"], replies  # each loading job really loaded
    assert all(r["result"].get("entries", 1) > 0 for r in replies)
    assert left == set(), f"{len(left)} directives outlived their job"
    assert same, "the engine object holds something it did not hold before the jobs"


@unittest.skipUnless(sys.platform == "darwin", MAC_ONLY)
def test_a_kept_load_result_is_seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Must not pass: a load result kept after its job (as a memo or a cache would keep it) is a resident ledger."""
    setup = save_setup(tmp_path)  # before any plant, as the fixture was
    kept: list[object] = []
    real: Callable[[str, list[str] | None], beancount_port.ParseResult] = beancount_port.real_load

    def keeping(root: str, parsed: list[str] | None = None) -> beancount_port.ParseResult:
        result = real(root, parsed)
        kept.append(result)
        return result

    monkeypatch.setattr(beancount_port, "real_load", keeping)
    left, replies, _ = run_jobs(setup)
    assert kept, replies  # the rebuild loaded through the planted function
    assert left, "the probe did not see the kept load result"


@unittest.skipUnless(sys.platform == "darwin", MAC_ONLY)
def test_one_directive_kept_in_an_engine_module_is_seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Must not pass (S0093 round 1, the verifier's probe): the engine itself keeps one directive of the open's parse
    in a module attribute of the port; the test holds no reference of its own."""
    setup = save_setup(tmp_path)  # before any plant, as the fixture was
    real = beancount_port.parse_string

    def keeping(data: bytes, report_filename: str | None = None) -> beancount_port.ParseResult:
        result = real(data, report_filename)
        if result[0]:
            monkeypatch.setattr(beancount_port, "_PLANTED_MEMO", result[0][-1], raising=False)
        return result

    monkeypatch.setattr(beancount_port, "parse_string", keeping)
    left, replies, _ = run_jobs(setup)
    assert hasattr(beancount_port, "_PLANTED_MEMO"), replies  # the open parsed through the planted function
    assert len(left) >= 1, "one directive kept in the engine was not seen"


@unittest.skipUnless(sys.platform == "darwin", MAC_ONLY)
@pytest.mark.parametrize("plant", ["filled", "added"])
def test_a_cache_on_the_engine_object_is_seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plant: str) -> None:
    """Must not pass: a store on the engine object, made at construction and filled by the jobs, or added by them."""
    setup = save_setup(tmp_path)  # before any plant, as the fixture was
    real = Engine.handle

    def remembering(self: Engine, line: bytes, channel: Any = None) -> bytes:
        reply = real(self, line, channel)
        vars(self).setdefault("_memo", {})[line] = reply  # added on first use
        return reply

    def with_store(e: Engine) -> None:
        if plant == "filled":
            vars(e)["_memo"] = {}  # made at construction, empty

    monkeypatch.setattr(Engine, "handle", remembering)
    _, _, same = run_jobs(setup, with_store)
    assert not same, "a store on the engine object was not seen"
