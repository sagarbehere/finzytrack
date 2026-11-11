"""One backend error model (L1-05; EP-13; ARCHITECTURE.md §6.2, §6.3; S0103).

@regression-guard (S0103: `next` meets these rules since S0046; this pins them). The edge's mapping (a well-formed
`DomainError` to its registry status, a malformed one, any other exception and a framework refusal to `INTERNAL` or
`ROUTE_NOT_FOUND`) is tested through the API in `tests/api/test_envelope.py`; the import contract `http-at-the-edge`
keeps every HTTP framework out of the modules below `app.api`. This file holds the two rules of EP-13 an import
contract cannot see, each default-deny with its reviewed sites:

1. **No swallowing** (§6.3). Every handler that catches everything (`except:`, `except Exception`, `except
   BaseException`, alone or in a tuple) is at a reviewed place (`CATCH_ALL`), counted per function. A handler that
   ends with a bare `raise` of what it caught (clean up, then re-raise) is counted apart, as `reraise`.
2. **No string matching on messages** (§6.2). The text of an exception (`str(e)`, `repr(e)`, `format(e)`, an
   f-string of it, `e.args`), where `e` is a name bound by an `except` clause or a parameter annotated as an
   exception, is never compared, searched or matched (`==`, `!=`, `in`, `startswith`, `find`, `re.*`, a `match`
   subject), directly or through a local bound to it. Putting the text into a value (a refusal's detail shown to the
   user) is not matching. The reviewed sites are `TEXT_MATCH`.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass

import pytest

from tests.architecture.loop_calls import SRC, module_name

LOSES_A_LINE = "a log line that raises loses that line, never a decision (S0041)"

CATCH_ALL: dict[tuple[str, str, str], tuple[int, str]] = {
    ("app.api.errors", "Edge.__call__", "swallow"): (
        1,
        "the API edge (§6.3): any exception logged with the request id and answered INTERNAL (re-raised when the "
        "answer had started)",
    ),
    ("app.api.errors", "Edge.__call__", "reraise"): (
        1,
        "the API edge (§6.3): a failed container factory answered INTERNAL, then re-raised for the server's log",
    ),
    ("app.platform.engine_jobs.scheduler", "EngineJobs._loop", "swallow"): (
        1,
        "the scheduler thread's top-level loop (§6.3's sidecar loop): logged `jobs.internal_error`, the loop goes on",
    ),
    ("app.platform.engine_jobs.scheduler", "EngineJobs._handle", "swallow"): (
        1,
        "a request's step on the scheduler thread: logged, the caller gets its fallback, never None",
    ),
    ("app.platform.engine_jobs.scheduler", "EngineJobs._step", "swallow"): (
        1,
        "one effect of a step: logged, the step's other effects still happen (S0058 verifier F1)",
    ),
    ("app.platform.engine_jobs.scheduler", "EngineJobs._joined", "swallow"): (
        1,
        "a queued work's join: logged, the request is not joined",
    ),
    ("app.platform.engine_jobs.scheduler", "EngineJobs._run", "swallow"): (
        1,
        "the job runner (§6.3): logged, the job ends INTERNAL",
    ),
    ("app.platform.engine_jobs.scheduler", "EngineJobs._reset", "swallow"): (
        1,
        "Try again's reset beside the scheduler thread: logged, the engine is tried again anyway",
    ),
    ("app.platform.engine_jobs.scheduler", "EngineJobs._wait_lock", "suppress"): (1, LOSES_A_LINE),
    ("app.platform.engine_jobs.scheduler", "EngineJobs._log_exit", "suppress"): (1, LOSES_A_LINE),
    ("app.platform.engine_jobs.scheduler", "EngineJobs._log_internal", "suppress"): (1, LOSES_A_LINE),
    ("app.platform.adapters.engine_client", "_pump_stderr", "swallow"): (
        1,
        "the engine's stderr pump: a log sink that raises is dropped, so the pump keeps draining the pipe (the "
        "failure cannot be logged to the sink that failed)",
    ),
    ("app.platform.adapters.engine_client", "_Filler._run", "suppress"): (1, LOSES_A_LINE),
    ("app.platform.adapters.engine_client", "EngineClient._effect", "suppress"): (1, LOSES_A_LINE),
    ("app.platform.adapters.engine_client", "EngineClient._spawn", "suppress"): (
        1,
        "the scheduler's spawn report: a receiver that raises must not take the start down; interrupts go on",
    ),
    ("app.platform.adapters.engine_client", "EngineClient._reap", "suppress"): (
        1,
        "the scheduler's end report: a receiver that raises must not take the reaper down",
    ),
    ("app.platform.adapters.engine_client", "EngineClient._call", "reraise"): (
        1,
        "feeds the supervision table `Interrupted`, then re-raises",
    ),
    ("app.mirror.files", "open_mirror", "swallow"): (
        1,
        "closes the connection; only the named outcomes become values (`NotAMirrorError`, `MemoryError`, a SQLite "
        "error `app_read_outcome` names), anything else is re-raised (a swallow by type, never by message)",
    ),
}
"""(module, function, kind) -> (how many such handlers, why)."""

TEXT_MATCH: dict[tuple[str, str], str] = {
    ("app.mirror.query", "_no_such_table"): (
        "SQLite's own message: it reports a missing table only as the generic SQLITE_ERROR, with no code of its own; "
        "the resolver fails closed on any other answer (a read of the name is refused)"
    ),
}

CATCH_ALL_NAMES = frozenset({"Exception", "BaseException"})
TEXT_CALLS = frozenset({"str", "repr", "format", "ascii"})
SEARCH_METHODS = frozenset(
    {"startswith", "endswith", "find", "rfind", "index", "rindex", "count", "partition", "rpartition", "split"}
)
TEXT_METHODS = frozenset({"lower", "upper", "casefold", "strip", "lstrip", "rstrip", "replace", "removeprefix"})
REGEX_FUNCTIONS = frozenset({"search", "match", "fullmatch", "findall", "finditer", "sub", "subn", "split"})


@dataclass(frozen=True, slots=True)
class Found:
    module: str
    function: str
    kind: str  # "swallow", "reraise", "suppress" or "match"
    line: int


def _catches_all(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return True
    names = t.elts if isinstance(t, ast.Tuple) else [t]
    return any(isinstance(n, ast.Name) and n.id in CATCH_ALL_NAMES for n in names)


def _suppresses_all(node: ast.expr) -> bool:
    """`contextlib.suppress(Exception)` (or `BaseException`, among other types): a catch-all without `except`."""
    return (
        isinstance(node, ast.Call)
        and ast.unparse(node.func) in ("contextlib.suppress", "suppress")
        and any(isinstance(a, ast.Name) and a.id in CATCH_ALL_NAMES for a in node.args)
    )


def _reraises(handler: ast.ExceptHandler) -> bool:
    """Every way out of the handler re-raises what it caught: it ends with a bare `raise` (or `raise e`), and nothing
    in it returns, breaks, continues or raises something else (S0103 round 1, verifier F5)."""
    last = handler.body[-1]
    if not (
        isinstance(last, ast.Raise)
        and (last.exc is None or (isinstance(last.exc, ast.Name) and last.exc.id == handler.name))
    ):
        return False
    stack: list[ast.AST] = list(handler.body[:-1])
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        if isinstance(node, ast.Return | ast.Break | ast.Continue):
            return False
        if isinstance(node, ast.Raise) and not (
            node.exc is None or (isinstance(node.exc, ast.Name) and node.exc.id == handler.name)
        ):
            return False
        stack.extend(ast.iter_child_nodes(node))
    return True


def _exception_annotation(node: ast.expr | None) -> bool:
    text = ast.unparse(node) if node is not None else ""
    return any(part.endswith(("Error", "Exception")) for part in text.replace("|", " ").split())


class _Function:
    """One function's scan: the names that hold an exception, the locals that hold its text, the matches."""

    def __init__(self, fn: ast.FunctionDef | ast.AsyncFunctionDef | None) -> None:
        self.exceptions: set[str] = set()
        if fn is not None:
            args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
            self.exceptions = {a.arg for a in args if _exception_annotation(a.annotation)}
        self.texts: set[str] = set()

    def text_of(self, node: ast.expr) -> bool:
        """Whether `node` is the text of an exception (or derived from it by a text method)."""
        if isinstance(node, ast.Name):
            return node.id in self.texts
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in TEXT_CALLS and node.args:
                arg = node.args[0]
                return isinstance(arg, ast.Name) and arg.id in self.exceptions
            if isinstance(f, ast.Attribute) and f.attr in TEXT_METHODS | SEARCH_METHODS:
                return self.text_of(f.value)
            return False
        if isinstance(node, ast.Attribute):
            return node.attr == "args" and isinstance(node.value, ast.Name) and node.value.id in self.exceptions
        if isinstance(node, ast.Subscript):
            return self.text_of(node.value)
        if isinstance(node, ast.NamedExpr):
            return self.text_of(node.value)
        if isinstance(node, ast.JoinedStr):
            return any(
                isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.value.id in self.exceptions
                for v in node.values
            )
        return False

    def matches(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Compare):
            if not any(isinstance(op, ast.Eq | ast.NotEq | ast.In | ast.NotIn) for op in node.ops):
                return False  # `is None` asks whether there is a text, not what it says
            return any(self.text_of(x) for x in [node.left, *node.comparators])
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in SEARCH_METHODS and self.text_of(f.value):
                return True
            if isinstance(f, ast.Attribute) and f.attr == "__contains__" and self.text_of(f.value):
                return True
            if (
                isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Name)
                and f.value.id == "re"
                and f.attr in REGEX_FUNCTIONS
            ):
                return any(self.text_of(a) for a in node.args)
        if isinstance(node, ast.Match):
            return self.text_of(node.subject)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "pop"):
            return any(self.text_of(a) for a in node.args[:1])  # a table keyed on the message (verifier F5)
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and not self.text_of(node.value):
            return self.text_of(node.slice)
        return False


