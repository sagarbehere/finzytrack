"""LoadPolicy: what a load may read and run (ADR-0010; WS-ISOLATION "seam now" for L11-02, L11-06, L11.M-1).

One policy is passed to every load. `overlay.load` puts four kinds of question to it, each with an absolute path or
a plugin's module name, before the thing happens: every file it reads (the root and each include, before the file is
touched), and, once the files are parsed and before any plugin runs, every user plugin, every `insert_pythonpath`
directory and every documents root. A "no" refuses the whole load; nothing is partly loaded.

Only the allowing desktop body is built. `DESKTOP` allows all four: a ledger the user trusts may include, run and
scan what it names (INV-03). On desktop a ledger that runs code (a plugin outside Beancount's own, `insert_pythonpath`,
or a plugin that evaluates a non-literal config) does not open until the user trusts it on the trust screen; Beancount's
own plugins are trusted (owner, 2026-10-29: "No, ledger that runs code dosen not open before I trust it."; ADR-0040
amendment of 2026-10-29). That trust body is BL-0156, built with M2's open flow (L11-02, L11-06, L11.M-1). The hosted
body is deferred until hosting is planned (ADR-0041). Two facts for whoever writes a confining body: `may_run_plugin`
gets the plugin's name, not its config, which BL-0156 adds; and an `include` glob lists directories before the policy
is asked about the files it matched. Encrypted files are not a policy choice: v0.4.0 refuses them under every policy
(ADR-0059).
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
    """The desktop body for a trusted ledger: everything the ledger names is allowed."""

    def may_read(self, path: str) -> bool:
        return True

    def may_run_plugin(self, name: str) -> bool:
        return True

    def may_add_pythonpath(self, path: str) -> bool:
        return True

    def may_scan_documents(self, path: str) -> bool:
        return True


DESKTOP: Final[LoadPolicy] = DesktopPolicy()
