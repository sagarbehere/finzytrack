"""Corpus plugin (S0020): adds the tag #seen to every transaction; no I/O."""

from beancount.core import data

__plugins__ = ["tag_all"]


def tag_all(entries, options_map):
    out = []
    for e in entries:
        if isinstance(e, data.Transaction):
            e = e._replace(tags=(e.tags or frozenset()) | {"seen"})
        out.append(e)
    return out, []
