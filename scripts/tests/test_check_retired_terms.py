"""The retired-terms check (S0047; scripts/check_retired_terms.py; ARCHITECTURE.md §0).

One must-not-pass test per rule: each retired term in each kind of current-state document, each exception and how
narrow it is, the allowlist's own rules, and the engine-jobs slice's terms active since S0057 (BL-0150).
The trap words of the convergence audit (bare slot, lane, gate, admit, warm, window, share, territory, stall,
heartbeat, timeout, Busy; RECOVERING) must pass. The last test runs the check over this repository, which is how it
runs at every push and every Stop (through `scripts/run_meta_tests.py`, as `check_failure_modes.py` does).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_retired_terms as crt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_retired_terms.py"
# The current-state documents, written out here and not read from the script (S0047 verifier V1: a test that
# iterates the script's own list cannot notice a document dropped from it).
EXPECTED_FIXED = (
    "design/ARCHITECTURE.md",
    "design/INVARIANTS.md",
    "design/ENGINEERING.md",
    "design/glossary.md",
    "design/capabilities.toml",
    "design/backlog.md",
    # S0055: the files agents read first, which the frontend sweep covered
    "design/README.md",
    "design/process/PROCESS.md",
    "CLAUDE.md",
    "backend/CLAUDE.md",
    "engine/CLAUDE.md",
    "frontend/CLAUDE.md",
    # S0068: the other live documents an agent reads for guidance (owner, 2026-10-31)
    "design/process/escalation-rubric.md",
    "design/journal/README.md",
    "design/inbox/README.md",
    "design/generated/README.md",
    "formal/tla/engine-jobs/HOW_TO_RUN.md",
)
# S0055: every agent definition and every skill, found by these patterns (one sample file each in a scratch root).
EXPECTED_GLOB_SAMPLES = (".claude/agents/a.md", ".claude/skills/x/SKILL.md")

# One line per refuse-now term, as an abandoned design would have written it.
RETIRED_LINES = {
    "semaphore": "Loads run under the heavy-load semaphore.",
    "limiter": "Writes run under a per-user `CapacityLimiter(1)`.",
    "heavy-load gate": "Blocking work runs behind the heavy-load gate.",
    "engine pool": "The engine pool grows with free memory.",
    "slot count": "The slot count is computed at start.",
    "engine slots": "Two engine slots serve the queue.",
    "admission": "Admission checks free memory first.",
    "memory pressure": "Under memory pressure the app waits.",
    "pressure level": "Three pressure levels decide it.",
    "Run anyway": 'The dialog offers "Run anyway".',
    "runaway": "A runaway job is stopped.",
    "learned usual": 'It compares with the learned "usual" time.',
    "engine or workspace lane": "Each per-workspace lane has its engine.",
    "Share workspace or mode": "Open it in Share mode.",
    "coexistence guard": "The coexistence guard checks the old app.",
    "legacy-lock wait": "The save waits through legacy-lock waits.",
    "territory": "The territory rule keeps files apart.",
    "LEDGER_SHARED": "Refused with `LEDGER_SHARED`.",
    "LEDGER_OUTSIDE_FOLDER": "Refused with `LEDGER_OUTSIDE_FOLDER`.",
    "FOLDER_IN_OTHER_WORKSPACE": "Refused with `FOLDER_IN_OTHER_WORKSPACE`.",
    "journal index": "The journal index lists every journal.",
    "cross-workspace journal": "A cross-workspace journal is recovered from the open one.",
    "several open workspaces": "With several open workspaces each has a container.",
    "several native windows": "Several native windows on different workspaces.",
    "multi-window": "The multi-window mode opens two.",
    "warm container": "One warm container is kept.",
    "kept warm": "The last container is kept warm.",
    "WRITE_IN_PROGRESS": "Refused with `WRITE_IN_PROGRESS`.",
    "phase C": "Tested when phase C lands.",
    "engine-scheduler.md": "See `design/specs/engine-scheduler.md` §6.",
    "engine scheduler": "The engine scheduler spec decides it.",
    "scheduler spec or slice": "With the scheduler slice the engine leads its group.",
    # S0055: the React target of ADR-0006, superseded by ADR-0053 (Vue 3)
    "React": "The UI is React 19 with the compiler.",
    "react package or rule": "Routes use react-router; machines use `@xstate/react`; `react/no-danger` is on.",
    "JSX or TSX": "Views are `*.tsx` files; a rule never lives only in JSX.",
    "Radix": "Pickers are built on Radix primitives.",
    "shadcn/ui": "Vendored shadcn/ui components.",
    "sonner": "Toasts use sonner, outside inert regions.",
    "React hook API": "Stores are read through `useSyncExternalStore`; no `useMemo`.",
    "QueryClientProvider": "The shell mounts `QueryClientProvider`.",
    "custom hook": "Headless logic as custom hooks.",
    "function or class component": "Hooks and plain function components (no class components).",
    "tsc type gate": "`tsc --noEmit` is the type gate.",
    "S13 pending": "React frontend, pending S13; Vue if S13 fails.",
    "Vue fallback": "The pins of the Vue fallback stack.",
    # S0056: the features' folder of composables was named `hooks/` (owner, 2026-10-29: rename to composables)
    "hooks/ folder": "    hooks/          the feature's composables; logic in `features/transactions/hooks/useEditBuffer`.",
    # S0057: the engine client's time limits and bounds are gone (engine-jobs spec §5.4, §5.5)
    "per-request time limit": "Each call has a per-request time limit.",
    "handshake time limit": "A start waits at most the handshake timeout.",
    "start_timeout": "The policy's `start_timeout` bounds the handshake.",
    "close_timeout": "The reap waits `close_timeout`.",
    "longest_call": "A call returns within `RestartPolicy.longest_call(timeout)`.",
    "longest_start": "A start is bounded by `RestartPolicy.longest_start`.",
    "TimedOut": "`Busy` -> `Crashed` on `TimedOut`.",
    "-I -m ledger_engine": "The engine runs as `python -I -m ledger_engine`.",
    "outcome unknown": "A write past its limit is outcome unknown, checking.",
    "quit grace": "The shell waits out its quit grace.",
    # S0068 (ADR-0067): the R4 gate and the accounting refusals, as the old design wrote them
    "check_pending": "the save refuses `WRITES_PAUSED {check_pending}` naming the record",
    "check_failed": "a mismatch refuses `check_failed`",
    "could_not_check": "the third failure renames it `could_not_check`",
    "could not check (R4)": "\"Finzytrack could not check your last change, so it did not save this one.\"",
    "Resume saving": "the user lifts the pause with Resume saving",
    "R4 attempt count": "each failure creates `<name>.r4-attempt-<n>` beside the record",
    "r4_confirm": "the sidecar asks `r4_confirm` in a fresh engine",
    "r4_resume": "Resume saving's op is `r4_resume`",
    ".mismatch record": "the record becomes `.mismatch` and saving is paused",
    "R4 mismatch outcome": "R4's confirm answers `mismatch`",
    "R4 nondeterministic outcome": "the parses agree: `nondeterministic`",
    "superseded_build": "the record becomes `.done {superseded_build}`",
    "owed R4": "the next save runs the owed R4 first",
    "R4 first": "1. **R4 first, if owed.**",
    "R4 gate": "the R4 gate survives a crash",
    "never acknowledgeable": "booking errors are never acknowledgeable",
    "May ask to acknowledge": "- **May ask to acknowledge:** BalanceError on the inserted assertion",
    "unless acknowledged": "the semantic diff equals the intent, no new errors unless acknowledged, the hash check passes",
    "pause on R4": "Finzytrack's check of your last change found a difference. Saving is paused.",
    "NoSaveWhileR4Owed": "the model checks `NoSaveWhileR4Owed`",
    "R4GateLiftedOnlyByUser": "and `R4GateLiftedOnlyByUser`",
    "OwedR4BeforeNextSave": "`OwedR4BeforeNextSave`: an owed R4 runs before the next save lands",
    "NoFalseMismatch": "`NoFalseMismatch` (O1)",
}
# Words the audit says must never be refused, in the senses the current design uses them.
TRAP_LINES = [
    # S0068: the current design's own words about R4 and the preview pass
    "R4 compares and shows a notice; it gates nothing (ADR-0067).",
    "the closed record is the save's last state; a mismatch of the fingerprint refuses `LEDGER_EXTERNALLY_CHANGED`",
    "R4 says `differs`; `pass` and `superseded` show nothing",
    "the preview lists the new errors and the user may save anyway",
    "new errors are listed in a preview and written only if the user saves anyway; the user acknowledged the notice",
    "a read-back mismatch rolls back",
    "A slot of the form; the one engine slot; an open slot is a suggestion target.",
    "The process lanes: full lane, light lane; the specialist lane.",
    "The workspace gate admits the request; the admitted key; a gate check.",
    "The workspace stays `WARMING`; the warm-up of the cache.",
    "The display delay; a matching window; five-minute window; native window.",
    "Share a dashboard by file; a shared helper; shares of a fund.",
    "Tax territory and portfolio analytics; union territory.",
    "`test.stall` makes the engine stall; a heartbeat; a timeout of the HTTP client.",
    "`EngineClient.state` reads `Busy`; the queue is busy.",
    "The workspace enters `RECOVERING`; recovery runs first.",
    "The phase change is shown; phase c of the plan.",
    "The scheduler thread runs the pure tables; the scheduler starts the oldest job.",
    "The model `formal/tla/engine-jobs/` and `scripts/tests/test_tla_engine_jobs.py`; EngineJobs.tla.",
    "A rate limiter on the price source.",
    "The CSV importer guesses the delimiter from the first line; delimiters per bank.",  # S0047 verifier V4
    "Engine-jobs spec §6; the engine-jobs slice.",
    # S0055: the Vue stack's own words, and words that only look like the React target
    "Toasts use vue-sonner; icons from `@lucide/vue`; shadcn-vue on Reka UI.",
    "`vue-tsc --noEmit` is the type gate; `@vitejs/plugin-vue` builds `.vue` files.",
    "Reactive state from `@vue/reactivity`; the view should react to a change and reacts once.",
    "Git hooks run first; Vue Query hooks; the `composables/` folder; functional components.",
    # S0056: git and Claude Code hook paths are not the frontend folder
    "`.githooks/pre-push`, `.claude/hooks/guards.py`, `core.hooksPath`, `scripts/tests/hook_pins/`.",
    "`parseInt` with radix 10.",
    "`useConfirm`, `useUnsavedGuard`, `useTemplateRef`, `useFieldErrors`, `useMutation`.",
    "Spike S13 decided Vue 3 (ADR-0053); the S13 measurements.",
    "`app.config.errorHandler` catches render errors.",
]


def hits(text: str, path: str = "design/specs/x.md") -> list[crt.Hit]:
    return crt.scan_text(path, text, crt.ACTIVE)


def names(found: list[crt.Hit]) -> list[str]:
    return [h.term for h in found]


class Terms(unittest.TestCase):
    def test_every_active_term_has_a_must_not_pass_line(self) -> None:
        self.assertEqual({t.name for t in crt.ACTIVE}, set(RETIRED_LINES))

    def test_each_retired_term_is_refused(self) -> None:
        for term, line in RETIRED_LINES.items():
            with self.subTest(term=term):
                self.assertIn(term, names(hits(line)), line)

    def test_every_limiter_spelling_is_refused(self) -> None:
        for line in ("the per-workspace limiter", "a `WorkspaceLimiter`", "Limiters are gone", "two limiters"):
            with self.subTest(line=line):
                self.assertEqual(names(hits(line)), ["limiter"], line)

    def test_every_spelling_of_the_hooks_folder_is_refused(self) -> None:  # S0056
        for line in ("    hooks/          the feature's composables", "the `hooks/` folder", "src/hooks/useTree.ts",
                     "`features/accounts/hooks/useTree.ts`", "src/features/transactions/hooks", "(hooks/, machines/)",
                     # S0056 verifier round 1, F4 (the verifier's probes)
                     "frontend/src/hooks/useTheme.ts holds shared composables", "`src/hooks/`",
                     "the features' `hooks` folder", "features/accounts/Hooks/useTree.ts",
                     "feature folders: queries.ts, machines/, hooks/, views/", "src/features/x/hooks.ts",
                     "the `hooks`\ndirectory of a feature"):
            with self.subTest(line=line):
                self.assertEqual(names(hits(line)), ["hooks/ folder"], line)

    def test_git_and_claude_code_hooks_in_prose_pass(self) -> None:  # S0056 verifier round 1, F4
        for line in ("Agents, skills and hooks/guards (S0000)", "Claude Code hooks/skills are installed once",
                     "the guard in `hooks/guards.py` of `.claude/`", "`~/.claude/hooks/`",
                     "git config core.hooksPath .githooks", "the `hooks` key of `.claude/settings.json`",
                     "`.claude/hooks/guards.py` and `.githooks/pre-commit`"):
            with self.subTest(line=line):
                self.assertEqual(names(hits(line)), [], line)

    def test_a_term_split_by_a_line_break_is_refused_on_its_first_line(self) -> None:  # S0047 verifier V6
        found = hits("Saves go through the engine\npool, one slot per workspace.\n")
        self.assertEqual([(h.term, h.line) for h in found], [("engine pool", 1)])
        self.assertEqual(names(hits("the heavy-load\ngate")), ["heavy-load gate"])

    def test_lines_are_joined_only_when_both_are_checked(self) -> None:
        text = "**Abandoned, so stale wherever it is stated as current:** an engine\npool here is fine.\n"
        self.assertEqual(hits(text, "design/ARCHITECTURE.md"), [])
        # two checked lines with an exempt one between them are not neighbours
        text = "Saves use the engine\n**Abandoned, so stale wherever it is stated as current:** x\npool later.\n"
        self.assertEqual(hits(text, "design/ARCHITECTURE.md"), [])

    def test_an_owner_quote_split_by_a_line_break_is_still_exempt(self) -> None:
        self.assertEqual(hits('as decided (owner: "no engine\npool, ever")\n'), [])

    def test_case_does_not_hide_a_term(self) -> None:
        for line in ("SEMAPHORE", "Engine Pool", "run ANYWAY", "Kept Warm"):
            with self.subTest(line=line):
                self.assertNotEqual(hits(line), [], line)

    def test_codes_are_matched_as_written(self) -> None:
        # a code is case-sensitive, so its prose neighbours pass and the code itself does not
        self.assertEqual(hits("a write in progress; a ledger shared by two apps"), [])
        self.assertEqual(names(hits("`WRITE_IN_PROGRESS`")), ["WRITE_IN_PROGRESS"])

    def test_the_trap_words_pass(self) -> None:
        for line in TRAP_LINES:
            with self.subTest(line=line):
                self.assertEqual(hits(line), [], line)

    def test_the_engine_jobs_terms_are_active_since_s0057(self) -> None:
        """S0057 (BL-0150) built the client without time limits: the terms its slice retires are refused now, in
        the same list as the others (the former `AFTER_ENGINE_JOBS`)."""
        retired = {
            "per-request time limit": "the per-request time limit",
            "handshake time limit": "the handshake timeout",
            "start_timeout": "`start_timeout`",
            "close_timeout": "`close_timeout`",
            "longest_call": "`RestartPolicy.longest_call`",
            "longest_start": "`RestartPolicy.longest_start`",
            "TimedOut": "`TimedOut`",
            "-I -m ledger_engine": "python -I -m ledger_engine",
            "outcome unknown": "the outcome unknown state",
            "quit grace": "the shell's quit grace",
        }
        self.assertFalse(hasattr(crt, "AFTER_ENGINE_JOBS"), "the engine-jobs list is merged into ACTIVE")
        self.assertLessEqual(set(retired), {t.name for t in crt.ACTIVE})
        for term, line in retired.items():
            with self.subTest(term=term):
                self.assertIn(term, names(hits(line)))


class Exceptions(unittest.TestCase):
    def test_an_owner_quote_may_hold_a_retired_term(self) -> None:
        self.assertEqual(hits('as decided (owner: "no engine pool, ever", 2026-10-27)'), [])
        self.assertEqual(hits('Owner: "we drop the semaphore."'), [])

    def test_a_term_outside_the_owner_quote_is_still_refused(self) -> None:
        self.assertEqual(names(hits('owner: "simplify", so the semaphore stays')), ["semaphore"])

    def test_a_quote_not_from_the_owner_is_refused(self) -> None:
        self.assertEqual(names(hits('the reviewer: "the engine pool is fine"')), ["engine pool"])

    def test_the_owner_quote_forms_of_the_repository_are_exempt(self) -> None:
        for line in (
            '(owner, 2026-10-27: "no engine pool")',
            'Owner, 2026-10-27, in session: "no engine pool"',
            "with the owner's approval, 2026-10-25, ADR-0063: \"no engine pool\"",
            'Owner A: "no engine pool"',
        ):
            with self.subTest(line=line):
                self.assertEqual(hits(line), [], line)

    def test_a_quote_near_the_word_owner_is_not_the_owners(self) -> None:  # S0047 verifier V2
        for line, term in (
            ('The owner-facing dialog reads "Run anyway" while a save runs.', "Run anyway"),
            ('The owner rejected the agent proposal "keep the engine pool".', "engine pool"),
            ("On the owner's machine the \"heavy-load gate\" admits two loads.", "heavy-load gate"),
            ('owner, 2026-10-27, rejected it: "the engine pool"', "engine pool"),
        ):
            with self.subTest(line=line):
                self.assertEqual(names(hits(line)), [term], line)

    def test_the_abandoned_paragraph_of_architecture_is_exempt(self) -> None:
        text = "**Abandoned, so stale wherever it is stated as current:** an engine pool, slots, admission.\n"
        self.assertEqual(hits(text, "design/ARCHITECTURE.md"), [])

    def test_only_that_paragraph_is_exempt(self) -> None:
        text = ("**Abandoned, so stale wherever it is stated as current:** an engine pool.\n"
                "Writes go through the engine pool.\n")
        found = hits(text, "design/ARCHITECTURE.md")
        self.assertEqual([(h.term, h.line) for h in found], [("engine pool", 2)])

    def test_the_marker_elsewhere_exempts_nothing(self) -> None:
        text = "**Abandoned, so stale wherever it is stated as current:** an engine pool.\n"
        self.assertEqual(names(hits(text, "design/ENGINEERING.md")), ["engine pool"])
        self.assertEqual(names(hits("Note: **Abandoned, so stale wherever it is stated as current:** a semaphore",
                                    "design/ARCHITECTURE.md")), ["semaphore"])


CAPS = """\
# header comment: a semaphore here is checked too

