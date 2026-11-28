"""The engine-jobs spec's limits index (S0162; M1b area review F7): `design/specs/engine-jobs.md` §15 lists every
**Limit (topic):** the body states, with the section that states it, and nothing else.

Limits of this check: a topic is matched by its words, ignoring case, so a renamed limit is a removal and an addition;
a limit stated outside the bold `**Limit (topic):**` form is not seen; §15 itself, §16 and later are not read as body.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SPEC = Path(__file__).resolve().parents[2] / "design" / "specs" / "engine-jobs.md"
HEADING = re.compile(r"^#{2,3} (\d+(?:\.\d+)?)[. ]")
LIMIT = re.compile(r"\*\*Limit \(([^)]+)\):?\*\*")
ENTRY = re.compile(r"^- (.+): §(\d+(?:\.\d+)?)$")


def body_and_index(text: str) -> tuple[dict[str, set[str]], dict[str, str]]:
    """The body's limits (topic -> the sections stating it) and §15's index (topic -> the section it names)."""
    body: dict[str, set[str]] = {}
    index: dict[str, str] = {}
    section, in_index = "", False
    for line in text.splitlines():
        if m := HEADING.match(line):
            section, in_index = m.group(1), m.group(1) == "15"
            continue
        if in_index:
            if e := ENTRY.match(line):
                index[e.group(1).lower()] = e.group(2)
        elif section != "16":
            for topic in LIMIT.findall(line):
                body.setdefault(topic.lower(), set()).add(section)
    return body, index


def problems(text: str) -> list[str]:
    body, index = body_and_index(text)
    out = [f"§15 does not list the limit '{t}' ({', '.join(sorted(s))})" for t, s in body.items() if t not in index]
    out += [f"§15 lists '{t}', which no section states" for t in index if t not in body]
    out += [
        f"§15 puts '{t}' in §{s}; it is stated in {sorted(body[t])}"
        for t, s in index.items()
        if s not in body.get(t, {s})
    ]
    return sorted(out)


class LimitsIndex(unittest.TestCase):
    SAMPLE = "## 2. Jobs\nA **Limit (a):** x. **Limit (b)** y.\n## 15. Honest limits\n- a: §2\n- b: §2\n## 16. Open\n"

    def test_the_repository_spec_index_matches_its_body(self) -> None:
        self.assertEqual(problems(SPEC.read_text(encoding="utf-8")), [])

    def test_a_matching_index_passes(self) -> None:
        self.assertEqual(problems(self.SAMPLE), [])

    def test_a_limit_missing_from_the_index_fails(self) -> None:
        self.assertEqual(len(problems(self.SAMPLE.replace("- b: §2\n", ""))), 1)

    def test_an_index_entry_no_section_states_fails(self) -> None:
        self.assertEqual(len(problems(self.SAMPLE.replace("- b: §2\n", "- b: §2\n- c: §2\n"))), 1)

    def test_an_entry_naming_the_wrong_section_fails(self) -> None:
        self.assertEqual(len(problems(self.SAMPLE.replace("- a: §2", "- a: §3"))), 1)


if __name__ == "__main__":
    unittest.main()
