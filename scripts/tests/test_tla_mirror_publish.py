"""Meta-tests for formal/tla/mirror-publish: the publish model stays in step with its spec and its mapping (S0085).

ENGINEERING.md EP-19: the model of the mirror's generation publish checks only `ReadersSeeCompleteGeneration` and
`SnapshotIsSomeCommittedState` (BL-0113), with a two-way mapping and a seeded mutant per property. These tests do not
run TLC (formal/tla/mirror-publish/run_all.sh does); they check that the names line up:
- the properties and mutants that design/specs/mirror/publish.md §10 names are exactly the ones the model defines and
  the configuration table checks, both ways, and the properties are exactly BL-0113's two;
- every property has a mutant that must break it, every mutant has a switch in the model;
- every vacuity witness is a predicate of the model, run on a small world;
- MAPPING.md lists exactly the actions of the model's Next relation, and each row names a rule of the spec (a §
  number) and a code function (built, or planned for S0086);
- every rule of publish.md that MAPPING.md §1 cites exists in the spec;
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
DIR = REPO / "formal/tla/mirror-publish"
SPEC = REPO / "design/specs/mirror/publish.md"
PROPERTIES = {"ReadersSeeCompleteGeneration", "SnapshotIsSomeCommittedState"}  # BL-0113, EP-19: these two only


def _gen_cfgs():
    spec = importlib.util.spec_from_file_location("_tla_mirror_publish_gen_cfgs", DIR / "gen_cfgs.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _between(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i : text.index(end, i)]


def _model() -> str:
    return (DIR / "MirrorPublish.tla").read_text(encoding="utf-8")


def _next_actions(model: str, groups: tuple[str, ...] = ("EngineStep", "ReaderStep", "EnvStep")) -> set[str]:
    names: set[str] = set()
    for group in groups:
        body = _between(model, f"\n{group} ==", "\n\n").split("==", 1)[1]
        names |= set(re.findall(r"\b([A-Z][A-Za-z0-9_]+)\b", body))
    return names - {"Files", "Gens", "Readers"}


class MirrorPublishModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gen = _gen_cfgs()
        cls.model = _model()
        cls.spec = SPEC.read_text(encoding="utf-8")
        cls.s10 = _between(cls.spec, "## 10. ", "## 11. ")
        cls.checked = set(cls.gen.INVARIANTS)

    def defined(self, name: str) -> bool:
        return re.search(rf"^{re.escape(name)}(\([^)]*\))?\s+==", self.model, re.M) is not None

    def test_configurations_are_generated_from_the_table(self) -> None:
        run = subprocess.run([sys.executable, str(DIR / "gen_cfgs.py"), "--check"], capture_output=True, text=True,
                             check=False)  # fmt: skip
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_the_properties_are_bl0113s_two_in_the_spec_the_model_and_the_table(self) -> None:
        names = set(re.findall(r"`([A-Z][A-Za-z0-9]+)`", _between(self.s10, "**Properties:**", "**Seeded bugs")))
        self.assertEqual(names, PROPERTIES, "§10 must name exactly BL-0113's two properties")
        self.assertEqual(self.checked, PROPERTIES, "the configurations must check exactly BL-0113's two properties")
        for name in PROPERTIES:
            self.assertTrue(self.defined(name), f"{name} is not defined in MirrorPublish.tla")

    def test_every_mutant_the_spec_names_is_seeded_and_switchable(self) -> None:
        listed = set(re.findall(r"`([a-z0-9]+(?:_[a-z0-9]+)+)`",
                                _between(self.s10, "**Seeded bugs", "**Not modelled")))  # fmt: skip
        seeded = {m[0] for m in self.gen.MUTANTS}
        self.assertEqual(sorted(listed - seeded), [], "mutants §10 names that gen_cfgs.py does not run")
        self.assertEqual(sorted(seeded - listed), [], "mutants gen_cfgs.py runs that §10 does not name")
        for name in seeded:
            self.assertIn(f'"{name}"', self.model, f"mutant {name} has no switch in MirrorPublish.tla")

    def test_every_property_has_a_mutant_that_must_break_it(self) -> None:
        targeted = {m[2] for m in self.gen.MUTANTS}
        self.assertEqual(targeted, PROPERTIES)
        for name, world, _prop, _extra in self.gen.MUTANTS:
            self.assertIn(world, self.gen.WORLDS, f"mutant {name} names no world")

    def test_every_witness_is_a_predicate_of_a_small_world(self) -> None:
        self.assertGreaterEqual(len(self.gen.WITNESSES), 6)
        for name, world in self.gen.WITNESSES:
            self.assertTrue(name.startswith("W_") and self.defined(name), f"witness {name} is not defined")
            self.assertIn(world, self.gen.WORLDS, f"witness {name} names no world")
            self.assertEqual(self.gen.WORLDS[world][0], "S", f"witness {name} runs on a world outside the small tier")

    def test_mapping_lists_exactly_the_actions_of_next_with_a_rule_and_a_function(self) -> None:
        mapping = (DIR / "MAPPING.md").read_text(encoding="utf-8")
        table = _between(mapping, "### 2.1 Actions", "### 2.2 Variables")
        rows: set[str] = set()
        env = _next_actions(self.model, ("EnvStep",))
        for line in table.splitlines():
            if not line.startswith("| `"):
                continue
            cells = line.split("|")
            named = set(re.findall(r"`([A-Za-z_0-9]+)`", cells[1]))
            rows |= named
            self.assertRegex(cells[2], r"§\d+", f"MAPPING.md row {sorted(named)} cites no rule of publish.md")
            if named <= env:  # the OS and another program: no function of ours
                self.assertIn("environment", cells[3], f"MAPPING.md row {sorted(named)}: say it is the environment")
            else:
                self.assertRegex(cells[3], r"`[a-z_]+(\.[A-Za-z_]+)+`", f"MAPPING.md row {sorted(named)} names no code")
        actions = _next_actions(self.model)
        self.assertEqual(sorted(actions - rows), [], "actions in Next without a MAPPING.md row")
        self.assertEqual(sorted(rows - actions), [], "MAPPING.md rows that name no action of Next")
        for name in actions:
            self.assertTrue(self.defined(name), f"{name} is in Next but not defined")

    def test_every_rule_the_mapping_cites_exists_in_the_spec(self) -> None:
        mapping = (DIR / "MAPPING.md").read_text(encoding="utf-8")
        sections = set(re.findall(r"^## (\d+)\. ", self.spec, re.M))
        cited = set(re.findall(r"§(\d+)", mapping))
        self.assertGreaterEqual(len(sections), 10)
        self.assertEqual(sorted(cited - sections, key=int), [], "MAPPING.md cites sections publish.md does not have")


class RunAllArgumentsTest(unittest.TestCase):
    """run_all.sh refuses an option value that names nothing; `--list` runs no TLC."""

    def run_all(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sh", str(DIR / "run_all.sh"), *args, "--list"], capture_output=True, text=True,
                              timeout=60, check=False)  # fmt: skip

    def test_unknown_values_are_refused(self) -> None:
        for args in (["--tier", "S."], ["--kind", "mutants"], ["--only", "S_publish,NoSuch"]):
            with self.subTest(args=args):
                done = self.run_all(*args)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertEqual(done.stdout, "")

    def test_known_values_list_their_configurations(self) -> None:
        done = self.run_all("--tier", "S", "--kind", "base")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("S_publish", done.stdout)


if __name__ == "__main__":
    unittest.main()
