"""Each git hook's checks, pinned (S0033, seeded-defect run 1 E03, E04).

A hook that silently loses a check passes every other test: the checks it runs still work, they are just no longer
run. So each hook's code is pinned here, line by line, except its messages (`echo` lines) and comments: every
`run` line (label and command), every line that gates, loops over or skips checks, every variable a check reads, and
the lines that decide the exit status. Adding, removing or changing a check means changing the pinned list in the
same commit, where review sees it.

The pinned code itself must keep its shape, so a loosening cannot land by editing the hook and the pin together:
each check is wrapped in `run`, no check is chained with `;`, no failure is discarded (`|| true`, `; true`, `|| :`)
outside a variable assignment, nothing exits early (`exit 0`, `return`, `trap`, `set +e`, `alias`), only the `run`
function is defined (exactly as pinned), there is no here-document (`<<` can turn lines into text; the here-string
`<<<` is allowed), `fail=0` is set once before the first check, and pre-commit and pre-push end with `exit "$fail"`.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / ".githooks"

PRE_COMMIT_RUN = '''run() {  # run <label> <command...>
  local label="$1"; shift
  if ! "$@"; then echo "pre-commit: FAILED: $label" >&2; fail=1; fi
}'''
PRE_PUSH_RUN = 'run() { local label="$1"; shift; if ! "$@"; then echo "pre-push: FAILED: $label" >&2; fail=1; fi; }'

PINNED: dict[str, list[str]] = {
    "pre-commit": [
        'set -euo pipefail',
        'export GIT_NO_REPLACE_OBJECTS=1  # S0031 verifier F2: read the objects git stores, not replacements',
        'root="$(git rev-parse --show-toplevel)"',
        'cd "$root"',
        'start=$(date +%s)',
        'fail=0',
        'run() {  # run <label> <command...>',
        'local label="$1"; shift',
        'if ! "$@"; then echo "pre-commit: FAILED: $label" >&2; fail=1; fi',
        '}',
        'run "staged-file guard" python3 scripts/check_staged.py',
        'run "privacy scan" python3 scripts/privacy_scan.py --staged --quiet',
        'run "generated headers" python3 scripts/check_generated.py --staged',
        'staged="$(git diff --cached --name-only --diff-filter=ACMR)"',
        'py_files="$(printf \'%s\\n\' "$staged" | grep -E \'^backend/.*\\.py$\' || true)"',
        'if [[ -n "$py_files" ]]; then',
        'if [[ -x backend/.venv/bin/ruff ]]; then',
        'run "ruff format" backend/.venv/bin/ruff format --check --config backend/ruff.toml $py_files',
        'run "ruff lint" backend/.venv/bin/ruff check --config backend/ruff.toml $py_files',
        'else',
        'fi',
        'fi',
        'fe_files="$(printf \'%s\\n\' "$staged" | grep -E \'^frontend/.*\\.(ts|tsx|vue|js|mjs|css|json)$\' || '
        'true)"',
        'if [[ -n "$fe_files" ]]; then',
        'if [[ -x frontend/node_modules/.bin/prettier && -x frontend/node_modules/.bin/eslint ]]; then',
        'rel="$(printf \'%s\\n\' "$fe_files" | sed \'s#^frontend/##\')"',
        'run "prettier" bash -c "cd frontend && node_modules/.bin/prettier --check $(echo $rel)"',
        'lint="$(printf \'%s\\n\' "$rel" | grep -E \'\\.(ts|tsx|vue|js|mjs)$\' || true)"',
        '[[ -n "$lint" ]] && run "eslint" bash -c "cd frontend && node_modules/.bin/eslint --max-warnings 0 '
        '$(echo $lint)"',
        'else',
        'fi',
        'fi',
        'elapsed=$(( $(date +%s) - start ))',
        'if (( elapsed > 30 )); then',
        'fi',
        'exit "$fail"',
    ],
    "pre-push": [
        'set -euo pipefail',
        'export GIT_NO_REPLACE_OBJECTS=1',
        'root="$(git rev-parse --show-toplevel)"',
        'cd "$root"',
        'remote_name="${1:-}"',
        'remote_url="${2:-}"',
        'if [[ "$(git config --bool finzytrack.allowPush || echo false)" != "true" ]]; then',
        'exit 1',
        'fi',
        'refs="$(cat)"',
        'if ! ranges="$(printf \'%s\\n\' "$refs" | python3 scripts/check_push.py "$remote_name" '
        '"$remote_url")"; then',
        'exit 1',
        'fi',
        'head_at_start="$(git rev-parse HEAD)"',
        'fail=0',
        'run() { local label="$1"; shift; if ! "$@"; then echo "pre-push: FAILED: $label" >&2; fail=1; fi; }',
        'while IFS= read -r range; do',
        '[[ -n "$range" ]] || continue',
        'if [[ "$range" == tag\\ * ]]; then',
        'run "privacy scan (pushed tag message ${range#tag })" python3 scripts/privacy_scan.py --tag-message '
        '"${range#tag }" --quiet',
        'continue',
        'fi',
        'run "staged-file guard (pushed commits $range)" python3 scripts/check_staged.py --range "$range"',
        'run "privacy scan (pushed commits and messages $range)" python3 scripts/privacy_scan.py --range '
        '"$range" --quiet',
        'run "deferred backlog items (pushed commits $range)" python3 scripts/check_deferrals.py --range "$range"',
        'done <<< "$ranges"',
        'run "staged-file guard (all tracked files)" bash -c "set -o pipefail; git ls-files -z | xargs -0 '
        'python3 scripts/check_staged.py --paths"',
        'run "check-script meta-tests" python3 -m unittest discover -s scripts/tests -q',
        'run "privacy scan (all tracked files)" python3 scripts/privacy_scan.py --all --quiet',
        'run "invariants" python3 scripts/check_invariants.py',
        'run "capability map" python3 scripts/check_capabilities.py',
        'run "generated artifacts" python3 scripts/check_generated.py --all',
        'uv_bin="$(command -v uv || echo "$HOME/.local/bin/uv")"',
        'run "write corpus" python3 scripts/check_write_corpus.py',
        'run "oracle tests" "$uv_bin" run --project scripts/oracle --locked python -m unittest discover -s '
        'scripts/oracle/tests -q',
        'upstream="$(git rev-parse --verify --quiet \'@{u}\' || echo none)"',
        'run "cargo 14-day rule (new entries verified)" python3 scripts/check_cargo_age.py --verify-since '
        '"$upstream"',
        'if [[ -f backend/pyproject.toml ]]; then',
        'run "ruff format (backend)" bash -c "cd backend && .venv/bin/ruff format --check --config ruff.toml ."',
        'run "ruff lint (backend)" bash -c "cd backend && .venv/bin/ruff check --config ruff.toml ."',
        'run "pyright" bash -c "cd backend && .venv/bin/pyright"',
        'run "import contracts" bash -c "cd backend && .venv/bin/lint-imports"',
        'run "declared dependencies" bash -c "cd backend && .venv/bin/deptry src"',
        'run "backend tests" bash -c "cd backend && .venv/bin/pytest -q -x"',
        'fi',
        'if [[ -f shell/src-tauri/Cargo.toml ]]; then',
        'cargo_bin="$(command -v cargo || echo "$HOME/.cargo/bin/cargo")"',
        'run "rustfmt" bash -c "cd shell/src-tauri && $cargo_bin fmt --check"',
        'run "clippy" bash -c "cd shell/src-tauri && $cargo_bin clippy --all-targets --locked --quiet -- -D '
        'warnings"',
        'run "shell unit tests" bash -c "cd shell/src-tauri && $cargo_bin test --locked --quiet"',
        'fi',
        'if [[ -f frontend/package.json ]]; then',
        'run "prettier (frontend)" bash -c "cd frontend && node_modules/.bin/prettier --check ."',
        'run "eslint (frontend)" bash -c "cd frontend && node_modules/.bin/eslint --max-warnings 0 ."',
        'run "vue-tsc" bash -c "cd frontend && node_modules/.bin/vue-tsc --noEmit"',
        'run "frontend unit tests" bash -c "cd frontend && node_modules/.bin/vitest run --allowOnly=false"',
        'run "unused code and dependencies (knip)" bash -c "cd frontend && node_modules/.bin/knip"',
        'fi',
        'run "tree unchanged during the checks" python3 scripts/check_push.py --still "$head_at_start"',
        'exit "$fail"',
    ],
    "pre-merge-commit": [
        'set -euo pipefail',
        'exec "$(dirname "$0")/pre-commit" "$@"',
    ],
    "commit-msg": [
        'set -euo pipefail',
        'export GIT_NO_REPLACE_OBJECTS=1',
        'cd "$(git rev-parse --show-toplevel)"',
        'if ! python3 scripts/privacy_scan.py --message "$1" --quiet; then',
        'exit 1',
        'fi',
    ],
}
RUN_DEFINITION = {"pre-commit": PRE_COMMIT_RUN, "pre-push": PRE_PUSH_RUN}

ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# A line that calls a check: it must be a `run` line, or the `if ! <check>; then ... exit 1` of a gate that stops the
# hook (check_push.py in pre-push, the message scan in commit-msg), or a tool-presence test `if [[ -x ... ]]`.
# Read on the line without its quoted text, so a message naming a tool is not a call and `bash -c "..."` is one.
CHECK_CALL = re.compile(r"\bpython3?\b|\.venv/bin/|node_modules/\.bin/|\$cargo_bin|\$uv_bin|\b(?:ba)?sh\b|\bxargs\b")
FUNCTION = re.compile(r"^(?:function\s+)?[A-Za-z_][A-Za-z0-9_-]*\s*\(\s*\)")


def code_lines(text: str) -> list[str]:
    """The hook's lines, stripped, without blank lines and whole-line comments."""
    return [s for s in (line.strip() for line in text.splitlines()) if s and not s.startswith("#")]


