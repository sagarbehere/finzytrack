# The Windows type-check pass: defer it to the Windows port (BL-0260), as S0113 did?
- Asked: 2026-11-13 by the S0113 implementer
- blocks: nothing (S0113 landed the macOS and Linux passes)
- Rubric item: 8 (a part of a plan shown to the owner left out)
- ADRs checked: ADR-0050 (pyright as the single type gate), ADR-0061 (macOS first; Windows in M1b with BL-0087); neither says which platforms the type check covers.

## Context (plain language, a concrete example first)
Pyright checks Python as if it ran on one operating system. On a Mac it accepts `fcntl.F_FULLFSYNC`, a call only macOS has; the Linux CI run rejected it. The S0113 plan proposed to you ran the check three times: as macOS, as Linux and as Windows.

macOS and Linux now pass with 0 errors. Windows gives 142 errors in the engine and 262 in the backend. They are not mistakes: the product code that manages the engine process (process groups, `os.killpg`, signals) and the lock files (`fcntl.flock`) has no Windows version yet; that is BL-0087's Windows port (M1b). To make the Windows pass green today, each of those modules would need a platform guard or a Windows branch, which means deciding now what the sidecar does on Windows. That is product work in the process-supervision code, not a refactor of imports.

## Options (cheapest first; PROCESS.md §5)
1. Keep the Windows pass out until the Windows port: S0113's `scripts/pyright_platforms.py` checks macOS and Linux; BL-0260 (M1b, with BL-0087) adds Windows when those modules get their Windows code.
2. A slice now that marks every POSIX-only module and test with its platform, so the Windows pass reaches 0 before any Windows code exists.

## Recommendation
Option 1. The Windows pass would check code that does not exist yet; the Linux pass fixes the real escape (the M1a exit CI run). No mechanism history: this gate is new in S0113.

## Answer (owner, verbatim, with date)
