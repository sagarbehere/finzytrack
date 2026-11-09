"""Meta-tests for formal/tla/engine-jobs/gen_traces.py (S0059): the trace module it writes, its reader of TLC's trace
values, and its staleness check. None runs TLC (the generator does, with Java); the conformance replays themselves are
engine/tests/ledger/test_save_conformance.py, test_create_conformance.py and test_recover_conformance.py (S0091).
"""

from __future__ import annotations

import hashlib
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
/\ app = "recover"
/\ run = [k |-> "none", id |-> "none"]
/\ ctl = [pc |-> "rc_init", tried |-> {}, s |-> "none", fi |-> 1, rj |-> "none",
     plan |-> [R |-> 0, N |-> 0, I |-> 0, J |-> 0, K |-> 0], rbt |-> "none", repl |-> FALSE, put |-> {}]
/\ fs = [cur |-> [R |-> 1, N |-> 0, I |-> 1, J |-> 1, K |-> 1],
     tc |-> ( <<"b2", "R">> :> 0 @@ <<"s1", "R">> :> 0 @@ <<"s1", "N">> :> 3 ),
     sg |-> ( <<"b2", "R">> :> 0 @@ <<"s1", "R">> :> -1 @@ <<"s1", "N">> :> 0 ),
     rt |-> ( <<"b2", "R">> :> 0 @@ <<"s1", "R">> :> 0 @@ <<"s1", "N">> :> 0 ),
     kp |-> ( <<"b2", "R">> :> {} @@ <<"s1", "R">> :> {11} @@ <<"s1", "N">> :> {} ),
     bk |-> [s1 |-> 0, s2 |-> 0, s3 |-> 0], ud |-> {}]
/\ jr = [b2 |-> [st |-> "none", files |-> {}], s1 |-> [st |-> "part", files |-> {"R", "N"}]]
/\ gh = [told |-> [s1 |-> "none", s2 |-> "none", s3 |-> "none"], ed |-> [R |-> 0]]
/\ tgt = [ex |-> FALSE, c |-> [a |-> "no", m |-> "no", o |-> "no", x |-> "frn"],
     pl |-> [a |-> {"no"}, m |-> {"no"}, o |-> {"no"}, x |-> {"frn"}]]
/\ env = [lnk |-> FALSE, unr |-> TRUE, outside |-> 0]

STATE_2 ==
/\ act = [a |-> "S_TempStart", p |-> 0]
/\ app = "run"
/\ run = [k |-> "save", id |-> "s1"]
/\ ctl = [pc |-> "s_tw2", tried |-> {}, s |-> "s1", fi |-> 2, rj |-> "none",
     plan |-> [R |-> 1, N |-> 0, I |-> 1, J |-> 0, K |-> 0], rbt |-> "none", repl |-> FALSE, put |-> {}]
/\ fs = [cur |-> [R |-> 1, N |-> 0, I |-> 1, J |-> 1, K |-> 1],
     tc |-> ( <<"b2", "R">> :> 0 @@ <<"s1", "R">> :> 0 @@ <<"s1", "N">> :> 3 ),
     sg |-> ( <<"b2", "R">> :> 0 @@ <<"s1", "R">> :> -1 @@ <<"s1", "N">> :> 0 ),
     rt |-> ( <<"b2", "R">> :> 0 @@ <<"s1", "R">> :> 0 @@ <<"s1", "N">> :> 0 ),
     kp |-> ( <<"b2", "R">> :> {} @@ <<"s1", "R">> :> {11} @@ <<"s1", "N">> :> {} ),
     bk |-> [s1 |-> 0, s2 |-> 0, s3 |-> 0], ud |-> {}]
/\ jr = [b2 |-> [st |-> "none", files |-> {}], s1 |-> [st |-> "open", files |-> {"R", "N"}]]
/\ gh = [told |-> [s1 |-> "none", s2 |-> "none", s3 |-> "none"], ed |-> [R |-> 0]]
/\ tgt = [ex |-> TRUE, c |-> [a |-> "ours", m |-> "no", o |-> "no", x |-> "frn"],
     pl |-> [a |-> {"no", "torn", "ours"}, m |-> {"no"}, o |-> {"no"}, x |-> {"frn"}]]
/\ env = [lnk |-> TRUE, unr |-> FALSE, outside |-> 0]

