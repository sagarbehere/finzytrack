"""Meta-tests for formal/tla/engine-jobs/gen_traces.py (S0059): the trace module it writes, its reader of TLC's trace
values, and its staleness check. None runs TLC (the generator does, with Java); the conformance replay itself is
backend/tests/platform/test_engine_jobs_conformance.py.
"""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIR = REPO / "formal/tla/engine-jobs"


def _gen():
    spec = importlib.util.spec_from_file_location("_tla_engine_jobs_gen_traces", DIR / "gen_traces.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _gen()
MODEL = (DIR / "EngineJobs.tla").read_text(encoding="utf-8")

SAMPLE = r"""---------------- MODULE trace_0_0 -----------------
STATE_1 ==
/\ act = [a |-> "Init", p |-> 0]
/\ q = << [ w |-> 0, s |-> "none", k |-> "recover", wf |-> TRUE, conf |-> FALSE, req |-> "present",
     plan |-> [I |-> 0, J |-> -1], id |-> 1, own |-> "switch" ] >>
/\ runs = {}
/\ eng = [st |-> "none", p |-> 0, ended |-> [k |-> "none"]]
/\ ctl = [pc |-> "none", past |-> FALSE, rinit |-> FALSE, tried |-> {}]
/\ ses = [app |-> "boot", stops |-> 0, advice |-> "none", opened |-> {}]
/\ pst = [orphan |-> FALSE, mirror |-> << [fp |-> [I |-> 1]] >>]
/\ fs = [tc |-> ( <<"s1", "R">> :> 0 @@ <<"s1", "N">> :> 0 )]

STATE_2 ==
/\ act = [a |-> "UserRead", p |-> 2]
/\ q = <<>>
/\ runs = {[w |-> 2, s |-> "none", k |-> "read", wf |-> TRUE, conf |-> FALSE, req |-> "gone", id |-> 3, own |-> "user"]}
/\ eng = [st |-> "busy", p |-> 1]
/\ ctl = [pc |-> "rd_load", past |-> FALSE, rinit |-> FALSE]
/\ ses = [app |-> "run", stops |-> 2, advice |-> "timer", opened |-> {2}]
/\ pst = [orphan |-> TRUE]
/\ fs = [tc |-> ( <<"s1", "R">> :> 0 )]

=================================================
"""


class ReaderTest(unittest.TestCase):
    def test_reads_the_shapes_the_traces_use(self) -> None:
        value = GEN.Reader('[a |-> <<1, -2, "x\\"y">>, b |-> {TRUE, FALSE}, c |-> [d |-> <<>>], e |-> {}]').value()
        self.assertEqual(value, {"a": [1, -2, 'x"y'], "b": {"set": [True, False]}, "c": {"d": []}, "e": {"set": []}})

    def test_refuses_what_it_does_not_read(self) -> None:
        for text in ['( <<"s1", "R">> :> 0 )', "[a |-> 1", "[a |-> 1] 2", "<<1, 2", "1..3"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                GEN.Reader(text).value()

    def test_reads_a_trace_keeping_only_the_replays_variables(self) -> None:
        states = [GEN.compact(s) for s in GEN.read_trace(SAMPLE)]
        self.assertEqual(len(states), 2)
        first, second = states
        self.assertEqual(first["act"], {"a": "Init", "p": 0})
        self.assertEqual(first["q"], [{"id": 1, "k": "recover", "w": 0, "wf": True, "conf": False, "req": "present",
                                       "own": "switch"}])  # fmt: skip
        self.assertIsNone(first["run"])
        self.assertEqual(second["run"]["req"], "gone")
        self.assertEqual(second["ses"], {"app": "run", "stops": 2, "advice": "timer", "opened": [2]})
        self.assertEqual(second["pst"], {"orphan": True})
        self.assertEqual(second["eng"], {"st": "busy"})
        self.assertEqual(second["ctl"], {"pc": "rd_load", "past": False, "rinit": False})

    def test_a_state_without_a_replay_variable_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            GEN.read_trace(SAMPLE.replace("/\\ runs = {}\n", ""))


class WrapperTest(unittest.TestCase):
    def test_every_action_but_the_left_out_ones_is_labelled_once(self) -> None:
        text = GEN.wrapper(MODEL, [])
        labelled = re.findall(r'act\' = \[a \|-> "(\w+)"', text)
        names = {name for _, name, _ in GEN.actions(MODEL)}
        self.assertEqual(set(labelled), names - GEN.OUT)
        self.assertFalse(set(labelled) & GEN.OUT)
        self.assertEqual(labelled.count("UserRead"), 1)

    def test_the_dispatch_is_taken_first_and_guards_are_applied(self) -> None:
        text = GEN.wrapper(MODEL, ["cancel"])
        self.assertIn("TNext == IF ENABLED Eager THEN TEager ELSE TOther", text)
        self.assertIn("Eager == Dispatch \\/ DispatchUnavail \\/ SpawnStep", text)
        self.assertIn('(\\E i \\in 1..Len(q) : UserCancelQueued(i) /\\ FALSE /\\ act\'', text)
        for name, guard in GEN.GUARDS.items():
            self.assertIn(guard, text, name)

    def test_left_out_and_guarded_names_are_actions_of_the_model(self) -> None:
        names = {name for _, name, _ in GEN.actions(MODEL)}
        self.assertLessEqual(GEN.OUT, names)
        self.assertLessEqual(set(GEN.GUARDS), names)
        for branches in GEN.LEAVE.values():
            self.assertLessEqual(set(branches), names)
        for world in GEN.WORLDS.values():
            self.assertLessEqual(set(world["leave"]), set(GEN.LEAVE))

    def test_an_unreadable_disjunct_is_refused(self) -> None:
        broken = MODEL.replace("\\/ Drain \\/ OrphanEnd", "\\/ (Drain) \\/ OrphanEnd")
        with self.assertRaises(SystemExit):
            GEN.actions(broken)

    def test_a_world_constant_that_does_not_exist_is_refused(self) -> None:
        worlds = dict(GEN.WORLDS)
        try:
            GEN.WORLDS["X"] = {"base": "S_queue", "set": {"NoSuchConstant": "1"}}
            with self.assertRaises(SystemExit):
                GEN.constants("X")
        finally:
            GEN.WORLDS.clear()
            GEN.WORLDS.update(worlds)


class StalenessTest(unittest.TestCase):
    def test_the_committed_corpus_is_current(self) -> None:
        self.assertEqual(GEN.stale(), [], f"regenerate: {GEN.COMMAND}")

    def test_a_corpus_of_another_model_is_stale(self) -> None:
        lines = GEN.CORPUS.read_text(encoding="utf-8").splitlines()
        meta = json.loads(lines[0])
        meta["header"]["model"] = {"sha256": "0" * 64}
        corpus = GEN.CORPUS
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "corpus.json"
            fake.write_text("\n".join([json.dumps(meta), *lines[1:]]) + "\n", encoding="utf-8")
            try:
                GEN.CORPUS = fake
                self.assertEqual(GEN.stale(), ["model"])
            finally:
                GEN.CORPUS = corpus


if __name__ == "__main__":
    unittest.main()
