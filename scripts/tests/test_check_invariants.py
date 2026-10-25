"""Meta-tests for scripts/check_invariants.py: fixtures it must flag and one it must pass."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_invariants as ci  # noqa: E402

GOOD = """
### INV-01: First
- **Rule:** r
- **Why:** w
- **Enforced by:** `scripts/tests/test_one.py`
- **Status:** enforced

### INV-02: Second
- **Rule:** r
- **Why:** w
- **Enforced by:** planned: something.
- **Status:** pending (BL-0002, by M3)
"""
BACKLOG = "| BL-0002 | Build enforcement for INV-02 | open | M3 |\n"


class InvariantCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts/tests").mkdir(parents=True)
        (self.root / "scripts/tests/test_one.py").write_text("# covers INV-01\n")
        (self.root / ".githooks").mkdir()
        (self.root / ".githooks/pre-push").write_text("run x python3 -m unittest discover -s scripts/tests -q\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_check(self, text: str, backlog: str | None = BACKLOG, exit_ms: str | None = None) -> list[str]:
        return ci.check(text, self.root, backlog, exit_ms)

    def test_good_file_passes(self) -> None:
        self.assertEqual(self.run_check(GOOD), [])

    def test_gap_in_numbering(self) -> None:
        self.assertTrue(self.run_check(GOOD.replace("INV-02", "INV-03")))

    def test_missing_why(self) -> None:
        self.assertTrue(any("Why" in e for e in self.run_check(GOOD.replace("- **Why:** w\n", "", 1))))

    def test_enforcement_file_missing(self) -> None:
        self.assertTrue(self.run_check(GOOD.replace("test_one.py", "test_nope.py")))

    def test_enforcement_file_not_tagged(self) -> None:
        (self.root / "scripts/tests/test_one.py").write_text("# no tag\n")
        self.assertTrue(any("does not mention" in e for e in self.run_check(GOOD)))

    def test_enforcement_file_that_never_runs(self) -> None:
        (self.root / "notes").mkdir()
        (self.root / "notes/docs.md").write_text("INV-01\n")
        text = GOOD.replace("`scripts/tests/test_one.py`", "`notes/docs.md`")
        self.assertTrue(any("no enforcement file is a test" in e for e in self.run_check(text)))

    def test_a_check_only_the_manual_ci_runs_enforces_nothing(self) -> None:  # S0031, M4
        (self.root / "scripts/check_x.py").write_text("# INV-01\n")
        (self.root / ".github/workflows").mkdir(parents=True)
        (self.root / ".github/workflows/ci.yml").write_text("run: python3 scripts/check_x.py\n")
        text = GOOD.replace("`scripts/tests/test_one.py`", "`scripts/check_x.py`")
        self.assertTrue(any("no enforcement file" in e for e in self.run_check(text)))
        (self.root / ".githooks/pre-commit").write_text("run y python3 scripts/check_x.py\n")
        self.assertEqual(self.run_check(text), [])

    def test_a_test_counts_only_where_the_pre_push_hook_runs_its_folder(self) -> None:  # S0031, M4
        for rel in ("frontend/e2e/harness.spec.ts", "scripts/acceptance/tests/test_x.py", "tests/test_x.py"):
            with self.subTest(rel=rel):
                (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
                (self.root / rel).write_text("// INV-01\n")
                text = GOOD.replace("scripts/tests/test_one.py", rel)
                self.assertTrue(any("no enforcement file" in e for e in self.run_check(text)), rel)
        (self.root / ".githooks/pre-push").write_text("run x python3 -m unittest discover -s scripts/oracle/tests -q\n")
        self.assertTrue(any("no enforcement file" in e for e in self.run_check(GOOD)))
        (self.root / ".githooks/pre-push").unlink()
        self.assertTrue(any("no enforcement file" in e for e in self.run_check(GOOD)))
        for folder, marker in ci.PUSHED_TEST_ROOTS.items():
            with self.subTest(folder=folder):
                rel = folder + "test_y.py"
                (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
                (self.root / rel).write_text("# INV-01\n")
                (self.root / ".githooks/pre-push").write_text(f"run x {marker}\n")
                self.assertEqual(self.run_check(GOOD.replace("scripts/tests/test_one.py", rel)), [])

    def test_the_real_pre_push_runs_every_test_root(self) -> None:
        hook = (Path(__file__).resolve().parents[2] / ".githooks/pre-push").read_text()
        self.assertEqual([m for m in ci.PUSHED_TEST_ROOTS.values() if m not in hook], [])
        self.assertEqual(ci.RUNNERS, [".githooks/pre-commit", ".githooks/pre-push"])

    def test_pending_without_backlog_item(self) -> None:
        self.assertTrue(self.run_check(GOOD, backlog="| BL-0009 | other | open |\n"))

    def test_pending_on_closed_item(self) -> None:
        self.assertTrue(self.run_check(GOOD, backlog="| BL-0002 | x | done | M3 |\n"))

    def test_a_mention_elsewhere_is_not_the_item(self) -> None:
        backlog = "| Invariant items (`BL-0002`) | 31 |\n| BL-0002 | x | done | M3 |\n"
        self.assertTrue(any("closed" in e for e in self.run_check(GOOD, backlog=backlog)))
        self.assertTrue(self.run_check(GOOD, backlog="- see BL-0002 for details\n"))

    def test_pending_past_milestone_exit(self) -> None:
        self.assertEqual(self.run_check(GOOD, exit_ms="M2"), [])
        self.assertTrue(self.run_check(GOOD, exit_ms="M3"))

    def test_bad_status(self) -> None:
        self.assertTrue(self.run_check(GOOD.replace("pending (BL-0002, by M3)", "pending (BL-TBD, by M3)")))

    def test_an_enforcement_file_with_fixme_tests_enforces_nothing(self) -> None:  # S0029 verifier F4
        at = "\n" + "@"
        for rel, body in (
                ("scripts/tests/test_one.py", "# covers INV-01\nimport pytest" + at
                 + 'pytest.mark.fixme(id="BL-0002", due="M3", reason="r")\ndef test_x() -> None:\n    pass\n'),
                ("scripts/tests/test_one.py", "# covers INV-01\nimport pytest" + at + "pytest.mark.fixme\ndef test_x() -> None:\n    pass\n"),
                ("scripts/tests/one.test.ts", "// INV-01\nfixme({ id: 'BL-0002', due: 'M3', reason: 'r' }, 't', async () => {})\n")):
            with self.subTest(rel=rel, body=body[:40]):
                (self.root / rel).write_text(body)
                text = GOOD.replace("scripts/tests/test_one.py", rel)
                self.assertTrue(any("holds fixme tests" in e for e in self.run_check(text)))
        (self.root / "scripts/tests/test_one.py").write_text("# covers INV-01\ndef test_x() -> None:\n    pass\n")
        self.assertEqual(self.run_check(GOOD), [])


FINDINGS = """
| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L1-06 | Layering | Medium | WS-X | M0 | {s0} | — | `review:L1` |
| L3-07 | dsum | Medium | WS-X | M7 | open | — | `review:L3` |
"""
ITEMS = """
| ID | Item | Area | Milestone | Status | Ref |
|---|---|---|---|---|---|
| BL-0001 | Corpus | WS-WRITE | {m1} | open | x |
"""


class BacklogAtMilestoneExit(unittest.TestCase):
    """Open review findings and items fail the exit of their milestone (S0023); closed ones never do."""

    def errors(self, backlog: str, exit_ms: str | None) -> list[str]:
        return ci.check_backlog(backlog, exit_ms)

    def test_an_open_finding_fails_its_milestone_exit(self) -> None:
        self.assertTrue(any("L1-06" in e for e in self.errors(FINDINGS.format(s0="open"), "M0")))
        self.assertTrue(any("L1-06" in e for e in self.errors(FINDINGS.format(s0="in-progress"), "M0")))

    def test_it_also_fails_any_later_exit(self) -> None:
        self.assertTrue(any("L1-06" in e for e in self.errors(FINDINGS.format(s0="open"), "M2")))

    def test_closed_findings_pass(self) -> None:
        for status in ("done", "removed", "post-v0.4", "parked"):
            with self.subTest(status=status):
                self.assertEqual(self.errors(FINDINGS.format(s0=status), "M0"), [])

    def test_a_later_milestone_does_not_fail_an_earlier_exit(self) -> None:
        self.assertEqual(self.errors(FINDINGS.format(s0="done"), "M6"), [])
        self.assertTrue(any("L3-07" in e for e in self.errors(FINDINGS.format(s0="done"), "M7")))

    def test_without_an_exit_only_the_shape_is_checked(self) -> None:
        self.assertEqual(self.errors(FINDINGS.format(s0="open"), None), [])

    def test_bare_m1_is_due_by_m1a(self) -> None:
        self.assertTrue(self.errors(ITEMS.format(m1="M1"), "M1a"))
        self.assertTrue(self.errors(ITEMS.format(m1="M1a (agent)"), "M1a"))
        self.assertEqual(self.errors(ITEMS.format(m1="M1b (agent)"), "M1a"), [])
        self.assertTrue(self.errors(ITEMS.format(m1="M1b"), "M1b"))
        self.assertEqual(self.errors(ITEMS.format(m1="M1"), "M0"), [])

    def test_unknown_status_is_refused(self) -> None:
        self.assertTrue(any("status" in e for e in self.errors(FINDINGS.format(s0="fixed"), None)))

    def test_open_item_without_a_milestone_is_refused(self) -> None:
        for cell in ("—", "later", "M99", ""):
            with self.subTest(cell=cell):
                self.assertTrue(any("needs a milestone" in e for e in self.errors(ITEMS.format(m1=cell), None)))

    def test_tables_without_an_id_header_are_not_items(self) -> None:
        other = "| Double | Canonical | Milestone |\n|---|---|---|\n| L1-06 | L1-07 | M0 |\n"
        status = "| Status | Meaning |\n|---|---|\n| open | Not started. |\n"
        self.assertEqual(self.errors(other + "\n" + status, "M12"), [])

    def test_a_table_ends_at_a_non_table_line(self) -> None:
        backlog = "| ID | Milestone | Status |\n|---|---|---|\n\ntext\n| L1-06 | M0 | open |\n"
        self.assertEqual(self.errors(backlog, "M0"), [])  # a headerless row is not under an ID table

    def test_the_real_backlog_has_a_valid_shape(self) -> None:
        text = (Path(__file__).resolve().parents[2] / "design/backlog.md").read_text(encoding="utf-8")
        self.assertEqual(self.errors(text, None), [])
        self.assertTrue(len(ci.table_rows(text)) > 250)  # 299 rows on 2026-10-25: catches a parser that reads nothing

    def test_item_tables_in_another_shape_are_refused(self) -> None:  # S0023 verifier F1: fail closed on shape
        row = "| L1-06 | x | M0 | open |\n"
        for header in ("| Id | Title | Milestone | Status |", "| **ID** | Title | Milestone | Status |",
                       "| Item ID | Title | Milestone | Status |", "| ID | Title | Milestone | State |",
                       "| ID | Title | Due | Status |"):
            with self.subTest(header=header):
                table = f"{header}\n|---|---|---|---|\n{row}"
                self.assertTrue(any("item table" in e for e in self.errors(table, None)))
        for prefix in ("  ", "> "):
            with self.subTest(prefix=prefix):
                table = "".join(prefix + ln + "\n" for ln in ("| ID | Title | Milestone | Status |", "|---|---|---|---|", row.strip()))
                self.assertTrue(any("item table" in e for e in self.errors(table, None)))

    def test_main_applies_the_backlog_rule_at_exit(self) -> None:  # S0023 verifier F3
        import os
        from unittest import mock
        invs = "### INV-01: One\n- **Rule:** r\n- **Why:** w\n- **Enforced by:** planned.\n- **Status:** pending (BL-0002, by M3)\n"
        for status, want in (("open", 1), ("done", 0)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "design").mkdir()
                (root / "design/INVARIANTS.md").write_text(invs)
                (root / "design/backlog.md").write_text(
                    "| ID | Item | Area | Milestone | Status | Ref |\n|---|---|---|---|---|---|\n"
                    "| BL-0002 | x | a | M3 | open | r |\n"
                    f"| L1-06 | y | a | M0 | {status} | r |\n")
                with mock.patch.dict(os.environ, {"FT_MILESTONE_EXIT": "M0"}):
                    self.assertEqual(ci.main(root), want)

    def test_main_refuses_an_unknown_exit_name(self) -> None:
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"FT_MILESTONE_EXIT": "m0"}):
            self.assertEqual(ci.main(), 1)


FIXME_BACKLOG = """
| ID | Title | Sev | Area | Milestone | Status | Doubles | Ref |
|---|---|---|---|---|---|---|---|
| L4-01 | Open slots | Critical | WS-IMPORT | M6 | open | — | r |
| I4 | Atomic publish | High | WS-MIRROR | M1 | open | — | r |
| L8-02 | Save | High | WS-FE | M5 | in-progress | — | r |
| L1-06 | Layering | Medium | WS-X | M0 | done | — | r |
| BL-0062 | Guard | Low | WS-QA | — | parked | — | r |
| L9-18 | Gone | Low | WS-UI | M2 | removed | — | r |
"""
DECO = "\n\n\n" + "@"  # joined at runtime: a newline escape before `@` reads as an email to the privacy scan
PY_OK = 'import pytest' + DECO + 'pytest.mark.fixme(id="L4-01", due="M6", reason="capability not built")\ndef test_x() -> None:\n    pass\n'
TS_OK = ("import { fixme } from '../fixme'\n\n"
         "fixme({ id: 'L4-01', due: 'M6', reason: 'capability not built' }, 'fills open slots', async () => {})\n")


class FixmeMarkers(unittest.TestCase):
    """S0029: every fixme names an open backlog row with the same milestone; the row keeps the exit run red."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def put(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def errors(self, backlog: str = FIXME_BACKLOG, exit_ms: str | None = None) -> list[str]:
        return ci.check_fixme(self.root, backlog, exit_ms)

    def py(self, marker: str) -> list[str]:
        self.put("backend/tests/integrity/test_x.py", PY_OK.replace(
            'pytest.mark.fixme(id="L4-01", due="M6", reason="capability not built")', marker))
        return self.errors()

    def ts(self, text: str, rel: str = "frontend/tests/unit/integrity/x.test.ts") -> list[str]:
        self.put(rel, text)
        return self.errors()

    def test_a_marker_on_an_open_row_with_its_milestone_passes(self) -> None:
        self.put("backend/tests/integrity/test_x.py", PY_OK)
        self.put("frontend/tests/unit/integrity/x.test.ts", TS_OK)
        self.put("frontend/e2e/flows/a7.spec.ts", TS_OK)
        self.assertEqual(self.errors(), [])

    def test_in_progress_counts_as_open(self) -> None:
        self.assertEqual(self.py('pytest.mark.fixme(id="L8-02", due="M5", reason="capability not built")'), [])

    def test_bare_m1_row_is_due_m1a(self) -> None:
        self.assertEqual(self.py('pytest.mark.fixme(id="I4", due="M1a", reason="capability not built")'), [])
        self.assertTrue(self.py('pytest.mark.fixme(id="I4", due="M1", reason="capability not built")'))

    def test_python_markers_that_must_not_pass(self) -> None:
        bad = {
            "no id": 'pytest.mark.fixme(due="M6", reason="capability not built")',
            "no due": 'pytest.mark.fixme(id="L4-01", reason="capability not built")',
            "no reason": 'pytest.mark.fixme(id="L4-01", due="M6")',
            "empty reason": 'pytest.mark.fixme(id="L4-01", due="M6", reason="")',
            "bare marker": "pytest.mark.fixme",
            "positional": 'pytest.mark.fixme("L4-01", "M6", "capability not built")',
            "unknown id": 'pytest.mark.fixme(id="L4-99", due="M6", reason="capability not built")',
            "id in prose": 'pytest.mark.fixme(id="see L4-01", due="M6", reason="capability not built")',
            "closed id": 'pytest.mark.fixme(id="L1-06", due="M0", reason="capability not built")',
            "parked id": 'pytest.mark.fixme(id="BL-0062", due="M0", reason="capability not built")',
            "removed id": 'pytest.mark.fixme(id="L9-18", due="M2", reason="capability not built")',
            "other milestone": 'pytest.mark.fixme(id="L4-01", due="M7", reason="capability not built")',
            "unknown milestone": 'pytest.mark.fixme(id="L4-01", due="M66", reason="capability not built")',
            "computed id": 'pytest.mark.fixme(id=ID, due="M6", reason="capability not built")',
            "extra keyword": 'pytest.mark.fixme(id="L4-01", due="M6", reason="r", extra="x")',
            "positional and keywords": 'pytest.mark.fixme("x", id="L4-01", due="M6", reason="r")',
        }
        for name, marker in bad.items():
            with self.subTest(name):
                self.assertTrue(self.py(marker), marker)

    def test_an_aliased_or_module_level_alias_is_refused(self) -> None:
        self.put("backend/tests/test_alias.py", "import pytest\nfm = pytest.mark.fixme" + DECO + "fm(id='L4-01', due='M6', reason='r')\ndef test_x() -> None:\n    pass\n")
        self.assertTrue(any("fixme" in e for e in self.errors()))

    def test_from_import_of_mark_is_still_checked(self) -> None:
        self.put("backend/tests/test_mark.py", "from pytest import mark" + DECO + "mark.fixme(id='L4-99', due='M6', reason='r')\ndef test_x() -> None:\n    pass\n")
        self.assertTrue(any("L4-99" in e for e in self.errors()))

    def test_a_module_level_pytestmark_is_checked(self) -> None:
        self.put("backend/tests/test_mod.py", "import pytest\npytestmark = pytest.mark.fixme(id='L1-06', due='M0', reason='r')\n")
        self.assertTrue(any("L1-06" in e for e in self.errors()))

    def test_an_unparseable_test_file_fails_closed(self) -> None:
        self.put("backend/tests/test_broken.py", "def (:\n")
        self.assertTrue(any("parse" in e for e in self.errors()))

    def test_typescript_markers_that_must_not_pass(self) -> None:
        call = "fixme({{ id: '{i}', due: '{d}', reason: '{r}' }}, 'title', async () => {{}})\n"
        bad = {
            "unknown id": call.format(i="L4-99", d="M6", r="capability not built"),
            "closed id": call.format(i="L1-06", d="M0", r="capability not built"),
            "other milestone": call.format(i="L4-01", d="M5", r="capability not built"),
            "empty reason": call.format(i="L4-01", d="M6", r=""),
            "no id": "fixme({ due: 'M6', reason: 'capability not built' }, 'title', async () => {})\n",
            "computed meta": "fixme(META, 'title', async () => {})\n",
            "raw playwright fixme": "test.fixme('title', async () => {})\n",
            "conditional playwright fixme": "test('t', async () => { test.fixme(true, 'later') })\n",
            "playwright expected failure": "test.fail('title', async () => {})\n",
            "describe fixme": "test.describe.fixme('group', () => {})\n",
            "raw vitest fails": "it.fails('title', async () => {})\n",
            "aliased helper": "import { fixme } from '../fixme'\nconst later = fixme\n",
            "aliased member": "const f = test.fixme\n",
            "indexed member": "test['fixme']('title', async () => {})\n",
            "indexed vitest fails": "it['fails']('title', async () => {})\n",
        }
        for name, text in bad.items():
            with self.subTest(name):
                self.assertTrue(self.ts(text), text)
                (self.root / "frontend/tests/unit/integrity/x.test.ts").unlink()

    def test_playwright_fixme_takes_its_details_from_the_helper(self) -> None:
        good = ("test.fixme(\n  'A7: title',\n  fixme({ id: 'L4-01', due: 'M6', reason: 'capability not built' }),\n"
                "  async ({ page }) => {},\n)\n")
        self.assertEqual(self.ts(good, rel="frontend/e2e/integrity/a7.spec.ts"), [])
        bad = {
            "details not from the helper": "test.fixme('t', META, async () => {})\n",
            "helper with an unknown id": "test.fixme('t', fixme({ id: 'L4-99', due: 'M6', reason: 'r' }), async () => {})\n",
            "helper with a closed id": "test.fixme('t', fixme({ id: 'L1-06', due: 'M0', reason: 'r' }), async () => {})\n",
            "computed title": "test.fixme(title, fixme({ id: 'L4-01', due: 'M6', reason: 'r' }), async () => {})\n",
            "another object": "other.fixme('t', fixme({ id: 'L4-01', due: 'M6', reason: 'r' }), async () => {})\n",
        }
        for name, text in bad.items():
            with self.subTest(name):
                self.assertTrue(self.ts(text, rel="frontend/e2e/integrity/a7.spec.ts"), text)

    def test_switching_a_test_off_is_refused_where_it_matters(self) -> None:  # S0029 verifier F3
        at = "\n" + "@"
        refused = {
            "backend/tests/integrity/test_a.py": "import pytest" + at + "pytest.mark.skip(reason='later')\ndef test_x():\n    assert False\n",
            "backend/tests/integrity/test_b.py": "import pytest" + at + "pytest.mark.xfail\ndef test_x():\n    assert False\n",
            "backend/tests/integrity/test_c.py": "import pytest\ndef test_x():\n    pytest.importorskip('app.importing')\n",
            "backend/tests/integrity/test_d.py": "import pytest\ndef test_x():\n    pytest.skip('later')\n",
            "backend/tests/integrity/test_e.py": "from pytest import skip\ndef test_x():\n    skip('later')\n",
            "backend/tests/integrity/test_f.py": "import pytest" + at + "pytest.mark.skipif(True, reason='x')\ndef test_x():\n    pass\n",
            "backend/tests/other/test_g.py": "import pytest\n# L4-01\ndef test_x():\n    pytest.xfail('later')\n",
            "backend/tests/other/test_h.py": "import pytest" + at + "pytest.mark.xfail(strict=False)\ndef test_x():\n    assert False\n",
            "frontend/tests/unit/a.test.ts": "import { it } from 'vitest'\nit.skip('x', () => { throw new Error() })\n",
            "frontend/tests/unit/b.test.ts": "import { it } from 'vitest'\nit.todo('x')\n",
            "frontend/tests/unit/c.test.ts": "import { describe, it } from 'vitest'\ndescribe.skip('x', () => { it('y', () => {}) })\n",
            "frontend/tests/unit/d.test.ts": "import { it } from 'vitest'\nit.skipIf(true)('x', () => {})\n",
            "frontend/tests/unit/e.test.ts": "import { it } from 'vitest'\nconst { fails: pending } = it\npending('x', () => {})\n",
            "frontend/tests/unit/f.test.ts": "import { it } from 'vitest'\nit['skip']('x', () => {})\n",
            "frontend/e2e/integrity/z.spec.ts": "import { test } from '@playwright/test'\ntest.skip('x', async () => {})\n",
        }
        for rel, body in refused.items():
            with self.subTest(rel):
                self.put(rel, body)
                self.assertTrue(self.errors(), rel)
                (self.root / rel).unlink()
        allowed = {  # outside the protected places Python keeps its platform skips and strict xfails (BL-0040)
            "backend/tests/content/test_case.py": "import pytest\ndef test_x():\n    pytest.skip('case-sensitive file system')\n",
            "backend/tests/content/test_known.py": "import pytest" + at + "pytest.mark.xfail(strict=True)\ndef test_x():\n    assert False\n",
            "frontend/tests/unit/g.test.ts": "import { it } from 'vitest'\nit('skips nothing', () => { const r = { skipped: 1 } })\n",
        }
        for rel, body in allowed.items():
            with self.subTest(rel):
                self.put(rel, body)
                self.assertEqual(self.errors(), [], rel)

    def test_the_helpers_call_only_their_runner_marker(self) -> None:  # S0029 verifier F9: pinned here, not in a test tree
        root = Path(__file__).resolve().parents[2]
        def code(rel: str) -> str:  # without comment lines, which describe the markers in prose
            text = (root / rel).read_text(encoding="utf-8")
            return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("//"))

        vitest = code("frontend/tests/fixme.ts")
        self.assertEqual(re.findall(r"\b(?:it|test|describe)\.\w+\(", vitest), ["it.fails("])
        e2e = code("frontend/e2e/fixme.ts")
        self.assertIn("ANNOTATION_TYPE = 'fixme'", e2e)
        self.assertEqual(re.findall(r"\b(?:it|test|describe)\.\w+\(", e2e), [])
        self.assertEqual(ci.FIXME_HELPERS, {"frontend/tests/fixme.ts", "frontend/e2e/fixme.ts"})

    def test_e2e_specs_are_scanned_at_any_depth(self) -> None:
        self.assertTrue(self.ts("test.fixme('title', async () => {})\n", rel="frontend/e2e/a/b/flow.spec.ts"))

    def test_the_helper_modules_alone_may_call_the_runner_markers(self) -> None:
        helper = "export function fixme(meta: Meta, title: string, body: Body): void {\n  test.fixme(title, body)\n}\n"
        self.put("frontend/e2e/fixme.ts", helper)
        self.put("frontend/tests/fixme.ts", helper.replace("test.fixme", "it.fails"))
        self.assertEqual(self.errors(), [])
        self.put("frontend/e2e/other.ts", helper)
        self.assertTrue(self.errors())
        (self.root / "frontend/e2e/other.ts").unlink()
        self.put("frontend/e2e/integrity/fixme.ts", helper)  # only the two named helpers, not any file of that name
        self.assertTrue(self.errors())

    def test_a_multiline_call_as_prettier_writes_it_passes(self) -> None:
        text = ("fixme(\n  { id: 'L4-01', due: 'M6', reason: 'capability not built' },\n"
                "  'a long title that prettier wraps',\n  async () => {},\n)\n")
        self.assertEqual(self.ts(text), [])

    def test_an_exit_run_fails_on_a_fixme_due_by_it(self) -> None:
        self.put("backend/tests/integrity/test_x.py", PY_OK)
        self.assertEqual(self.errors(exit_ms="M5"), [])
        self.assertTrue(any("exit of M6" in e for e in self.errors(exit_ms="M6")))
        self.assertTrue(any("exit of M7" in e for e in self.errors(exit_ms="M7")))

    def test_files_outside_the_test_trees_are_not_scanned(self) -> None:
        self.put("docs/notes.py", "import pytest\nfm = pytest.mark.fixme\n")
        self.assertEqual(self.errors(), [])

    def test_main_reports_a_bad_fixme(self) -> None:
        import os
        from unittest import mock
        invs = "### INV-01: One\n- **Rule:** r\n- **Why:** w\n- **Enforced by:** planned.\n- **Status:** review-only\n"
        self.put("design/INVARIANTS.md", invs)
        self.put("design/backlog.md", FIXME_BACKLOG)
        self.put("backend/tests/test_x.py", PY_OK)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FT_MILESTONE_EXIT", None)
            self.assertEqual(ci.main(self.root), 0)
            self.put("backend/tests/test_x.py", PY_OK.replace("L4-01", "L1-06"))
            self.assertEqual(ci.main(self.root), 1)

    def test_the_real_tree_passes_and_has_the_p1_markers(self) -> None:
        root = Path(__file__).resolve().parents[2]
        backlog = (root / "design/backlog.md").read_text(encoding="utf-8")
        self.assertEqual(ci.check_fixme(root, backlog, None), [])
        ids = {m["id"] for m in ci.fixme_markers(root)[0]}
        p1 = {"I4", "I5", "I6", "L1-04", "L4-01", "L4-02", "L4-03", "L4-06", "L3-01", "L3-11", "L3-14", "L10-02",
              "L10.M-1", "L10.M-2", "L10-06", "L11.M-2", "L6-12", "I7", "I8", "L9-01", "L9.M-1", "L9.M-3", "L8-01",
              "L8-02", "L8.M-1"}
        self.assertEqual(p1, set(ci.P1_FINDINGS))
        p1 |= {f"BL-00{n}" for n in range(64, 71)}  # the integrity flows A1, A2, A4 (two parts), A7, A10, A13
        closed = {r["ID"] for r in ci.table_rows(backlog) if r.get("Status") == "done"}
        # review:27 §1(d): each P1 finding has its fixme test until a slice closes it (then the test runs for real).
        self.assertEqual(sorted(p1 - ids - closed), [])


