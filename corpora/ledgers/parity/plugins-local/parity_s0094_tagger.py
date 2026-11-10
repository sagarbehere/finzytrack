"""Parity corpus plugin (S0094): tags every transaction with its configuration string; no I/O."""

from beancount.core import data

__plugins__ = ["tag_with_config"]


def tag_with_config(entries, options_map, config):
    out = []
    for e in entries:
        if isinstance(e, data.Transaction):
            e = e._replace(tags=(e.tags or frozenset()) | {config})
        out.append(e)
    return out, []
