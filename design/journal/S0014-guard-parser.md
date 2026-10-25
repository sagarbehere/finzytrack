# S0014: The Bash guard reads commands as a shell does (BL-0042)

## Brief
- Goal: stop the Bash guard refusing text that the shell never runs (the body of a quoted heredoc fed to `cat`, a commit message, a single-quoted string given to `sed`), without letting anything through that it refuses today, and close the gaps that the regex splitter hid: command substitutions inside double quotes, `bash -c`/`sh -c`/`eval` strings, and heredocs fed to a shell.
- Implements: BL-0042 (kept in M0 by ADR-0058 with the owner's approval: "I agree with your recommendations 1-4", 2026-10-25); INV-24's guard (`.claude/hooks/guards.py`); PROCESS.md §8.
- Capabilities: none. Invariants touched: INV-24 (the guard is part of its enforcement).
- Risk class / lane: **High** (a security guard for the owner's data; a loosening of what it refuses, limited to text the shell does not execute). A fresh verifier round after a High fix or three or more Medium fixes.
- Design (EP-30): a small shell lexer (stdlib, the hooks run on the system Python) that knows quoting, `$(…)`, backticks, heredocs, comments and the operators `; & | && || ( )` and newlines. Text is exempt from the whole-text checks only where the shell cannot execute it **and** its consumer is a sink on an allowlist:
  - the body of a heredoc with a quoted delimiter whose receiver is `cat` or `tee` (its output not piped on, and, inside a substitution, only when the enclosing command is a message sink) or `git commit -F -`;
  - the message values of `git commit -m/--message` and `gh … --title/--body/-t/-b`, when they hold no `$(…)` or backtick.
  Everything else is still checked as today, and now also: each `$(…)` and backtick (outside single quotes) as a command; the string of `bash|sh|zsh|dash|ksh -c` and of `eval`; the body of a heredoc fed to a shell.
- How it could be loosened or bypassed (written before coding; each gets a must-not-pass test):
  1. `cat <<'EOF' | bash` (sink piped into a shell); 2. `bash <<'EOF'` / `sh -s <<'EOF'` / `python3 - <<'EOF'` (body executed); 3. an unquoted delimiter `<<EOF` whose body holds `$(…)` (expanded by the shell); 4. `bash -c "$(cat <<'EOF' … EOF)"` (a sink's output becomes code); 5. `eval "$(cat <<'EOF' …)"`; 6. `git commit -m "$(rm -rf …)"` (substitution inside a message); 7. a message flag on a non-sink (`echo -m …`); 8. `git -c core.hooksPath=… commit -m x` (the message exemption must not skip the git checks); 9. `x=$(cat <<'EOF' …)` then `$x` (the variable is expanded later: the assignment line itself is not a sink); 10. `tee >(bash) <<'EOF'` (process substitution); 11. a backslash-escaped quote that ends a string early in a naive lexer; 12. a delimiter line with trailing spaces or `<<-` tabs; 13. an unterminated quote or heredoc (the lexer cannot decide → refuse, INV-22's spirit); 14. a heredoc body that ends the outer `$(` early (`)` inside the body); 15. `cat > <the live data folder>/x <<'EOF'` (the redirect target is still checked); 16. `cat <<'EOF' > script.sh` then `bash script.sh` (allowed today through the Write tool as well; out of scope, stated).
- Acceptance tests: `scripts/tests/test_guards.py` (every existing refusal and allowance unchanged; the false positives from S0001/S0005/S0012 become allowed; the 16 cases above refused or stated).
- Expected doc impact: `guards.py` module doc; backlog BL-0042 done; roadmap §1; this journal; the working note in the session prompt stays valid for Python heredocs (still checked).
- Open questions: none.
- Base SHA: c640611.

## Decisions
- A hand-written lexer, not bashlex: the hooks run on the system Python with no dependencies, and the guard needs quoting, substitutions and heredocs, not a full grammar (agent).
- Exemption by allowlist of sinks (`cat`/`tee` not piped on, `git commit -F -`, the values of `git commit -m/--message` and `gh --title/--body`), never by "looks like prose" (EP-30) (agent).
- A heredoc body that is not plain data and goes to `cat`/`tee` is read as a command as well, since its output goes somewhere that may run it (agent).
- What the lexer cannot read is refused rather than guessed (agent).
- Stated limit (unchanged from today): code inside a heredoc or `-c` string for a non-shell interpreter gets the text checks only (live data names, the owner's port, process-stopping words), like a script written with the Write tool and run. The old regex splitter caught shell-shaped lines in such code by accident, which was also the source of the false positives (agent).
- Loosening check: every refusal in the existing meta-tests still holds unchanged; the new allowances are limited to text bash does not execute. BL-0042 was kept in M0 with the owner's approval of ADR-0058 ("I agree with your recommendations 1-4 on improving the process and re-evaulating as in 5", 2026-10-25) (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks with must-not-pass tests: heredoc-as-data (piped on, procsub, unquoted, non-sink receiver, sink output into `bash -c`/`eval`/an assignment); message values (substitutions inside, non-sink with `-m`, other git subcommands, `-n` hidden in a cluster, global `-c`); `-c` strings incl. `-lc`; `eval`; lexer (escaped quotes, unterminated quote/heredoc/substitution, `)` inside a body, `<<-` tabs, delimiter with trailing space); redirect targets of sinks. 28 refusals, 11 allowances, plus the 9 existing tests unchanged.
- Self-mutation: 16 tried, 12 killed first; 3 survivors got tests (`git stash -m`, an unquoted heredoc to Python, `-nmMSG`) and are killed; 1 equivalent (keeping the backslash with an escaped `"` still consumes the quote, so the string does not end early).
- Population: the false positives of S0001, S0005 and S0012 (commit messages and doc heredocs mentioning guarded words; `sed` with a backticked command in single quotes); twelve commands typical of this session still pass.
