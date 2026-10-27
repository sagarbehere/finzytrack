"""The offline OpenAPI export and the contract's own rules (S0046).

Spec: ADR-0038 ("`backend/scripts/export_openapi.py` imports the app with a no-I/O container and writes
`contracts/openapi.json` offline; no server is started"), its amendment ("response models carry no server-side
defaults, so every output field is required in the generated TypeScript types (one schema per model; FastAPI's
separate input/output schemas are not used)"), ARCHITECTURE.md §4.1 (sorted keys, stable output; one envelope for
every endpoint, so no FastAPI default error schema appears), §6.2 (the registry is exported into the spec: an
`ErrorCode` enum and a discriminated union of per-code details). Each rule of `contract_problems` sits beside the
planted contract that must not pass (EP-29).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.api import export_openapi
from app.api.contract import contract_problems, render
from app.platform.errors import specs

BACKEND = Path(__file__).resolve().parents[2]


def test_the_export_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    class SocketOpened(AssertionError):
        """The export opened a socket."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise SocketOpened

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_server", refuse)
    assert export_openapi()["openapi"].startswith("3.")


def test_the_export_is_stable_and_sorted() -> None:
    first, second = render(export_openapi()), render(export_openapi())
    assert first == second
    data = json.loads(first)
    assert next(iter(data)) == "$comment"
    assert "GENERATED" in data["$comment"]
    rest = {k: v for k, v in data.items() if k != "$comment"}
    assert json.dumps(rest, sort_keys=True, indent=2) == json.dumps(rest, indent=2)  # keys already sorted


def test_the_export_script_prints_what_render_returns() -> None:
    """The script is what `scripts/check_generated.py` runs; run from the repo root, isolated (`-I`)."""
    out = subprocess.run(  # noqa: S603 (fixed argv: this interpreter running the script under test)
        [sys.executable, "-I", str(BACKEND / "scripts" / "export_openapi.py"), "--stdout"],
        cwd=BACKEND.parent,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert out == render(export_openapi())


def _export_script() -> Any:
    spec = importlib.util.spec_from_file_location("export_openapi_script", BACKEND / "scripts" / "export_openapi.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_export_script_refuses_a_contract_that_breaks_a_rule(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _export_script()
    broken = _doc()
    _first_op(broken).pop("x-ft-scope")
    monkeypatch.setattr(script, "export_openapi", lambda: broken)
    monkeypatch.setattr(script, "OUT", tmp_path / "openapi.json")
    assert script.main([]) == 1
    assert script.main(["--stdout"]) == 1
    assert not (tmp_path / "openapi.json").exists()
    assert capsys.readouterr().out == ""


def test_the_export_script_writes_a_good_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = _export_script()
    monkeypatch.setattr(script, "OUT", tmp_path / "openapi.json")
    assert script.main([]) == 0
    assert (tmp_path / "openapi.json").read_text(encoding="utf-8") == render(export_openapi())


def test_the_product_contract_passes_its_own_rules() -> None:
    assert contract_problems(export_openapi()) == []


def test_the_contract_lists_every_registry_code_once_in_the_error_union() -> None:
    doc = export_openapi()
    union = doc["components"]["schemas"]["ErrorEnvelope"]["properties"]["error"]
    mapping = union["discriminator"]["mapping"]
    assert union["discriminator"]["propertyName"] == "code"
    assert sorted(mapping) == sorted(s.code for s in specs())
    assert len(union["oneOf"]) == len(mapping)


def test_the_workspace_not_open_details_are_typed_in_the_contract() -> None:
    schemas = export_openapi()["components"]["schemas"]
    details = schemas["WorkspaceNotOpenDetails"]
    assert sorted(details["required"]) == ["open_workspace", "switching"]
    assert details["properties"]["switching"]["type"] == "boolean"


def _doc() -> dict[str, Any]:
    return copy.deepcopy(export_openapi())


def _first_op(doc: dict[str, Any]) -> dict[str, Any]:
    return next(iter(doc["paths"]["/health"].values()))


def _plant_optional_response_field(doc: dict[str, Any]) -> None:
    doc["components"]["schemas"]["Meta"]["required"].remove("request_id")


def _plant_optional_nested_field(doc: dict[str, Any]) -> None:
    doc["components"]["schemas"]["WorkspaceNotOpenDetails"]["required"].remove("switching")


def _plant_no_scope(doc: dict[str, Any]) -> None:
    _first_op(doc).pop("x-ft-scope")


def _plant_no_writes(doc: dict[str, Any]) -> None:
    _first_op(doc).pop("x-ft-writes")


def _plant_default_error_schema(doc: dict[str, Any]) -> None:
    doc["components"]["schemas"]["HTTPValidationError"] = {"type": "object"}


def _plant_output_schema(doc: dict[str, Any]) -> None:
    doc["components"]["schemas"]["Probe-Output"] = {"type": "object"}


def _plant_no_error_envelope(doc: dict[str, Any]) -> None:
    _first_op(doc)["responses"].pop("default")


def _plant_bare_success(doc: dict[str, Any]) -> None:
    _first_op(doc)["responses"]["200"] = {"content": {"application/json": {"schema": {"type": "object"}}}}


def _plant_unwrapped_success(doc: dict[str, Any]) -> None:
    """The success answer names a schema, but the data itself instead of the envelope around it."""
    _first_op(doc)["responses"]["200"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/HealthData"
    }


type Plant = Callable[[dict[str, Any]], None]


@pytest.mark.parametrize(
    ("plant", "problem"),
    [
        pytest.param(_plant_no_scope, "x-ft-scope", id="an operation without its scope"),
        pytest.param(_plant_no_writes, "x-ft-writes", id="an operation without writes"),
        pytest.param(_plant_optional_response_field, "Meta.request_id", id="an optional response field"),
        pytest.param(_plant_optional_nested_field, "WorkspaceNotOpenDetails.switching", id="optional, nested"),
        pytest.param(_plant_default_error_schema, "HTTPValidationError", id="FastAPI's default error schema"),
        pytest.param(_plant_output_schema, "Probe-Output", id="a separate output schema"),
        pytest.param(_plant_no_error_envelope, "default", id="an operation without the error envelope"),
        pytest.param(_plant_bare_success, "envelope", id="a success answer outside the envelope"),
        pytest.param(_plant_unwrapped_success, "envelope", id="a success answer naming the bare data schema"),
    ],
)
def test_a_contract_that_breaks_a_rule_is_refused(plant: Plant, problem: str) -> None:
    doc = _doc()
    plant(doc)
    problems = contract_problems(doc)
    assert any(problem in p for p in problems), problems
