"""The bundle "manifest" — computed at runtime by walking the seed trees.

There is **no shipped `manifest.json`** (design D1). What the app ships is
knowable by walking the bundled seed trees, so the file list, per-file bundle
hashes, the aggregate content-digest, and each file's `kind` are all *derived*,
never stored. The only thing we persist is the record of what we last wrote to
the user's disk (`installed`, in the upgrade-state) — the one value that can't be
recomputed. See dev-docs/seed-content-refresh.md §3.

Scope (design §11): recipes (`seed_config/recipes/**`) and ledgers
(`seed_data/ledgers/**`, all three demo ledgers incl. the `fake-multi/` tree).
Import rules / ofx mappings are Phase S4 — add a `_KINDS` entry when the need
arises and they flow through the same detect→notice→apply path (§7.4), no new
task. Living app config (config.yaml, .env) is deliberately **not** walked: it's
user settings, not refreshable demo content.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.core.seed import SEED_DATA_DIR, substitute_currency
from app.service_factory import SEED_CONFIG_DIR

# Each bundled asset class: the sub-tree under a seed root and the derived kind.
# `relroot` is the seed root the sub-tree lives in; `subdir` is the tree we walk;
# `prefix` is the relpath prefix used both as the provenance key and (joined to a
# target root) as the on-disk location. `needs_currency` marks trees whose files
# carry the `{default_currency}` placeholder (ledgers), so the target bytes we
# hash and write are the post-substitution bytes.
@dataclass(frozen=True)
class _Kind:
    kind: str
    root: Path            # SEED_CONFIG_DIR or SEED_DATA_DIR
    prefix: str           # "recipes" | "ledgers" — top of the relpath key
    needs_currency: bool


def _kinds() -> list[_Kind]:
    # A function (not a module constant) so the SEED_*_DIR values are read at
    # call time — they resolve differently frozen vs. dev, and tests monkeypatch.
    return [
        _Kind("recipe", SEED_CONFIG_DIR, "recipes", needs_currency=False),
        _Kind("ledger", SEED_DATA_DIR, "ledgers", needs_currency=True),
        # Dashboard color themes — user-editable, so provenance-protected like
        # recipes (a non-ledger kind → the §4 pristine-only rule in refresh._decide).
        _Kind("dashboard-theme", SEED_CONFIG_DIR, "dashboard-themes", needs_currency=False),
    ]


@dataclass(frozen=True)
class SeedFile:
    """One bundled file, everything about it derived from the bytes on disk."""
    relpath: str          # e.g. "recipes/dashboards/x.json", "ledgers/fake.beancount"
    kind: str             # "recipe" | "ledger"
    source: Path          # absolute path in the bundle
    raw_bytes: bytes      # the file as shipped (currency placeholder intact)
    _needs_currency: bool

    def bundle_hash(self) -> str:
        """Hash of the file *as shipped* — feeds the content-digest (§3). It is a
        property of the bundle bytes (placeholder intact), independent of the
        user's currency, so a release's digest is the same for every user."""
        return _sha256(self.raw_bytes)

    def target_bytes(self, currency: str) -> bytes:
        """The exact bytes we write to the user's disk: currency-substituted for
        ledgers, verbatim otherwise."""
        if not self._needs_currency:
            return self.raw_bytes
        return substitute_currency(self.raw_bytes.decode("utf-8"), currency).encode("utf-8")

    def target_hash(self, currency: str) -> str:
        """Hash of the post-substitution bytes — this is what gets recorded in
        `installed` and compared against the on-disk file for provenance (§4)."""
        return _sha256(self.target_bytes(currency))

    def target_path(self, config_dir: Path, data_dir: Path) -> Path:
        """Where this file lives on the user's disk. Recipes land under the config
        dir, ledgers under the data dir; the relpath already carries the sub-tree
        prefix, so a plain join reproduces the layout."""
        root = data_dir if self.kind == "ledger" else config_dir
        return root / self.relpath

    def display_path(self, config_dir: Path, data_dir: Path) -> str:
        """A short, human-locatable path anchored at the install root (the parent
        both config/ and data/ share): "config/recipes/…" or "data/ledgers/…"."""
        root = data_dir if self.kind == "ledger" else config_dir
        return f"{root.name}/{self.relpath}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def walk_bundle() -> list[SeedFile]:
    """Walk the bundled seed trees and return one `SeedFile` per shipped file,
    sorted by relpath. Dotfiles/dot-directories (`.gitkeep`, `.env.example`,
    `.migration-backups/`, …) are skipped — they are never refreshable content.
    Missing trees (e.g. a partial bundle) are simply absent, not an error."""
    files: list[SeedFile] = []
    for k in _kinds():
        subtree = k.root / k.prefix
        if not subtree.is_dir():
            continue
        for path in sorted(subtree.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(k.root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            files.append(
                SeedFile(
                    relpath=rel.as_posix(),
                    kind=k.kind,
                    source=path,
                    raw_bytes=path.read_bytes(),
                    _needs_currency=k.needs_currency,
                )
            )
    return files


def content_digest(files: list[SeedFile]) -> str:
    """The aggregate "is anything new?" signal (§3): a hash of the sorted
    per-file `relpath:bundle_hash` lines. Changes iff any bundled file's shipped
    bytes change, so nothing needs bumping by hand — add/edit a bundled file and
    the digest moves on its own (D1)."""
    lines = sorted(f"{f.relpath}:{f.bundle_hash()}" for f in files)
    return _sha256("\n".join(lines).encode("utf-8"))
