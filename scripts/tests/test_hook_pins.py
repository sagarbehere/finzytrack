r"""Each git hook, pinned byte for byte (S0033, seeded-defect run 1 E03, E04; verifier F1, R2-1, R2-2).

A hook that silently loses a check passes every other test: the checks it runs still work, they are just no longer
run. So each hook is pinned whole: its bytes must equal its copy in `hook_pins/<name>.pinned`, comments and blank
lines included, so no part of a hook is outside the pin (verifier R2-1: a carriage return inside a comment line made
bash read the next check as part of the comment while a line-by-line pin still saw the check). Changing a hook means
copying it to its pin in the same commit, where review sees both.

The hook must also keep its shape, so a loosening cannot land by editing the hook and its pin together. The rules read
the hook's bytes and are deliberately coarse: they refuse whole forms rather than model bash.
- Printable ASCII with tab and newline only (no CR, form feed or other control or non-ASCII byte), no line ending in
  `\`, no quote left open across a line, no here-document (`<<`; the here-string `<<<` is allowed), and no quoting
  the pin's quote reading does not follow: `$'...'`, `$"..."` or a backslash before a quote (verifier R2-2).
- Each check is wrapped in `run`, never chained with `;`, and never in a pipeline, the background, a subshell or a
  command substitution (each loses `fail=1`): no `|` or `&` outside quotes but `||`, `&&` and redirections, no
  backtick, the parentheses of each line balance, and no `(` comes before a `run` on its line.
- No failure is discarded (`|| true`, `; true`, `|| :`) outside a variable assignment; nothing ends the hook or a
  loop early or changes the shell (`exit 0`, `return`, `break`, `trap`, `alias`, `kill`, `eval`, `source`, `shopt`,
  `unset`, `enable`, `builtin`, `coproc`, `exec` but pre-merge-commit's, `set` but `set -euo pipefail`); `exit` only
  takes `1` or `"$fail"`; `fail` is only set to `0` or `1`.
- Only the `run` function is defined, exactly as pinned: no `function` keyword, and no `()` or command group `{` on
  any other line; `fail=0` is set once before the first check; pre-commit and pre-push end with `exit "$fail"`.
A loosening that edits the hook and its pin together and avoids these forms (a gate turned false, `if false`, a check
removed, a loop fed nothing, `false && run ...`, `[[ -z x ]] || run ...`, a tool variable pointed at `true`, a `PATH`
prefix, `; fail=1` dropped from a branch) is review's to see: the pin's change is in the diff. The list is not complete.
"""

from __future__ import annotations

import difflib
import os
import re
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / ".githooks"
PINS = Path(__file__).resolve().parent / "hook_pins"
NAMES = ("commit-msg", "pre-commit", "pre-merge-commit", "pre-push")

PRE_COMMIT_RUN = """run() {  # run <label> <command...>
  local label="$1"; shift
  if ! "$@"; then echo "pre-commit: FAILED: $label" >&2; fail=1; fi
}"""
# S0034: each pre-push check runs through the check timer (scripts/gate_timer.py: the hang detector and the time
# report), which exits 0 only for a check that ran and passed; the canary line, run before the first check, fails the
# push when the timer passes a failing command.
PRE_PUSH_RUN = ('run() { local label="$1"; shift; if ! python3 scripts/gate_timer.py run pre-push "$label" "$@"; '
                'then echo "pre-push: FAILED: $label" >&2; fail=1; fi; }')
RUN_DEFINITION = {"pre-commit": PRE_COMMIT_RUN, "pre-push": PRE_PUSH_RUN}
PRE_PUSH_CANARY = ('if ! bash -c "! python3 scripts/gate_timer.py run pre-push canary false"; then echo "pre-push: '
                   'FAILED: the check timer passed a failing check" >&2; fail=1; fi')

QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'')
EXEC_PRE_COMMIT = 'exec "$(dirname "$0")/pre-commit" "$@"'
NOT_PLAIN = re.compile(rb"[^\x20-\x7e\n\t]")
# a signal to the hook's own shell, text run as code, a loop left early, or the shell itself changed
ENDS_OR_EVALS = re.compile(r"\b(?:kill|eval|source|break|shopt|unset|enable|builtin|coproc)\b|(?:^|[;&|]\s*)\.\s")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# A line that calls a check: it must be a `run` line, or the `if ! <check>; then ... exit 1` of a gate that stops the
# hook (check_push.py in pre-push, the message scan in commit-msg), or a tool-presence test `if [[ -x ... ]]`.
# Read on the line without its quoted text, so a message naming a tool is not a call and `bash -c "..."` is one.
CHECK_CALL = re.compile(r"\bpython3?\b|\.venv/bin/|node_modules/\.bin/|\$cargo_bin|\$uv_bin|\b(?:ba)?sh\b|\bxargs\b")
FUNCTION = re.compile(r"^(?:function\s+)?[A-Za-z_][A-Za-z0-9_-]*\s*\(\s*\)")
# S0033 verifier R2-2: what opens a function or a command group, and what runs a command in a pipeline, in the
# background or in a backtick substitution (outside quotes; `||`, `&&`, `>&2` and `&>` are not these)
DEFINES = re.compile(r"\bfunction\b|\(\s*\)|(?:^|[\s;&|()])\{(?=[\s;]|$)")
PIPE_OR_BACKGROUND = re.compile(r"(?<!\|)\|(?!\|)|(?<![&>])&(?![&>])|`")


def read_hook(path: Path) -> bytes:
    """A hook as git runs it: its bytes, with no newline translation."""
    return path.read_bytes()


def code_lines(text: str) -> list[str]:
    """The hook's lines as bash splits them (at newlines only), without surrounding spaces and tabs, blank lines and
    whole-line comments."""
    return [s for s in (line.strip(" \t") for line in text.split("\n")) if s and not s.startswith("#")]


def unquoted(line: str) -> str:
    """The line with quoted text removed (a `;` inside `bash -c "..."` is the command's). A trailing comment is kept
    and read by the rules too: coarse on purpose, since where a comment starts depends on bash's word splitting."""
    return QUOTED.sub('""', line)


