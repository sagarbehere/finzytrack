"""Parity corpus plugin (S0094): found through the included file's folder on the path; reports how many
transactions it saw as an error; no I/O."""

import collections

from beancount.core import data

__plugins__ = ["count"]

CountError = collections.namedtuple("CountError", "source message entry")


def count(entries, options_map):
    n = sum(isinstance(e, data.Transaction) for e in entries)
    return entries, [CountError(data.new_metadata("<parity_s0094_from_sub>", 0), f"saw {n} transactions", None)]
