"""The engine-jobs spec's structure (S0079, BL-0192): each message has one home, and each limit is stated once.

`design/specs/engine-jobs.md` cites every user-facing text of engine jobs by a key (`msg:…`) into
`design/generated/engine-job-texts.md`, which `backend/scripts/gen_engine_job_texts.py` renders from the code
(`scripts/check_generated.py` keeps it current). These tests check, without importing the backend:
- the reference is registered as a generated file and carries its header;
- the keys line up both ways: every key the spec cites exists, and every text in the reference is cited by the spec;
- one home: no sentence of the reference is also quoted in the spec;
- each limit is stated with the rule it qualifies, marked `**Limit (<topic>):**`, and §15 lists every topic with
  the section that holds it, both ways;
- the sections keep their numbers and titles, so the many citations of "engine-jobs §n" elsewhere stay valid.
"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "design/specs/engine-jobs.md"
TEXTS = REPO / "design/generated/engine-job-texts.md"
TEXTS_REL = "design/generated/engine-job-texts.md"

# The headings as they stood at S0079's base (3ce7b5bf): other documents cite these numbers.
HEADINGS = (
    "## 1. Terms",
    "## 2. Jobs",
    "## 3. The queue",
    "## 4. Cancel",
    "## 5. The engine process",
    "### 5.1 One engine at a time, fresh after user code",
    "### 5.2 Ending an engine",
    "### 5.3 Crash and bounded restart",
    '### 5.4 No time limits; "Still working…"',
    "### 5.5 The client and the engine",
    "## 6. The save",
    "### 6.1 Steps",
    "### 6.2 `durable_sync`",
    "### 6.3 Paths",
    "### 6.4 Rollback inside the engine",
    "### 6.5 Records on disk",
    "### 6.6 The outcome after an engine death in the session",
    "### 6.7 After the save: the rebuild",
    "### 6.8 Volumes without the swap (best effort)",
    "## 7. Loads that read the ledger",
    "## 8. Recovery and recover-all-before-open",
    "### 8.1 Journals",
    "### 8.2 Create and copy",
    "## 9. The switch, Reopen, quit and boot",
    "### 9.1 The switch (and Reopen)",
    "### 9.2 Boot",
    "### 9.3 Quit",
    "## 10. What survives a crash at each step",
    "## 11. Messages and codes",
    "## 12. The decision log",
    "## 13. What the TLA+ model must check",
    "## 14. Tests the slice must have",
    "## 15. Honest limits",
    "## 16. Open questions",
)

KEY = re.compile(r"`(msg:[A-Za-z0-9_.]+)`")
ROW = re.compile(r"^\| `(msg:[A-Za-z0-9_.]+)` \| [^|]*(?:\\\|[^|]*)* \| (.*) \|$")
LIMIT = re.compile(r"\*\*Limit \(([A-Za-z0-9 -]+)\):\*\*")
INDEX = re.compile(r"^- ([A-Za-z0-9 -]+): §(\d+(?:\.\d+)?)\b", re.M)
HEADING = re.compile(r"^#{2,3} (\d+(?:\.\d+)?)[. ]", re.M)
PLACEHOLDER = re.compile(r"<[^<>]+>|\*\*(?:Message|Next step):\*\*")
MIN_FRAGMENT = 40


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def _texts() -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in TEXTS.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m:
            rows[m.group(1)] = m.group(2).replace("\\|", "|")
    return rows


def _section(text: str) -> str:
    """§15's body."""
    start = text.index("## 15. ")
    return text[start : text.index("## 16. ", start)]


def _section_at(text: str, pos: int) -> str:
    """The number of the section or subsection that holds `pos`."""
    found = [m.group(1) for m in HEADING.finditer(text, 0, pos)]
    return found[-1] if found else ""


class GeneratedTexts(unittest.TestCase):
    def test_the_reference_is_generated_and_registered(self) -> None:
        self.assertTrue(TEXTS.exists(), f"{TEXTS_REL} is missing; run backend/scripts/gen_engine_job_texts.py")
        self.assertIn("GENERATED", TEXTS.read_text(encoding="utf-8").splitlines()[0])
        spec = importlib.util.spec_from_file_location("_check_generated", REPO / "scripts/check_generated.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn(TEXTS_REL, module.GENERATORS)
        _, sources = module.GENERATORS[TEXTS_REL]
        for source in ("backend/scripts/gen_engine_job_texts.py", "backend/src/app/platform/engine_jobs/"):
            self.assertIn(source, sources)

    def test_every_key_the_spec_cites_exists_and_every_text_is_cited(self) -> None:
        texts = _texts()
        self.assertGreaterEqual(len(texts), 60, "the reference's rows were not found")
        cited = set(KEY.findall(_spec()))
        self.assertEqual(sorted(cited - set(texts)), [], "keys the spec cites that the reference does not have")
        self.assertEqual(sorted(set(texts) - cited), [], "texts in the reference that the spec never cites")

    def test_no_sentence_of_the_reference_is_also_quoted_in_the_spec(self) -> None:
        spec = " ".join(_spec().split())
        doubled = sorted(
            f"{key}: {fragment}"
            for key, text in _texts().items()
            for fragment in (f.strip(" .,;:") for f in PLACEHOLDER.split(text))
            if len(fragment) >= MIN_FRAGMENT and fragment in spec
        )
        self.assertEqual(doubled, [], "words with two homes: cite the key instead")


class Limits(unittest.TestCase):
    def test_each_limit_is_marked_where_it_lives_and_listed_in_section_15_both_ways(self) -> None:
        text = _spec()
        s15 = _section(text)
        start = text.index(s15)
        marks = {
            (m.group(1), _section_at(text, m.start()))
            for m in LIMIT.finditer(text)
            if not start <= m.start() < start + len(s15)
        }
        self.assertGreaterEqual(len(marks), 20, "the limit marks were not found")
        listed = set(INDEX.findall(s15))
        self.assertEqual(sorted(marks - listed), [], "limits stated in the rules that §15 does not list")
        self.assertEqual(sorted(listed - marks), [], "limits §15 lists that no rule states where it says")
        topics = [topic for topic, _ in marks]
        self.assertEqual(sorted(t for t in set(topics) if topics.count(t) > 1), [], "a limit stated twice")


class Sections(unittest.TestCase):
    def test_the_sections_keep_their_numbers_and_titles(self) -> None:
        lines = [line for line in _spec().splitlines() if re.match(r"^#{2,3} \d", line)]
        self.assertEqual(lines, list(HEADINGS))


if __name__ == "__main__":
    unittest.main()
