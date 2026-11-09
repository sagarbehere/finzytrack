"""No resident parsed ledger (INV-10's first sentence; ADR-0011; S0093, BL-0010).

An engine serves job after job until it ran user code (`design/specs/engine-jobs.md` §5.2), so anything a job's load
leaves reachable stays in memory for the engine's life: a resident ledger by another name. The probe runs the jobs
that load a ledger today, the open (the overlay load) and the rebuild after a save (Beancount's own loader), through
one `Engine`, then collects garbage and counts the Beancount directives still alive that those jobs made. The count
must be 0. The planted retention (a load result kept in a list, as a cache would keep it) shows the probe sees one.

Stated limits: the probe sees directive objects only (an entry's text kept in a string is not a parsed ledger, and is
the registry's concern); it covers the jobs that load today; the `bql` job joins it when it lands (BL-0227).
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
from tests.test_save_ops import Channel, engine, request
from tests.test_save_ops import setup as setup  # the fixture, shared

pytestmark = pytest.mark.inv("INV-10")
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


def run_jobs(setup: tuple[Path, Path, dict[str, Any]]) -> tuple[set[int], list[dict[str, Any]]]:
    _, ald, params = setup
    e = engine(ald)  # one engine for every job, as in the product
    before = alive()
    replies = list(jobs(e, params))
    left = alive() - before
    assert e.ready  # the engine is still in use: what it holds now is what a resident ledger would be
    return left, replies


@unittest.skipUnless(sys.platform == "darwin", MAC_ONLY)
def test_no_directive_outlives_its_job(setup: tuple[Path, Path, dict[str, Any]]) -> None:
    """@regression-guard (it holds on 321be18d: the engine keeps no load result; its must-not-pass twin is below)."""
    left, replies = run_jobs(setup)
    outcomes = [r["result"]["outcome"] for r in replies]
    assert outcomes == ["loaded", "saved", "loaded"], replies  # each loading job really loaded
    assert all(r["result"].get("entries", 1) > 0 for r in replies)
    assert left == set(), f"{len(left)} directives outlived their job"


@unittest.skipUnless(sys.platform == "darwin", MAC_ONLY)
def test_a_kept_load_result_is_seen(setup: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    """Must not pass: a load result kept after its job (as a memo or a cache would keep it) is a resident ledger."""
    kept: list[object] = []
    real: Callable[[str], beancount_port.ParseResult] = beancount_port.real_load

    def keeping(root: str) -> beancount_port.ParseResult:
        result = real(root)
        kept.append(result)
        return result

    monkeypatch.setattr(beancount_port, "real_load", keeping)
    left, replies = run_jobs(setup)
    assert kept, replies  # the rebuild loaded through the planted function
    assert left, "the probe did not see the kept load result"