=================================================
"""


class ReaderTest(unittest.TestCase):
    def test_reads_the_shapes_the_traces_use(self) -> None:
        value = GEN.Reader('[a |-> <<1, -2, "x\\"y">>, b |-> {TRUE, FALSE}, c |-> [d |-> <<>>], e |-> {}]').value()
        self.assertEqual(value, {"a": [1, -2, 'x"y'], "b": {"set": [True, False]}, "c": {"d": []}, "e": {"set": []}})

    def test_reads_a_function_of_tuples(self) -> None:
        value = GEN.Reader('( <<"s1", "R">> :> 0 @@ <<"s1", "N">> :> -1 )').value()
        self.assertEqual(value, {"fn": [[["s1", "R"], 0], [["s1", "N"], -1]]})

    def test_refuses_what_it_does_not_read(self) -> None:
        for text in ['( <<"s1", "R">> :> 0 2 )', '( <<"s1">> 0 )', "[a |-> 1", "[a |-> 1] 2", "<<1, 2", "1..3"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                GEN.Reader(text).value()

    def test_reads_a_trace_keeping_only_the_replays_variables(self) -> None:
        states = [GEN.compact(s) for s in GEN.read_trace(SAMPLE)]
        self.assertEqual(len(states), 2)
        first, second = states
        self.assertEqual(first["act"], {"a": "Init", "p": 0})
        self.assertEqual(first["app"], "recover")
        self.assertIsNone(first["run"])
        self.assertEqual(second["run"], {"k": "save", "id": "s1"})
        self.assertEqual(second["ctl"], {"pc": "s_tw2"})
        self.assertEqual(first["fs"], {"cur": {"R": 1, "N": 0, "I": 1, "J": 1}, "tc": {"s1/R": 0, "s1/N": 3},
                                       "sg": {"s1/R": -1, "s1/N": 0}, "rt": {"s1/R": 0, "s1/N": 0},
                                       "kp": {"s1/R": [11], "s1/N": []},
                                       "bk": {"s1": 0, "s2": 0, "s3": 0}})  # fmt: skip
        self.assertEqual(second["jr"], {"b2": "none", "s1": "open"})
        self.assertEqual(second["sv"], {"s": "s1", "fi": 2, "rj": "none", "plan": {"R": 1, "N": 0, "I": 1},
                                        "rbt": "none"})  # fmt: skip
        self.assertEqual(first["told"], {"s1": "none", "s2": "none", "s3": "none"})
        # S0084: the create's target, each name's power-loss values sorted
        self.assertEqual(first["tgt"], {"ex": False, "c": {"a": "no", "m": "no", "o": "no", "x": "frn"},
                                        "pl": {"a": ["no"], "m": ["no"], "o": ["no"], "x": ["frn"]}})  # fmt: skip
        self.assertEqual(second["tgt"]["pl"]["a"], ["no", "ours", "torn"])

    def test_a_recover_world_keeps_the_boot_journals_and_recover_alls_progress(self) -> None:
        """S0091 (BL-0164): in a recover world each state also carries `rc` (`recover_state`), and only there."""
        sample = SAMPLE.replace(
            'jr = [b2 |-> [st |-> "none", files |-> {}], s1',
            'jr = [b2 |-> [st |-> "open", files |-> {"R", "J"}, why |-> "stopped"], '
            'b3 |-> [st |-> "topen", files |-> {"K"}, why |-> ""], s1',
        ).replace('[st |-> "part", files', '[st |-> "part", why |-> "", files').replace(
            '[st |-> "open", files |-> {"R", "N"}]', '[st |-> "open", why |-> "", files |-> {"R", "N"}]'
        )
        sample = sample.replace("put |-> {}]", "put |-> {}, taken |-> FALSE]")
        sample = sample.replace('<<"b2", "R">> :> 0 @@', '<<"b2", "R">> :> 0 @@ <<"b2", "J">> :> 0 @@ <<"b3", "K">> :> 2 @@')
        sample = sample.replace('<<"b2", "R">> :> {} @@', '<<"b2", "R">> :> {} @@ <<"b2", "J">> :> {12} @@ <<"b3", "K">> :> {} @@')
        first, second = (GEN.compact(s, True) for s in GEN.read_trace(sample))
        self.assertEqual(first["rc"]["cur"], {"R": 1, "J": 1, "K": 1})
        self.assertEqual(first["rc"]["jr"], {"b2": "open", "b3": "topen"})
        self.assertEqual(first["rc"]["why"], {"b2": "stopped", "b3": ""})
        self.assertEqual(first["rc"]["tc"], {"b2/R": 0, "b2/J": 0, "b3/K": 2})
        self.assertEqual(first["rc"]["kp"], {"b2/R": [], "b2/J": [12], "b3/K": []})
        self.assertEqual((first["rc"]["rj"], first["rc"]["fi"], first["rc"]["tried"]), ("none", 1, []))
        self.assertEqual((first["rc"]["lnk"], first["rc"]["unr"]), (False, True))
        self.assertEqual(second["rc"]["lnk"], True)
        self.assertNotIn("rc", GEN.compact(GEN.read_trace(sample)[0]))

    def test_a_state_without_a_replay_variable_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            GEN.read_trace(SAMPLE.replace('/\\ app = "recover"\n', ""))


class CorpusFormTest(unittest.TestCase):
    """S0064: the save state kept only around saves, the steps as deltas, the traces kept by the branches they add."""

    def test_the_deltas_rebuild_the_states(self) -> None:
        a = {"act": {"a": "Init"}, "run": None, "fs": {"x": 1}}
        b = {"act": {"a": "StartSave"}, "run": None}
        c = {"act": {"a": "S_Gate"}, "run": {"k": "save"}, "fs": {"x": 2}}
        steps = GEN.delta([a, b, c])
        self.assertEqual(steps[1], {"act": {"a": "StartSave"}, "-": ["fs"]})
        self.assertEqual(GEN.undelta(steps), [a, b, c])

    def test_the_save_state_is_kept_only_around_a_save(self) -> None:
        def st(k: str | None) -> dict:
            return {"run": None if k is None else {"k": k}, "fs": 1, "jr": 2, "sv": 3, "told": 4, "app": "run"}

        out = GEN.prune([st(None), st("recover"), st("save"), st(None), st(None)])
        self.assertEqual(["fs" in s for s in out], [True, False, True, True, False])
        self.assertEqual(out[1]["app"], "run")
        # S0084: around a copy too (the journals it checks), never around a create (its target is `tgt`)
        out = GEN.prune([st(None), st("create"), st("copy"), st(None), st(None)])
        self.assertEqual(["fs" in s for s in out], [True, False, True, True, False])

    def test_the_traces_that_add_branches_are_kept_first(self) -> None:
        def tr(*acts: str) -> list[dict]:
            return [{"act": {"a": a}, "run": None, "ctl": {"pc": "none"}} for a in ("Init", *acts)]

        long = [tr("A"), tr("A"), tr("A", "B"), tr("C")]
        self.assertEqual(GEN.select(long, 2), [long[2], long[3]])
        self.assertEqual(GEN.select(long, 4), long)


class WrapperTest(unittest.TestCase):
    def test_every_action_but_the_left_out_ones_is_labelled_once(self) -> None:
        jobs = set().union(*GEN.JOB_ACTIONS.values())
        rc = GEN.JOB_ACTIONS["recover"]
        names = {name for _, name, _ in GEN.actions(MODEL)}
        for world_jobs, left in (([], GEN.OUT | jobs), (["create", "copy"], GEN.OUT | rc),
                                 (["copy"], GEN.OUT | GEN.JOB_ACTIONS["create"] | rc),
                                 (["recover"], GEN.OUT | GEN.JOB_ACTIONS["create"] | GEN.JOB_ACTIONS["copy"])):  # fmt: skip
            with self.subTest(jobs=world_jobs):
                labelled = re.findall(r'act\' = \[a \|-> "(\w+)"', GEN.wrapper(MODEL, [], world_jobs))
                self.assertEqual(set(labelled), names - left)
                self.assertEqual(len(labelled), len(set(labelled)))
                self.assertEqual(labelled.count("Editor"), 1)

    def test_a_world_without_a_job_cannot_take_its_actions(self) -> None:
        """S0084: `JOB_ACTIONS` are left out of a world that does not name the job, which changes no behaviour only
        while that world's constants never start the job. Must not pass: a save world with `DoCopy = TRUE`."""
        self.assertEqual(GEN.INERT_ELSEWHERE, {"create", "copy"})
        for name, world in GEN.WORLDS.items():
            consts = GEN.constants(name)
            jobs = world.get("jobs", [])
            self.assertLessEqual(set(jobs), set(GEN.JOB_ACTIONS), name)
            if "create" not in jobs:
                self.assertEqual((consts["CreateTarget"], consts["MaxDrop"]), ('"none"', "0"), name)
            if "copy" not in jobs:
                self.assertEqual(consts["DoCopy"], "FALSE", name)

    def test_a_recover_world_runs_nothing_but_recover_all(self) -> None:
        """S0091: a recover world names only `recover`, starts no save (`MaxSaves = 0`), create or copy, so its replay
        (`test_recover_conformance.py`) meets recover-all alone; and the save replay's worlds are not recover worlds
        (their boot journals are `{}`). Must not pass: a recover world with `MaxSaves = 1`."""
        recover = [n for n, w in GEN.WORLDS.items() if "recover" in w.get("jobs", [])]
        self.assertGreaterEqual(len(recover), 5)
        for name in recover:
            consts = GEN.constants(name)
            self.assertEqual(GEN.WORLDS[name]["jobs"], ["recover"], name)
            self.assertEqual((consts["MaxSaves"], consts["CreateTarget"], consts["DoCopy"]), ("0", '"none"', "FALSE"))
            self.assertNotEqual(consts["BootJ"], "{}", name)

    def test_the_left_out_branches_are_guarded(self) -> None:
        text = GEN.wrapper(MODEL, ["crash_in_save", "edit_at_swap"])
        self.assertIn('(EngineCrash /\\ (run.k = "save") /\\ act\'', text)
        self.assertIn('(\\E f \\in Files : Editor(f) /\\ (Busy /\\ run.k = "save" /\\ ctl.pc = "s_sw") /\\ act\'', text)
        for name, guard in GEN.GUARDS.items():
            self.assertIn(f"/\\ ({guard})", text, name)  # parenthesized (S0064: a guard with a disjunction)

    def test_left_out_and_guarded_names_are_actions_of_the_model(self) -> None:
        names = {name for _, name, _ in GEN.actions(MODEL)}
        self.assertLessEqual(GEN.OUT, names)
        self.assertLessEqual(set().union(*GEN.JOB_ACTIONS.values()), names)
        self.assertFalse(GEN.OUT & set().union(*GEN.JOB_ACTIONS.values()))
        self.assertLessEqual(set(GEN.GUARDS), names)
        for branches in GEN.LEAVE.values():
            self.assertLessEqual(set(branches), names)
        for world in GEN.WORLDS.values():
            self.assertLessEqual(set(world["leave"]), set(GEN.LEAVE))

    def test_an_unreadable_disjunct_is_refused(self) -> None:
        broken = MODEL.replace("\\/ PathChange \\/ Unreach", "\\/ (PathChange) \\/ Unreach")
        self.assertNotEqual(broken, MODEL)
        with self.assertRaises(SystemExit):
            GEN.actions(broken)

    def test_a_world_constant_that_does_not_exist_is_refused(self) -> None:
        worlds = dict(GEN.WORLDS)
        try:
            GEN.WORLDS["X"] = {"base": "S_save_crash", "set": {"NoSuchConstant": "1"}}
            with self.assertRaises(SystemExit):
                GEN.constants("X")
        finally:
            GEN.WORLDS.clear()
            GEN.WORLDS.update(worlds)


class StalenessTest(unittest.TestCase):
    def test_the_committed_corpus_is_current(self) -> None:
        self.assertEqual(GEN.stale(), [], f"regenerate: {GEN.COMMAND}")

    def test_the_save_create_and_copy_traces_are_as_s0084_left_them(self) -> None:
        """S0091 added the recover worlds and walks recover-all's actions only there (`JOB_ACTIONS`), so every trace of
        the save, create and copy worlds is byte for byte as S0084 generated it (217 rows; checked against the corpus
        of 06d538f0 when S0091 regenerated it). A change here must be deliberate: a world added or changed, never a
        side effect of another job's actions (S0084: dropping actions from OUT changed 122 of 144 save traces)."""
        lines = GEN.CORPUS.read_text(encoding="utf-8").splitlines()
        worlds = json.loads(lines[0])["header"]["worlds"]
        rows = [r for r in lines[1:] if "recover" not in worlds[json.loads(r)["world"]].get("jobs", [])]
        self.assertEqual(len(rows), 217)
        sha256 = "dd0547f4c71605fd00214f7d89c56eda484b3a75ac064bba1ec346a33612ed7f"
        self.assertEqual(hashlib.sha256("\n".join(rows).encode()).hexdigest(), sha256)

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
