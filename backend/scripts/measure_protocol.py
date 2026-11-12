#!/usr/bin/env python3
"""Measure the engine protocol's cost per write (S0106; BL-0082; ADR-0054 Consequences; roadmap M1a exit).

usage (cwd = backend/):
    .venv/bin/python scripts/measure_protocol.py ROOT [--runs 5] [--pings 50]

ROOT is a ledger's root file (the demo generator's ledger: `scripts/gen_demo.py --size 54k`); it is only read, and
every run works on a fresh copy of its folder. Everything happens in a fresh temp folder (honours TMPDIR): the app's
folder and cache are made there, and the engine is started with the sidecar's own command (`app.main.engine_command`,
the engine from `../engine/.venv`) through the sidecar's client (`EngineClient`), after the sidecar's lock wait.

Each run: the `open` op loads the copy and publishes its mirror (the save's gate needs it); the engine is retired, as
after any job that ran the ledger's plugins, and a fresh one started by a `ping`; the plan's fingerprint is read from
the published mirror's `meta` (`app.mirror`; for a change to one file whose includes stay the same, the plan's
fingerprint is the disk's); then one real `save` of the root file with a transaction appended, sent as the sidecar
sends it, its gate answered with `proceed`, then the `rebuild` the landed save queues (Beancount's own load, the
build, the publish). The report gives, from the client's side, the time to the gate, from the gate to the committed
event, from that event to the reply, the rebuild, and the whole from sending the save to the new generation's
publish: ADR-0008's 5 s at 54k entries counts "until everything on screen reflects the change" (the sidecar's read
of the new generation, a few milliseconds, is not in it). Then the round trip of a `save` the engine refuses
right after reading it (an unreadable fingerprint: `protocol.invalid_params`), once with the whole file in it and once
with a one-line file: the protocol's own cost of a request that size, with no ledger work in it. Also: `ping` round
trips (the smallest request), and the client's encode of the full request (`engine_protocol.encode_request`).

It prints a JSON report in milliseconds and removes the folder. It reports and never fails, except when a save does
not land (nothing would have been measured). The engine's own time for the same save is
`engine/scripts/measure_save.py`'s "save" line; the protocol's cost per write is this script's save total less that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn

from app.main import engine_command, parse_args
from app.mirror.files import MirrorInfo, pin
from app.mirror.reader import Reader
from app.platform.adapters.app_folder import AppFolder, prepare
from app.platform.adapters.engine_client import EngineClient
from app.platform.adapters.engine_lock import wait_engine_lock
from app.platform.engine import Committed, EngineEvent, EngineFailure, EngineRefusal, GateReached
from app.platform.engine_protocol import encode_request

BACKEND = Path(__file__).resolve().parents[1]
ENGINE_VENV = BACKEND.parent / "engine" / ".venv"
TXN = '\n2026-10-30 * "Measured save"\n  Expenses:Food:Groceries  12.34 USD\n  Assets:Bank:Checking\n'
TODAY = "2026-10-30"
BUILT_AT = "2026-10-30T10:00:00+00:00"
LINEAGE = "lin1"
SESSION = "m1"


def ms(seconds: float) -> float:
    return round(seconds * 1000, 2)


def summary(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "median": round(statistics.median(ordered), 2),
        "min": round(ordered[0], 2),
        "max": round(ordered[-1], 2),
    }


class Measured(Exception):
    """A run that did not end as the measurement expects."""


def fail(what: str) -> NoReturn:
    raise Measured(what)


def call(client: EngineClient, op: str, params: Mapping[str, object]) -> Mapping[str, object]:
    out = client.call(op, params)
    if isinstance(out, EngineRefusal | EngineFailure):
        fail(f"{op}: {out}")
    return out


def pings(client: EngineClient, count: int) -> list[float]:
    samples: list[float] = []
    for _ in range(count):
        t0 = time.perf_counter()
        client.ping()
        samples.append(ms(time.perf_counter() - t0))
    return samples


def one_run(
    client: EngineClient, folder: AppFolder, cache: Path, src: Path, work: Path, n: int
) -> dict[str, float | str]:
    ledger = work / f"run{n}" / "L"
    shutil.copytree(src.parent, ledger)
    root = ledger / src.name
    key = f"k{n}"
    opened = call(
        client,
        "open",
        {
            "root": str(root),
            "app_local_data": str(folder.path),
            "workspace_key": key,
            "lineage": LINEAGE,
            "floor": 0,
            "today": TODAY,
            "built_at": BUILT_AT,
        },
    )
    if opened.get("publish") != "published":
        fail(f"open: {opened}")
    client.end("retire")  # the open ran the ledger's plugins (OpenResult): the scheduler retires its engine
    pings(client, 1)  # a fresh engine, so its start is not in the save's time
    path = pin(str(cache / "workspaces" / key / "mirror"))
    if not isinstance(path, str):
        fail(f"pin: {path}")
    info = Reader(path).info()
    if not isinstance(info, MirrorInfo):
        fail(f"meta: {info}")
    pre = root.read_bytes()
    params: dict[str, object] = {
        "session": SESSION,
        "job": f"j{n}",
        "workspace_key": key,
        "lineage": LINEAGE,
        "root": str(root),
        "changes": [{"path": str(root), "pre": hashlib.sha256(pre).hexdigest(), "post": pre.decode() + TXN}],
        "fingerprint": info.fingerprint,
        "app_local_data": str(folder.path),
    }
    t0 = time.perf_counter()
    line = encode_request(1, "save", params)
    encode_s = time.perf_counter() - t0
    seen: dict[str, float] = {}

    def on_event(event: EngineEvent) -> None:
        if isinstance(event, GateReached):
            seen["gate"] = time.perf_counter()
            client.proceed()
        elif isinstance(event, Committed):
            seen["committed"] = time.perf_counter()

    t0 = time.perf_counter()
    out = client.call("save", params, on_event=on_event)
    end = time.perf_counter()
    if isinstance(out, EngineRefusal | EngineFailure) or out.get("outcome") != "saved":
        fail(f"save: {out}")
    gate, committed = seen["gate"], seen["committed"]
    rebuild = {  # the background rebuild the landed save queues, as `engine_jobs.rebuild.params_of` sends it
        "root": str(root),
        "fingerprint": info.fingerprint,
        "app_local_data": str(folder.path),
        "workspace_key": key,
        "lineage": LINEAGE,
        "floor": opened["number"],
        "today": TODAY,
        "built_at": BUILT_AT,
    }
    rebuilt = call(client, "rebuild", rebuild)
    published = time.perf_counter()
    if rebuilt.get("outcome") != "loaded" or rebuilt.get("publish") != "published":
        fail(f"rebuild: {rebuilt}")
    client.end("retire")  # the rebuild ran the ledger's plugins
    pings(client, 1)
    refused: dict[str, float] = {}
    for size, post in (("full", pre.decode() + TXN), ("tiny", TXN)):
        bad = {
            **params,
            "job": f"r{n}{size}",
            "fingerprint": "x",
            "changes": [{"path": str(root), "pre": None, "post": post}],
        }
        t1 = time.perf_counter()
        answer = client.call("save", bad)
        refused[size] = ms(time.perf_counter() - t1)
        if not (isinstance(answer, EngineRefusal) and answer.key == "protocol.invalid_params"):
            fail(f"the refused save: {answer}")
    shutil.rmtree(ledger.parent)
    return {
        "outcome": str(out["outcome"]),
        "request_mb": round(len(line) / 1e6, 2),
        "client_encode_ms": ms(encode_s),
        "total_ms": ms(end - t0),
        "to_gate_ms": ms(gate - t0),
        "gate_to_committed_ms": ms(committed - gate),
        "committed_to_reply_ms": ms(end - committed),
        "rebuild_ms": ms(published - end),
        "save_until_published_ms": ms(published - t0),
        "refused_full_ms": refused["full"],
        "refused_tiny_ms": refused["tiny"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("root", type=Path)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--pings", type=int, default=50)
    args = ap.parse_args(argv)
    src = args.root.resolve()
    top = Path(tempfile.mkdtemp(prefix="ft-measure-protocol-"))
    client: EngineClient | None = None
    try:
        cache, work = top / "cache", top / "work"
        cache.mkdir()
        work.mkdir()
        folder = prepare(top / "app")
        if not isinstance(folder, AppFolder):
            fail(f"the app folder: {folder}")
        sidecar = parse_args(
            ["--app-local-data", str(folder.path), "--app-cache", str(cache), "--engine-venv", str(ENGINE_VENV)]
        )
        wait_engine_lock(folder)  # the sidecar's wait before its first engine (engine-jobs §5.1)
        client = EngineClient(engine_command(sidecar, folder, "0.0.0-measure"))
        started = client.start()
        if isinstance(started, EngineFailure):
            fail(f"the engine did not start: {started}")
        ping_ms = pings(client, args.pings)
        saves = [one_run(client, folder, cache, src, work, n) for n in range(args.runs)]
        report = {
            "ledger_mb": round(src.stat().st_size / 1e6, 2),
            "ping_ms": summary(ping_ms),
            "save": saves,
            "save_total_ms": summary([float(s["total_ms"]) for s in saves]),
            "save_until_published_ms": summary([float(s["save_until_published_ms"]) for s in saves]),
            "refused_after_decode": {
                "full_ms": summary([float(s["refused_full_ms"]) for s in saves]),
                "tiny_ms": summary([float(s["refused_tiny_ms"]) for s in saves]),
            },
            "python": sys.version.split()[0],
            "platform": sys.platform,
        }
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        return 0
    finally:
        if client is not None:
            client.close()
        shutil.rmtree(top, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
