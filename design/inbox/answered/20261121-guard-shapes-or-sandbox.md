# S0139's guard: keep chasing command shapes, or let the OS stop agents' writes to the live data folder and the legacy worktree?
- Asked: 2026-11-21 by the orchestrator, after S0139's fresh round (FAIL, one High). This is the second fix round on the guard's command-shape list, so it needs the owner's options note (PROCESS.md §3).
- blocks: S0139's landing (its two commits are pushed: 46b9f7d8, the fix 473dedb1). Everything else goes on.
- Rubric item: a guard of real data (INV-24), and a change to how agents run on the owner's machine (rubric item 6, the security posture).
- ADRs checked: none covers the guard's mechanism. INV-24 names the hook. S0054 and BL-0054 are its review history.

## Situation
The guard hook (`.claude/hooks/guards.py`) keeps agents' shell commands from writing into the live data folder and the legacy worktree. It does this by reading each command and recognising the shapes that write: `rm`, `tee`, `cp` and their destinations, `sed -i`, redirections, `npm --prefix`, and so on.

S0139 fixed BL-0307 and, in round 1, a gap into the live data folder: `npm --prefix=<a symlink to it> install`. The fresh round then found more gaps of the same kind. **The live data folder can still be written through a symlink** by `git -C <link>`, `git --work-tree=<link>`, `curl -o<link>/x`, `tar -C<link>` and `sort -o<link>/x` (R2-2, High; the base had it too). **The legacy worktree can be written** by `pip install --target DIR`, `pnpm -C DIR`, `yarn --cwd DIR`, `cp -rt DIR`, and `git --work-tree <legacy> reset --hard`, which could wipe uncommitted work in the tree your v0.3 runs from (R2-3, Medium; the base had it too). Two macOS spellings of a repository file now pass the generated-file rule, which the base refused: macOS's firmlink path to the data volume and its volfs path by device and inode number (R2-1, Low; `check_generated.py` still catches any change at commit).

Every round has found new shapes, and every shell command with a path-taking option is another one. A list of shapes cannot be complete: the same lesson as BL-0286's test switch, and as BL-0054's quoted-message change, which you reverted after two rounds.

## Likelihood
Low. No agent has written into either folder that we know of. A write needs an unusual command aimed at the folder, and for the live data folder, a symlink to it under another name first (the guard refuses making one by the folder's name). What is at stake if it happens: your real ledgers, or uncommitted work in the legacy tree.

## The structural option: Claude Code's sandbox
Facts from Claude Code's documentation (code.claude.com/docs/en/sandboxing, /permissions):
- The Bash tool can run inside a sandbox that **macOS itself enforces** (Seatbelt), "regardless of what the model chose to run". It covers every child process (git, npm, perl, curl, Python), and symlinks to a denied place.
- By default a sandboxed command may write only in the working folder and its subfolders, and the temp folder. Both protected folders are outside it, so they are not writable, whatever the command's shape. `sandbox.filesystem.denyWrite` can name them as well, to be explicit.
- Subagents use the same sandbox. Our own hooks are not sandboxed, and do not need to be.
- An agent could ask to rerun a refused command unsandboxed. `allowUnsandboxedCommands: false` turns that off.
- Known friction: `git push` over SSH can fail inside the sandbox on macOS (our remote is SSH), so `git push` is listed in `excludedCommands` or the remote switched to HTTPS. Network access is an allowlist of hosts (github.com, pypi, npm). Tools that write caches outside the project (uv, pnpm's store) may need an `allowWrite` entry. Not verified yet: the exact `denyWrite` syntax, credential helpers, which caches. A probe session settles them.
- The settings go in your user settings (`~/.claude/settings.json`), so no project file can loosen them.

## Options (cheapest first)
1. **Land S0139 as it is and state the rest as limits.** Write R2-2, R2-3 and the other shapes into the guard's stated limits, and fix R2-1, the one thing this slice made worse: allow a generated-file copy outside the repo only under the temp folder, as the base effectively did. The live data folder keeps a known gap that needs a symlink and an unusual command. Cost: one small fix, one more review round on it (the guard's rule), and the docs.
2. **Turn on the sandbox, then land S0139 with option 1's limits.**
   - **What it does:** the OS stops every agent shell command from writing outside the checkout and the temp folder, so the guard's shape gaps no longer reach the live data folder or the legacy worktree. The guard stays as a second line, with its gaps stated.
   - **Cost:** about 5 minutes for you, to approve the settings change (I prepare it, with the update-config skill). Then a probe session: a sandboxed `perl`, `git -C`, `curl -o` and `tar -C` each tries to write a stand-in folder outside the checkout and must be refused. The normal work is checked too: the gates, `uv`, `pnpm`, `git push`, `gh`. Whatever breaks gets an `allowWrite` or `excludedCommands` entry, never one for the protected folders.
   - **Expect some friction in the first sessions** (a cache path, the SSH push).
   - **Separate checkouts:** work in the v0.2.x checkout runs from that folder, so it is its own working folder and unaffected.
3. **A third round of shapes**: add R2-2's and R2-3's shapes to the guard. Not recommended: each round so far found a new batch, and the next will too.

## Recommendation
**Option 2.** It is the only option that makes the live data folder unwritable to agents' shell commands whatever they type, it costs you minutes, and it makes the shape list's gaps harmless instead of chasing them. The guard's rules for generated files inside the repo stay the guard's and `check_generated.py`'s job, since the sandbox allows writes in the checkout.

Review history of the mechanism (the guard's command-shape list): S0054 (heredocs, verifier rounds), BL-0054 (a quoted-message change, reverted by the owner after two rounds found executable shapes), S0139 round 1 (F1 to F5, plus F3-live: a High-class gap into the live data folder, fixed), S0139 round 2 (R2-1 to R2-6, R2-2 High). Highs on this mechanism: 2, both found by review, neither ever hit.

## Answer (owner, verbatim, with date)
Owner, 2026-11-21, in session: "Sandbox + land (Recommended)"

**Decision: option 2.** Claude Code's sandbox is turned on in the owner's user settings: writes are denied to the live data folder and the legacy worktree, and unsandboxed retries are off. A probe session then checks the denials and the normal work. After that, S0139 lands with option 1's limits (R2-1 fixed; R2-2, R2-3 and the other shapes stated as the guard's limits, now behind the sandbox).
