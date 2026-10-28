"""The workspace manifest, `.finzytrack/workspace.json` (ADR-0017, ADR-0019, EP-14, INV-17; S0051). Pure: it reads
bytes, never a folder (INV-30: the open reads it; nothing here writes it).

Format 1:

    {"format": 1, "min_reader": 1, "lineage_id": "<uuid>", "ledger": {"root": "<WorkspacePath>"} | "external"}

- `format` is the format the file is written in; `min_reader` the oldest format a reader must understand to read it
  (EP-14). A reader that understands `FORMAT` reads any file whose `min_reader` is at most `FORMAT`, ignoring fields
  it does not know; a newer file it may read is still never rewritten by it (INV-17, the writers of M3).
- `lineage_id` identifies "this set of books" wherever the folder travels; it is never the registry key, which is
  machine-local (ADR-0017). Canonical lower-case UUID text only, so two spellings of one id cannot pass as two.
- `ledger` is where the ledger's root file is: a `WorkspacePath` inside the folder, or `"external"`, whose absolute
  path lives only in the machine-local registry row (ADR-0017).

`read_manifest` tells a newer manifest apart before it validates anything else (a newer app may change every other
field): `ManifestNewer` is the open's NEWER_WORKSPACE, `ManifestInvalid` its WS_CONFIG_INVALID (ADR-0019). Reading
the file from a folder and `assess_workspace` are the open's (BL-0102, M2); writing a manifest is M3's (L10-08).
The JSON Schema in `contracts/workspace-manifest.schema.json` is generated from `ManifestV1` (EP-16).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal, NewType

from pydantic import AfterValidator, BaseModel, Field, ValidationError

from app.domain.paths import WorkspacePath

FORMAT: Final = 1
"""The newest manifest format this app understands, and the one it writes."""

MANIFEST_PATH: Final = ".finzytrack/workspace.json"
"""Where the manifest is, inside the folder (a `WorkspacePath`, ADR-0017)."""

LineageId = NewType("LineageId", str)
"""Which set of books a folder holds, wherever it travels (ADR-0017); never a registry key (`WorkspaceKey`)."""

UUID_PATTERN: Final = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


@dataclass(frozen=True, slots=True)
class Inside:
    """The ledger's root file, inside the workspace folder."""

    root: WorkspacePath


@dataclass(frozen=True, slots=True)
class External:
    """The ledger lives outside the folder; its path is in the machine-local registry row only."""


type LedgerPlace = Inside | External


@dataclass(frozen=True, slots=True)
class Manifest:
    format: int
    min_reader: int
    lineage_id: LineageId
    ledger: LedgerPlace


@dataclass(frozen=True, slots=True)
class ManifestNewer:
    """Written by a newer app for newer readers: refused as NEWER_WORKSPACE (INV-17)."""

    format: int
    min_reader: int


type InvalidReason = Literal["not-utf8", "not-json", "duplicate-key", "not-an-object", "invalid"]


@dataclass(frozen=True, slots=True)
class ManifestInvalid:
    """Not a manifest this app can read: WS_CONFIG_INVALID. `field` names the top-level field when there is one."""

    field: str | None
    reason: InvalidReason


type ManifestRead = Manifest | ManifestNewer | ManifestInvalid


# ---- the wire model (the source of the JSON Schema; EP-16) ---------------------------------------------------------


def _workspace_path(text: str) -> str:
    WorkspacePath.parse(text)  # raises InvalidWorkspacePath, a ValueError: pydantic reports it on the field
    return text


class InsideV1(BaseModel, extra="ignore", frozen=True, strict=True):
    root: Annotated[
        str,
        AfterValidator(_workspace_path),
        Field(description="The ledger's root file: a relative POSIX path inside the folder (app.domain.paths)."),
    ]


class ManifestV1(BaseModel, extra="ignore", frozen=True, strict=True, title="Finzytrack workspace manifest"):
    """`.finzytrack/workspace.json`, format 1. Unknown fields are ignored, so a later format a reader may still
    read (its `min_reader` at most 1) can add fields."""

    format: Annotated[int, Field(ge=1, description="The format this file is written in.")]
    min_reader: Annotated[int, Field(ge=1, description="The oldest format a reader must understand to read it.")]
    lineage_id: Annotated[str, Field(pattern=UUID_PATTERN, description="Which set of books; lower-case UUID text.")]
    ledger: Annotated[
        InsideV1 | Literal["external"],
        Field(description='The ledger\'s root inside the folder, or "external" (its path is machine-local).'),
    ]


# ---- reading -------------------------------------------------------------------------------------------------------


class _DuplicateKeyError(ValueError):
    pass


class _ConstantError(ValueError):
    pass


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _DuplicateKeyError
    return dict(pairs)


def _no_constant(_name: str) -> Any:
    raise _ConstantError  # NaN, Infinity, -Infinity: not JSON


def read_manifest(data: bytes) -> ManifestRead:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ManifestInvalid(None, "not-utf8")
    try:
        raw: object = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except _DuplicateKeyError:
        return ManifestInvalid(None, "duplicate-key")
    except (ValueError, RecursionError):
        return ManifestInvalid(None, "not-json")
    if not isinstance(raw, dict):
        return ManifestInvalid(None, "not-an-object")
    obj: dict[str, Any] = raw  # pyright: ignore[reportUnknownVariableType] (JSON's own types)
    fmt, min_reader = obj.get("format"), obj.get("min_reader")
    if type(fmt) is not int or fmt < 1:  # a bool is not a format
        return ManifestInvalid("format", "invalid")
    if type(min_reader) is not int or not 1 <= min_reader <= fmt:
        return ManifestInvalid("min_reader", "invalid")
    if min_reader > FORMAT:
        return ManifestNewer(fmt, min_reader)
    try:
        wire = ManifestV1.model_validate(obj)
    except ValidationError as e:
        return ManifestInvalid(str(e.errors()[0]["loc"][0]), "invalid")
    ledger: LedgerPlace = External() if wire.ledger == "external" else Inside(WorkspacePath.parse(wire.ledger.root))
    return Manifest(wire.format, wire.min_reader, LineageId(wire.lineage_id), ledger)
