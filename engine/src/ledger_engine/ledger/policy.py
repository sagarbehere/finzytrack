"""LoadPolicy: what a load may read and run (ADR-0010; WS-ISOLATION "seam now" for L11-02, L11-06, L11.M-1).

One policy is passed to every load. `overlay.load` puts four kinds of question to it, each with an absolute path or
a plugin's module name, before the thing happens: every file it reads (the root and each include, before the file is
touched), and, once the files are parsed and before any plugin runs, every user plugin, every `insert_pythonpath`
directory and every documents root. A "no" refuses the whole load; nothing is partly loaded.

Only the allowing desktop body is built, and v0.4.0 needs no other on desktop. `DESKTOP` allows all four: every
ledger may include, run and scan what it names (INV-03). There is no trust prompt in v0.4.0: a ledger's plugins and
`insert_pythonpath` run as Beancount runs them, as `bean-check` and Fava do (owner, 2026-10-29: "I'd say, pause this
feature. Mark it as something to be done later, and be done with it."; ADR-0040 amendment "no trust prompt on desktop
in v0.4.0"). The limit users are told: opening a ledger runs its code, so open only ledgers you trust (BL-0157). A
later trust prompt is BL-0156 and the hosted body ADR-0041, both post-v0.4 (L11-02, L11-06, L11.M-1). Two facts for
whoever writes a confining body: `may_run_plugin` gets the plugin's name, not its config (one of Beancount's own
plugins, `pedantic`, passes its config to `eval`); and an `include` glob lists directories before the policy is asked
about the files it matched. Encrypted files are not a policy choice: v0.4.0 refuses them under every policy (ADR-0059).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol


class LoadPolicy(Protocol):
    def may_read(self, path: str) -> bool:
        """A ledger file, by its absolute normalized path, before it is read."""
        ...

    def may_run_plugin(self, name: str) -> bool:
        """A user plugin, by the module name its `plugin` directive gives (Beancount's own built-in steps are not
        asked)."""
        ...

    def may_add_pythonpath(self, path: str) -> bool:
        """A directory `insert_pythonpath` would put first on `sys.path` while plugins run."""
        ...

    def may_scan_documents(self, path: str) -> bool:
        """A documents root (`option "documents"`), resolved as Beancount resolves it."""
        ...


@dataclass(frozen=True, slots=True)
class DesktopPolicy:
    """The desktop body: everything the ledger names is allowed (no trust prompt in v0.4.0, ADR-0040)."""

    def may_read(self, path: str) -> bool:
        return True

    def may_run_plugin(self, name: str) -> bool:
        return True

    def may_add_pythonpath(self, path: str) -> bool:
        return True

    def may_scan_documents(self, path: str) -> bool:
        return True


DESKTOP: Final[LoadPolicy] = DesktopPolicy()
