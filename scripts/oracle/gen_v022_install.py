"""Produce `corpora/workspaces/legacy/v022-fresh/` from the RELEASED v0.2.2 code (S0095; EP-29: the oracle, not our code).

The folder is what a v0.2.2 desktop install holds right after its setup wizard's "fresh" path, made by v0.2.2's own
code read from the `v0.2.2` tag of this repository (`git show`, read-only):
  backend/app/core/seed.py              substitute_currency, run as seed_data_with_currency runs it: the bundled
                                        `backend/resources/seed_data/` copied whole into `data/`, and every
                                        `.beancount` file holding the placeholder rewritten with the chosen currency
  backend/app/api/routers/config.py     _deep_merge: the wizard's config patch for "fresh" (setup_complete and
                                        accounts.default_currency), merged into the seeded `config/config.yaml` and
                                        written with ruamel.yaml's round trip (the version v0.2.2 locks, 0.18.15), as
                                        `complete_setup` in backend/app/api/routers/setup.py writes it
  backend/app/write_lock.py             WriteLockManager.acquire: the wizard's first load and export
                                        (`startup_user_services`, through `SqliteReader._ensure_fresh`) take the write
                                        lock, which opens `data/.write.lock` in append mode and never writes it: an
                                        empty file (S0095 verifier F1)
The install's own location (the platform folder, `Finzytrack` in the user's application-data folder) is never
written into the folder: v0.2.2 resolves every path in it against that folder at run time.

Not in the fixture, though a real install has them (each a known gap, listed in the spec too):
- `data/ledger.db`, v0.2.2's mirror, written by that same first export: a SQLite file, which the repository ignores
  by name (`.gitignore`, INV-23) and the staged-file guard refuses as a binary file and a mirror database. v0.4 never
  reads it (it builds its own mirror); BL-0026's adoption test must still leave it as it is.
- `data/ledgers/fake.beancount`, the demo ledger: a build-time output of `backend/scripts/generate_fake.py`, not in
  the tag. That script is deterministic (seeded, stdlib only); for the release's build month (`--anchor 2026-08`) it
  gives about 2 MB, 5404 transactions, each with a 64-hex `content_hash` the privacy scan reads as a secret, so it is
  not committed. It holds the same keys as `fake-multi` (`id`, `content_hash`, `source_account`).
- The rest of the seeded `config/` (bank rule files, dashboard recipes, themes), the wizard's timestamped backup of
  the old `config.yaml` under `data/backups/`, `logs/` and the upgrade state: no v0.4 reader, and INV-21 covers
  ledgers only (BL-0026's adoption corpus may add them).

**Committed as `_data/`, not `data/`.** The repository ignores every folder named `data` (INV-23: real data never
enters it; `.gitignore` says those lines stay), so the fixture stores the install's `data/` folder as `_data/`, and
every reader copies the fixture into a temp folder and names it `data/` again (`materialize`) before using it.

Usage (oracle env):  uv run --project scripts/oracle --locked python scripts/oracle/gen_v022_install.py [--check]
"""

from __future__ import annotations

import argparse
import ast
import io
import subprocess
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "corpora" / "workspaces" / "legacy" / "v022-fresh"
TAG = "v0.2.2"
CURRENCY = "EUR"  # the wizard's currency: not the seed's USD, so the substitution shows
SEED_DATA = "backend/resources/seed_data/"
SEED_CONFIG = "backend/resources/seed_config/config.yaml"
COMMITTED_DATA = "_data"  # the install's `data/` as committed (see the docstring)


def committed(rel: str) -> str:
    """An install path as the fixture stores it."""
    return COMMITTED_DATA + rel[len("data") :] if rel == "data" or rel.startswith("data/") else rel


def materialize(fixture: Path, dst: Path) -> Path:
    """Copy the committed fixture to `dst` as the install it is: `_data/` named `data/` again."""
    import shutil

    shutil.copytree(fixture, dst)
    (dst / COMMITTED_DATA).rename(dst / "data")
    return dst


def git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True).stdout


def show(path: str) -> bytes:
    return git("show", f"{TAG}:{path}")


def function(path: str, name: str, ns: dict[str, Any]) -> Any:
    """A top-level function of a v0.2.2 module, extracted verbatim by syntax tree and defined in `ns`."""
    src = show(path).decode("utf-8")
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module([node], []), path, "exec"), ns)
    return ns[name]


def generate() -> dict[str, bytes]:
    """{path relative to the install folder: bytes}."""
    seed_ns: dict[str, Any] = {"CURRENCY_PLACEHOLDER": "{default_currency}"}
    substitute_currency = function("backend/app/core/seed.py", "substitute_currency", seed_ns)
    deep_merge = function("backend/app/api/routers/config.py", "_deep_merge", {"MutableMapping": MutableMapping,
                                                                               "Any": Any})
    out: dict[str, bytes] = {}
    # seed_data_with_currency: shutil.copytree(SEED_DATA_DIR, data_dir), then the placeholder substituted in every
    # .beancount file that holds it (read and written with newline="": the bytes are kept).
    names = git("ls-tree", "-r", "--name-only", TAG, SEED_DATA).decode("utf-8").splitlines()
    for name in names:
        body = show(name)
        rel = "data/" + name[len(SEED_DATA):]
        if rel.endswith(".beancount"):
            text = body.decode("utf-8")
            if seed_ns["CURRENCY_PLACEHOLDER"] in text:
                body = substitute_currency(text, CURRENCY).encode("utf-8")
        out[rel] = body
    # complete_setup, ledger_mode "fresh": the patch, merged with _deep_merge and dumped by ruamel's round trip.
    yaml = YAML()
    doc = yaml.load(show(SEED_CONFIG).decode("utf-8"))
    deep_merge(doc, {"setup_complete": True, "accounts": {"default_currency": CURRENCY}})
    buf = io.StringIO()
    yaml.dump(doc, buf)
    out["config/config.yaml"] = buf.getvalue().encode("utf-8")
    # WriteLockManager.acquire during the wizard's first export: `open(lock_file, "a")`, locked, never written.
    out["data/.write.lock"] = b""
    return out


def on_disk() -> dict[str, bytes]:
    """The committed fixture, keyed by install path (`_data/` read as `data/`)."""
    out = {}
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            rel = p.relative_to(OUT).as_posix()
            out["data" + rel[len(COMMITTED_DATA) :] if rel.startswith(COMMITTED_DATA + "/") else rel] = p.read_bytes()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare with the committed folder instead of writing")
    args = ap.parse_args()
    want = generate()
    if args.check:
        same = OUT.is_dir() and on_disk() == want
        print("v022-fresh matches v0.2.2" if same else "v022-fresh differs from what v0.2.2 gives")
        return 0 if same else 1
    if OUT.exists() and set(on_disk()) - set(want):
        print(f"{OUT.relative_to(REPO)} holds files v0.2.2 does not make; remove them first", file=sys.stderr)
        return 2
    for rel, body in want.items():
        (OUT / committed(rel)).parent.mkdir(parents=True, exist_ok=True)
        (OUT / committed(rel)).write_bytes(body)
    print(f"wrote {OUT.relative_to(REPO)} ({len(want)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
