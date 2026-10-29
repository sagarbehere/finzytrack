"""Meta-tests for the Claude Code guard hooks (INV-24): commands and paths they must block and allow."""

from __future__ import annotations

import json
import os
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
        self.env = {"FT_LIVE_DATA": str(self.live), "FT_LEGACY_ROOT": str(self.legacy)}
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
        """S0054 verifier F2, F3: a body that a shell reads is parsed as commands (as before S0054); a body written
        to a file or another command is data. A here-string (<<<) opens no heredoc, so the lines after it are
        commands. Stated limit (PROCESS.md §11; round 2): a "<<EOF" inside quotes or a comment is taken as an
        opener, so `echo "<<EOF"`, then commands, then an EOF line, hides those commands; only a command built on
        purpose has that shape."""
        L = self.legacy
        refused = [
            f"bash <<'EOF'\nrm -rf {L}/backend\nEOF", "sh <<EOF\ngit push --force origin next\nEOF",
            f"cat <<'EOF' | bash\nrm -rf {L}/backend\nEOF",
            f"cat > /tmp/x.sh <<'EOF' && bash /tmp/x.sh\nrm -rf {L}/backend\nEOF",
            f"cat <<<EOF\nrm -rf {L}/backend\nEOF",  # F3: a here-string is not a heredoc opener
            # a body with no end line is not taken as data; the end line is the delimiter alone
            f"cat > /tmp/x <<'EOF'\nrm -rf {L}/backend", f"cat > /tmp/x <<'EOF'\nrm -rf {L}/backend # EOF",
        ]
        for cmd in refused:
            with self.subTest(cmd=cmd):
                self.assertTrue(self.blocked(cmd), cmd)
        allowed = [
            f"cat > /tmp/x.sh <<'EOF'\nrm -rf {L}/backend\nEOF", "tee /tmp/x <<EOF\ngit push --force origin next\nEOF",
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
        L = self.legacy
        net = "/dev/" + "tcp"
        for cmd in ("pkill -f uvicorn", "killall python3", "lsof -ti:8001 | xargs kill", "kill -9 $(pgrep -f app.main)",
                    "fuser -k 8001/tcp", "npx kill-port 8001", "kill -TERM -1", "curl -s http://localhost:8001/health",
                    "nc -z 127.0.0.1 8001", f"cat < {net}/127.0.0.1/8001", f"bash {L}/run-v0.3.sh",
                    "git grep -n pkill", "cat > /tmp/n.md <<'EOF'\nlocalhost:8001\nEOF"):
            with self.subTest(cmd=cmd):
                self.assertFalse(self.blocked(cmd), cmd)

    def test_a_heredoc_body_is_data(self) -> None:
        """S0054 (BL-0042's false positive): the lines of a heredoc body written to a file or a non-shell command
        are not parsed as commands; the opener's line still is."""
        L = self.legacy
        allowed = [
            f"cat > /tmp/notes.md <<'EOF'\nrm -rf {L}/backend\ngit push --force origin next\nEOF",
            "git commit -q -F - <<'EOF'\nS0054: the guard no longer refuses git push --force in a message body\nEOF",
            "git commit -q -m \"$(cat <<'EOF'\nS1: msg\n\ngit push --force origin next\nEOF\n)\"",
            f"cat > /tmp/x <<-EOF\n\trm -rf {L}/backend\n\tEOF",
        ]
        for cmd in allowed:
            self.assertFalse(self.blocked(cmd), cmd)
        self.assertTrue(self.blocked(f"cat <<EOF && rm -rf {L}/backend\nbody\nEOF"))  # the opener's line runs
        self.assertTrue(self.blocked("cat > /tmp/x <<'EOF'\nsee ~/FinzytrackData\nEOF"))  # live data: any mention

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
            "git commit -qnm msg", "git -c core.hooksPath=/dev/null commit -m x", "git config core.hooksPath /tmp/h",
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
            "git log --oneline $(git merge-base HEAD main)..HEAD", "git log > /tmp/x.txt", "cat .git/config",
            "printf x > .githooks/pre-push.tmp", "git add $FILES", "git commit -F \"$F\" $EXTRA",
            "echo hi > /dev/" + "udp/localhost/9",
            "cat > /tmp/notes.md <<'EOF'\ngit push origin next $(echo x)\nEOF",
            f"cat > /tmp/notes.md <<'EOF'\na doc that mentions {net} in its text\nEOF",
            "git commit -m \"user's fix\" -m 'costs $5'",
        ]
        for cmd in allowed:
            self.assertFalse(self.blocked(cmd), cmd)
        self.assertTrue(guards.path_problem(str(self.repo / ".git" / "config"), self.repo, writing=True))
        self.assertTrue(guards.path_problem(str(self.repo / ".git" / "hooks" / "pre-push"), self.repo, writing=True))
        self.assertIsNone(guards.path_problem(str(self.repo / ".git" / "config"), self.repo, writing=False))
        self.assertIsNone(guards.path_problem(str(self.repo / ".githooks" / "pre-push"), self.repo, writing=True))
        # Stated limits (PROCESS.md §11): code given to `sh -c` or `eval`, or run by an interpreter, is not parsed;
        # nor are commands built on purpose to slip past the parser (BL-0042 and BL-0062 removed, BL-0055's
        # anti-bypass refusals removed; owner, 2026-10-29).

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


if __name__ == "__main__":
    unittest.main()
