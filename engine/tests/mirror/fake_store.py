"""The publish's store over the in-memory file system (`tests/fakes/memfs.py`), for the crash, power-loss and I/O-error
sweeps of `design/specs/mirror/publish.md` §9 (S0087, BL-0113).

SQLite cannot run over the fake, so the build is reduced to the effects SQLite makes on its own file, in the order it
makes them (the model does the same, `formal/tla/mirror-publish/MAPPING.md` §3): the exclusive create through the
folder's handle (the real store's `DirHandle.create`, data synced, entry not), the rollback journal made beside it, the
database's bytes written into the file without a sync (SQLite's own syncs do not reach the disk on macOS, §4 step 5),
the journal removed at the commit. `meta` refuses what a reader refuses: a file with its journal beside it (a hot
journal), and bytes that are not a whole mirror (torn, cut, empty). A file it accepts is whole and says what the build
stamped it with.
"""

from __future__ import annotations

import contextlib
import json
from datetime import date

from ledger_engine.fs.port import DirHandle
from ledger_engine.ledger.beancount_port import ParseResult
from ledger_engine.ledger.core.ledger_set import Fingerprint, digest, encode
from ledger_engine.mirror import publish as pb
from ledger_engine.mirror.build import BuildFailed, Built, Stamp
from ledger_engine.mirror.core import schema
from tests.fakes.memfs import MemDir, MemFS

MAGIC = b"FAKE MIRROR v1\n"


class FakeStore:
    def __init__(self, *, complete: bool = True) -> None:
        self.complete = complete

    def build(
        self, d: DirHandle, name: str, result: ParseResult, today: date, stamp: Stamp
    ) -> Built | BuildFailed | pb.Replaced:
        del result
        assert isinstance(d, MemDir)
        facts = {
            "surface_version": str(schema.SURFACE_VERSION),
            "ledger_version": stamp.ledger_version,
            "fingerprint": stamp.fingerprint,
            "export_version": schema.EXPORT_VERSION,
            "built_at": stamp.built_at,
            "build_complete": "1" if self.complete else "0",
            "engine_build": stamp.engine_build,
            "as_of": today.isoformat(),
            "published_by": stamp.published_by,
        }
        journal = f"{name}-journal"
        made = d.create(name, b"", 0o600)
        try:
            d.create(journal, b"hot journal", 0o600)
            d.write_in(name, MAGIC + json.dumps(facts, sort_keys=True).encode())
            d.remove(journal)  # the commit
        except OSError:
            for leftover in (name, journal):
                with contextlib.suppress(OSError):
                    d.remove(leftover)
            raise
        st = d.stat(name)
        if st is None or st.identity != made:
            return pb.Replaced()
        return Built(name, {}, self.complete)

    def meta(self, d: DirHandle, name: str) -> pb.Meta | None:
        try:
            if d.stat(f"{name}-journal") is not None:
                return None
            data = d.read(name)
        except OSError:
            return None
        if data is None or not data.startswith(MAGIC):
            return None
        try:
            facts = json.loads(data[len(MAGIC) :])
        except ValueError:
            return None
        if not isinstance(facts, dict):
            return None
        return pb.meta_of({str(k): v for k, v in facts.items()})  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def outside_publish(fs: MemFS, fp: Fingerprint, key: str, cache: str = "/cache") -> None:
    """A published generation stamped with `fp`, put in place durably and with no effect counted (as an earlier job
    left it), so a save's gate over the fake reads a fresh mirror (publish.md §7) without moving the save's
    effects."""
    folder = f"{cache}/workspaces/{key}/mirror"
    fs.outside_mkdirs(folder)
    name = f"gen-1-{'0' * 16}.db"
    facts = {
        "ledger_version": f"1/{digest(fp)}",
        "fingerprint": encode(fp).decode("ascii"),
        "export_version": schema.EXPORT_VERSION,
        "engine_build": "b",
        "as_of": "2026-11-08",
        "build_complete": "1",
        "published_by": "rebuild",
    }
    fs.outside_write(f"{folder}/{name}", MAGIC + json.dumps(facts, sort_keys=True).encode(), mode=0o600)
    fs.outside_symlink(f"{folder}/{pb.CURRENT}", name)
