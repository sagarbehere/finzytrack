"""What a new workspace's `.finzytrack/` holds when the `create` job writes it (engine-jobs §8.2; ADR-0017; S0080).

`manifest_files(root)` is the manifest of a new workspace whose ledger's root is `root`, with a new lineage id (a new
set of books gets a new id, ADR-0017), as the `create` job's `.finzytrack/` files
(`app.platform.engine_jobs.create.CreateRequest.manifest`). The wizard, "New workspace" and the demo that build the
rest of a create are M3's (CAP-WS-CREATE, CAP-SETUP-FRESH). `copy_manifest_files(source)` is a copy's (S0081).
"""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath

from app.domain.paths import WorkspacePath
from app.platform.engine_jobs.create import NewFile
from app.workspaces.format.manifest import FORMAT, MANIFEST_PATH, Inside, LineageId, Manifest, encode_manifest


def new_lineage_id() -> LineageId:
    """A new set of books' id: a random UUID in canonical lower-case text (ADR-0017)."""
    return LineageId(str(uuid.uuid4()))


def manifest_files(root: WorkspacePath, lineage: LineageId | None = None) -> tuple[NewFile, ...]:
    """The `.finzytrack/` files of a new workspace (paths relative to `.finzytrack/`): its manifest."""
    m = Manifest(FORMAT, FORMAT, lineage or new_lineage_id(), Inside(root))
    return (NewFile(MANIFEST_PATH.split("/", 1)[1], encode_manifest(m).decode("utf-8")),)


def copy_manifest_files(source: str) -> tuple[NewFile, ...]:
    """The `.finzytrack/` files of a copy of the ledger whose root is `source` (an absolute path): a **new** lineage id
    (a copy is a new set of books, ADR-0017: "only "copy" writes a new lineage id"), and the root's file name, where
    the `copy` job puts it (`ledger_engine.ledger.core.copy_layout`: the root's folder becomes the copy's folder;
    S0081)."""
    return manifest_files(WorkspacePath.parse(PurePosixPath(source).name))