ENFORCED = """
### INV-01: First
- **Rule:** r
- **Why:** w
- **Enforced by:** `{path}`
- **Status:** enforced
"""


class EnforcementFilesDoNotSwitchTestsOff(unittest.TestCase):
    """S0031 (the M0 gate red team M2): an enforced invariant does not count a test file that skips its tests."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.put(".githooks/pre-push", "discover -s scripts/tests\n.venv/bin/pytest\nnode_modules/.bin/vitest run\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def put(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def errors(self, rel: str, body: str) -> list[str]:
        self.put(rel, body)
        try:
            return ci.check(ENFORCED.format(path=rel), self.root, None, None)
        finally:
            (self.root / rel).unlink()

    def test_python_skip_forms_are_refused(self) -> None:
        at = "\n" + "@"
        head = "# INV-01\nimport unittest, pytest, sys\n"
        refused = [
            "pytestmark = pytest.mark.skip(reason='slow')\n",
            "pytestmark = [pytest.mark.skipif(True, reason='x')]\n",
            at + "pytest.mark.xfail(strict=True)\ndef test_x():\n    pass\n",
            "def test_x():\n    pytest.importorskip('x')\n",
            "def test_x():\n    pytest.skip('later')\n",
            "def test_x():\n    pytest.xfail('later')\n",
            at + "unittest.skip('flaky')\nclass T(unittest.TestCase):\n    pass\n",
            at + "unittest.skipIf(True, 'x')\nclass T(unittest.TestCase):\n    pass\n",
            at + "unittest.skipUnless(False, 'x')\nclass T(unittest.TestCase):\n    pass\n",
            at + "unittest.expectedFailure\ndef test_x():\n    pass\n",
            "from unittest import expectedFailure" + at + "expectedFailure\ndef test_x():\n    pass\n",
            "from unittest import skip as later" + at + "later('x')\ndef test_x():\n    pass\n",
            "from pytest import importorskip as need\n",
            "class T(unittest.TestCase):\n    def test_x(self):\n        self.skipTest('x')\n",
            "def test_x():\n    raise unittest.SkipTest('x')\n",
            "from unittest import SkipTest\ndef test_x():\n    raise SkipTest\n",
            "def load_tests(loader, tests, pattern):\n    return loader.suiteClass()\n",
            "load_tests = lambda *a: None\n",
            # the macOS exception is exact: any other condition, function or shape is refused
            at + "unittest.skipIf(sys.platform == 'darwin', 'x')\ndef test_x():\n    pass\n",
            at + "unittest.skipUnless(sys.platform == 'linux', 'x')\ndef test_x():\n    pass\n",
            at + "unittest.skipUnless(sys.platform != 'darwin', 'x')\ndef test_x():\n    pass\n",
            at + "unittest.skipUnless(sys.platform == 'darwin' or True, 'x')\ndef test_x():\n    pass\n",
            at + "unittest.skipUnless(sys.platform == 'darwin', 'x', extra=1)\ndef test_x():\n    pass\n",
            at + "pytest.mark.skipif(sys.platform == 'darwin', reason='x')\ndef test_x():\n    pass\n",
            "import sys as s" + at + "unittest.skipUnless(s.platform == 'darwin', 'x')\ndef test_x():\n    pass\n",
            # S0031 verifier F1: what pytest and unittest read to skip, without a skip name
            at + "pytest.mark.parametrize('x', [])\ndef test_x(x):\n    pass\n",
            at + "pytest.mark.parametrize('x', ())\ndef test_x(x):\n    pass\n",
            at + "pytest.mark.parametrize(argnames='x', argvalues=[])\ndef test_x(x):\n    pass\n",
            "CASES = []" + at + "pytest.mark.parametrize('x', CASES)\ndef test_x(x):\n    pass\n",
            "from pytest import mark" + at + "mark.parametrize('x', [])\ndef test_x(x):\n    pass\n",
            "class T(unittest.TestCase):\n    __unittest_skip__ = True\n",
            "class T(unittest.TestCase):\n    __unittest_skip__ = True\n    __unittest_skip_why__ = 'x'\n",
            "def test_x():\n    pass\ntest_x.__unittest_expecting_failure__ = True\n",
            "def test_x():\n    pass\ntest_x.__test__ = False\n",
            "class TestX:\n    __test__ = False\n",
            "__test__ = False\n",
        ]
        for body in refused:
            with self.subTest(body=body):
                found = self.errors("scripts/tests/test_one.py", head + body)
                self.assertTrue(any("switches a test off" in e for e in found), found)
        self.assertTrue(self.errors("scripts/tests/test_one.py", head + "def test_x(:\n"))  # unparsable fails closed

    def test_the_macos_exception_and_plain_tests_pass(self) -> None:
        at = "\n" + "@"
        head = "# INV-01\nimport unittest, sys\n"
        ok = [head + "def test_x():\n    assert 'skip' != 'xfail'  # words in strings are not markers\n",
              head + at + "unittest.skipUnless(sys.platform == \"darwin\", \"macOS firmlink spelling\")\n"
              "def test_x():\n    pass\n"]
        for body in ok:
            with self.subTest(body=body):
                self.assertEqual(self.errors("scripts/tests/test_one.py", body), [])

    def test_a_non_test_enforcement_source_may_use_the_names(self) -> None:  # S0031 verifier F8
        self.put(".githooks/pre-commit", "python3 scripts/check_x.py\n")
        self.assertEqual(self.errors("scripts/check_x.py", "# INV-01\nskip = None\n__test__ = 1\n"), [])
        self.put(".githooks/pre-commit", "python3 scripts/check_x.py frontend/eslint.boundaries.js\n")
        self.assertEqual(self.errors("frontend/eslint.boundaries.js", "// INV-01\nconst a = { only: 1 }.only\n"), [])

    def test_a_non_empty_literal_parametrize_passes(self) -> None:
        body = "# INV-01\nimport pytest\n" + "@" + "pytest.mark.parametrize('x', [1, 2])\ndef test_x(x):\n    pass\n"
        self.assertEqual(self.errors("scripts/tests/test_one.py", body), [])

    def test_a_conftest_above_an_enforcement_test_cannot_drop_it(self) -> None:
        test = "backend/tests/architecture/test_globals.py"
        body = "# INV-01\ndef test_x():\n    pass\n"
        for conf in ("backend/tests/conftest.py", "backend/tests/architecture/conftest.py", "backend/conftest.py"):
            for text in ("collect_ignore = ['architecture/test_globals.py']\n", "collect_ignore_glob = ['*']\n",
                         "def pytest_ignore_collect(collection_path, config):\n    return True\n",
                         "import pytest\ndef pytest_runtest_setup(item):\n    pytest.skip('x')\n",
                         "def pytest_collection_modifyitems(items):\n    for i in items:\n        i.obj.__test__ = False\n"):
                with self.subTest(conf=conf, text=text):
                    self.put(conf, text)
                    self.assertTrue(any("switches a test off" in e for e in self.errors(test, body)))
                    (self.root / conf).unlink()
        # the fixme hook's strict xfail in backend/tests/conftest.py stays allowed (a strict xfail fails a passing test)
        self.put("backend/tests/conftest.py", (Path(__file__).resolve().parents[2] / "backend/tests/conftest.py").read_text())
        self.assertEqual(self.errors(test, body), [])

    def test_typescript_skip_forms_are_refused(self) -> None:
        for body in ("it.skip('x', () => {})\n", "describe.skip('x', () => {})\n", "it.todo('x')\n",
                     "it.skipIf(true)('x', () => {})\n", "it.runIf(false)('x', () => {})\n", "it['skip']('x', () => {})\n",
                     "test.skip('x', async () => {})\n", "const { skip: later } = it\n"):
            with self.subTest(body=body):
                found = self.errors("frontend/tests/unit/boundaries.test.ts", "// INV-01\nimport { it } from 'vitest'\n" + body)
                self.assertTrue(any("switches a test off" in e for e in found), found)
        self.assertEqual(self.errors("frontend/tests/unit/boundaries.test.ts",
                                     "// INV-01\nimport { it } from 'vitest'\nit('x', () => {})\n"), [])
        self.assertEqual(self.errors("frontend/tests/unit/boundaries.test.ts",  # the word in prose is not the marker
                                     "// INV-01\nimport { it } from 'vitest'\nit('open slots only, then more', () => {})\n"), [])

    def test_the_red_team_probe_c_is_refused_on_the_real_tree(self) -> None:
        real = Path(__file__).resolve().parents[2]
        invariants = (real / "design/INVARIANTS.md").read_text(encoding="utf-8")
        for rel, extra in (("backend/tests/architecture/test_globals.py", "\n\npytestmark = pytest.mark.skip(reason='slow')\n"),
                           ("scripts/tests/test_guards.py", "\n\n" + "@" + "unittest.skip('flaky')\nclass Later(unittest.TestCase):\n    pass\n")):
            with self.subTest(rel=rel):
                found = [e for e in ci.check(invariants, real, None, None) if rel in e]
                self.assertEqual(found, [])
                text = (real / rel).read_text(encoding="utf-8") + extra
                self.assertTrue(any("switches a test off" in e for e in ci.switched_off(rel, text, real)))


class FixmeScanCoversTheScriptTests(unittest.TestCase):
    """S0031 (M2): the S0029 fixme and skip scan covers scripts/tests and scripts/oracle/tests, with its rules."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def errors(self, rel: str, text: str) -> list[str]:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        try:
            return ci.check_fixme(self.root, FIXME_BACKLOG, None)
        finally:
            path.unlink()

    def test_scripts_trees_are_scanned(self) -> None:
        at = "\n" + "@"
        for folder in ("scripts/tests", "scripts/oracle/tests"):
            for body in ("import pytest" + at + "pytest.mark.xfail(strict=False)\ndef test_x():\n    pass\n",
                         "import pytest" + at + "pytest.mark.fixme\ndef test_x():\n    pass\n",
                         "import unittest\n# L4-01\n" + "@" + "unittest.skip('x')\nclass T(unittest.TestCase):\n    pass\n",
                         "import unittest\n# L4-01\n" + "@" + "unittest.expectedFailure\ndef test_x():\n    pass\n",
                         "import unittest\n# L4-01\ndef test_x():\n    raise unittest.SkipTest('x')\n",
                         "from unittest import expectedFailure\n# L4-01\n" + "@" + "expectedFailure\ndef test_x():\n    pass\n",
                         "from unittest import SkipTest\n# L4-01\ndef test_x():\n    raise SkipTest\n"):
                with self.subTest(folder=folder, body=body):
                    self.assertTrue(self.errors(f"{folder}/test_a.py", body))

    def test_only_is_left_to_the_runners(self) -> None:  # S0031 verifier R2-2, R2-7: --allowOnly=false, forbidOnly
        self.assertEqual(self.errors("frontend/tests/unit/any.test.ts",
                                     "import { it } from 'vitest'\n// Run it once. only then compare.\nit('x', () => {})\n"), [])

    def test_outside_the_protected_places_a_platform_skip_stays(self) -> None:
        body = "import unittest\n" + "@" + "unittest.skipUnless(False, 'legacy tags not in this clone')\nclass T(unittest.TestCase):\n    pass\n"
        self.assertEqual(self.errors("scripts/tests/test_a.py", body), [])


if __name__ == "__main__":
    unittest.main()