[[capability]]
id = "CAP-A"
capability = "Several native windows on different workspaces"
status = "cut"
milestone = "M2"

[[capability]]
id = "CAP-B"
v04_note = "load under the heavy-load semaphore"
status = "{status}"

[[capability]]
id = "CAP-C"
v04_note = "kept warm, and no status line at all"
"""


class Capabilities(unittest.TestCase):
    def scan(self, status: str) -> list[tuple[str, int]]:
        return [(h.term, h.line) for h in hits(CAPS.format(status=status), "design/capabilities.toml")]

    def test_a_cut_row_is_exempt_and_every_other_row_is_checked(self) -> None:
        for status in ("planned", "partial", "deferred", "built"):
            with self.subTest(status=status):
                self.assertEqual(self.scan(status), [("semaphore", 1), ("semaphore", 11), ("kept warm", 16)])

    def test_two_cut_rows_exempt_only_themselves(self) -> None:
        self.assertEqual(self.scan("cut"), [("semaphore", 1), ("kept warm", 16)])


BACKLOG = """\
## 2. Workstreams
- **Work items.** offload under a per-user `CapacityLimiter(1)`.

| ID | Item | Area | Milestone | Status | Ref |
|---|---|---|---|---|---|
| BL-0901 | the engine pool, open | WS-QA | M2 | open | `x` |
| BL-0902 | the engine pool, in progress | WS-QA | M2 | in-progress | `x` |
| BL-0903 | the engine pool, parked | WS-QA | M2 | parked | `x` |
| BL-0904 | the engine pool, done | WS-QA | M2 | done | `x` |
| BL-0905 | the engine pool, removed | WS-QA | M2 | removed | `x` |
| BL-0906 | the engine pool, hosted later | WS-QA | M2 | post-v0.4 | `x` |
| BL-0907 | the engine pool, `a \\| b` in a cell | WS-QA | M2 | done | `x` |
| BL-0908 | the engine pool | broken | row | done |
| BL-0909 | the engine pool, odd status | WS-QA | M2 | finished | `x` |

