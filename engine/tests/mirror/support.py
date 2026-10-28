"""Helpers for the mirror tests: load a synthetic ledger with the overlay load, build its mirror into a temp folder
and read it back with plain `sqlite3`, as an outside tool would (INV-23: synthetic data only; INV-24: temp dirs)."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from ledger_engine.ledger.overlay import Loaded, load
from ledger_engine.ledger.policy import DESKTOP
from ledger_engine.mirror.build import BuildFailed, Built, Stamp, build

TODAY = date(2026, 10, 28)
STAMP = Stamp(ledger_version="7:5f3a", fingerprint='{"root": "main.beancount"}', built_at="2026-10-28T09:30:00+05:30")


def write_ledger(folder: Path, text: str, files: dict[str, str] | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    root = folder / "main.beancount"
    root.write_text(text, encoding="utf-8")
    for name, body in (files or {}).items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(body, encoding="utf-8")
    return root


def load_root(root: Path) -> Loaded:
    result = load(str(root), {}, DESKTOP)
    assert isinstance(result, Loaded), result
    return result


def build_loaded(loaded: Loaded, out: Path, today: date = TODAY) -> sqlite3.Connection:
    result = build(loaded, str(out), today, STAMP)
    assert isinstance(result, Built), result
    return sqlite3.connect(f"file:{out}?mode=ro", uri=True)


def build_text(
    tmp_path: Path, text: str, today: date = TODAY, files: dict[str, str] | None = None
) -> sqlite3.Connection:
    """The mirror of a ledger written as `text` (and `files` beside it), as of `today`."""
    loaded = load_root(write_ledger(tmp_path / "ledger", text, files))
    return build_loaded(loaded, tmp_path / "mirror.db", today)


def try_build(tmp_path: Path, text: str, today: date = TODAY) -> Built | BuildFailed:
    loaded = load_root(write_ledger(tmp_path / "ledger", text))
    return build(loaded, str(tmp_path / "mirror.db"), today, STAMP)


def rows(con: sqlite3.Connection, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in con.execute(sql, params).fetchall()]


def one(con: sqlite3.Connection, sql: str, *params: Any) -> tuple[Any, ...]:
    found = rows(con, sql, *params)
    assert len(found) == 1, found
    return found[0]
