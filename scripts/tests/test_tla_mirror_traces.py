"""Meta-tests for formal/tla/mirror-publish/gen_traces.py (S0091, BL-0210): the trace module it writes, its reading of
TLC's trace values, the branches it leaves out, and its staleness check. None runs TLC (the generator does, with
Java); the conformance replay itself is engine/tests/mirror/test_publish_conformance.py.
"""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIR = REPO / "formal/tla/mirror-publish"


def _gen():
    spec = importlib.util.spec_from_file_location("_tla_mirror_publish_gen_traces", DIR / "gen_traces.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _gen()
MODEL = (DIR / "MirrorPublish.tla").read_text(encoding="utf-8")

SAMPLE = r"""---------------- MODULE trace_0_0 -----------------
STATE_1 ==
/\ act = [a |-> "P_Create", p |-> 0]
/\ disk = <<1, 2>>
/\ jopen = FALSE
/\ jpre = <<1, 1>>
/\ jpost = 0
/\ nv = 3
/\ hist = {<<1, 1>>, <<1, 2>>}
/\ gens = << [c |-> [k |-> "part", rows |-> <<0, 0>>, lab |-> <<0, 0>>], jl |-> TRUE,
     pl |-> {<<[k |-> "abs", rows |-> <<0, 0>>, lab |-> <<0, 0>>], FALSE>>}],
   [c |-> [k |-> "done", rows |-> <<1, 2>>, lab |-> <<1, 2>>], jl |-> FALSE, pl |-> {}] >>
/\ used = {1, 2}
/\ lk = 2
/\ lkpl = {2}
/\ ctl = [pc |-> "commit", g |-> 1, lab |-> <<1, 2>>, rows |-> <<1, 2>>]
/\ rd = [r1 |-> [st |-> "one", g |-> 2, s1 |-> [k |-> "done", rows |-> <<1, 2>>, lab |-> <<1, 2>>],
     s2 |-> [k |-> "abs", rows |-> <<0, 0>>, lab |-> <<0, 0>>]]]
/\ n = [b |-> 1]
/\ gh = [moved |-> FALSE]

=================================================
"""


class ReaderTest(unittest.TestCase):
    def test_reads_a_trace_keeping_only_what_the_replay_reads(self) -> None:
        (state,) = [GEN.compact(s) for s in GEN.read_trace(SAMPLE)]
        self.assertEqual(state["act"], {"a": "P_Create", "p": 0})
        self.assertEqual(state["disk"], [1, 2])
        self.assertEqual(state["gens"][0], {"k": "part", "rows": [0, 0], "lab": [0, 0], "jl": True})
        self.assertEqual(state["gens"][1], {"k": "done", "rows": [1, 2], "lab": [1, 2], "jl": False})
        self.assertEqual((state["lk"], state["ctl"]), (2, {"pc": "commit", "g": 1}))
        self.assertEqual(state["rd"]["r1"]["st"], "one")
        self.assertEqual(state["rd"]["r1"]["s1"]["rows"], [1, 2])
        self.assertNotIn("hist", state)

    def test_a_state_without_a_replay_variable_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            GEN.read_trace(SAMPLE.replace("/\\ lk = 2\n", ""))


class WrapperTest(unittest.TestCase):
    def test_every_action_but_the_left_out_ones_is_labelled_once_under_its_binder(self) -> None:
        text = GEN.wrapper(MODEL, [])
        labelled = re.findall(r'act\' = \[a \|-> "(\w+)"', text)
        names = {name for _, name, _ in GEN.actions(MODEL)}
        self.assertEqual(set(labelled), names - GEN.OUT)
        self.assertEqual(len(labelled), len(set(labelled)))
        # an existential reaches every disjunct of its line: P_CleanJl(g) keeps the binder of P_CleanDb(g)
        self.assertIn('(\\E g \\in Gens : P_CleanJl(g) /\\ act\' = [a |-> "P_CleanJl", p |-> g])', text)
        self.assertIn('(\\E r \\in Readers : R_End(r) /\\ act\' = [a |-> "R_End", p |-> r])', text)
        self.assertIn('(\\E i \\in Files : P_ReObs(i) /\\ act\'', text)

    def test_the_left_out_branches_are_guarded(self) -> None:
        text = GEN.wrapper(MODEL, ["whole_cleanup", "edit_outside_the_load"])
        self.assertIn("(P_CleanDone /\\ (\\A g \\in Gens : g \\in Keep", text)
        self.assertIn('Editor(i) /\\ (ctl.pc \\notin {"obs2", "read1", "read2"})', text)

    def test_left_out_and_guarded_names_are_actions_of_the_model(self) -> None:
        names = {name for _, name, _ in GEN.actions(MODEL)}
        self.assertLessEqual(GEN.OUT, names)
        for branches in GEN.LEAVE.values():
            self.assertLessEqual(set(branches), names)
        for world in GEN.WORLDS.values():
            self.assertLessEqual(set(world["leave"]), set(GEN.LEAVE))
            self.assertIn("whole_cleanup", world["leave"])  # the replay compares the cleanup as one block
            self.assertIn("names_left", world["leave"])  # the code's names have no bound

    def test_every_disjunct_of_next_is_read(self) -> None:
        """Every operator `EngineStep`, `ReaderStep` and `EnvStep` name is an action of the wrapper (or OUT): none is
        dropped by the reader."""
        names = {name for _, name, _ in GEN.actions(MODEL)}
        for group in GEN.GROUPS:
            body = MODEL[MODEL.index(f"{group} ==") :].split("\n\n", 1)[0]
            used = set(re.findall(r"\b([A-Z][A-Za-z_]*[a-z][A-Za-z0-9_]*)\(?", body.split("==", 1)[1]))
            used -= {"Gens", "Files", "Readers"}
            self.assertLessEqual(used, names, group)

    def test_an_unreadable_disjunct_is_refused(self) -> None:
        broken = MODEL.replace("\\/ EngineCrash \\/ AppCrash", "\\/ (EngineCrash) \\/ AppCrash")
        self.assertNotEqual(broken, MODEL)
        with self.assertRaises(SystemExit):
            GEN.actions(broken)


class StalenessTest(unittest.TestCase):
    def test_the_committed_corpus_is_current(self) -> None:
        self.assertEqual(GEN.stale(), [], f"regenerate: {GEN.COMMAND}")

    def test_a_corpus_of_another_model_or_engine_jobs_reader_is_stale(self) -> None:
        lines = GEN.CORPUS.read_text(encoding="utf-8").splitlines()
        corpus = GEN.CORPUS
        for key in ("model", "engine_jobs_generator"):
            meta = json.loads(lines[0])
            meta["header"][key] = {"sha256": "0" * 64}
            with tempfile.TemporaryDirectory() as tmp, self.subTest(key=key):
                fake = Path(tmp) / "corpus.json"
                fake.write_text("\n".join([json.dumps(meta), *lines[1:]]) + "\n", encoding="utf-8")
                try:
                    GEN.CORPUS = fake
                    self.assertEqual(GEN.stale(), [key])
                finally:
                    GEN.CORPUS = corpus


if __name__ == "__main__":
    unittest.main()
