# ADR-0061: macOS first; Linux and Windows ported in M11, except the write path's Windows semantics from M1b on

This record sets the platform order for v0.4 development: build, test and dogfood on macOS, keep the cheap cross-platform signals, and port to Linux and Windows in dedicated sessions in M11, except for the write path, whose Windows behaviour is designed in and checked at every exit from M1b on.

- **Status:** Accepted. Amended by ADR-0062 (2026-10-27): INV-26's three-OS lock-interleaving test narrows to the legacy-lock detection test. **Amended 2026-11-14 (below): Linux joins Windows for the write path from M1b on, and a platform-only failure is fixed by the next milestone's exit instead of waiting for M11; M11 keeps the work that needs real machines.**
- **Date:** 2026-10-26
- **Deciders:** owner: "I agree, also with the exception as you have described it." (2026-10-26, in session), answering the agent's recommendation on the owner's proposal: "Would it make sense to 1) Defer Linux and Windows builds for now (since we don't have ready access to those operating systems) 2) Still do the deveolpment in a way that would theoretically work on those operating systems 3) Focus on MacOS development for now 4) Later, once v0.4.0 works on MacOS, we spend some dedicated sessions on making sure they work in Linux and Windows (by then, I'll have computers running those operating systems and maybe we could run claude code on those and make sure it works on both those operating systems). What do you think?"
- **Area:** process, platforms, release
- **Amends:** ADR-0060 (decision 2, "the full manual run must be green at each milestone exit": a platform-only failure may be deferred as below, so the run is green with it marked) and ADR-0046's G1 criterion (the same meaning of a green run at each exit and at G1); roadmap §3.2 and the M1b exit. **Unchanged:** ADR-0043's release gate (packaged builds and the updater on three OSes), ADR-0013's swap primitive tested on all three OSes, INV-26's lock-interleaving test on all three OSes (BL-0026, M3; ADR-0005, ADR-0020).

## Context

The owner develops and dogfoods on macOS and has no Linux or Windows machine at hand yet. GitHub Actions gives non-interactive Linux and Windows runners: the manual CI run (ADR-0060) builds the packaged app on three OSes with `--smoke-test`, and its T1 and T2 jobs run on Linux. The first M0 run showed both sides: the three-OS packaged builds passed, and a Linux-only test assumption (inode reuse, S0036) failed. Porting late is cheap for most code and expensive for the write path: atomic replace (ADR-0013) behaves differently on Windows when another process holds the file open (editors, antivirus), locking differs, engine supervision uses POSIX signals and process groups (a Job Object on Windows), and line endings and case-insensitive paths matter to the ledger writer. The agent recommended: macOS first; keep the packaged three-OS smoke run and Linux T1 at manual runs; send platform-only failures that are not cheap to fix and not safety-related to the backlog instead of holding an exit; dedicated Linux and Windows sessions later; and design the write path's Windows semantics in from M1b, with the engine's write-contract tests on a Windows runner. The owner agreed to all of it, the exception included.

## Decision

