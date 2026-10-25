"""The oracle works on copies only (INV-24): which folders it refuses, how a case root is staged, what it may print.

Standard library only, so the guards run in CI's repo-checks job without the harness's Beancount env.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORPORA = REPO / "corpora"
GENERATED_MARKER = ".oracle-generated"


LOCAL_TIER_ENV = "FT_LOCAL_TIER_DIR"


def legacy_root() -> Path:
    """The legacy worktree to run (FT_LEGACY_ROOT, else the sibling worktree)."""
    return Path(os.environ.get("FT_LEGACY_ROOT", REPO.parent / "finzytrack-legacy")).expanduser().resolve()


def live_data_root() -> Path:
    return Path(os.environ.get("FT_LIVE_DATA", Path.home() / "FinzytrackData")).expanduser().resolve()


def _forbidden_roots() -> list[tuple[Path, str]]:
    """Every folder refuse_dir refuses. An environment override adds a folder; it never removes a default (S0030)."""
    roots = [(Path.home() / "FinzytrackData", "the owner's live data folder"),
             (REPO.parent / "finzytrack-legacy", "the legacy worktree"),
             (Path.home() / "experiments" / "finzytrack-legacy", "the legacy worktree")]
    for env, why in (("FT_LIVE_DATA", "the owner's live data folder"), ("FT_LEGACY_ROOT", "the legacy worktree")):
        if os.environ.get(env):
            roots.append((Path(os.environ[env]).expanduser(), why))
    return [*roots, (REPO, "this repo")]


def _key(p: Path) -> str:
    """Resolved (symlinks, `..`), lower-cased path text: macOS disks are case-insensitive (as the guard hooks' `_norm`)."""
    try:
        r = p.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        r = p
    return r.as_posix().rstrip("/").lower()


def within(p: Path, root: Path) -> bool:
    """Whether *p* is *root* or inside it, compared resolved and case-insensitively."""
    a, b = _key(p), _key(root)
    return a == b or a.startswith(b + "/")


def refuse_dir(folder: Path, *, allow_repo_corpora: bool = False) -> str | None:
    """Why *folder* must not be used (as a legacy root or a ledger source), or None.

    Refused: the owner's live data folder, the legacy worktree and this repo, any folder inside one of them, and
    any folder that contains one of them. Symlinks are resolved first and case is ignored. With
    *allow_repo_corpora*, a folder inside `corpora/` may be read (as a copy source only).
    """
    for forbidden, why in _forbidden_roots():
        if allow_repo_corpora and forbidden == REPO and within(folder, CORPORA):
            continue
        if within(folder, forbidden) or within(forbidden, folder):
            return f"{folder} is (or contains, or is inside) {why}; use a copy in a temp dir"
    return None


def local_tier_dir() -> tuple[Path | None, str | None]:
    """The T-local folder named by FT_LOCAL_TIER_DIR, or why there is none usable (PROCESS.md §8; S0030).

    It must be set, absolute and an existing folder, and refuse_dir must accept it: outside the repo, the live data
    folder and the legacy worktree, and containing none of them.
    """
    raw = os.environ.get(LOCAL_TIER_ENV, "")
    if not raw:
        return None, f"{LOCAL_TIER_ENV} is not set; real-ledger copies are read only from that folder (T-local)"
    tier = Path(raw)
    if not tier.is_absolute() or not tier.is_dir():
        return None, f"{LOCAL_TIER_ENV}={raw} is not an absolute path to an existing folder"
    problem = refuse_dir(tier)
    if problem:
        return None, f"{LOCAL_TIER_ENV}: {problem}"
    return tier, None


def refuse_local_tier_source(folder: Path) -> str | None:
    """Why *folder* must not be read as a ledger source by a run that may see real-ledger copies, or None.

    Allowed: the repo's synthetic `corpora/`, and folders inside the local-tier folder (local_tier_dir). Anything
    else could be a real ledger in an unexpected place (PROCESS.md §8; INV-24; S0030).
    """
    if within(folder, CORPORA):
        return refuse_dir(folder, allow_repo_corpora=True)
    tier, why = local_tier_dir()
    if tier is None:
        return why
    problem = refuse_dir(folder)
    if problem:
        return problem
    if not within(folder, tier):
        return f"{folder} is not inside {LOCAL_TIER_ENV} ({tier}); copy the ledgers there first"
    return None


def stage_root(ledger_src: Path, root_file: str, template: Path, root: Path) -> Path:
    """Create a fresh legacy data root at *root*: the template's config/ plus a copy of *ledger_src* in data/ledgers/.

    The config points at the copied root file and is marked set up. Returns the copied root file's path. The source
    folder is only read.
    """
    for folder, kwargs in ((ledger_src, {"allow_repo_corpora": True}), (root, {})):
        problem = refuse_dir(folder, **kwargs)
        if problem:
            raise ValueError(problem)
    if root.exists():
        raise ValueError(f"{root} already exists; every case gets a fresh root")
    rel = Path(root_file)
    if rel.is_absolute() or ".." in rel.parts or not (ledger_src / rel).is_file():
        raise ValueError(f"root file {root_file!r} must be a file inside {ledger_src}")
    ledgers = root / "data/ledgers"
    shutil.copytree(template / "config", root / "config")
    shutil.copytree(ledger_src, ledgers, symlinks=False)
    (root / "data/backups").mkdir()  # the legacy app refuses to start set up without it
    cfg = root / "config/config.yaml"
    text = cfg.read_text(encoding="utf-8")
    text, n_ledger = re.subn(r'(?m)^ledger_file: .*$', f'ledger_file: "./data/ledgers/{rel.as_posix()}"', text)
    text, n_setup = re.subn(r"(?m)^setup_complete: .*$", "setup_complete: true", text)
    if (n_ledger, n_setup) != (1, 1):
        raise ValueError("the legacy config template has no single ledger_file / setup_complete line")
    cfg.write_text(text, encoding="utf-8")
    return ledgers / rel


def mark_generated(folder: Path) -> None:
    """Mark a ledger folder the harness generated itself (synthetic data), so its entries may be printed."""
    (folder / GENERATED_MARKER).write_text("synthetic ledger generated by scripts/oracle\n", encoding="utf-8")


def may_show_entries(ledger_src: Path) -> bool:
    """Entry text may appear in a report only for repo corpora and generated ledgers; anything else could be a copy
    of a real ledger, whose reports carry counts and case IDs only (PROCESS.md §8). Nothing inside the local-tier
    folder shows entries, marker or not (S0030)."""
    tier, _ = local_tier_dir()
    if tier is not None and within(ledger_src, tier):
        return False
    return within(ledger_src, CORPORA) or (ledger_src.resolve() / GENERATED_MARKER).is_file()
