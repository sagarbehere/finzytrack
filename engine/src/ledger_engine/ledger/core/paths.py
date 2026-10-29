"""`canonical_path` and `path_key` as values (`design/specs/engine-jobs.md` §6.3; S0060). Pure.

`path_key` is the identity of every lock, fingerprint entry, journal match and registry deduplication: the canonical
spelling of the deepest existing ancestor joined with the rest as written, each component folded by the rule of the
directory that holds it (macOS: NFD always, plus `casefold` on a case-insensitive volume). So the key of a path does
not depend on whether its file exists: an absent `Prices.beancount` and the file a save then creates as
`prices.beancount` on a case-insensitive volume have one key. Python's folding can only merge names a volume keeps
apart, never split names it treats as one (§15: it fails closed, with more locking).
"""

from __future__ import annotations

import unicodedata

from ledger_engine.fs.port import FoldRule, MalformedPathError, Resolved


def fold(component: str, rule: FoldRule) -> str:
    """One component folded by its directory's rule. Caseless matching is NFD(casefold(NFD(x))) (Unicode §3.13)."""
    out = unicodedata.normalize("NFD", component) if rule.nfd or rule.casefold else component
    if rule.casefold:
        out = unicodedata.normalize("NFD", out.casefold())
    return out


def canonical_path(resolved: Resolved) -> str:
    """The canonical path: the existing part's stored spelling, then the rest as written."""
    return "/" + "/".join(resolved.existing + resolved.rest)


def path_key(resolved: Resolved) -> str:
    """The key: every component of the canonical path folded by its rule."""
    parts = resolved.existing + resolved.rest
    if len(resolved.rules) != len(parts):
        raise MalformedPathError("one_rule_per_component")
    return "/" + "/".join(fold(p, r) for p, r in zip(parts, resolved.rules, strict=True))
