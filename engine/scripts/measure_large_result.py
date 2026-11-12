"""Measure what a large query result costs on the engine protocol (S0106; BL-0082; ADR-0054 Consequences).

usage (cwd = engine/): .venv/bin/python scripts/measure_large_result.py ROOT [--runs N]
ROOT is the ledger's root file (the demo generator's 54k ledger: backend/scripts/gen_demo.py --size 54k). It is only
read. No BQL op exists yet (CAP-ENGINE-BQL, M7), so this measures the reply such an op would send for a result with
one row per posting, the largest a query over the ledger's postings returns, in two shapes:

- `wide`: every column of the mirror's `postings` table (`mirror.core.rows.posting_rows`), the upper bound;
- `narrow`: date, flag, payee, narration, account, number and currency, what `SELECT date, account, position ...`
  returns in BQL.

Each is encoded as a reply line the way the engine encodes one (`json.dumps` with the protocol's separators, rows as
arrays), and decoded once plainly and once as the sidecar's client decodes a reply (`json.loads` with
`parse_float=Decimal`, a duplicate-key refusal on every object, and refusing NaN and Infinity, replicating
`app.platform.engine_protocol.decode_reply`, which this environment cannot import). It prints the line's size
against `MAX_LINE_BYTES` and the median encode and decode times, and the decode's peak memory (tracemalloc, Python
objects only). It reports and never fails. This measurement script is not product code, so it reads the clock.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from ledger_engine.ledger.overlay import Loaded, load
from ledger_engine.ledger.policy import DESKTOP
from ledger_engine.mirror.core import rows, schema
from ledger_engine.protocol.messages import MAX_LINE_BYTES, PROTOCOL_VERSION

STAMP = rows.Stamp(
    ledger_version="0:measure",
    fingerprint="{}",
    built_at="1970-01-01T00:00:00+00:00",
    engine_build="measure",
    published_by="open",
)
NARROW = (
    "transaction_date",
    "transaction_flag",
    "transaction_payee",
    "transaction_narration",
    "account",
    "amount",
    "currency",
)


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({k for k, _ in pairs}) != len(pairs):
        msg = "duplicate key"
        raise ValueError(msg)
    return dict(pairs)


def _refuse_constant(name: str) -> Any:
    msg = f"{name} is not JSON"
    raise ValueError(msg)


def client_decode(line: bytes) -> object:
    return json.loads(
        line.decode("utf-8"),
        object_pairs_hook=_refuse_duplicates,
        parse_constant=_refuse_constant,
        parse_float=Decimal,
    )


def median_ms(fn: Callable[[], object], runs: int) -> tuple[float, object]:
    samples: list[float] = []
    out: object = None
    for _ in range(runs):
        t0 = time.perf_counter()
        out = fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples), out


def peak_mb(fn: Callable[[], object]) -> float:
    tracemalloc.start()
    try:
        kept = fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del kept
    return peak / 1e6


def say(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def measure(shape: str, columns: list[str], table: list[tuple[object, ...]], runs: int) -> None:
    reply = {
        "v": PROTOCOL_VERSION,
        "id": 1,
        "op": "bql",
        "ok": True,
        "result": {"columns": columns, "rows": table},
    }
    encode_ms, line = median_ms(lambda: json.dumps(reply, separators=(",", ":")).encode() + b"\n", runs)
    assert isinstance(line, bytes)  # noqa: S101 (narrowing)
    plain_ms, _ = median_ms(lambda: json.loads(line), runs)
    client_ms, _ = median_ms(lambda: client_decode(line), runs)
    peak = peak_mb(lambda: client_decode(line))
    size = len(line) - 1
    say(
        f"{shape}: {len(columns)} columns, reply line {size / 1e6:.2f} MB"
        f" ({size / MAX_LINE_BYTES:.0%} of MAX_LINE_BYTES {MAX_LINE_BYTES / 1e6:.2f} MB);"
        f" encode {encode_ms:.0f} ms; decode plain {plain_ms:.0f} ms, as the client {client_ms:.0f} ms;"
        f" decode peak {peak:.0f} MB of Python objects"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("root")
    p.add_argument("--runs", type=int, default=5)
    a = p.parse_args(argv)
    loaded = load(os.path.abspath(a.root), {}, DESKTOP)
    if not isinstance(loaded, Loaded):
        sys.exit(f"the load was refused: {loaded}")
    postings = rows.table_rows(loaded.entries, loaded.errors, loaded.options, date(2026, 10, 28), STAMP)["postings"]
    names = [c.name for c in schema.BY_NAME["postings"].columns]
    say(f"entries {len(loaded.entries)} postings {len(postings)}")
    measure("wide", names, [tuple(r) for r in postings], a.runs)
    picks = [names.index(n) for n in NARROW]
    measure("narrow", list(NARROW), [tuple(r[i] for i in picks) for r in postings], a.runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
