# ADR-0061: macOS first; Linux and Windows in dedicated sessions after v0.4.0 works on macOS, except the write path's Windows semantics

This record sets the platform order for v0.4 development: build and dogfood on macOS, keep the cheap cross-platform signals, and port to Linux and Windows in dedicated sessions later, except for the write path, whose Windows behaviour is designed in from M1b.

- **Status:** Accepted
- **Date:** 2026-10-26
- **Deciders:** owner: "I agree, also with the exception as you have described it." (2026-10-26, in session), answering the agent's recommendation to the owner's proposal: "1) Defer Linux and Windows builds for now (since we don't have ready access to those operating systems) 2) Still do the deveolpment in a way that would theoretically work on those operating systems 3) Focus on MacOS development for now 4) Later, once v0.4.0 works on MacOS, we spend some dedicated sessions on making sure they work in Linux and Windows"
- **Area:** process, platforms, release
- **Amends:** roadmap §3.2 (what a milestone exit requires of Linux and Windows). ADR-0043's release gate (packaged builds and the updater on three OSes) is unchanged.

## Context

The owner develops and dogfoods on macOS and has no Linux or Windows machine at hand yet. GitHub Actions gives non-interactive Linux and Windows runners: the manual CI run (ADR-0060) already builds the packaged app on three OSes with `--smoke-test` and runs T1 on Linux. The first M0 run showed both sides: the three-OS packaged builds passed, and a Linux-only test assumption (inode reuse, S0036) failed. Porting late is cheap for most code and expensive for the write path: atomic replace (ADR-0013) behaves differently on Windows when another process holds the file open (editors, antivirus), locking differs, engine supervision uses POSIX signals, process groups and a parent-pid check, and line endings and case-insensitive paths matter to the ledger writer.

## Decision

1. **macOS first.** Development, dogfooding and the acceptance suite's main runs target macOS.
2. **Written to be portable.** Code avoids needless platform assumptions (paths through `pathlib`, no shell-specific behaviour in the product, platform behaviour behind the existing platform ports).
3. **Cheap cross-platform signals stay.** The manual CI run at each milestone exit keeps the three-OS packaged build with `--smoke-test` and T1 on Linux. A Linux- or Windows-only failure that is not cheap to fix and not a safety issue becomes a backlog row due in the cross-platform sessions and does not hold a milestone exit; the exit needs macOS green plus a green three-OS packaged smoke run.
4. **Dedicated cross-platform sessions** after v0.4.0 works on macOS, on real Linux and Windows machines (Claude Code may run there): platform bugs, installers, platform UX, dogfooding. They come before the release gate of ADR-0043, which still requires three OSes.
5. **Exception: the write path's Windows semantics from M1b.** The write-contract primitives (atomic swap replace, locks, the journal, recovery) and engine supervision are designed against documented Windows behaviour from the start, and the engine's write-contract tests run on a Windows runner in the manual CI run at the M1b exit. A failure there holds the M1b exit like any write-path defect.

## Alternatives considered and rejected

- **Three OSes at every milestone exit, as before.** Rejected: without machines to debug on, platform-only test failures would stall milestones on work the owner cannot dogfood yet.
- **Drop Linux and Windows CI until the cross-platform sessions.** Rejected: the packaged smoke run and Linux T1 cost nothing extra at a manual run and catch drift while it is still a one-line fix (the S0036 inode case).
- **No exception for the write path.** Rejected: retrofitting Windows semantics into the write contract would mean redesigning the safety-critical core late.

## Consequences

- Roadmap §3.2 states the platform rule for exits; M1b's exit adds the Windows run of the engine's write-contract tests.
- Platform-only failures get backlog rows with a "cross-platform sessions" milestone note (due before M12).
- The release gate (ADR-0043) and the packaged three-OS build are unchanged.

## References

ADR-0013, ADR-0043, ADR-0054, ADR-0056, ADR-0060; S0036 (the Linux inode case).