def scan(source: str, module: str) -> list[Found]:
    out: list[Found] = []

    def visit(body: list[ast.stmt], scope: str, fn: _Function) -> None:
        stack: list[ast.AST] = list(reversed(body))
        while stack:
            node = stack.pop()
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = f"{scope}.{node.name}" if scope else node.name
                own = _Function(node if not isinstance(node, ast.ClassDef) else None)
                own.exceptions |= fn.exceptions  # a closure sees the enclosing names
                visit(node.body, inner, own)
                continue
            if isinstance(node, ast.ExceptHandler):
                if node.name:
                    fn.exceptions.add(node.name)
                if _catches_all(node):
                    out.append(Found(module, scope, "reraise" if _reraises(node) else "swallow", node.lineno))
            if isinstance(node, ast.With) and any(_suppresses_all(i.context_expr) for i in node.items):
                out.append(Found(module, scope, "suppress", node.lineno))
            if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None and fn.text_of(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                fn.texts.update(t.id for t in targets if isinstance(t, ast.Name))
            if isinstance(node, ast.NamedExpr) and fn.text_of(node.value):
                fn.texts.add(node.target.id)
            if fn.matches(node):
                out.append(Found(module, scope, "match", getattr(node, "lineno", 0)))
            stack.extend(reversed(list(ast.iter_child_nodes(node))))

    visit(ast.parse(source).body, "", _Function(None))
    return out


def tree_findings() -> list[Found]:
    out: list[Found] = []
    for path in sorted((SRC / "app").rglob("*.py")):
        out.extend(scan(path.read_text(), module_name(path, SRC)))
    return out


def problems(found: list[Found]) -> list[str]:
    out: list[str] = []
    counts = Counter((f.module, f.function, f.kind) for f in found if f.kind != "match")
    for key, n in sorted(counts.items()):
        want = CATCH_ALL.get(key)
        if want is None:
            out.append(f"{key[0]}:{key[1]}: {n} catch-all handler(s) ({key[2]}) outside the reviewed places (§6.3)")
        elif want[0] != n:
            out.append(f"{key[0]}:{key[1]}: {n} catch-all handler(s) ({key[2]}), {want[0]} reviewed")
    out.extend(f"stale catch-all review: {k}" for k in sorted(CATCH_ALL.keys() - counts.keys()))
    matched = {(f.module, f.function) for f in found if f.kind == "match"}
    out.extend(
        f"{f.module}:{f.function} line {f.line}: matches an exception's text (EP-13: compare codes)"
        for f in found
        if f.kind == "match" and (f.module, f.function) not in TEXT_MATCH
    )
    out.extend(f"stale text-match review: {k}" for k in sorted(TEXT_MATCH.keys() - matched))
    return sorted(out)


def test_catch_alls_and_text_matches_are_only_at_the_reviewed_places() -> None:
    assert problems(tree_findings()) == []


def test_every_reviewed_entry_has_a_reason() -> None:
    assert all(why.strip() for _, why in CATCH_ALL.values())
    assert all(why.strip() for why in TEXT_MATCH.values())


def handler(caught: str, *lines: str) -> str:
    """`def f()` with `g()` in a `try` and `lines` in its `except <caught>:` handler."""
    return "def f():\n    try:\n        g()\n    except " + caught + ":\n" + "".join(f"        {x}\n" for x in lines)


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        (handler("Exception", "pass"), "swallow"),
        (handler("", "return None").replace("except :", "except:"), "swallow"),
        (handler("(ValueError, BaseException) as e", "log(e)"), "swallow"),
        (handler("BaseException as e", "raise RuntimeError() from e"), "swallow"),
        (handler("BaseException", "close()", "raise"), "reraise"),
        # S0103 round 1 (verifier F5): one path swallows, so it is no re-raise.
        (
            handler(
                "BaseException as e", "close()", "if isinstance(e, KeyboardInterrupt):", "    return None", "raise"
            ),
            "swallow",
        ),
        (handler("BaseException", "raise RuntimeError('x')", "raise"), "swallow"),
        (handler("OSError as e", "return MESSAGES.get(str(e))"), "match"),
        (handler("OSError as e", "return MESSAGES[str(e)]"), "match"),
        ("import contextlib\n\ndef f():\n    with contextlib.suppress(Exception):\n        g()\n", "suppress"),
        (
            "from contextlib import suppress\n\ndef f():\n    with suppress(OSError, BaseException):\n        g()\n",
            "suppress",
        ),
        (handler("ValueError as e", "if 'bad' in str(e):", "    h()"), "match"),
        (handler("ValueError as e", "return str(e).startswith('x')"), "match"),
        (handler("OSError as e", "return e.args[0] == 'gone'"), "match"),
        (handler("OSError as e", "m = str(e).lower()", "return m == 'x'"), "match"),
        (handler("OSError as e", "return f'{e}' != 'x'"), "match"),
        ("import re\n\n" + handler("OSError as e", "return re.search('x', repr(e))"), "match"),
        ("def f(err: ValueError) -> bool:\n    return 'x' in str(err)\n", "match"),
        ("def f(err: 'sqlite3.Error | None') -> bool:\n    return str(err).endswith('x')\n", "match"),
        (handler("OSError as e", "match str(e):", "    case 'x':", "        pass"), "match"),
        (handler("OSError as e", "if (t := str(e)) == 'x':", "    h()"), "match"),
    ],
)
def test_planted_shapes_are_found(source: str, kind: str) -> None:
    found = scan(source, "app.planted")
    assert kind in [f.kind for f in found]
    assert problems(found + tree_findings()) != []


@pytest.mark.parametrize(
    "source",
    [
        "def f():\n    try:\n        g()\n    except ValueError as e:\n        return Refused('invalid', str(e))\n",
        "def f():\n    try:\n        g()\n    except OSError as e:\n        return e.errno == 2\n",
        "def f(e: sqlite3.Error) -> bool:\n    return e.sqlite_errorcode == 1\n",
        "def f(text: str) -> bool:\n    return 'x' in str(text)\n",
        "def f():\n    try:\n        g()\n    except (ValueError, OSError):\n        return None\n",
    ],
)
def test_codes_and_text_as_a_value_pass(source: str) -> None:
    assert scan(source, "app.planted") == []


def test_a_second_catch_all_in_a_reviewed_function_fails() -> None:
    found = [*tree_findings(), Found("app.api.errors", "Edge.__call__", "swallow", 1)]
    assert any("2 catch-all handler(s)" in p for p in problems(found))


def test_a_reviewed_place_that_is_gone_is_stale() -> None:
    found = [f for f in tree_findings() if f.function != "_pump_stderr"]
    assert any(p.startswith("stale catch-all review") for p in problems(found))
