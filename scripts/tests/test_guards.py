"""Meta-tests for the Claude Code guard hooks (INV-24): commands and paths they must block and allow."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".claude/hooks"))

import guards  # noqa: E402


class GuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.live = base / "live"
        self.legacy = base / "legacy"
        self.repo = base / "repo"
        for d in (self.live, self.legacy, self.repo):
            d.mkdir()
        self.t = base / "scratch"  # the test's own folder for files it writes: outside the stand-in repo (BL-0307)
        self.t.mkdir()
        self.env = {"FT_LIVE_DATA": str(self.live), "FT_LEGACY_ROOT": str(self.legacy), "FT_GUARD_REPO": str(self.repo)}
        self.old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)

    def tearDown(self) -> None:
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def blocked(self, cmd: str) -> bool:
        return guards.bash_problem(cmd, self.repo) is not None

    def test_git_clean_ignored(self) -> None:
        for cmd in ("git clean -fdx", "git clean -xfd", "git clean -X -n", "git -C . clean -ffx"):
            self.assertTrue(self.blocked(cmd), cmd)
        self.assertFalse(self.blocked("git clean -n"))
        self.assertFalse(self.blocked("git clean -fd"))

    def test_live_data(self) -> None:
        self.assertTrue(self.blocked(f"ls {self.live}"))
        self.assertTrue(self.blocked("cat ~/FinzytrackData/config/config.yaml"))
        self.assertTrue(guards.path_problem(str(self.live / "data/ledger.db"), self.repo, writing=False))

    def test_a_heredoc_fed_to_a_shell_is_code(self) -> None:
        """S0054 verifier F2, F3: a body is parsed as commands (as before S0054) only when a shell word stands on
        its opener's line; any other body is data. A here-string (<<<) opens no heredoc, so the lines after it are
        commands. Stated limits (PROCESS.md §11; rounds 2 and 3; BL-0155): a script written to a file by a heredoc
        (or by the Write tool) and run later, by `bash x.sh` after the end line or by running the file
        directly, is not inspected; a "<<EOF" inside quotes or a comment is taken as an opener, so
        `echo "<<EOF"`, then commands, then an EOF line, hides those commands."""
        L, T = self.legacy, self.t
        refused = [
            f"bash <<'EOF'\nrm -rf {L}/backend\nEOF", "sh <<EOF\ngit push --force origin next\nEOF",
            f"cat <<'EOF' | bash\nrm -rf {L}/backend\nEOF",
            f"cat > {T}/x.sh <<'EOF' && bash {T}/x.sh\nrm -rf {L}/backend\nEOF",
            f"cat <<<EOF\nrm -rf {L}/backend\nEOF",  # F3: a here-string is not a heredoc opener
            # a body with no end line is not taken as data; the end line is the delimiter alone
            f"cat > {T}/x <<'EOF'\nrm -rf {L}/backend", f"cat > {T}/x <<'EOF'\nrm -rf {L}/backend # EOF",
        ]
        for cmd in refused:
            with self.subTest(cmd=cmd):
                self.assertTrue(self.blocked(cmd), cmd)
        allowed = [
            f"cat > {T}/x.sh <<'EOF'\nrm -rf {L}/backend\nEOF", f"tee {T}/x <<EOF\ngit push --force origin next\nEOF",
            # R2-F3: `.` on the opener's line is an argument, not the `.` builtin
            f"git add . && git commit -q -F - <<'EOF'\nS1: a message\n\nrm -rf {L}/backend\ngit push --force origin next\nEOF",
            "git commit -F - <<'EOF'\nS1: notes # with a hash\ngit push --force is refused\nEOF",
        ]
        for cmd in allowed:
            with self.subTest(cmd=cmd):
                self.assertFalse(self.blocked(cmd), cmd)

    def test_the_owners_app_is_not_guarded(self) -> None:
        """S0054 verifier round 2 (owner, 2026-10-29: "Why do we even care about the running v0.3 app? You should
        just ask me to kill the app myself, so you can proceed."): the guard has no rules about the owner's app.
        Agents never stop, start or call it; if it is in their way they ask the owner (INV-24, a working rule).
        These commands pass on purpose, so a rule that comes back is a deliberate change."""
        L, T = self.legacy, self.t
        net = "/dev/" + "tcp"
        for cmd in ("pkill -f uvicorn", "killall python3", "lsof -ti:8001 | xargs kill", "kill -9 $(pgrep -f app.main)",
                    "fuser -k 8001/tcp", "npx kill-port 8001", "kill -TERM -1", "curl -s http://localhost:8001/health",
                    "nc -z 127.0.0.1 8001", f"cat < {net}/127.0.0.1/8001", f"bash {L}/run-v0.3.sh",
                    "git grep -n pkill", f"cat > {T}/n.md <<'EOF'\nlocalhost:8001\nEOF"):
            with self.subTest(cmd=cmd):
                self.assertFalse(self.blocked(cmd), cmd)

    def test_a_heredoc_body_is_data(self) -> None:
        """S0054 (BL-0042's false positive): the lines of a heredoc body written to a file or a non-shell command
        are not parsed as commands; the opener's line still is."""
        L, T = self.legacy, self.t
        allowed = [
            f"cat > {T}/notes.md <<'EOF'\nrm -rf {L}/backend\ngit push --force origin next\nEOF",
            "git commit -q -F - <<'EOF'\nS0054: the guard no longer refuses git push --force in a message body\nEOF",
            "git commit -q -m \"$(cat <<'EOF'\nS1: msg\n\ngit push --force origin next\nEOF\n)\"",
            f"cat > {T}/x <<-EOF\n\trm -rf {L}/backend\n\tEOF",
        ]
        for cmd in allowed:
            self.assertFalse(self.blocked(cmd), cmd)
        self.assertTrue(self.blocked(f"cat <<EOF && rm -rf {L}/backend\nbody\nEOF"))  # the opener's line runs
        self.assertTrue(self.blocked(f"cat > {T}/x <<'EOF'\nsee ~/FinzytrackData\nEOF"))  # live data: any mention

    def test_pushes(self) -> None:
        self.assertTrue(self.blocked("git push --force origin next"))
        self.assertTrue(self.blocked("git push origin :next"))
        self.assertTrue(self.blocked("git push origin v0.3.0"))
        self.assertTrue(self.blocked("git push --tags"))
        self.assertFalse(self.blocked("git push origin next"))  # gated by the pre-push hook and an 'ask' rule

    def test_legacy_worktree_writes(self) -> None:
        self.assertTrue(self.blocked(f"rm -rf {self.legacy}/backend"))
        self.assertTrue(self.blocked(f"cp x.py {self.legacy}/backend/x.py"))
        self.assertTrue(self.blocked(f"echo hi > {self.legacy}/f.txt"))
        self.assertTrue(self.blocked(f"git -C {self.legacy} checkout main"))
        self.assertFalse(self.blocked(f"cp {self.legacy}/backend/app/x.py backend/src/x.py"))
        self.assertFalse(self.blocked(f"git -C {self.legacy} log --oneline -3"))
        self.assertFalse(self.blocked(f"grep -rn foo {self.legacy}/backend"))
        self.assertTrue(guards.path_problem(str(self.legacy / "a.py"), self.repo, writing=True))
        self.assertIsNone(guards.path_problem(str(self.legacy / "a.py"), self.repo, writing=False))

    def test_hook_skipping_and_force_add(self) -> None:
        self.assertTrue(self.blocked("git commit --no-verify -m x"))
        self.assertTrue(self.blocked("git commit -n -m x"))
        self.assertTrue(self.blocked("git push --no-verify origin next"))
        self.assertTrue(self.blocked("git add -f data/ledger.db"))
        self.assertFalse(self.blocked("git add design/README.md"))
        self.assertFalse(self.blocked("git commit -m 'fix -n handling'"))

    def test_generated_files_are_not_hand_edited(self) -> None:
        gen = self.repo / "out.md"
        gen.write_text("<!-- GENERATED by x. Do not edit. -->\n")
        self.assertTrue(guards.path_problem(str(gen), self.repo, writing=True))
        self.assertIsNone(guards.path_problem(str(gen), self.repo, writing=False))

    def test_bypasses_found_by_review(self) -> None:
        """review:G G-12..G-20: each must be refused (G-14 and G-19, the owner's app, removed in S0054 round 2)."""
        L = self.legacy
        refused = [
            # G-12 hook skipping
            "git commit -qnm msg", "git -c core.hooksPath=/dev/null commit -m x", "git config core.hooksPath hooks-off",
            "git commit --no-v -m x",
            # G-13 push gate and refspecs
            "git config finzytrack.allowPush true", "git -c core.hooksPath=/dev/null push", "git push origin +next",
            "git push origin HEAD:main", "git push -d origin next",
            # G-15 live data by indirection
            "find ~ -name '*.beancount'", "ls ~/Finzy*Data", 'cat "$FT_LIVE_DATA/config/config.yaml"',
            "ls ~/finzytrack-data",
            # G-16 case-insensitive disk
            f"ls {str(self.live).upper()}", f"rm -rf {str(L).upper()}/x",
            # G-17 writes into the legacy worktree
            f"cd {L} && rm -rf backend", f"touch {L}/x", f"sed -i '' s/a/b/ {L}/f.py", f"cp -t {L} x.py",
            f"dd if=/dev/zero of={L}/f", f"npm --prefix {L}/frontend install", f"find {L} -name '*.pyc' -delete",
            f"git -C {L} branch -D main", "git tag -d v0.3.0", f"git worktree remove --force {L}",
            f"rm -rf {L.parent}/*legacy*", f"cd {L} && python3 -m app.main",
            # G-20 clean / stash
            "git -c x=y clean -fdx", "git stash --all", "git clean -e '!*' -fd",
        ]
        for cmd in refused:
            self.assertTrue(self.blocked(cmd), cmd)

    def test_only_the_owner_writes_the_export_manifest(self) -> None:  # BL-0023 (INV-23)
        manifest = self.repo / "corpora" / "MANIFEST.tsv"
        self.assertTrue(guards.path_problem(str(manifest), self.repo, writing=True))
        self.assertIsNone(guards.path_problem(str(manifest), self.repo, writing=False))
        self.assertIsNone(guards.path_problem(str(self.repo / "other" / "MANIFEST.tsv"), self.repo, writing=True))
        for cmd in ("printf 'x' >> corpora/MANIFEST.tsv", "cp x corpora/MANIFEST.tsv", "tee -a corpora/MANIFEST.tsv"):
            with self.subTest(cmd=cmd):
                self.assertTrue(guards.bash_problem(cmd, self.repo))
        self.assertIsNone(guards.bash_problem("cat corpora/MANIFEST.tsv", self.repo))
        # S0026 verifier F2: other spellings of the same file are the same file (case-insensitive disk, `..`, symlinks)
        (self.repo / "corpora").mkdir()
        manifest.write_text("")
        (self.repo / "c2").symlink_to(self.repo / "corpora")
        (self.repo / "m.tsv").symlink_to(manifest)
        for raw in ("corpora/manifest.tsv", "Corpora/MANIFEST.tsv", "corpora/x/../MANIFEST.tsv", "c2/MANIFEST.tsv", "m.tsv"):
            with self.subTest(raw=raw):
                self.assertTrue(guards.path_problem(str(self.repo / raw), self.repo, writing=True))
                self.assertTrue(guards.bash_problem(f"printf x >> {raw}", self.repo))
        self.assertTrue(guards.bash_problem("sed -i '' s/a/b/ corpora/MANIFEST.tsv", self.repo))
        self.assertTrue(guards.path_problem(str(self.repo / ".GIT" / "Config"), self.repo, writing=True))
        # Stated limit (PROCESS.md §11, the guards are a safety net): writers the guard does not parse (interpreters,
        # git checkout/restore/apply, patch, curl -o, awk -i inplace, ed) are not caught.

    def test_the_git_gates(self) -> None:
        """BL-0055's forms that reach the git gates: each must be refused; the ordinary forms beside them must not.
        S0054 dropped its refusals of deliberately built commands (a flag carried by a `$(...)` next to git) and,
        in round 2, every network redirect rule: stated limits, PROCESS.md §11."""
        nv = "--no-" + "verify"  # spelled in two parts so this file's text is not itself a command the guard reads
        net = "/dev/" + "tcp"
        T = self.t
        refused = [
            "git log > .git/config", "printf x >> .git/hooks/pre-commit", "git show HEAD:x > .git/config",
            f"git commit -m \"it's\" {nv} -m 'x'",  # an apostrophe inside double quotes is not a quote
            f"cat <<EOF && git commit -m x {nv}\nbody\nEOF",  # the opener's line still runs
            f"git commit -m x \\\n {nv}",  # a line continuation joins the command
        ]
        for cmd in refused:
            self.assertTrue(self.blocked(cmd), cmd)
        allowed = [
            'git commit -q -m "S0001: msg $(date)"', 'git commit -m "$MSG"', "git push origin next",
            f"git commit -q -F - <<'EOF'\nmsg with $(echo {nv}) inside the body\nEOF",
            "git log --oneline $(git merge-base HEAD main)..HEAD", f"git log > {T}/x.txt", "cat .git/config",
            "printf x > .githooks/pre-push.tmp", "git add $FILES", "git commit -F \"$F\" $EXTRA",
            "echo hi > /dev/" + "udp/localhost/9",
            f"cat > {T}/notes.md <<'EOF'\ngit push origin next $(echo x)\nEOF",
            f"cat > {T}/notes.md <<'EOF'\na doc that mentions {net} in its text\nEOF",
            "git commit -m \"user's fix\" -m 'costs $5'",
        ]
        for cmd in allowed:
            self.assertFalse(self.blocked(cmd), cmd)
        self.assertTrue(guards.path_problem(str(self.repo / ".git" / "config"), self.repo, writing=True))
        self.assertTrue(guards.path_problem(str(self.repo / ".git" / "hooks" / "pre-push"), self.repo, writing=True))
        self.assertIsNone(guards.path_problem(str(self.repo / ".git" / "config"), self.repo, writing=False))
        self.assertIsNone(guards.path_problem(str(self.repo / ".githooks" / "pre-push"), self.repo, writing=True))
        # Stated limits (PROCESS.md §11): code given to `sh -c` or `eval`, or run by an interpreter, is not parsed;
        # nor are commands built on purpose to slip past the parser (BL-0062 removed, BL-0055's anti-bypass
        # refusals removed; owner, 2026-10-29), nor a heredoc-written script run later or a `(cd …` subshell (BL-0155).

    def test_quoted_message_shapes_bash_would_execute_are_refused(self) -> None:
        """S0105 (BL-0054, reverted): the shapes two review rounds used against the blanking of quoted message values
        (heredoc bodies kept as code, backtick substitutions spanning a quote, quoting the blanking did not follow).
        The guard reads them as it did before S0105 and refuses each (owner, 2026-11-12, in session: "Revert BL-0054
        (Recommended)")."""
        gc = "git clean " + "-x"  # spelled in parts so this file's text is not itself a command the guard reads
        nv = "--no-" + "verify"
        hp = "core.hooks" + "Path"
        refused = [
            f'git commit -m "$({gc})"',  # owner's planted case: bash runs $(...) inside double quotes
            f"git commit -m 'x'; {gc}",  # owner's planted case: a command after the message
            f"git commit -m 'x' && {gc}", f"git commit -m 'x' || {gc}", f"git commit -m 'x' | {gc}",
            f"git commit -m 'x'\n{gc}",
            f"git commit -m 'x' $({gc})", f"git commit -m 'x' `{gc}`", f"git commit -m 'x'\"$({gc})\"",
            f"git commit -m \"{hp} x\"",  # double quotes stay as today
            f"git commit -m 'x' {nv}", f"git commit {nv} -m 'x'", f"git commit -m 'x' -c {hp}=/dev/null",
            f"git commit -m $'x\\' -m \\'; {gc}; #'",  # ANSI-C quoting: \' does not end it
            f"git commit -m \\' ; {gc} ; echo \\'",  # an escaped quote is not a quote
            f"git commit -m 'x' # -m '\n{gc}\n'",  # a quote inside a comment opens nothing
            f"git commit -am '{hp} x'", f"git -C . commit -m '{hp} x'",  # not one of the exact shapes
            f"git tag --message '{hp} x' v1", f"git commit --body '{hp} x'",
            f"git commit -m '{hp} x",  # an unclosed quote
            f"echo 'git commit -m ' ; {gc}",
            # the quoting the blanking does not follow stops it (kills the loosening mutants of S0105 part B)
            f"git commit -m \"x -m '\" ; {gc} ; echo \"'\"",  # a quote inside double quotes
            f"git commit -m \\' -m ' -m '; {gc}; echo '#'",  # an escaped quote shifts every later quote
            f"git commit -m $'x\\' -m '; {gc}; #'",  # in $'...', \' does not end it
            f"git commit -m 'a'; git -C . commit -m '{hp} x'",  # a separator starts a new command
            # round 1 F1 (High): a heredoc body run as code is never blanked; in an unquoted heredoc, single quotes
            # are literal and the outer shell runs $(...) and backticks
            f"bash <<EOF\ngit commit -m 'x $({gc})'\nEOF",
            f"bash <<EOF\ngit commit -m 'x `{gc}`'\nEOF",
            f"cat <<EOF | sh\ngit commit -m 'x $({gc})'\nEOF",
            f"bash <<EOF\ngit tag -m 'v $({gc})' v1\nEOF",
            f"bash <<EOF\ngh pr create --title 'v `{gc}`'\nEOF",
            f"cat <<EOF\ngit commit -m 'x $({gc})'",  # no end line: bash still expands the body
            f"git commit -F - <<EOF\nx\nEOF\ngit commit -m '{hp} x'",
            # round 2 R2-F1 (High): inside backticks, a backtick ends the substitution even within single quotes
            f"echo `git commit -m 'x`; {gc}; echo `'`",
            f"echo `git commit -m 'x`; git config {hp} /dev/null; echo `'`",
            f"echo `git tag -m 'x` && {gc} && echo `'`",
            f"echo `gh pr create --title 'x`\n{gc}\necho `'`",
            f"x=`git commit -m 'x`; {gc} #'",
            # the single-quoted messages BL-0054 would have allowed stay refused (rewording is the workaround)
            f"git commit -m 'docs: {hp} must stay .githooks'",
            f"git commit -m 'fix: a; {gc} is refused'",
        ]
        # Not refused today and unchanged (outside the owner's shapes, stated in the hook's docstring): a command
        # after a lone `&`, and a separator inside double quotes or inside a quote of another option (the split
        # leaves a quote on the next word, so its flags are not read).
        for cmd in refused:
            with self.subTest(refused=cmd):
                self.assertTrue(self.blocked(cmd), cmd)

    def test_hooks_fail_closed(self) -> None:
        """INV-22's spirit for the guards (S0014 round-2 verifier F2): Claude Code runs a command when the hook
        exits with anything but 2, so an internal error must refuse, never allow."""
        hooks = Path(__file__).resolve().parents[2] / ".claude/hooks"
        cases = {
            "guard_bash.py": {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(self.repo)},
            "guard_paths.py": {"tool_name": "Read", "tool_input": {"file_path": "x"}, "cwd": str(self.repo)},
        }
        for hook, event in cases.items():
            with self.subTest(hook=hook):
                # a guards module that raises stands in for any bug in the checks
                shim = Path(self.tmp.name) / "shim"
                shim.mkdir(exist_ok=True)
                (shim / "guards.py").write_text(
                    "def bash_problem(*a, **k):\n    raise ValueError('bug')\n"
                    "def path_problem(*a, **k):\n    raise ValueError('bug')\n")
                (shim / hook).write_text((hooks / hook).read_text())
                r = subprocess.run([sys.executable, str(shim / hook)], input=json.dumps(event), text=True,
                                   capture_output=True, check=False)
                self.assertEqual(r.returncode, 2, r.stderr)

    def test_an_unknown_home_does_not_crash_the_guard(self) -> None:
        self.assertFalse(self.blocked("ls ~nosuchuserzz9"))
        self.assertTrue(self.blocked(f"ls ~nosuchuserzz9; rm -rf {self.legacy}/backend"))

    def test_ordinary_commands(self) -> None:
        L = self.legacy
        allowed = [
            "python3 -m unittest discover -s scripts/tests", "git status", "ls -la design",
            "git commit -m 'scripts: add -n flag to orient'", "git push origin next", "git log --oneline main..next",
            f"grep -rn def {L}/backend", f"cat {L}/backend/app/main.py", f"git -C {L} log -3", f"git -C {L} show HEAD:README.md",
            f"cd {L} && grep -rn foo backend", "find . -name '*.py'", "lsof -iTCP -sTCP:LISTEN -nP",
            "curl -s http://127.0.0.1:18950/health", "kill 12345", "uv run pytest -q", "pnpm install --frozen-lockfile",
            "git stash", "git clean -n", "git branch -a",
            "git config --get core.hooksPath", "git config core.hooksPath", "git config --get finzytrack.allowPush",
        ]
        for cmd in allowed:
            self.assertFalse(self.blocked(cmd), cmd)

    # BL-0307 (S0139): the generated-file rule covers the repository only, and each refusal says its own reason.

    def generated(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<!-- GENERATED by x. Do not edit. -->\n")
        return path

    def test_a_generated_file_outside_the_repo_may_be_written_or_removed(self) -> None:
        """2026-11-21: `rm` of a reviewer's copy of a generated page (and of a backup beside it) in a temp folder was
        refused as if it were the repository's page. INV-19 covers the repository's generated files only."""
        T = self.t
        f, g = self.generated(T / "x"), self.generated(T / "ct.bak")
        for raw, cwd in ((str(f), self.repo), ("../scratch/x", self.repo), ("x", T)):
            with self.subTest(raw=raw):
                self.assertIsNone(guards.path_problem(raw, cwd, writing=True))
        for cmd in (f"rm {f} {g}", f"rm -f {T}/*", f"tee {f}", f"cp y {f}", f"mv {f} {g}", f"echo y > {f}",
                    f"cd {T} && rm x ct.bak", f"sed -i '' s/a/b/ {f}", f"touch {f}", f"cp -t {T} y"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(guards.bash_problem(cmd, self.repo), cmd)

    def test_a_heredoc_over_a_stray_generated_copy_is_allowed(self) -> None:
        """The incident's shape: a generated page's copy left where a heredoc writes made every push gate fail."""
        f, L = self.generated(self.t / "x"), self.legacy
        for cmd in (f"cat > {f} <<'EOF'\nrm -rf {L}/backend\ngit push --force origin next\nEOF",
                    f"tee {f} <<EOF\nbody\nEOF", f"cat > {f} <<-EOF\n\tbody\n\tEOF"):
            with self.subTest(cmd=cmd):
                self.assertFalse(self.blocked(cmd), cmd)

    def assert_generated_refused(self, raw: str, cwd: Path) -> None:
        self.assertIn("is generated", guards.path_problem(raw, cwd, writing=True) or "", raw)
        self.assertIsNone(guards.path_problem(raw, cwd, writing=False), raw)
        for cmd in (f"rm {raw}", f"tee {raw}", f"cp y {raw}", f"printf y > {raw}", f"sed -i '' s/a/b/ {raw}"):
            with self.subTest(cmd=cmd, cwd=str(cwd)):
                self.assertIn("is generated", guards.bash_problem(cmd, cwd) or "", cmd)

    def test_a_generated_file_in_the_repo_stays_refused_by_every_spelling(self) -> None:
        """The scope check must not open another spelling of a repository file: absolute, relative from another
        cwd, `..`, a case variant (on a case-insensitive volume), a symlinked folder or file outside the repo."""
        R, T = self.repo, self.t
        gen = self.generated(R / "design" / "generated" / "page.md")
        (T / "into-repo").symlink_to(R / "design")
        (T / "page-link.md").symlink_to(gen)
        spellings = [(str(gen), T), ("design/generated/page.md", R), ("generated/page.md", R / "design"),
                     ("design/generated/../generated/page.md", R), ("../repo/design/generated/page.md", T),
                     (f"{T}/into-repo/generated/page.md", T), ("into-repo/generated/page.md", T), ("page-link.md", T),
                     (f"{T}/../repo/design/generated/page.md", R), ("design/generated/*.md", R), (f"{T}/into-repo/*/p*", T)]
        if Path(str(gen).upper()).exists():  # a case-insensitive volume: the variant names the same file
            spellings += [(str(gen).upper(), T), ("Design/GENERATED/Page.md", R)]
        for raw, cwd in spellings:
            with self.subTest(raw=raw):
                self.assert_generated_refused(raw, cwd)

    def test_the_repos_own_generated_page_stays_refused(self) -> None:
        """The checkout the hook lives in is the repository, whatever the stand-in says (only read here)."""
        rel = "design/generated/engine-job-texts.md"
        real = guards.REPO / rel
        self.assertTrue(guards._generated(real), "the page this test reads must carry its GENERATED header")
        (self.t / "r").symlink_to(guards.REPO / "design")
        for raw, cwd in ((str(real), self.t), (rel, guards.REPO), ("generated/engine-job-texts.md", guards.REPO / "design"),
                         ("design/generated/../generated/engine-job-texts.md", guards.REPO),
                         (f"{self.t}/r/generated/engine-job-texts.md", self.t)):
            with self.subTest(raw=raw):
                self.assert_generated_refused(raw, cwd)

    MARKERS = {"live": "live data folder", "legacy": "legacy worktree", "generated": "is generated",
               "git": ".git/config or .git/hooks", "manifest": "only the owner adds rows"}

    def test_each_writer_refusal_names_its_own_reason(self) -> None:
        """A refusal by `rm`, `tee`, `cp` and the other writers says why: before S0139 every one read "would write
        into the legacy worktree (read-only) or the live data folder", so a generated-file refusal read as live data."""
        L, T = self.legacy, self.t
        self.generated(self.repo / "out.md")
        (T / "live-link").symlink_to(self.live)  # the live folder by another name: the path rule decides, not the text
        (T / "legacy-link").symlink_to(L)
        live = f"{T}/live-link/ledger.beancount"
        cases = {
            "legacy": [f"rm -rf {L}/backend", f"rmdir {L}/x", f"tee {L}/f", f"touch {L}/x", f"chmod +x {L}/run.sh",
                       f"cp x.py {L}/x.py", f"cp -t {L} x.py", f"mv {L}/a.py b.py", f"mv b.py {L}/a.py",
                       f"ln -s x {L}/y", f"sed -i '' s/a/b/ {L}/f.py", f"perl -i -pe s/a/b/ {L}/f.py",
                       f"npm --prefix {L}/frontend install", f"rm {T}/legacy-link/f", f"cd {L} && touch",
                       f"cd {L} && sed -i -es/a/b/ {T}/f"],  # an in-place edit run from inside it, as before S0139
            "generated": [f"rm {self.repo}/out.md", "tee -a out.md", "cp x out.md", "mv x out.md", "mv out.md y",
                          "sed -i '' s/a/b/ out.md", "touch out.md", "truncate -s 0 out.md"],
            "live": [f"rm {live}", f"tee {live}", f"cp x {live}", f"mv {live} x", f"touch {live}"],
            "git": ["cp x .git/config", "tee .git/hooks/pre-push", "rm .git/hooks/pre-commit"],
            "manifest": ["tee -a corpora/MANIFEST.tsv", "cp x corpora/MANIFEST.tsv"],
        }
        for kind, cmds in cases.items():
            for cmd in cmds:
                with self.subTest(cmd=cmd):
                    reason = guards.bash_problem(cmd, self.repo) or ""
                    self.assertIn(self.MARKERS[kind], reason, cmd)
                    for other, marker in self.MARKERS.items():
                        if other != kind:
                            self.assertNotIn(marker, reason, f"{cmd}: {reason}")

    def test_a_hard_link_outside_the_repo_to_a_generated_file_stays_refused(self) -> None:
        """S0139 verifier F1: a hard link is the repository's file by another name; the base refused it (no scope),
        and the scope must not open it. A copy (one link) outside the repo may be written."""
        gen = self.generated(self.repo / "design" / "generated" / "page.md")
        os.link(gen, self.t / "hard.md")
        self.assert_generated_refused(str(self.t / "hard.md"), self.t)
        self.assert_generated_refused("hard.md", self.t)
        (self.t / "copy.md").write_text(gen.read_text())
        self.assertIsNone(guards.bash_problem(f"tee {self.t}/copy.md", self.t))

    def test_option_values_glued_with_equals_are_checked_like_arguments(self) -> None:
        """S0139 F3-live (pre-existing): `npm --prefix=DIR` passed with DIR the live data folder by a symlink or `..`,
        since an argument that starts with `-` was never checked. A glued value is now an argument the path rules
        check, for every writer and, for the live data folder, every command."""
        L, T, V = self.legacy, self.t, self.live
        (T / "ll").symlink_to(V)
        (T / "lg").symlink_to(L)
        spellings = {"live": [str(V), f"{T}/ll", f"{T}/../live"], "legacy": [str(L), f"{T}/lg", f"{T}/../legacy"]}
        shapes = ["npm --prefix={}/fe install", "pnpm --prefix={} install", "pip install --target={}/site",
                  "cp --target-directory={} y", "cp -t{} y", "mv --target-directory={} y", "ln -s -t{} y",
                  "rm --dir={}/x", "uv pip install --target={}/x"]
        for kind, dirs in spellings.items():
            for d in dirs:
                for shape in shapes:
                    cmd = shape.format(d)
                    with self.subTest(cmd=cmd):
                        self.assertIn(self.MARKERS[kind], guards.bash_problem(cmd, self.repo) or "", cmd)
        for d in spellings["live"]:  # a reader too: the live data folder is refused for reading
            with self.subTest(reader=d):
                self.assertIn("live data folder", guards.bash_problem(f"pytest --rootdir={d}", self.repo) or "")
        for cmd in ("npm --prefix=frontend install", f"pip install --target={T}/site", f"cp --target-directory={T} y",
                    f"cp -t{T} y", f"pytest --rootdir={T}", "uv run --directory=backend pytest", f"rsync -tv y {T}/z",
                    f"pytest --rootdir={L}"):  # reading the legacy worktree is allowed
            with self.subTest(allowed=cmd):
                self.assertIsNone(guards.bash_problem(cmd, self.repo), cmd)


# A path under the system temp folder, by its four absolute spellings (with or without the `private` and `var`
# parents), or a name joined to the result of tempfile's temp-folder call (both written in parts here, so this file's
# own text passes). Limit: a path assembled from parts, as here, is not seen.
FIXED_TEMP = re.compile(r"(?<![\w.~-])/(?:private/)?(?:var/)?" + "t" + r"mp(?![\w.-])|\bget" + "tempdir" + r"\(")


class NoFixedTempPath(unittest.TestCase):
    """BL-0307 (S0139): a meta-test that writes or reads a fixed global path fails when a stray file is there (twice
    on 2026-11-21: a reviewer's copy of a generated page made every push gate and the Stop hook fail). Each test
    writes in its own temporary folder."""

    def test_the_pattern_catches_the_temp_spellings(self) -> None:
        tmp = "t" + "mp"
        for text in (f'"/{tmp}/x"', f"cat > /{tmp}/x.sh", f"'/private/{tmp}/y'", f"/var/{tmp}/z", f"/private/var/{tmp}/w",
                     f"git log > /{tmp}", "Path(tempfile.get" + "tempdir()) / 'x'", "os.path.join(get" + "tempdir(), 'x')",
                     f"--root /{tmp}/ft"):
            with self.subTest(text=text):
                self.assertTrue(FIXED_TEMP.search(text), text)
        for text in (f"/d/{tmp}", f"self.{tmp}.name", f"~/{tmp}/x", f"/{tmp}x", f"{tmp}_path", f"/{tmp}.bak"):
            with self.subTest(text=text):
                self.assertFalse(FIXED_TEMP.search(text), text)

    def test_no_meta_test_names_a_fixed_temp_path(self) -> None:
        root = Path(__file__).resolve().parents[2]
        hits = [f"{f.relative_to(root)}:{n}" for folder in ("scripts/tests", "scripts/oracle/tests")
                for f in sorted((root / folder).rglob("*.py"))
                for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if FIXED_TEMP.search(line)]
        self.assertEqual(hits, [], "use the test's own temporary folder (tempfile), never a fixed global path")


if __name__ == "__main__":
    unittest.main()
