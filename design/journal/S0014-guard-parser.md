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

## Round 2 design (after the verifier's FAIL; supersedes the lexer above)
- The round-1 design (a hand-written shell lexer exempting text sent to sinks) failed: 14 commands the old guard refused passed, through nested quotes in `${…}`, `case` patterns inside `$(…)`, delimiters the shell cooks differently, `<<` that is not a heredoc, and compound commands piped into a shell. A lexer that skips text will keep disagreeing with zsh and bash at the edges (EP-30).
- Round 2 keeps the 18af3c8 guard unchanged and adds one thing: in five exact whole-command shapes, anchored at the command's first character, the body of a heredoc whose delimiter is a plain word in **single quotes** is removed before the old checks run: `cat > path <<'W'`, `cat >> path <<'W'`, `cat <<'W' > path`, `tee [-a] path <<'W'`, `git commit [flags] -F - <<'W'`, `git commit [flags] -m "$(cat <<'W'` … `W` / `)"`. The opening line may hold nothing after the delimiter; the path is plain (no `$`, quotes or `/dev/`); a body that never closes is not stripped; everything after the terminator (and after `)"` on its line) is checked as a new command. Neither bash nor zsh expands such a body.
- Dropped: the commit-message exemption (`-m 'text'` and `gh --body` still refuse guarded words; the verifier noted it was beyond what BL-0042 named, F8), and the lexer (`shell_lex.py` deleted).
- Remaining false positives, accepted: guarded words in a single-quoted `-m` message; backticks inside single quotes split as commands (the sed case). Workaround: the heredoc shapes, or the Edit tool. BL-0042's second half stays open as BL-0054.

## Decisions
- Exact shapes over a parser, after a parser failed a High review (EP-30) (agent).
- Loosening check: the diff against 18af3c8 is a pure addition (`literal_heredoc_parts` and a wrapper); every old refusal holds, and the only new allowance is a single-quoted heredoc body in the shapes above. BL-0042 was kept in M0 with the owner's approval of ADR-0058 ("I agree with your recommendations 1-4 on improving the process and re-evaulating as in 5", 2026-10-25) (agent).

## Hand-off (round 2)
- Checks with must-not-pass tests: 31 refusals in `LiteralHeredocs` (rest of the command, `--no-verify` after `)"`, redirect targets incl. the legacy worktree, the live folder and `/dev/tcp`, pipes after the delimiter, unquoted and double-quoted delimiters, other receivers, prefixes, unclosed bodies, more lines inside the substitution, and the verifier's 14 bypasses of round 1), 7 allowances, the 9 old tests unchanged.
- Self-mutation: 9 tried, 8 killed (two survivors got tests first); 1 equivalent (`search` instead of `match` on a pattern anchored with `\A`).

## Verifier
- Round 1, head cbd5389: FAIL. Attacks: 14 (nested quotes, case patterns, delimiter cooking, non-heredoc `<<`, compound commands, fds/FIFOs/coprocs, message flags swallowing redirects, word splitting, `/dev/tcp`, `env -i`, zsh glob qualifiers, comments, mutants, tests on base).
- F1–F5 High: lexer mis-parses and compound commands let executable text pass (see Round 2 design); fixed by dropping the lexer; all 14 repros are now tests.
- F6 Medium: a message flag swallowed a redirect into the legacy worktree; fixed (exemption dropped; test kept).
- F7 Medium: a cat heredoc sent to code through an fd, FIFO or coproc; fixed (the shapes allow no second redirect; the fd case is a test). A FIFO created by an earlier, separate command remains possible, as with a file written by the Write tool and run later (stated limit).
- F8 Medium: the message exemption went beyond BL-0042's approved scope; dropped.
- F9 Low: surviving mutants and no zsh-specific cases; the new suite's mutants are above; zsh forms (`$[ ]`) are among the refusals.
- Q1–Q3 (open before this slice, not regressions): word-split flags from a substitution (`git commit -m x $(echo --no-verify)`), `/dev/tcp` redirects and writes to `.git/config`, redirect targets of git commands → BL-0055.
- Re-verification required (High fixes): round 2 below.
- Round 2, 406d1d1..483b900: NOT VERIFIED. The verifier stopped partway: a safety filter interrupted its generation of attack strings against the guard. It confirmed the tests pass, the lexer is gone and the diff against 18af3c8 is a pure addition, but did no adversarial testing.
- **Parked** (agent): a High-risk loosening of the INV-24 guard does not land unverified. The active guard is restored to 18af3c8 (commit after 59a7a4d); round 2 stays in history (483b900) and is re-applied with `git checkout 483b900 -- .claude/hooks/guards.py scripts/tests/test_guards.py` after a fresh verifier round, or the owner's review, passes.
