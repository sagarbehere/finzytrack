"""The vitest pending-test helper hands its runner the wrapped body (S0032; seeded plants G02/G03).

`frontend/tests/fixme.ts` runs a test written before its capability exists as vitest's known-failure runner. The
runner passes on any thrown error, so the helper wraps the body in `expectedToFail`, which rethrows only
`PlannedMissing` (the planned file does not exist yet) and swallows every other error, which the runner then reports as
red. `frontend/tests/unit/fixmeHelpers.test.ts` checks `expectedToFail` and `planned()` by behaviour; this pins the
wiring the unit test cannot name (a test tree may not mention the runner marker: scripts/check_invariants.py). Pinned
here beside `test_check_invariants.py`'s pin of the helper's one runner call.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parents[2] / "frontend" / "tests" / "fixme.ts"


def code(text: str) -> str:
    """The helper without comment lines, which describe the runner in prose."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("//"))


def problems(text: str) -> list[str]:
    src = code(text)
    out = []
    calls = re.findall(r"\bit\.fails\(([^\n]*)\)\s*$", src, flags=re.MULTILINE)
    if len(calls) != 1 or not re.fullmatch(r"`[^`]*`, expectedToFail\(meta, body\)", calls[0]):
        out.append(f"the runner call must pass expectedToFail(meta, body): {calls}")
    if src.count("expectedToFail(") != 2:  # its definition and the one call
        out.append("expectedToFail is defined once and called once")
    wrapper = re.search(r"export function expectedToFail\(.*?\n}\n", src, flags=re.DOTALL)
    body = wrapper.group(0) if wrapper else ""
    rethrows = re.findall(r"\bthrow\b[^\n]*", body)
    if rethrows != ["throw error"] or "if (error instanceof PlannedMissing) throw error" not in body:
        out.append(f"the wrapper rethrows only PlannedMissing: {rethrows}")
    if "await body()" not in body:
        out.append("the wrapper awaits the body")
    return out


class PendingHelper(unittest.TestCase):
    def test_the_real_helper(self) -> None:
        self.assertEqual(problems(HELPER.read_text(encoding="utf-8")), [])

    def test_loosened_helpers_are_refused(self) -> None:
        real = HELPER.read_text(encoding="utf-8")
        loosened = {
            "the raw body handed to the runner": real.replace("expectedToFail(meta, body))", "body)"),
            "every error rethrown": real.replace(
                "if (error instanceof PlannedMissing) throw error", "throw error"
            ),
            "any error rethrown before the check": real.replace(
                "if (error instanceof PlannedMissing) throw error", "throw error\n      if (error) return"
            ),
            "the body not awaited": real.replace("await body()", "void body()"),
            "a wrapper that never runs the body": real.replace("expectedToFail(meta, body))", "async () => body())"),
        }
        for label, text in loosened.items():
            with self.subTest(label):
                self.assertNotEqual(text, real, "the mutant must change the helper")
                self.assertNotEqual(problems(text), [], label)


if __name__ == "__main__":
    unittest.main()
