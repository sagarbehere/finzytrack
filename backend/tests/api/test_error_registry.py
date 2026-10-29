"""The error registry: one entry per code, plain-language parts, and a test through the API for every code (S0046).

Spec: ARCHITECTURE.md §6.1 (the registry in `app.platform.errors` is the single source for codes; each code declares
its class, a `retryable` flag, a plain-language message, the data-safety statement, the next step and a typed
`details` model), §6.5 and EP-27 (what happened, whether the data is safe, what to do next), §6.7 ("every registry
code has at least one test, tagged `@pytest.mark.error_code("CODE")`, that triggers it through the API … a check
fails when a code is unused or untested"), EP-13 (the taxonomy; codes compared, never messages). Each rule sits beside
the planted registry or test tree that must not pass (EP-29).
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Iterable
from pathlib import Path

import pytest

from app.domain.errors import Conflict, ErrorClass, Internal, Invalid, NotFound
from app.platform.errors import ErrorCode, ErrorSpec, error, registry_problems, spec_for, specs

TESTS = Path(__file__).resolve().parents[1]


def tagged_codes(sources: Iterable[str]) -> set[str]:
    """Every code named by a `pytest.mark.error_code("…")` marker in these test sources."""
    out: set[str] = set()
    for text in sources:
        for node in ast.walk(ast.parse(text)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "error_code"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "mark"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                out.add(node.args[0].value)
    return out


def untested(codes: Iterable[str], sources: Iterable[str]) -> list[str]:
    return sorted(set(codes) - tagged_codes(sources))


def _test_sources() -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted(TESTS.rglob("test_*.py"))]


def test_every_registry_code_has_a_tagged_test_through_the_api() -> None:
    assert untested((s.code for s in specs()), _test_sources()) == []


def test_every_tagged_code_is_in_the_registry() -> None:
    assert sorted(tagged_codes(_test_sources()) - {s.code for s in specs()}) == []


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("def test_x(): pass\n", id="no marker"),
        pytest.param('@pytest.mark.inv("WORKSPACE_UNKNOWN")\ndef test_x(): pass\n', id="another marker"),
        pytest.param('# @pytest.mark.error_code("WORKSPACE_UNKNOWN")\n', id="a comment"),
        pytest.param('"""pytest.mark.error_code("WORKSPACE_UNKNOWN")"""\n', id="a docstring"),
        pytest.param('@pytest.mark.error_code("WORKSPACE_UNKNOWNX")\ndef test_x(): pass\n', id="another code"),
    ],
)
def test_a_code_without_a_tagged_test_is_reported(source: str) -> None:
    assert untested(["WORKSPACE_UNKNOWN"], [source]) == ["WORKSPACE_UNKNOWN"]


def test_a_tagged_test_counts() -> None:
    source = '@pytest.mark.error_code("WORKSPACE_UNKNOWN")\ndef test_x(): pass\n'
    assert untested(["WORKSPACE_UNKNOWN"], [source]) == []


def test_the_registry_passes_its_own_check() -> None:
    assert registry_problems(specs()) == []


def test_the_code_enum_and_the_registry_name_the_same_codes() -> None:
    assert sorted(c.value for c in ErrorCode) == sorted(s.code for s in specs())


def test_the_codes_of_architecture_6_1_this_slice_raises_have_their_classes() -> None:
    expected = {
        "VALIDATION_FAILED": ErrorClass.INVALID,
        "ROUTE_NOT_FOUND": ErrorClass.NOT_FOUND,
        "WORKSPACE_UNKNOWN": ErrorClass.NOT_FOUND,
        "WORKSPACE_NOT_OPEN": ErrorClass.CONFLICT,
        "WORKSPACE_NOT_READY": ErrorClass.CONFLICT,
        # S0058 (engine-jobs §11; ARCHITECTURE §6.1 "their class ... join with the engine-jobs slice"): a full queue
        # and an engine that is down are a dependency busy or down; a Cancel is the user's own end of the request.
        "ENGINE_QUEUE_FULL": ErrorClass.UNAVAILABLE,
        "JOB_CANCELLED": ErrorClass.CONFLICT,
        "ENGINE_STOPPED": ErrorClass.UNAVAILABLE,
        "ENGINE_UNAVAILABLE": ErrorClass.UNAVAILABLE,
        # S0058 round 1 (verifier F5): a writing job cut past its commit point, until BL-0151 resolves it from disk
        "CHANGE_OUTCOME_UNKNOWN": ErrorClass.UNAVAILABLE,
        "INTERNAL": ErrorClass.INTERNAL,
    }
    assert {s.code: s.error_class for s in specs()} == expected


def test_a_refusal_that_happens_before_any_handler_says_the_data_is_safe() -> None:
    """Nothing ran: an unknown route, a validation failure, a workspace gate refusal (§6.5: say so only when true)."""
    for code in (
        "VALIDATION_FAILED",
        "ROUTE_NOT_FOUND",
        "WORKSPACE_UNKNOWN",
        "WORKSPACE_NOT_OPEN",
        "WORKSPACE_NOT_READY",
    ):
        spec = spec_for(code)
        assert spec is not None
        assert spec.data_safe is True, code


def test_internal_does_not_claim_the_data_is_safe() -> None:
    """An unexpected exception may have happened anywhere; the edge cannot know the disk equals the pre-image."""
    spec = spec_for("INTERNAL")
    assert spec is not None
    assert spec.data_safe is False


def test_not_ready_is_retryable_and_the_others_are_not() -> None:
    """S0058: §11 offers Try again for a full queue, a stopped engine and an unavailable one; a Cancel is not retried
    for the user."""
    retryable = {"WORKSPACE_NOT_READY", "ENGINE_QUEUE_FULL", "ENGINE_STOPPED", "ENGINE_UNAVAILABLE"}
    assert {s.code for s in specs() if s.retryable} == retryable


def test_error_builds_the_class_the_registry_gives() -> None:
    assert type(error(ErrorCode.VALIDATION_FAILED)) is Invalid
    assert type(error(ErrorCode.WORKSPACE_UNKNOWN)) is NotFound
    assert type(error(ErrorCode.INTERNAL)) is Internal
    from app.platform.errors import WorkspaceNotOpenDetails

    built = error(ErrorCode.WORKSPACE_NOT_OPEN, WorkspaceNotOpenDetails(open_workspace=None, switching=True))
    assert type(built) is Conflict
    assert built.code == "WORKSPACE_NOT_OPEN"


def test_error_refuses_details_that_do_not_belong_to_the_code() -> None:
    from app.platform.errors import WorkspaceNotReadyDetails

    with pytest.raises(TypeError):
        error(ErrorCode.WORKSPACE_NOT_OPEN, WorkspaceNotReadyDetails(phase="WARMING"))
    with pytest.raises(TypeError):
        error(ErrorCode.WORKSPACE_NOT_OPEN)
    with pytest.raises(TypeError):
        error(ErrorCode.WORKSPACE_UNKNOWN, WorkspaceNotReadyDetails(phase="WARMING"))


def test_the_spec_of_an_unknown_code_is_none() -> None:
    assert spec_for("NO_SUCH_CODE") is None


GOOD = ErrorSpec(
    "WORKSPACE_UNKNOWN",
    ErrorClass.NOT_FOUND,
    retryable=False,
    message="Finzytrack does not know this workspace.",
    data_safe=True,
    next_step="Open a workspace from the list.",
    details=None,
)


@dataclasses.dataclass(frozen=True)
class SomeDetails:
    n: int


class NotADataclass:
    pass


@pytest.mark.parametrize(
    ("registry", "problem"),
    [
        pytest.param((GOOD, GOOD), "listed twice", id="a code twice"),
        pytest.param((dataclasses.replace(GOOD, code="workspace_unknown"),), "upper-case", id="a lower-case code"),
        pytest.param((dataclasses.replace(GOOD, code="WORKSPACE UNKNOWN"),), "upper-case", id="a space"),
        pytest.param((dataclasses.replace(GOOD, message=""),), "message", id="no message"),
        pytest.param((dataclasses.replace(GOOD, message="no full stop"),), "message", id="not a sentence"),
        pytest.param((dataclasses.replace(GOOD, next_step=" "),), "next_step", id="no next step"),
        pytest.param((dataclasses.replace(GOOD, details=NotADataclass),), "details", id="details not a dataclass"),
        pytest.param(
            (dataclasses.replace(GOOD, error_class="not_found"),),
            "class",
            id="a class that is not an ErrorClass",
        ),
    ],
)
def test_a_registry_that_breaks_a_rule_is_refused(registry: tuple[ErrorSpec, ...], problem: str) -> None:
    problems = registry_problems(registry)
    assert any(problem in p for p in problems), problems


def test_a_good_registry_passes() -> None:
    assert registry_problems((GOOD, dataclasses.replace(GOOD, code="OTHER_CODE", details=SomeDetails))) == []
