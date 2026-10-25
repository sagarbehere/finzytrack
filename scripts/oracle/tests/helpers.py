"""Shared helpers for the oracle tests: write a small ledger set into a temp dir."""

from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class LedgerDir:
    """A temp folder holding a ledger set; files are given as {relative path: text}."""

    def __init__(self, files: dict[str, str]) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        self.write(files)

    def write(self, files: dict[str, str]) -> None:
        for rel, text in files.items():
            p = self.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")

    def close(self) -> None:
        self._tmp.cleanup()


OPENS = """
2020-01-01 open Assets:Cash USD
2020-01-01 open Expenses:Food USD
2020-01-01 open Expenses:Rent USD
"""
