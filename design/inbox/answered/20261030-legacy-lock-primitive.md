# Should the engine call portalocker itself to detect the old app's save lock, or make the same system call directly?
- Asked: 2026-10-30 by the implementer of S0063 (BL-0151 slice 4)
- blocks: nothing (S0063 lands with option 2 on macOS, tested against portalocker itself; the choice is reversible in one function)
- Rubric item: 4 (an ADR's rejected alternative would become the decision) and 6 (a dependency with system access on Windows)
- ADRs checked: ADR-0062 (decision 2 "with the legacy primitive", decision 5 "portalocker stays pinned at 3.2.0", and its rejected alternative "Detect with `fcntl.flock` and a Windows lock call made directly, without portalocker", an agent's rejection); ADR-0054 decision 5 and its amendment (the engine's dependencies must pass `[engine]` in `licences.allow.toml`); ADR-0005 (the legacy app's lock). None decides what to do when portalocker brings a Windows-only dependency the licence gate cannot check on macOS.

## Context (plain language, a concrete example first)
Before a save, Finzytrack checks whether your old Finzytrack (v0.2/v0.3) is saving at that very moment, by trying the old app's lock file once (ADR-0062). Example: you run v0.3 every day; it saves a price; at that second you save a transaction in v0.4. v0.4 must see the old app's lock and refuse with "The old Finzytrack app is saving. Quit the old Finzytrack first, then try again."

The old app takes its lock with a library, portalocker 3.2.0. On macOS and Linux that library makes one system call, `flock`. ADR-0062 said v0.4 should use the same library, and rejected calling `flock` directly, so that a difference between the two could not make the check see nothing.

When S0063 added portalocker 3.2.0 to the engine, two things appeared: on Windows it pulls in pywin32 (a large package with deep system access), and the licence gate (`scripts/check_licences.py`) refuses the engine because pywin32 is listed but not installed on a Mac. Making the gate skip Windows-only packages would loosen a gate, which is yours to decide.

## Options
1. **Use portalocker in the engine** (ADR-0062 as written). Needs a change to the licence gate (check Windows-only packages from the lock file, or on the Windows CI run) and accepts pywin32 in the Windows engine. More code and a gate change, for a call that on macOS is the same `flock`.
2. **Make the system call directly on macOS and Linux (`flock`, exclusive, non-blocking), and test it against portalocker 3.2.0 itself** (what S0063 did): the tests start a second process that takes the lock exactly as the old app does, with portalocker 3.2.0, and check that v0.4 reports it held; and that while v0.4 holds it, portalocker can take neither an exclusive nor a shared lock. portalocker stays a test-only dependency. Windows decides its own call (portalocker's `msvcrt.locking` over the first 64 KiB) with the same kind of test when Windows arrives (BL-0087).
3. **Make the check a no-op until M3**, when v0.4 first knows where the old installs are (BL-0026). Today the list of old installs is always empty, so the check never runs in the product yet.

## Recommendation
Option 2. It is the same system call the old app makes, it is proven against the real library in a test, it keeps a Windows-only package with deep system access out of the engine, and it needs no gate change. The rejected alternative's worry (a silent mismatch) is answered by the test, which fails if the two ever stop seeing each other. If you agree, ADR-0062 gets an amendment that moves the rejected alternative to the decision for POSIX, with your words.

## Answer (owner, verbatim, with date)
2026-10-31, in session, choosing the option labelled "Direct flock (Recommended)" (this file's option 2): "Direct flock (option 1) is fine. But I have a question: Some time ago, we said, \"Don't assume co-existence of multiple Finzytrack versions. Assume that the version running is the only one. If a user starts multiple versions, any ill-effects are on them.\" If that's the case, why are we still considering cases where multiple Finzytrack versions may be running? I don't mind implementing the recommended direct flock, but I just want to know if we are ignoring what we previously decided."