def unquoted(line: str) -> str:
    """The line with quoted text and trailing comments removed (a `;` inside `bash -c "..."` is the command's)."""
    line = re.sub(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'', '""', line)
    return re.sub(r"\s#.*$", "", line)


def problems(name: str, text: str, pin: list[str] | None = None) -> list[str]:
    """What is wrong with hook `name` whose text is `text`, against `pin` (default: PINNED[name])."""
    expected = PINNED[name] if pin is None else pin
    out: list[str] = []
    lines = code_lines(text)
    pinned = [s for s in lines if not s.startswith("echo ")]
    if pinned != expected:
        missing = [s for s in expected if s not in pinned]
        extra = [s for s in pinned if s not in expected]
        out.append(f"{name}: the code differs from the pinned lines (missing {missing}, unexpected {extra}, or the "
                   "order changed); update PINNED in this test in the same change if that is intended")
    if "set -euo pipefail" not in lines:
        out.append(f"{name}: `set -euo pipefail` is missing")
    if name in RUN_DEFINITION:
        if text.count(RUN_DEFINITION[name]) != 1:
            out.append(f"{name}: the `run` function differs from the pinned definition")
        if lines[-1] != 'exit "$fail"':
            out.append(f'{name}: the last line must be `exit "$fail"`')
        fails = [i for i, s in enumerate(lines) if re.search(r"(?<![\w$])fail=0\b", unquoted(s))]
        first_run = next((i for i, s in enumerate(lines) if s.startswith("run ") or " run " in s), len(lines))
        if len(fails) != 1 or fails[0] > first_run:
            out.append(f"{name}: `fail=0` must be set once, before the first check")
    functions = [s for s in lines if FUNCTION.match(s)]
    if functions != ([RUN_DEFINITION[name].splitlines()[0]] if name in RUN_DEFINITION else []):
        out.append(f"{name}: only the `run` function may be defined: {functions}")
    for s in lines:
        bare = unquoted(s)
        words = bare.split()
        if re.search(r"\bexit\s+0\b|\breturn\b|\btrap\b|\balias\b|\bset\s+\+[a-z]*e", bare):
            out.append(f"{name}: {s!r} can end or weaken the hook early")
        if re.search(r"(?<!<)<<(?!<)", bare):
            out.append(f"{name}: {s!r} starts a here-document, which can hide lines as text")
        if not ASSIGNMENT.match(s) and re.search(r"\|\|\s*(?:true|:)(?![\w-])|;\s*(?:true|:)(?![\w-])", bare):
            out.append(f"{name}: {s!r} discards a failure")
        if CHECK_CALL.search(bare) and not s.startswith(("run ", "if ! ", "if [[ -x ")) and " run " not in bare:
            out.append(f"{name}: {s!r} runs a check outside `run`")
        if words and words[0] == "run" and ";" in bare:
            out.append(f"{name}: {s!r} chains a second command after a check")
    return out


