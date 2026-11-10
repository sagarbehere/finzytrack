"""Parity corpus plugin (S0094): lists its function itself in __plugins__, not its name; adds a note; no I/O."""

import datetime

from beancount.core import data


def add_note(entries, options_map):
    meta = data.new_metadata("<parity_s0094_function_object>", 0)
    return [*entries, data.Note(meta, datetime.date(2020, 1, 4), "Assets:Cash", "added by a plugin", None, None)], []


__plugins__ = [add_note]