def problems(name: str, raw: bytes, pin: bytes | None = None) -> list[str]:
    """What is wrong with hook `name` whose bytes are `raw`, against `pin` (default: its pinned copy)."""
    expected = read_hook(PINS / f"{name}.pinned") if pin is None else pin
    out: list[str] = []
    if raw != expected:
        diff = "".join(difflib.unified_diff(expected.decode("ascii", "replace").splitlines(keepends=True),
                                            raw.decode("ascii", "replace").splitlines(keepends=True),
                                            f"hook_pins/{name}.pinned", f".githooks/{name}", n=1))
        out.append(f"{name}: the hook's bytes differ from its pinned copy; if that is intended, copy it to "
                   f"scripts/tests/hook_pins/{name}.pinned in the same change:\n{diff}")
    # Every line must start where bash starts a command and read the same to bash and to these rules: no CR, form
    # feed or other control or non-ASCII byte (Python splits and strips lines at some of them, and bash does not end
    # a comment at a CR), no line continued onto the next, no quote open across a line, no here-document (below),
    # and no quoting that the QUOTED pattern does not follow.
    if NOT_PLAIN.search(raw):
        out.append(f"{name}: a byte other than printable ASCII, tab or newline")
    text = raw.decode("ascii", errors="replace")
    for raw_line in text.split("\n"):
        if raw_line.rstrip(" \t").endswith("\\"):
            out.append(f"{name}: {raw_line.strip()!r} continues onto the next line")
    lines = code_lines(text)
    for s in lines:
        if re.search(r"[\"']", QUOTED.sub("", s)):
            out.append(f"{name}: {s!r} leaves a quote open, so a string can span lines")
        if re.search(r"\\[\"']", s) or any(m.start() > 0 and s[m.start() - 1] == "$" for m in QUOTED.finditer(s)):
            out.append(f"{name}: {s!r} uses `$'...'`, `$\"...\"` or a backslash before a quote")
    if "set -euo pipefail" not in lines:
        out.append(f"{name}: `set -euo pipefail` is missing")
    run_first = RUN_DEFINITION[name].splitlines()[:1] if name in RUN_DEFINITION else []
    if name in RUN_DEFINITION:
        if text.count(RUN_DEFINITION[name]) != 1:
            out.append(f"{name}: the `run` function differs from the pinned definition")
        if not lines or lines[-1] != 'exit "$fail"':
            out.append(f'{name}: the last line must be `exit "$fail"`')
        if name == "pre-push" and (lines.count(PRE_PUSH_CANARY) != 1 or PRE_PUSH_RUN not in lines
                                   or lines.index(PRE_PUSH_CANARY) != lines.index(PRE_PUSH_RUN) + 1):
            out.append(f"{name}: the check timer's canary must follow the `run` function, once")
        fails = [i for i, s in enumerate(lines) if re.search(r"(?<![\w$-])fail=0\b", s)]
        first_run = next((i for i, s in enumerate(lines) if s.startswith("run ") or " run " in s), len(lines))
        if len(fails) != 1 or fails[0] > first_run:
            out.append(f"{name}: `fail=0` must be set once, before the first check")
    functions = [s for s in lines if FUNCTION.match(s)]
    if functions != run_first:
        out.append(f"{name}: only the `run` function may be defined: {functions}")
    for s in lines:
        bare = unquoted(s)
        words = bare.split()
        if re.search(r"\bexit\s+0\b|\breturn\b|\btrap\b|\balias\b", bare) or (
                re.search(r"\bset\b", bare) and s != "set -euo pipefail"):
            out.append(f"{name}: {s!r} can end or weaken the hook early")
        if re.search(r"(?<!<)<<(?!<)", bare):
            out.append(f"{name}: {s!r} starts a here-document, which can hide lines as text")
        if not ASSIGNMENT.match(s) and re.search(r"\|\|\s*(?:true|:)(?![\w-])|;\s*(?:true|:)(?![\w-])", bare):
            out.append(f"{name}: {s!r} discards a failure")
        if CHECK_CALL.search(bare) and not s.startswith(("run ", "if ! ", "if [[ -x ")) and " run " not in bare:
            out.append(f"{name}: {s!r} runs a check outside `run`")
        if words and words[0] == "run" and ";" in bare:
            out.append(f"{name}: {s!r} chains a second command after a check")
        # S0033 verifier R2-2: a function, a command group, a pipeline, a background job, a subshell or a substitution
        if s not in run_first and DEFINES.search(bare):
            out.append(f"{name}: {s!r} defines a function or opens a command group")
        if PIPE_OR_BACKGROUND.search(bare):
            out.append(f"{name}: {s!r} uses a pipeline, a background job or a backtick outside quotes")
        before_run = re.split(r"\brun\b", bare, maxsplit=1)
        if bare.count("(") != bare.count(")") or (len(before_run) == 2 and "(" in before_run[0]):
            out.append(f"{name}: {s!r} opens a subshell or substitution across lines or around a check")
        # S0033 verifier F1: the hook only ever exits with 1 or "$fail", and `fail` is only ever set to 0 or 1
        for m in re.finditer(r"\bexit\b(\s+\S+)?", bare):
            arg = (m.group(1) or "").strip()
            if not (arg == "1" or (arg == '""' and s == 'exit "$fail"')):
                out.append(f"{name}: {s!r} exits with a status other than 1 or \"$fail\"")
        if ENDS_OR_EVALS.search(bare) or (re.search(r"\bexec\b", bare) and s != EXEC_PRE_COMMIT):
            out.append(f"{name}: {s!r} can end the hook or run text as code")
        for m in re.finditer(r"(?<!\w)fail(?!\w)", s):
            ok = re.match(r"=[01](?=$|[;\s])", s[m.end():])
            if not ok and not (s == 'exit "$fail"' and s[m.start() - 1] == "$"):
                out.append(f"{name}: {s!r} uses `fail` other than as `fail=0`, `fail=1` or `exit \"$fail\"`")
    return out