1. **macOS first** (owner). Development and dogfooding target macOS.
2. **Written to be portable** (owner). Code avoids needless platform assumptions (agent: paths through `pathlib`, no shell-specific behaviour in the product, platform behaviour behind the existing platform ports).
3. **Cheap cross-platform signals stay** (owner). The manual CI run at each milestone exit keeps the three-OS packaged build with `--smoke-test` and T1 and T2 on Linux.
4. **What a green exit means** (agent, from the owner's decision): (a) **macOS green**: the T0 pre-push gate passes on the owner's Mac at the exit commit, and the acceptance suite runs locally on macOS through the harness (`pnpm -s e2e`, Chromium and WebKit) and passes, recorded in the milestone's journal entry with the commit; (b) the manual CI run is green, where a test that fails **only on Linux or Windows**, is not cheap to fix and is **not a safety test**, is marked `fixme` with `due="M11"` and a backlog row due M11 citing this ADR, so the run stays green and M11's exit holds it. **Never deferred:** tests tagged with an invariant or capability of data safety, the write path and its contract (ADR-0008, ADR-0012, ADR-0013), INV-26's lock interleaving, and the integrity core; PROCESS.md §9's rule that INV-tagged and integrity-core tests are never quarantined stands.
5. **Dedicated cross-platform sessions in M11** (owner: "once v0.4.0 works on MacOS"; agent: placed in M11, release hardening, which already builds the three-OS release): on real Linux and Windows machines (Claude Code may run there), before M11's dogfood window starts, since a write-path fix restarts the window (M12): platform bugs, installers, platform UX, the M11 `fixme` rows.
6. **Exception: the write path's Windows semantics** (owner, as described; agent: "every exit from M1b on" widens "the M1b exit" because the write path keeps changing in M3–M5, a tightening): the write-contract primitives (atomic swap replace, locks, the write-intent journal, recovery) and engine supervision are designed against documented Windows behaviour from the start, and the engine's write-contract tests run on a Windows runner in the manual CI run at **every milestone exit from M1b on**. A failure there holds that exit like any write-path defect. The Windows job is tracked as BL-0087, due M1b.

## Alternatives considered and rejected

- **Three OSes green at every milestone exit, as before.** Rejected: without machines to debug on, platform-only test failures would stall milestones on work the owner cannot dogfood yet.
- **Drop Linux and Windows CI until the cross-platform sessions.** Rejected: the packaged smoke run and Linux T1 cost nothing extra at a manual run and catch drift while it is still a one-line fix (the S0036 inode case).
- **No exception for the write path, or only at M1b.** Rejected: retrofitting Windows semantics into the write contract, or letting a later write-path change regress on Windows unseen, would mean reworking the safety-critical core late.
- **A separate "cross-platform" milestone for deferred rows.** Rejected: M11 already holds the three-OS release work, and `check_invariants.py` only reads milestone IDs.

## Consequences

- Roadmap §3.2 states the platform rule for exits; the M1b exit and every later exit include the Windows run of the engine's write-contract tests; M11 includes the cross-platform sessions before its dogfood window.
- Platform-only failures become `fixme` tests due M11 with backlog rows; safety tests are never deferred.
- The release gate (ADR-0043), ADR-0013's three-OS swap test and INV-26's three-OS lock test are unchanged.

## Amendment (2026-10-27): INV-26's three-OS test is the detection test (ADR-0062)

Owner: "Drop the coexistence policy; I approve the needed changes to INV-26 and ADR-0020. The rest of the pending work you have identified should be done." (2026-10-27, in session).
- Where this ADR names **INV-26's lock-interleaving test on all three OSes** (Amends, decision 4, Consequences), read **INV-26's legacy-lock detection test**: v0.4 no longer interleaves writes with a legacy app; it tries the legacy lock once without waiting and refuses while it is held (ADR-0062). The detection test runs in the unit tests on macOS and on Linux and Windows in the manual CI run, and stays **never deferred**, like every INV-tagged test.

## Amendment (2026-11-14): Linux and Windows no more than one milestone behind

Owner, on where the deferred Linux work would land: "I want Finzytrack to work well on Linux, Windows, and MacOS.. it's fine to develop for MacOS first, but Linux and windows should not lag too far behind." Agreeing to the three changes below: "Yes, go with all three" (2026-11-14, in session). On the Windows scope of decision 6, the same day: "(b) for windows" (a Windows port of the engine's write path, so its real-disk write-contract tests run on the Windows runner, rather than only the pure and fake-file-system tests).
1. **Linux joins decision 6.** The engine's write path (the file-system port: the one-step swap, the data and folder syncs, the locks, drive and folder identity, the engine's process group) is ported to Linux in M1b beside Windows, and the engine's write-contract tests run on a Linux runner as well as a Windows runner in the manual CI run at **every milestone exit from M1b on**; a failure there holds the exit like any write-path defect (BL-0087, BL-0266).
2. **A platform-only failure waits at most one milestone.** Decision 4(b) still lets an exit be green with a platform-only failure that is not cheap to fix and not a safety test marked `fixme`, but it is now due **the next milestone**, not M11: its backlog row takes that milestone. So Linux and Windows are never more than one milestone behind macOS on what the CI runs can check. The rows deferred under 4(b) before this amendment move the same way: BL-0263 (the AppImage's engine) to M1b. (Agent: BL-0206, the mirror publish on Windows, moves to M1b too, as a dependency of the Windows port: the save gates on the published mirror; ADR-0015 amendment of 2026-11-14.)
3. **M11 keeps what needs real machines** (decision 5): installers, each platform's look and feel, hands-on testing, before the dogfood window. A session on a real Linux or Windows machine may come earlier whenever the owner has one.

Unchanged: decisions 1 to 3, decision 4(a) and 4(b)'s "safety, write-path, INV-26 and integrity-core tests are never deferred", ADR-0043's release gate.

## References

ADR-0005, ADR-0008, ADR-0012, ADR-0013, ADR-0020, ADR-0043, ADR-0046, ADR-0054, ADR-0056, ADR-0060; INV-26; BL-0026; S0036 (the Linux inode case).
