# One signal to a dead engine's process group misses a plugin child that forks at that moment (macOS): bring back the member listing, or accept it as a limit?
- Asked: 2026-11-01 by the orchestrator, from S0073 part A (verifier r1 F1, red team r1 F1/F2, found independently)
- blocks: nothing (S0073 lands with the one-signal drain as built; the fix is BL-0190)
- Rubric item: 4 (ADR-0068 D7: "signal the group, reap, never signal after the reap; the `libproc` listing goes")
- ADRs checked: ADR-0068 D7 (the cut; its premise: one signal ends the group); ADR-0064 (one engine, the engine lock); ADR-0068 D3 (the engine lock is the guard). None covers a member that survives the signal.

## Context (plain language, a concrete example first)
When an engine ends (a crash, a Cancel, the fresh engine after user code), Finzytrack kills its whole process group, so a plugin's child processes die with it. Until S0073 it then listed the group's members (`libproc` on macOS, `/proc` on Linux) and signalled again until none was left. D7 cut that: one signal, then reap.

On macOS one signal does not reach a child that is being forked at that very moment. Example: a plugin starts a helper that forks in a loop (a shell loop, a file watcher). The engine crashes; Finzytrack sends the one kill; the child forked in that instant lives on, now outside any engine.
- **If it inherited the engine lock** (a plugin child made by a plain fork): it holds the lock forever. Every later ledger job waits; the notice says "Quit and reopen Finzytrack", but reopening does not help, since the child outlives the app. You must restart the computer or kill the process. Reproduced: 9/10 and 4/30 runs; the old drain: 0/10 and 0/30.
- **If it did not** (a child that runs another program, the usual case: the lock is not passed on through exec): it keeps running beside the next engine. If it writes your ledger, that is a plugin's program writing, like any outside editor: on your Mac's own disk the save's displaced check catches it (no false "saved"); on a USB stick or share it is D2's accepted loss, but now not only "in the same instant". Reproduced: 5/30 runs; old drain 0/30.

Bytes: none of Finzytrack's own. The writer is user code (a plugin), which can already write any file while it runs. The lock case is a block you can clear only by a restart.

Likelihood: rare. It needs a plugin that starts child processes that fork, and an engine end at the moment of a fork. Common Beancount plugins start no processes. Linux most likely closes this race in the kernel (not tested); Windows has no process groups in this sense.

Why "keep signalling until nothing is left" is not enough on its own: on macOS, `killpg` cannot tell "nobody left" from "somebody left that we may not signal" while the dead leader is held unreaped, so the loop needs the member listing back.

## Options
1. **Bring back the member listing** (revert cut 4: the code at dfe3aee8, built and tested, macOS `libproc` and Linux `/proc`, about 100 lines). Closes the race. D7's simplification of the drain is withdrawn; the rest of D7 stands.
2. **Accept it as an honest limit.** Keep one signal; say in spec §5.2/§15 that a plugin whose child processes fork may, rarely, leave a process behind on macOS; change the "cannot stop" notice to say what helps: "If this stays after you reopen Finzytrack, restart your computer." Add the must-not-pass test as a documented limit test.
3. **Signal a few times** (a fixed count with short pauses): narrows the race but does not close it. Not recommended.

## Recommendation
Option 1. The listing is already built and tested, and it is the only way to keep "a plugin's child processes cannot outlive their engine" true, so a stuck lock never needs a restart to clear. D7's gain was about 100 lines of platform code; the cost of option 2 is a rare block that only a restart clears, plus a limit to explain. If you prefer the smaller code, option 2 is safe for your ledger bytes.

## Answer (owner, verbatim, with date)
"I accept your recommendations for lineage match and drain fork race." (owner, 2026-11-01, in session)