class HookChecksArePinned(unittest.TestCase):
    def test_the_hooks_match_their_pinned_copies(self) -> None:
        self.assertEqual(sorted(p.name for p in HOOKS.iterdir() if p.is_file()), sorted(NAMES))
        self.assertEqual(sorted(p.name for p in PINS.iterdir()), sorted(f"{n}.pinned" for n in NAMES))
        for name in NAMES:
            with self.subTest(hook=name):
                self.assertEqual(problems(name, read_hook(HOOKS / name)), [])
                # git skips a hook that is not executable, with only a hint on the terminal
                self.assertTrue(os.access(HOOKS / name, os.X_OK), f"{name} is not executable")

    def test_loosened_hooks_are_refused(self) -> None:
        pre_push, pre_commit, commit_msg, merge = (read_hook(HOOKS / n).decode("ascii") for n in (
            "pre-push", "pre-commit", "commit-msg", "pre-merge-commit"))
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
            # S0033 verifier F1: message lines are code too
            "an echo line continues into a check": ("pre-push", pre_push.replace(
                inv, 'echo "pre-push: invariants" >&2 \\\n' + inv)),
            "fail=1 dropped from a tool-missing branch": ("pre-commit", pre_commit.replace(
                "run 'uv sync' in backend/\" >&2; fail=1", "run 'uv sync' in backend/\" >&2")),
            "fail reset inside quotes": ("pre-push", pre_push.replace(
                '\nexit "$fail"', '\necho "done $((fail=0))"\nexit "$fail"')),
            "an echo then a bare exit": ("pre-push", pre_push.replace("fail=0\n", "fail=0\necho start; exit\n")),
            "an echo then exec": ("pre-push", pre_push.replace("fail=0\n", "fail=0\necho start && exec true\n")),
            "fail=00": ("pre-push", pre_push.replace('\nexit "$fail"', '\necho done; fail=00\nexit "$fail"')),
            "an echo then kill": ("pre-push", pre_push.replace("fail=0\n", "fail=0\necho start; " + "kill -9 $$\n")),
            "a string spanning lines hides a check": ("pre-push", pre_push.replace(
                inv, 'echo "pre-push: next\n' + inv + '"\n')),
            # Python splits lines at a form feed, bash does not: `: <FF> exit` would read as two lines to the pin
            "a form feed splits a line for the pin but not for bash": ("pre-push", pre_push.replace(
                inv, ": \f" + inv)),
            "a carriage return inside a pinned line": ("pre-push", pre_push.replace(inv, inv.replace(".py", ".py\r"))),
        }
        # S0034: the timer bypassed, the canary removed, moved after the checks or inverted
        cases["run bypasses the timer"] = ("pre-push", pre_push.replace(
            'python3 scripts/gate_timer.py run pre-push "$label" "$@"', '"$@"'))
        cases["the canary removed"] = ("pre-push", pre_push.replace(PRE_PUSH_CANARY + "\n", ""))
        cases["the canary moved after the checks"] = ("pre-push", pre_push.replace(PRE_PUSH_CANARY + "\n", "").replace(
            '\nexit "$fail"', "\n" + PRE_PUSH_CANARY + '\nexit "$fail"'))
        cases["the canary inverted"] = ("pre-push", pre_push.replace(PRE_PUSH_CANARY, PRE_PUSH_CANARY.replace(
            'bash -c "! python3', 'bash -c "python3')))
        for label, (name, text) in cases.items():
            with self.subTest(label):
                self.assertNotEqual(text.encode(), read_hook(HOOKS / name), "the mutation did not apply")
                self.assertNotEqual(problems(name, text.encode()), [])
        # The same loosenings with the pin updated to match: the shape rules still refuse them. (Removing, reordering
        # or changing a check with the pin updated is a visible change to the pinned copy, which review reads.)
        shaped = ("E04 privacy scan chained with ; true", "a check or-true", "a check then ; true", "a check unwrapped",
                  "early exit 0", "fail reset before the end", "last line exits 0", "run never fails",
                  "run redefined later", "checks hidden in a here-document", "a check in an unused function",
                  "set -e dropped", "set +e added", "an exit trap", "commit-msg scan or-true",
                  "pre-merge-commit no longer runs pre-commit", "a check through bash -c outside run",
                  "an echo line continues into a check", "fail reset inside quotes", "an echo then a bare exit",
                  "an echo then exec", "fail=00", "an echo then kill", "a string spanning lines hides a check",
                  "a form feed splits a line for the pin but not for bash", "a carriage return inside a pinned line",
                  "a comment line ends in a backslash", "fail set to a variable", "fail decremented",
                  "fail reset in spaced arithmetic", "run bypasses the timer", "the canary removed",
                  "the canary moved after the checks", "the canary inverted")
        cases["a check through bash -c outside run"] = ("pre-push", pre_push.replace(
            inv, 'bash -c "python3 scripts/check_invariants.py || true"\n'))
        cases["a comment line ends in a backslash"] = ("pre-push", pre_push.replace(inv, "# note \\\n" + inv))
        cases["fail set to a variable"] = ("pre-push", pre_push.replace('\nexit "$fail"', '\nfail=$ok\nexit "$fail"'))
        cases["fail reset in spaced arithmetic"] = ("pre-push", pre_push.replace(
            '\nexit "$fail"', '\necho "done $(( fail=0 ))"\nexit "$fail"'))
        cases["fail decremented"] = ("pre-push", pre_push.replace('\nexit "$fail"', '\n((fail-=1))\nexit "$fail"'))
        # S0033 verifier round 2 (R2-2) and the same class: with the pin updated, a quote bash opens where the pin's
        # quote reading does not, a check in a function that is never called, in a subshell, a pipeline or the
        # background (each loses `fail=1`), or a loop left early.
        later = {
            "an ANSI-C quote left open": ("echo $'\\'\n", "# '\n"),
            "an escaped quote then a quote": ("echo \\' x '\n", "# '\n"),
            "a locale quote": ('echo $"x\n', '"\n'),
            "a function keyword": ("function later {\n", "}\n"),
            "a function keyword with an if body": ("function later if true; then\n", "fi\n"),
            "a function after a colon with an if body": (": ; later() if true; then\n", "fi\n"),
            # bash reads `\ #` as part of a word, not a comment: the function after it is code
            "a function after an escaped space and a hash": ("echo a\\ # ; later() {\n", "}\n"),
            "a function after a colon": (": ; later() {\n", "}\n"),
            "a function with its brace on the next line": ("later ()\n{\n", "}\n"),
            "a command group skipped": ("false && {\n", "}\n"),
            "a subshell over checks": ("(\n", ")\n"),
            "a command substitution over checks": (": $(\n", ")\n"),
            "a backtick substitution over checks": (": `\n", "`\n"),
            "a loop left early": ("", "break\n"),
            "errexit turned off by name": ("set +o errexit\n", ""),
        }
        for label, (before, after) in later.items():
            cases[label] = ("pre-push", pre_push.replace(inv, before + inv + after))
        cases["a check in a pipeline"] = ("pre-push", pre_push.replace(inv, inv.rstrip("\n") + " | cat\n"))
        cases["a check in the background"] = ("pre-push", pre_push.replace(inv, inv.rstrip("\n") + " &\n"))
        cases["a check in a one-line subshell"] = ("pre-push", pre_push.replace(inv, "( " + inv.rstrip("\n") + " )\n"))
        shaped += (*later, "a check in a pipeline", "a check in the background", "a check in a one-line subshell")
        for label in shaped:
            name, text = cases[label]
            with self.subTest(f"{label}, pin updated"):
                self.assertNotEqual(problems(name, text.encode(), text.encode()), [])

    def test_a_carriage_return_read_from_the_file_is_refused(self) -> None:
        """S0033 verifier R2-1: bash reads `# note<CR>run ...` as one comment line; the check never runs. The hook
        goes through the same reading path as the real hooks, from a file."""
        inv = b'run "invariants" python3 scripts/check_invariants.py\n'
        raw = (HOOKS / "pre-push").read_bytes()
        for label, mutated in (("a CR in a comment line", raw.replace(inv, b"# note\r" + inv)),
                               ("a CR at the end of a line", raw.replace(inv, inv.replace(b"\n", b"\r\n"))),
                               ("a comment with no CR (unpinned text)", raw.replace(inv, b"# note\n" + inv))):
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                self.assertNotEqual(mutated, raw)
                path = Path(tmp) / "pre-push"
                path.write_bytes(mutated)
                self.assertNotEqual(problems("pre-push", read_hook(path)), [])
                if label != "a comment with no CR (unpinned text)":  # the CR alone, with the pin updated to match
                    self.assertNotEqual(problems("pre-push", read_hook(path), read_hook(path)), [])


if __name__ == "__main__":
    unittest.main()
