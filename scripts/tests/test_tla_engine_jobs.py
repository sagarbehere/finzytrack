"""Meta-tests for formal/tla/engine-jobs: the TLA+ model stays in step with its spec and its mapping.

ENGINEERING.md EP-19: the model comes with a two-way mapping table and a seeded mutant per property.
These tests do not run TLC (formal/tla/engine-jobs/run_all.sh does); they check that the names line up:
- every property and every mutant that design/specs/engine-jobs.md §13 names exists in the model and
  in the configuration table, so a spec change that renames or adds one fails here until the model follows;
- every property the configurations check has at least one mutant that must break it;
- MAPPING.md lists exactly the actions of the model's Next relation;
- the generated configurations are in step with gen_cfgs.py.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIR = REPO / "formal/tla/engine-jobs"
SPEC = REPO / "design/specs/engine-jobs.md"
HELPERS = {"JobIdsBounded", "QuitAnywayAlwaysEnabled"}  # model sanity, and a conjunct of EngineCanAlwaysBeFreed


def _gen_cfgs():
    spec = importlib.util.spec_from_file_location("_tla_engine_jobs_gen_cfgs", DIR / "gen_cfgs.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _between(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i)]


def _section13() -> str:
    return _between(SPEC.read_text(encoding="utf-8"), "## 13. ", "## 14. ")


def _model() -> str:
    return (DIR / "EngineJobs.tla").read_text(encoding="utf-8")


def _next_actions(model: str) -> set[str]:
    names: set[str] = set()
    for group in ("EngineStep", "AppStep", "UserStep", "EnvStep"):
        body = _between(model, f"\n{group} ==", "\n\n").split("==", 1)[1]
        names |= set(re.findall(r"\b([A-Z][A-Za-z0-9_]+)\b", body))
    return names - {"WS", "Files", "Len", "E", "JobBound"}


class EngineJobsModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gen = _gen_cfgs()
        cls.model = _model()
        cls.s13 = _section13()
        cls.checked = set(cls.gen.INVARIANTS) | set(cls.gen.SAFETY_PROPS) | set(cls.gen.LIVENESS)

    def defined(self, name: str) -> bool:
        return re.search(rf"^{re.escape(name)}(\([^)]*\))? ==", self.model, re.M) is not None

    def test_configurations_are_generated_from_the_table(self) -> None:
        run = subprocess.run([sys.executable, str(DIR / "gen_cfgs.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_every_property_section13_names_is_defined_and_checked(self) -> None:
        names = set(re.findall(r"`([A-Z][A-Za-z0-9]+)`", _between(self.s13, "**Safety properties:**", "**Seeded bugs")))
        self.assertGreaterEqual(len(names), 30, "the §13 property list was not found")
        missing = sorted(n for n in names if not self.defined(n))
        self.assertEqual(missing, [], "properties §13 names that the model does not define")
        unchecked = sorted(names - self.checked)
        self.assertEqual(unchecked, [], "properties §13 names that no configuration checks")

    def test_every_mutant_section13_names_is_seeded_and_switchable(self) -> None:
        listed = set(re.findall(r"`([a-z0-9]+(?:_[a-z0-9]+)+)`",
                                _between(self.s13, "**Already checked by two small executable models**", "**Not modelled")))
        # §13's "thirty-five mutants of the two executable models", less the two of the R4 gate (S0068, ADR-0067)
        self.assertEqual(len(listed), 33, "§13's mutants of the two executable models")
        seeded = {m[0] for m in self.gen.MUTANTS}
        self.assertEqual(sorted(listed - seeded), [], "mutants §13 names that gen_cfgs.py does not run")
        for name in seeded:
            self.assertTrue(f'"{name}"' in self.model, f"mutant {name} has no switch in EngineJobs.tla")

    def test_every_property_has_a_mutant_that_must_break_it(self) -> None:
        targeted = {m[2] for m in self.gen.MUTANTS}
        self.assertEqual(sorted(self.checked - HELPERS - targeted), [])
        self.assertEqual(sorted(targeted - self.checked), [], "a mutant targets a property no configuration checks")

    def test_mapping_lists_exactly_the_actions_of_next(self) -> None:
        mapping = (DIR / "MAPPING.md").read_text(encoding="utf-8")
        table = _between(mapping, "### 2.1 Actions", "### 2.2 Variables")
        rows = set()
        for line in table.splitlines():
            if line.startswith("| `"):
                rows |= set(re.findall(r"`([A-Za-z_0-9]+)`", line.split("|")[1]))
        actions = _next_actions(self.model)
        self.assertEqual(sorted(actions - rows), [], "actions in Next without a MAPPING.md row")
        self.assertEqual(sorted(rows - actions), [], "MAPPING.md rows that name no action of Next")
        for name in actions:
            self.assertTrue(self.defined(name), f"{name} is in Next but not defined")


class RunAllArgumentsTest(unittest.TestCase):
    """run_all.sh refuses an option value that names nothing (owner, 2026-10-29: `--tier S.` ran nothing and looked
    like a success). `--list` runs no TLC."""

    def run_all(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sh", str(DIR / "run_all.sh"), *args, "--list"], capture_output=True, text=True,
                              timeout=60, check=False)  # fmt: skip

    def test_unknown_values_are_refused(self) -> None:
        for args in (["--tier", "S."], ["--tier", "S,,M"], ["--tier", ""], ["--kind", "mutants"],
                     ["--only", "S_queue,NoSuch"]):  # fmt: skip
            with self.subTest(args=args):
                done = self.run_all(*args)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn("--", done.stderr)
                self.assertEqual(done.stdout, "")

    def test_known_values_list_their_configurations(self) -> None:
        done = self.run_all("--tier", "S", "--kind", "base")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("S_queue", done.stdout)
        done = self.run_all("--only", "S_queue,S_cancel")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(done.stdout.splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
