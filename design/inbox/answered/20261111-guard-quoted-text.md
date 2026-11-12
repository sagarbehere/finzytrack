# May the guard stop refusing guarded words inside a quoted commit message (BL-0054), and may engine tests count as capability tests?
- Asked: 2026-11-11 by the implementer, S0105-gate-tooling
- blocks: S0105 part B, row BL-0054 only (the other rows go on); the engine half of BL-0077 (a new backlog row, BL-0252)
- Rubric item: "Always the owner's: loosening any gate" (both questions narrow what a check refuses)
- ADRs checked: none covers the guard's parser (S0014 and S0054 shaped it; PROCESS.md §8 and §11 state its limits); ADR-0058 and IMP-0045 freeze the gates' self-defence but do not say whether a false positive may be removed.

## Context (plain language, a concrete example first)

**1. The guard (BL-0054).** The Claude Code guard (`.claude/hooks/guards.py`) reads every shell command an agent runs and refuses the ones aimed at the live data, the legacy worktree or the git gates. It splits a command at `;`, `|`, `&&` and backticks before it looks at quotes. So text inside a quoted message is read as if it were a command. Today, checked on `fb529a9c`:

- `git commit -m 'docs: core.hooksPath must stay .githooks'` is refused ("changing core.hooksPath switches off the gates");
- `git commit -m 'fix: a; git clean -x is refused'` is refused (the `;` inside the quotes starts a "command");
- `sed 's/x/`git clean -x`/' f.txt` is refused (bash does not run backticks inside single quotes).

None of these runs anything dangerous. Agents work around it by rewording the message. The cost is small (a reworded message), but it happens on slices that change the gates, and the workaround teaches agents to avoid naming the thing they changed.

Any fix makes the guard refuse fewer commands, so it is a loosening. The row itself says: only with a design that removes the risk class by exact shapes, not a general parser.

**2. Engine tests and capabilities.** `scripts/check_capabilities.py` lets a capability be `built` only when a test is tagged with its ID. It looks in `backend/`, `frontend/`, `shell/` and `scripts/tests/`, but not in `engine/` (the ledger engine, added in S0035). The engine's pytest config does not even register the `cap` mark. So a capability whose tests are all engine tests (the mirror, for example) could never be marked `built`. No row is `built` yet, so nothing fails today. Counting engine tests would make the check accept more, which is a loosening too.

## Options

For the guard:
1. **Leave it as it is** (close BL-0054 as "won't fix"). Agents keep rewording messages.
2. **Exact shapes only:** for `git commit -m/--message/-F`, `git tag -m` and `gh ... --body/--title`, a value in single quotes is data, and is removed before the split. Double-quoted values stay as today (bash runs `$(...)` and backticks inside double quotes). Everything else is unchanged. A planted test pins that `git commit -m "$(git clean -x)"` and `git commit -m 'x'; git clean -x` are still refused.
3. A quote-aware splitter for every command. More false positives go, but it is the "general parser" the row rules out, and it is more code inside a safety net.

For the engine tests:
1. **Count tagged engine tests** (add `engine` to the folders, register the `cap` mark in `engine/pyproject.toml`), under the same real-test rule as the others (BL-0077).
2. Keep engine tests out; a capability tested only in the engine stays `partial`.

## Recommendation

Guard: option 2. It removes the false positives agents actually hit (a single-quoted message), and leaves every shape bash would execute refused. If you prefer no change to the safety net at all, option 1 is fine; the cost is only reworded messages.

Engine tests: option 1. The engine holds most of the tested code; leaving it out would keep the mirror and the write path from ever being `built`. The real-test rule still applies, so a comment or a skipped test still does not count.

## Answer (owner, verbatim, with date)
Owner, 2026-11-11, in session (relayed by the orchestrator):
- BL-0054: "Exact shapes only (Recommended)". The option as put to the owner: "For git commit -m/--message/-F, git tag -m and gh --body/--title, a single-quoted value is data. Double quotes stay as today (bash runs $(...) inside them). Planted tests pin that `git commit -m \"$(git clean -x)\"` and `-m 'x'; git clean -x` are still refused."
- BL-0252: "Count engine tests (Recommended)". The option as put to the owner: "Add engine/ to the folders and register the cap mark, under the same real-test rule (no comment, skip or fixme counts)."
Recorded in `design/journal/S0105-gate-tooling.md` and `design/process/IMPROVEMENTS.md`; built in S0105 part B.
- Follow-up, 2026-11-12, owner in session, after two review rounds found shapes bash executes that the change let through (heredoc bodies; a backtick substitution ending inside a single-quoted value): "Revert BL-0054 (Recommended)". The option as put to the owner: "Restore the guard to its pre-S0105 behaviour and close BL-0054 as won't-fix with the evidence. Agents keep rewording messages; the safety net stays simple." Reverted in S0105 part B round 2 (IMP-0063).
