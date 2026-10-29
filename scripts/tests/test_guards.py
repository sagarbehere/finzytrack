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

    def test_owner_app(self) -> None:
        self.assertTrue(self.blocked("lsof -ti:8001 | xargs kill"))
        self.assertTrue(self.blocked("pkill -f app.main"))
        self.assertTrue(self.blocked("killall python3"))
        self.assertFalse(self.blocked("kill 12345"))

    def test_kills_are_judged_by_what_runs_not_by_words(self) -> None:
        """S0054 (reflection proposal 8): the guard refuses acts aimed at the owner's app, not text that mentions
        them. pkill and killall pick processes by name, so any use is refused; kill is refused when the same command
        looks a process up (pgrep, pidof, ps, lsof) or names port 8001."""
        refused = [
            "pkill -f my_worker", "killall node", "kill $(pgrep -f uvicorn)", "ps aux | grep x | xargs kill -9",
            "kill $(pidof something)", "kill -9 $(lsof -t -i :8001)",
        ]
        for cmd in refused:
            self.assertTrue(self.blocked(cmd), cmd)
        allowed = [
            # S0038, S0040, S0048 false positives: test code that ends a process, written through a heredoc
            "cat > /tmp/t.py <<'EOF'\nos.kill(proc.pid, signal.SIGTERM)  # end the python backend\nEOF",
            'grep -rn "kill" backend/src', "python3 -m pytest -k test_kill_the_engine backend/tests",
            "kill $PID && uv run pytest backend/tests -q", "kill %1",
        ]
        for cmd in allowed:
            self.assertFalse(self.blocked(cmd), cmd)

    def test_a_kill_is_judged_by_its_words_wherever_they_stand(self) -> None:
        """S0054 verifier F1: the kill rule reads the words of the whole command, not the first word of each
        simple command, so xargs flags, shell keywords, groups and wrappers do not hide a kill. pkill, killall,
        kill-port and fuser -k are refused anywhere; kill is refused beside port 8001 or a process finder."""
        refused = [
            # the verifier's 14 (each passed at f66811dd)
            "lsof -ti:8001 | xargs -r kill", "lsof -ti :8001 | xargs -n1 kill",
            "lsof -nP -iTCP:8001 -sTCP:LISTEN -t | xargs -r kill -TERM",
            "for p in $(lsof -ti:8001); do kill $p; done",
            "if pgrep -f uvicorn >/dev/null; then kill $(pgrep -f uvicorn); fi",
            'while read p; do kill "$p"; done < <(pgrep -f uvicorn)',
            "{ kill $(pgrep -f uvicorn); }", "(kill -9 $(lsof -ti:8001))",
            "pgrep -f uvicorn | xargs -I{} kill -9 {}",
            "ps aux | grep uvicorn | awk '{print $2}' | xargs -r kill",
            "timeout 5 pkill -f uvicorn", "nice -n 5 pkill -f uvicorn", "sudo -n pkill -f app.main",
            "npx kill-port 8001",
            # more of the same class
            "npx --yes kill-port 8001", "lsof -ti:8001 | parallel kill", "timeout 5 fuser -k 8001/tcp",
            "sudo fuser -k 8001/tcp", '"kill" $(lsof -ti:8001)', "/bin/kill $(pgrep -f app.main)",
            "bash -c 'pkill -f uvicorn'", "pgrep -f uvicorn | xargs -I{} sh -c 'kill {}'",
            'git commit -m "$(pkill -f uvicorn)"', "echo `pkill -f uvicorn`",
            "python3 -c \"import os; os.system('pkill -f uvicorn')\"",
            "launchctl kill TERM gui/501/finzytrack.backend", "kill -9 4242 # the python backend on 8001",
        ]
        for cmd in refused:
            with self.subTest(cmd=cmd):
                self.assertTrue(self.blocked(cmd), cmd)
        allowed = [
            # the approved removal: the word in data (a message, a heredoc written to a file, a search pattern)
            'git commit -m "kill the old helper"', "cat > notes.md <<'EOF'\nwe kill the process\nEOF",
            'git commit -m "pkill and killall are refused; so is lsof -ti:8001 | xargs kill"',
            "git commit -q -F - <<'EOF'\nthe guard refuses pkill -f uvicorn and kill $(lsof -ti:8001)\nEOF",
            "git commit -m \"$(cat <<'EOF'\nS1: pkill and kill-port are refused\nEOF\n)\"",
            "grep -rn pkill scripts/", 'rg -n "kill-port" .', "ps aux | grep kill",
            "python3 -c 'import os; os.kill(pid, 15)'", "kill $PID", "lsof -iTCP -sTCP:LISTEN -nP",
        ]
        for cmd in allowed:
            with self.subTest(cmd=cmd):
                self.assertFalse(self.blocked(cmd), cmd)

    def test_a_kill_beside_port_8001_needs_no_finder(self) -> None:
        """S0054 verifier F4: the port clause stands on its own (a mutant that drops it passed every other test)."""
        for cmd in ("kill -9 $(cat /tmp/app.pid) # whatever holds 8001",
                    "kill $(ss -ltnp 'sport = :8001' | awk 'NR>1{print $6}')", "kill 4242; echo 8001",
                    f"kill -TERM $(cat {self.legacy}/backend.pid)"):  # a PID file in the legacy worktree
            with self.subTest(cmd=cmd):
                self.assertTrue(self.blocked(cmd), cmd)
        self.assertFalse(self.blocked("kill 4242; echo 18001"))

    def test_a_heredoc_fed_to_a_shell_or_an_interpreter_is_code(self) -> None:
        """S0054 verifier F2, F3: a body that a shell or an interpreter reads is parsed as commands (as before
        S0054); only a body written to a file or another command is data. A here-string (<<<) and a quoted "<<EOF"
        open no heredoc, so the lines after them are commands."""
        L = self.legacy
        refused = [
            f"bash <<'EOF'\nrm -rf {L}/backend\nEOF", "sh <<EOF\npkill -f uvicorn\nEOF",
            "bash <<'EOF'\nkill $(lsof -ti:8001)\nEOF", f"cat <<'EOF' | bash\nrm -rf {L}/backend\nEOF",
            "python3 - <<'EOF'\nimport subprocess; subprocess.run('pkill -f uvicorn', shell=True)\nEOF",
            f"cat > /tmp/x.sh <<'EOF' && bash /tmp/x.sh\nrm -rf {L}/backend\nEOF",
            # F3: not heredoc openers
            f"cat <<<EOF\nrm -rf {L}/backend\nEOF", f'echo "<<EOF"\nrm -rf {L}/backend\nEOF',
            f"echo '<<EOF'\nrm -rf {L}/backend\nEOF",
            # a body with no end line is not taken as data
            f"cat > /tmp/x <<'EOF'\nrm -rf {L}/backend",
        ]
        for cmd in refused:
            with self.subTest(cmd=cmd):
                self.assertTrue(self.blocked(cmd), cmd)
        allowed = [
            f"cat > /tmp/x.sh <<'EOF'\nrm -rf {L}/backend\nEOF", "tee /tmp/x <<EOF\npkill -f uvicorn\nEOF",
            "cat > /tmp/t.py <<'EOF'\nos.kill(proc.pid, signal.SIGTERM)\nEOF",
        ]
        for cmd in allowed:
            with self.subTest(cmd=cmd):
                self.assertFalse(self.blocked(cmd), cmd)

    def test_the_owners_app_is_not_started_or_probed(self) -> None:
        """BL-0140 (S0054 verifier F10): the legacy app's start script run from anywhere, and a loopback host and
        port 8001 written with a space or as host 0, are refused; reading the script and other ports pass."""
        L = self.legacy
        for cmd in (f"bash {L}/run-v0.3.sh", f"{L}/run-v0.3.sh", f"nohup {L}/run-v0.3.sh &", "./run-v0.3.sh",
                    "nc localhost 8001", "nc -z 127.0.0.1 8001", "curl -s http://0:8001/", "telnet localhost 8001"):
            with self.subTest(cmd=cmd):
                self.assertTrue(self.blocked(cmd), cmd)
        for cmd in (f"cat {L}/run-v0.3.sh", f"grep -n uvicorn {L}/run-v0.3.sh", "nc -z localhost 18950",
                    "curl -s http://127.0.0.1:18001/", "curl -s http://10:8001/"):
            with self.subTest(cmd=cmd):
                self.assertFalse(self.blocked(cmd), cmd)

    def test_a_heredoc_body_is_data(self) -> None:
        """S0054 (BL-0042's false positive): the lines of a heredoc body written to a file or a non-shell command
        are not parsed as commands; the opener's line still is."""
        L = self.legacy
        allowed = [
            f"cat > /tmp/notes.md <<'EOF'\nrm -rf {L}/backend\ngit push --force origin next\nEOF",
            "git commit -q -F - <<'EOF'\nS0054: the guard no longer refuses git push --force in a message body\nEOF",
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
        """review:G G-12..G-20 and G-14: each must be refused."""
        L = self.legacy
        refused = [
            # G-12 hook skipping
            "git commit -qnm msg", "git -c core.hooksPath=/dev/null commit -m x", "git config core.hooksPath /tmp/h",
            "git commit --no-v -m x",
            # G-13 push gate and refspecs
            "git config finzytrack.allowPush true", "git -c core.hooksPath=/dev/null push", "git push origin +next",
            "git push origin HEAD:main", "git push -d origin next",
            # G-14 the owner's running app over HTTP
            "curl -X POST http://localhost:8001/api/transactions -d x", "curl -X DELETE 127.0.0.1:8001/api/x",
            "wget -qO- http://[::1]:8001/health",
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
            # G-19 kills
            "fuser -k 8001/tcp", "killall Python", "kill -TERM -1", "pkill -f backend",
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

    def test_the_owners_port_and_the_git_gates(self) -> None:
        """BL-0055's forms that reach the owner's app or the git gates: each must be refused; the ordinary forms
        beside them must not. S0054 dropped its refusals of deliberately built commands (a flag carried by a
        `$(...)` next to git; a network redirect to another port): stated limits, PROCESS.md §11."""
        nv = "--no-" + "verify"  # spelled in two parts so this file's text is not itself a command the guard reads
        net = "/dev/" + "tcp"
        refused = [
            f"cat < {net}/127.0.0.1/8001",
            f'exec 3<>"{net}/127.0.0.1/8001"',  # a quoted redirect target still opens the connection
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
        # Stated limits (PROCESS.md §11): code given to `sh -c` or `eval`, or run by an interpreter, is checked for
        # kill words only, its paths and git flags are not parsed; nor are commands built on purpose to slip past the
        # parser (BL-0042 and BL-0062 removed, BL-0055's anti-bypass refusals removed; owner, 2026-10-29).

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
