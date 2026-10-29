# ADR-0065: Agents ask the owner to stop the running app; the guard protects data, not the app

This record decides that the owner's running app is protected by a working rule for agents, not by the bash guard: agents never stop, start or call it, and ask the owner to stop it when it is in their way. INV-24 keeps its data part.

- **Status:** Accepted
- **Date:** 2026-10-29
- **Deciders:** owner: "Why do we even care about the running v0.3 app? You should just ask me to kill the app myself, so you can proceed. In fact, I have just killed it. So check and proceed." (2026-10-29, in session, relayed by the orchestrator; S0054 verifier round 2)
- **Area:** process, agents, invariants
- **Supersedes:** in INV-24, "or running backend" as a guarded rule; the guard rules of S0054 (IMP-0045 item 8) and BL-0140 that refused kills by name or port, calls to the owner's port and the legacy start script.
- **Unchanged:** INV-24's data part (agents and tests never read the live data folder and never write into the legacy worktree; tests run on copies in isolated data dirs and ports); the guard's live data, legacy worktree and git gate refusals (ADR-0045); the acceptance and oracle launchers' refusal of the owner's port (tests never use it).

## Context

S0054 narrowed the bash guard to acts aimed at the owner's app and data. Two verifier rounds then failed on the app part alone: kills written inside interpreter code, `fuser` spellings, start-script wrappers, and false positives in commit messages (round 2: 1 High, 4 Medium, 4 Low, all on the kill and app rules). Each fix added parser code (a second command parser, interpreter word lists) and each round found new spellings. The app holds no data of its own: the ledger in the live data folder is what an agent mistake must never reach, and that stays guarded.

Example: an agent needs port 8001 or finds a leftover v0.3 process. Before: the guard tried to recognise every way of stopping or calling it. Now: the agent asks the owner to stop the app, and goes on once the owner has.

## Decision

1. **INV-24 is amended** (owner, 2026-10-29): agents and tests never touch the owner's real data (never read the live data folder, never write into the legacy worktree). Agents never stop, start or call the owner's running app themselves; if it is in their way they ask the owner to stop it. That second part is a working rule (CLAUDE.md, the agent definitions, PROCESS.md §8), not a guard check.
2. **The bash guard drops every rule about the owner's app** (owner, 2026-10-29): the kill, pkill, killall, kill-port and fuser rules (with `kill -1`), the owner's address and network redirect rules, the start-script rule, and the second command parser that served them. `scripts/tests/test_guards.py` asserts that those commands now pass, so a rule that comes back is a deliberate change.
3. **The heredoc rule is kept, smaller** (agent): a heredoc body is data unless a shell word stands on its opener's line, so a commit message may name a refused command. It is one regex; openers inside quotes or comments are not told apart (a stated limit, PROCESS.md §11).

## Alternatives considered and rejected

- **Keep hardening the kill rule.** Rejected: two rounds showed the spellings do not run out, and the app holds no data (the owner's decision).
- **Drop heredoc-as-data too.** Rejected: commit messages written through a heredoc that name a refused command were the slice's recorded false positive (BL-0042), and the regex form costs a few lines.

## Consequences

- The guard is smaller (274 lines, from 442 at S0054's round-1 fix and 307 before S0054) and has one command parser.
- An agent that ignores the working rule can stop or call the owner's app; the owner's data is still out of reach. An app stopped by mistake is restarted by the owner and loses nothing.
- INV-24's "Enforced by" line lists only what enforces the data part.

## References

INV-24; ADR-0045; IMP-0045 item 8; BL-0140; PROCESS.md §8, §11; S0054 journal, verifier rounds 1 and 2 (private archive `m1a/s0054-verifier/`, `m1a/s0054-verifier-r2/`).
