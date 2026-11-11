"""L11-03: logging is configured through injected callbacks, never by a settings change (ADR-0039, ADR-0041; INV-11;
S0103).

@regression-guard (S0103: `next` meets the rule since S0039; this pins it). The risk (review:L11, S0039 journal): a
tenant's settings change reconfigures the process-wide logger, so one user's choice changes every user's log. The
rule as built: the composition root builds the one `EventLog` (`app.main.open_log`) and routes the rest of the
process into it once (`capture_process`, in `app.main.main`); after that nothing reconfigures logging. What may
change while the app runs is read through a callback on every line (`EventLog(debug=...)`, held by
`tests/contract/test_log_redaction.py::test_debug_mode_is_read_per_line`); the live log level of CAP-SET-LOGGING
(M10) must come the same way. The `one-logger` import contract keeps `logging` and `structlog` inside
`app.platform.observability`. This file holds, default-deny:

- every call or assignment that configures logging (levels, handlers, filters, formatters, structlog's or the
  stdlib's global configuration, the process's exception hooks) is in `EventLog.__init__`, `capture_process` (with
  its undo) or `close` (`CONFIGURES`);
- `EventLog` is built and `capture_process` called only by the composition root (`BUILDERS`).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

import pytest

from tests.architecture.loop_calls import SRC, module_name

pytestmark = pytest.mark.inv("INV-11")

CONFIG_METHODS = frozenset(
    {
        "setLevel", "addHandler", "removeHandler", "addFilter", "removeFilter", "setFormatter", "basicConfig",
        "dictConfig", "fileConfig", "captureWarnings", "disable", "getLogger", "configure", "configure_once",
        "reset_defaults", "setLoggerClass", "setLogRecordFactory",
    }
)  # fmt: skip
CONFIG_ATTRIBUTES = frozenset(
    {"propagate", "level", "disabled", "handlers", "filters", "excepthook", "unraisablehook", "lastResort"}
)

LOG = "app.platform.observability.log"
CONFIGURES = frozenset(
    (LOG, f)
    for f in ("EventLog.__init__", "EventLog.capture_process", "EventLog.capture_process.release", "EventLog.close")
)
BUILDERS: dict[tuple[str, str, str], str] = {
    ("app.main", "open_log", "EventLog"): "the composition root builds the one log, before anything logs",
    ("app.main", "main", "capture_process"): "once per process, right after the log is built",
}


@dataclass(frozen=True, slots=True)
class Found:
    module: str
    function: str
    what: str
    line: int


def scan(source: str, module: str) -> list[Found]:
    out: list[Found] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(child, f"{scope}.{child.name}" if scope else child.name)
                continue
            if isinstance(child, ast.Call):
                name = child.func.attr if isinstance(child.func, ast.Attribute) else getattr(child.func, "id", "")
                if name in CONFIG_METHODS:
                    out.append(Found(module, scope, name, child.lineno))
                if name in ("EventLog", "capture_process"):
                    out.append(Found(module, scope, name, child.lineno))
            if isinstance(child, ast.Assign | ast.AugAssign | ast.AnnAssign):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for t in targets:
                    for el in t.elts if isinstance(t, ast.Tuple) else [t]:
                        if isinstance(el, ast.Attribute) and el.attr in CONFIG_ATTRIBUTES:
                            out.append(Found(module, scope, f"{el.attr} =", child.lineno))
            visit(child, scope)

    visit(ast.parse(source), "")
    return out


def tree_findings() -> list[Found]:
    out: list[Found] = []
    for path in sorted((SRC / "app").rglob("*.py")):
        out.extend(scan(path.read_text(), module_name(path, SRC)))
    return out


def problems(found: list[Found]) -> list[str]:
    out: list[str] = []
    builders_seen: set[tuple[str, str, str]] = set()
    for f in found:
        if f.what in ("EventLog", "capture_process"):
            key = (f.module, f.function, f.what)
            builders_seen.add(key)
            if key not in BUILDERS:
                out.append(f"{f.module}:{f.function} line {f.line}: {f.what} outside the composition root")
        elif (f.module, f.function) not in CONFIGURES:
            out.append(f"{f.module}:{f.function} line {f.line}: {f.what} configures logging after the start")
    out.extend(f"stale builder: {k}" for k in sorted(BUILDERS.keys() - builders_seen))
    return sorted(out)


def test_logging_is_configured_only_where_the_log_is_built() -> None:
    assert problems(tree_findings()) == []


def test_the_scan_sees_the_logs_own_configuration() -> None:
    """Not vacuous: the handler, the root handler and the hooks of `EventLog` are found."""
    whats = {f.what for f in tree_findings() if f.module == LOG}
    assert {"addHandler", "setFormatter", "captureWarnings", "getLogger", "excepthook =", "propagate ="} <= whats


@pytest.mark.parametrize(
    "source",
    [
        "def on_settings(log, level):\n    log._logger.setLevel(level)\n",
        "import logging\n\ndef apply(cfg):\n    logging.basicConfig(level=cfg.level)\n",
        "import logging.config\n\ndef apply(cfg):\n    logging.config.dictConfig(cfg)\n",
        "import structlog\n\ndef apply():\n    structlog.configure(processors=[])\n",
        "import logging\n\ndef quiet():\n    logging.getLogger('uvicorn').propagate = False\n",
        "import sys\n\ndef hook(f):\n    sys.excepthook = f\n",
        "def mute(handler):\n    handler.addFilter(lambda r: False)\n",
        "import logging\n\ndef off():\n    logging.disable(logging.INFO)\n",
        "def switch(log):\n    log._handler.level = 10\n",
        "from app.platform.observability import EventLog\n\ndef per_tenant(p, **kw):\n    return EventLog(p, **kw)\n",
        "def again(log):\n    log.capture_process()\n",
    ],
)
def test_a_reconfiguration_after_the_start_is_refused(source: str) -> None:
    found = scan(source, "app.workspaces.settings")
    assert found
    assert problems(found + tree_findings()) != []


def test_a_builder_that_is_gone_is_stale() -> None:
    found = [f for f in tree_findings() if f.what != "capture_process"]
    assert "stale builder: ('app.main', 'main', 'capture_process')" in problems(found)
