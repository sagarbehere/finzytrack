"""The publish's store on the real file system: SQLite's build of a generation and its `meta` read
(`ledger_engine.mirror.publish.Store`; `design/specs/mirror/publish.md` §2 "The build's own open", §4 step 3, §7; S0087,
BL-0113).

SQLite cannot open a file relative to a folder's descriptor, so this is the one step that opens by path: the path is
the folder's own (`DirHandle.path`, `F_GETPATH` of the job's descriptor) and the generation's name. The build first
creates the file exclusively through the descriptor, then opens it with the URI mode `rw`, which never creates a file:
if the path no longer leads to our folder, the open fails instead of writing elsewhere. After the build, the entry at
the name must still be the file the create made (device and inode). The `meta` read opens read-only (`mode=ro`) a
regular file in rollback mode and reads `m_meta` only. A hot journal beside it makes SQLite refuse the read (a
read-only connection may not roll it back), as for every reader (§6); a journal SQLite does not find hot (empty,
zeroed) is a finished transaction's, and the file is read (probed with SQLite 3.53 in S0087).
"""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import date
from pathlib import Path
from typing import Final

from ledger_engine.fs.port import DirHandle
from ledger_engine.ledger.beancount_port import ParseResult
from ledger_engine.mirror import build as _build
from ledger_engine.mirror import publish as pb
from ledger_engine.mirror.build import FILE_MODE, BuildFailed, Built, Stamp
from ledger_engine.mirror.core import rows, schema
from ledger_engine.mirror.core.rows import Row
from ledger_engine.tla import tla_action

HEADER: Final = b"SQLite format 3\x00"
HEADER_BYTES: Final = 100
ROLLBACK: Final = 1  # header bytes 18 and 19: 1 rollback journal, 2 WAL
MAX_META_ROWS: Final = 64


def _uri(d: DirHandle, name: str, mode: str) -> str:
    return f"{Path(d.path(), name).as_uri()}?mode={mode}"


class SqliteStore:
    @tla_action("P_Create", "P_Commit", model="mirror-publish")
    def build(
        self, d: DirHandle, name: str, result: ParseResult, today: date, stamp: Stamp
    ) -> Built | BuildFailed | pb.Replaced:
        entries, errors, options = result
        made = _build.tables(entries, errors, options, today, stamp)
        if isinstance(made, BuildFailed):
            return made  # before any file exists
        identity = d.create(name, b"", FILE_MODE)
        try:
            _build.write(str(Path(d.path(), name)), made)
        except BaseException:
            for leftover in (name, f"{name}-journal"):
                with contextlib.suppress(OSError):
                    d.remove(leftover)
            raise
        st = d.stat(name)
        if st is None or st.identity != identity:
            return pb.Replaced()
        return Built(name, _build.counts(made), rows.COMPLETE)

    def meta(self, d: DirHandle, name: str) -> pb.Meta | None:
        try:
            head = d.read(name, limit=HEADER_BYTES)  # a regular file only, never through a link
        except OSError:
            return None
        if head is None or len(head) < HEADER_BYTES or not head.startswith(HEADER):
            return None
        if head[18] != ROLLBACK or head[19] != ROLLBACK:
            return None  # WAL mode: SQLite would make files beside it even read-only
        try:
            con = sqlite3.connect(_uri(d, name, "ro"), uri=True)
            try:
                con.execute("PRAGMA query_only = 1")
                found = con.execute(f"SELECT key, value FROM m_meta LIMIT {MAX_META_ROWS + 1}").fetchall()  # noqa: S608
            finally:
                con.close()
        except sqlite3.Error:
            return None
        facts: dict[str, object] = {}
        for key, value in found:
            if not isinstance(key, str) or key in facts:
                return None
            facts[key] = value
        if len(found) > MAX_META_ROWS:
            return None
        return pb.meta_of(facts)

    def errors(self, d: DirHandle, name: str) -> list[Row] | None:
        """The generation's `ledger_errors` rows in their order (`pb.BaselineStore`, the preview's baseline, S0116),
        opened read-only as `meta` is; None when it cannot be read."""
        if self.meta(d, name) is None:  # the same checks of the file first: a regular file, rollback mode, whole
            return None
        table = next(t for t in schema.TABLES if t.name == "ledger_errors")  # columns in `rows.error_rows`' order
        columns = ", ".join(c.name for c in table.columns)
        try:
            con = sqlite3.connect(_uri(d, name, "ro"), uri=True)
            try:
                con.execute("PRAGMA query_only = 1")
                found = con.execute(f"SELECT {columns} FROM {table.base} ORDER BY id").fetchall()  # noqa: S608
            finally:
                con.close()
        except sqlite3.Error:
            return None
        return [tuple(r) for r in found]