class HookChecksArePinned(unittest.TestCase):
    def test_the_hooks_match_their_pinned_checks(self) -> None:
        self.assertEqual(sorted(p.name for p in HOOKS.iterdir() if p.is_file()), sorted(PINNED))
        for name in PINNED:
            with self.subTest(hook=name):
                self.assertEqual(problems(name, (HOOKS / name).read_text()), [])
                # git skips a hook that is not executable, with only a hint on the terminal
                self.assertTrue(os.access(HOOKS / name, os.X_OK), f"{name} is not executable")

    def test_loosened_hooks_are_refused(self) -> None:
        pre_push = (HOOKS / "pre-push").read_text()
        pre_commit = (HOOKS / "pre-commit").read_text()
        commit_msg = (HOOKS / "commit-msg").read_text()
        merge = (HOOKS / "pre-merge-commit").read_text()
        inv = 'run "invariants" python3 scripts/check_invariants.py\n'
        scan = 'run "privacy scan" python3 scripts/privacy_scan.py --staged --quiet'
        cases = {
            "E03 invariants line removed": ("pre-push", pre_push.replace(inv, "")),
            "E04 privacy scan chained with ; true": ("pre-commit", pre_commit.replace(
                scan, "python3 scripts/privacy_scan.py --staged --quiet; true")),
            "check kept, command swapped": ("pre-push", pre_push.replace(inv, 'run "invariants" true\n')),
            "a check or-true": ("pre-push", pre_push.replace(inv, inv.rstrip("\n") + " || true\n")),
            "a check then ; true": ("pre-push", pre_push.replace(inv, inv.rstrip("\n") + "; true\n")),
            "a check unwrapped": ("pre-push", pre_push.replace(inv, "python3 scripts/check_invariants.py\n")),
            "early exit 0": ("pre-push", pre_push.replace("fail=0\n", "fail=0\nexit 0\n")),
            "fail reset before the end": ("pre-push", pre_push.replace('\nexit "$fail"', '\nfail=0\nexit "$fail"')),
            "last line exits 0": ("pre-commit", pre_commit.replace('exit "$fail"', "exit 0")),
            "run never fails": ("pre-push", pre_push.replace("fail=1; fi; }", "fi; }")),
            "run redefined later": ("pre-push", pre_push.replace(inv, "run() { :; }\n" + inv)),
            "a gate made false": ("pre-push", pre_push.replace("if [[ -f backend/pyproject.toml ]]; then",
                                                               "if [[ -f backend/pyproject.tomlx ]]; then")),
            "checks wrapped in if false": ("pre-push", pre_push.replace(inv, "if false; then\n" + inv + "fi\n")),
            "checks hidden in a here-document": ("pre-push", pre_push.replace(inv, ": <<'X'\n" + inv + "X\n")),
            "a check in an unused function": ("pre-push", pre_push.replace(inv, "later() {\n" + inv + "}\n")),
            "set -e dropped": ("pre-commit", pre_commit.replace("set -euo pipefail\n", "")),
            "set +e added": ("pre-push", pre_push.replace("fail=0\n", "fail=0\nset +e\n")),
            "an exit trap": ("pre-push", pre_push.replace("fail=0\n", "fail=0\ntrap 'exit 0' EXIT\n")),
            "a check moved out of order": ("pre-push", pre_push.replace(inv, "").replace(
                'run "tree unchanged', inv + 'run "tree unchanged')),
            "commit-msg scan removed": ("commit-msg", commit_msg.replace(
                'if ! python3 scripts/privacy_scan.py --message "$1" --quiet; then', "if false; then")),
            "commit-msg scan or-true": ("commit-msg", commit_msg.replace(
                '--message "$1" --quiet; then', '--message "$1" --quiet || true; then')),
            "pre-merge-commit no longer runs pre-commit": ("pre-merge-commit", merge.replace(
                'exec "$(dirname "$0")/pre-commit" "$@"', "exit 0")),
        }
        for label, (name, text) in cases.items():
            with self.subTest(label):
                self.assertNotEqual(text, (HOOKS / name).read_text(), "the mutation did not apply")
                self.assertNotEqual(problems(name, text), [])
        # The same loosenings with the pin updated to match: the shape rules still refuse them. (Removing, reordering
        # or changing a check with the pin updated is a visible change to this file, which review reads.)
        shaped = ("E04 privacy scan chained with ; true", "a check or-true", "a check then ; true", "a check unwrapped",
                  "early exit 0", "fail reset before the end", "last line exits 0", "run never fails",
                  "run redefined later", "checks hidden in a here-document", "a check in an unused function",
                  "set -e dropped", "set +e added", "an exit trap", "commit-msg scan or-true",
                  "pre-merge-commit no longer runs pre-commit", "a check through bash -c outside run")
        cases["a check through bash -c outside run"] = ("pre-push", pre_push.replace(
            inv, 'bash -c "python3 scripts/check_invariants.py || true"\n'))
        for label in shaped:
            name, text = cases[label]
            with self.subTest(f"{label}, pin updated"):
                pin = [s for s in code_lines(text) if not s.startswith("echo ")]
                self.assertNotEqual(problems(name, text, pin), [])


if __name__ == "__main__":
    unittest.main()