| Canonical | Doubles |
|---|---|
| L1-02 | done: the engine pool |
"""


class Backlog(unittest.TestCase):
    def test_rows_are_checked_by_status_and_prose_always(self) -> None:
        found = [(h.term, h.line) for h in hits(BACKLOG, "design/backlog.md")]
        self.assertEqual(found, [
            ("limiter", 2),  # workstream text
            ("engine pool", 6), ("engine pool", 7), ("engine pool", 8),  # open, in-progress, parked
            # done, removed and post-v0.4 rows are history; an escaped pipe does not shift the status cell (line 12)
            ("engine pool", 13),  # a row whose cells do not match its header fails closed
            ("engine pool", 14),  # an unknown status fails closed
            ("engine pool", 18),  # a table without a Status column is checked
        ])


def make_root(case: unittest.TestCase, files: dict[str, str]) -> Path:
    """A scratch repository holding every current-state document (clean), then `files` on top."""
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, True)
    base = {path: "nothing retired\n" for path in EXPECTED_FIXED}
    base["design/specs/a.md"] = "nothing retired\n"
    base.update({path: "nothing retired\n" for path in EXPECTED_GLOB_SAMPLES})
    for path, text in {**base, **files}.items():
        (tmp / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp / path).write_text(text, encoding="utf-8")
    return tmp


class Scope(unittest.TestCase):
    def make(self, files: dict[str, str]) -> Path:
        return make_root(self, files)

    def test_each_current_state_document_is_checked(self) -> None:
        for path in (*EXPECTED_FIXED, *EXPECTED_GLOB_SAMPLES, "design/specs/a.md", "design/specs/write-path/deep/b.md",
                     ".claude/agents/verifier.md", ".claude/skills/slice/SKILL.md"):
            with self.subTest(path=path):
                root = self.make({path: "a semaphore\n"})
                self.assertEqual([p.split(":")[0] for p in crt.check(root, allow=())], [path])

    def test_history_is_not_checked(self) -> None:
        root = self.make({
            "design/journal/S0001-x.md": "a semaphore",
            "design/inbox/answered/q.md": "a semaphore",
            "design/process/IMPROVEMENTS.md": "a semaphore",
            "design/decisions/ADR-0001-x.md": "a semaphore",
            "design/specs/notes.txt": "a semaphore",  # specs are Markdown
            ".claude/worktrees/w/CLAUDE.md": "a semaphore",  # S0055: another checkout, not this one's documents
            ".claude/skills/x/notes.md": "a semaphore",  # only a skill's SKILL.md
            ".claude/agents/old/a.md": "a semaphore",  # only the agent files themselves
        })
        self.assertEqual(crt.check(root, allow=()), [])

    def test_a_missing_current_state_document_is_a_problem(self) -> None:
        root = self.make({})
        (root / "design/glossary.md").unlink()
        self.assertTrue(any("design/glossary.md" in p and "missing" in p for p in crt.check(root, allow=())))

    def test_no_agent_or_skill_files_is_a_problem(self) -> None:  # S0055
        for sample, folder in zip(EXPECTED_GLOB_SAMPLES, (".claude/agents", ".claude/skills")):
            with self.subTest(folder=folder):
                root = self.make({})
                (root / sample).unlink()
                self.assertTrue(any(folder in p for p in crt.check(root, allow=())), folder)

    def test_no_specs_is_a_problem(self) -> None:
        root = self.make({})
        (root / "design/specs/a.md").unlink()
        self.assertTrue(any("design/specs" in p for p in crt.check(root, allow=())))


class Allowlist(unittest.TestCase):
    make = Scope.make
    LINE = "There is no admission or memory check, and a semaphore."

    def test_an_entry_allows_its_term_on_its_line_only(self) -> None:
        root = self.make({"design/specs/a.md": f"{self.LINE}\nAdmission again.\n"})
        allow = (crt.Allow("design/specs/a.md", "admission", "no admission or memory check", "states the absence"),)
        self.assertEqual([p.split(": ", 1)[0] for p in crt.check(root, allow=allow)],
                         ["design/specs/a.md:1", "design/specs/a.md:2"])
        problems = crt.check(root, allow=allow)
        self.assertIn("'semaphore'", problems[0])
        self.assertIn("'admission'", problems[1])

    def test_an_entry_for_another_file_allows_nothing(self) -> None:
        root = self.make({"design/specs/a.md": "no admission or memory check\n",
                          "design/specs/b.md": "no admission or memory check\n"})
        allow = (crt.Allow("design/specs/a.md", "admission", "no admission or memory check", "states the absence"),)
        self.assertEqual([p.split(":")[0] for p in crt.check(root, allow=allow)], ["design/specs/b.md"])

    def test_a_stale_entry_is_a_problem(self) -> None:
        root = self.make({})
        allow = (crt.Allow("design/specs/a.md", "admission", "no admission or memory check", "states the absence"),)
        self.assertTrue(any("stale allowlist entry" in p for p in crt.check(root, allow=allow)))

    def test_an_entry_needs_a_reason_and_a_known_term(self) -> None:
        root = self.make({"design/specs/a.md": "no admission or memory check\n"})
        for entry, problem in (
            (crt.Allow("design/specs/a.md", "admission", "no admission or memory check", " "), "no reason"),
            (crt.Allow("design/specs/a.md", "admision", "no admission or memory check", "x"), "unknown term"),
            (crt.Allow("design/specs/a.md", "admission", " ", "x"), "fragment"),
        ):
            with self.subTest(problem=problem):
                self.assertTrue(any(problem in p for p in crt.check(root, allow=(entry,))), problem)

    def test_the_real_allowlist_entries_all_have_reasons(self) -> None:
        self.assertTrue(all(a.reason.strip() and a.fragment.strip() for a in crt.ALLOW))


class RealRepository(unittest.TestCase):
    def test_the_repository_passes(self) -> None:  # @regression-guard
        r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)  # noqa: S603
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 refused", r.stdout)
        # every current-state document was read: the fixed ones and every Markdown file under design/specs/
        specs = [f for _, _, files in os.walk(ROOT / "design/specs") for f in files if f.endswith(".md")]
        agents = [f for f in os.listdir(ROOT / ".claude/agents") if f.endswith(".md")]
        skills = [d for d in os.listdir(ROOT / ".claude/skills") if (ROOT / ".claude/skills" / d / "SKILL.md").is_file()]
        self.assertIn(f"retired terms: {len(EXPECTED_FIXED) + len(specs) + len(agents) + len(skills)} documents,",
                      r.stdout)

    def test_a_retired_term_in_the_repository_fails_the_script(self) -> None:
        root = make_root(self, {"design/glossary.md": "- **Semaphore.** retired\n"})
        r = subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False)  # noqa: S603
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("design/glossary.md:1", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()


class LiveDocsAfterS0068(unittest.TestCase):
    """The owner's requirement of 2026-10-31 (S0068): "Make sure no docs inadvertently reference the old design. We
    don't want AI agents implementing future features to get confused and build in what we just dialled down". A
    seeded mutant: a line of the old R4 gate put back into a live spec copied into a scratch root is refused."""

    def test_a_retired_r4_line_put_back_into_the_spec_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in EXPECTED_FIXED:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("current text\n", encoding="utf-8")
            for sample in EXPECTED_GLOB_SAMPLES:
                (root / sample).parent.mkdir(parents=True, exist_ok=True)
                (root / sample).write_text("current text\n", encoding="utf-8")
            spec = root / "design" / "specs" / "engine-jobs.md"
            spec.parent.mkdir(parents=True, exist_ok=True)
            spec.write_text(
                (ROOT / "design" / "specs" / "engine-jobs.md").read_text(encoding="utf-8")
                + "\n1. **R4 first, if owed.** A `mismatch` refuses it with `WRITES_PAUSED {check_failed}`.\n",
                encoding="utf-8",
            )
            done = subprocess.run(  # noqa: S603 (this interpreter and the repository's own script)
                [sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False
            )
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        spec_hits = [line for line in done.stdout.splitlines() if line.startswith("design/specs/engine-jobs.md:")]
        last = len((ROOT / "design" / "specs" / "engine-jobs.md").read_text(encoding="utf-8").splitlines()) + 2
        self.assertEqual({line.split(":")[1] for line in spec_hits}, {str(last)}, spec_hits)  # the real text passes
        for term in ("R4 first", "R4 mismatch outcome", "check_failed"):
            self.assertIn(f"engine-jobs.md:{last}: retired term '{term}'", done.stdout)
