"""Meta-tests for the push gate (S0031, the M0 gate red team H1, H2, M3, M4): `scripts/check_push.py`, the
`--range` modes of `scripts/check_staged.py` and `scripts/privacy_scan.py`, and the hook files that call them.

Every case runs in a scratch git repository (a bare origin and a clone) under a temporary folder, with the user's
git configuration switched off; nothing is pushed anywhere. The red team's probes are the must-not-pass cases:
A (a commit whose file is restored in the working tree), J (partial staging), B (a ledger and a secret that
skipped pre-commit; the secret removed again in the next commit), the other-branch push.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
ZERO = "0" * 40


def j(*parts: str) -> str:
    return "".join(parts)


class Scratch(unittest.TestCase):
    """A bare origin with one commit on `next`, and a clone of it checked out on `next`."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "gitconfig").write_text("")
        self.env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(base / "gitconfig"),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.com"}
        # the pre-push hook exports GIT_NO_REPLACE_OBJECTS; the scratch repos start from git's defaults
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS"):
            self.env.pop(k, None)
        self.origin = base / "origin.git"
        self.clone = base / "clone"
        self.git_in(base, "init", "-q", "--bare", "-b", "next", str(self.origin))
        self.git_in(base, "clone", "-q", str(self.origin), str(self.clone))
        self.git("checkout", "-q", "-b", "next")
        self.write(".gitignore", "data\nignore/\n*.log\n__pycache__/\n.venv/\nnode_modules/\nbuild/\n")
        self.write("README.md", "hello\n")
        self.commit("first")
        self.git("push", "-q", "origin", "next")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def git_in(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True, text=True).stdout

    def git(self, *args: str) -> str:
        return self.git_in(self.clone, *args)

    def write(self, rel: str, text: str | bytes) -> None:
        p = self.clone / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text)

    def commit(self, msg: str, *paths: str) -> str:
        self.git("add", "-A", *paths)
        self.git("commit", "-q", "-m", msg)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def remote_next(self) -> str:
        return self.git_in(self.origin, "rev-parse", "next").strip()

    def script(self, name: str, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPTS / name), *args], cwd=self.clone, env=self.env,
                              input=stdin, capture_output=True, text=True)

    def push_line(self, local_sha: str | None = None, remote_sha: str | None = None, ref: str = "refs/heads/next") -> str:
        local_sha = local_sha or self.head()
        remote_sha = self.remote_next() if remote_sha is None else remote_sha
        return f"{ref} {local_sha} {ref} {remote_sha}\n"

    def check_push(self, stdin: str | None = None, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.script("check_push.py", "origin", *extra, stdin=self.push_line() if stdin is None else stdin)


class PushedCommitsAreTheTree(Scratch):
    """H1: the checks read the working tree, so the push is refused unless the tree is what is pushed."""

    def test_a_clean_tree_at_head_passes_and_names_the_range(self) -> None:
        base = self.remote_next()
        self.write("a.txt", "a\n")
        tip = self.commit("second")
        r = self.check_push()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), [f"{base}..{tip}"])

    def test_probe_a_a_committed_change_restored_in_the_working_tree(self) -> None:
        self.write("src.py", "x = 1\n")
        self.commit("clean")
        self.write("src.py", "x = 1\n_CACHE: dict[str, int] = {}\n")
        self.commit("violation")
        self.write("src.py", "x = 1\n")  # the working tree is clean again; the violation is in the pushed commit
        r = self.check_push()
        self.assertEqual(r.returncode, 1)
        self.assertIn("differs from HEAD", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_probe_j_partial_staging(self) -> None:
        self.write("m.ts", "export const zero = '0'\n")
        self.commit("honest")
        self.write("m.ts", "import { apiBase } from './api'\nexport const zero = apiBase\n")
        self.git("add", "m.ts")
        self.write("m.ts", "export const zero = '0'\n")
        self.git("commit", "-q", "-m", "staged violation, clean working copy")
        self.assertEqual(self.check_push().returncode, 1)

    def test_an_untracked_file_whatever_the_status_config(self) -> None:
        self.write("forgot.py", "x = 1\n")
        self.assertEqual(self.check_push().returncode, 1)
        self.git("config", "status.showUntrackedFiles", "no")
        self.assertEqual(self.check_push().returncode, 1)

    def test_a_staged_but_uncommitted_change(self) -> None:
        self.write("README.md", "changed\n")
        self.git("add", "README.md")
        self.assertEqual(self.check_push().returncode, 1)

    def test_index_flags_that_hide_a_change(self) -> None:
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag):
                self.git("update-index", flag, "README.md")
                self.write("README.md", "hidden change\n")
                self.assertEqual(self.git("status", "--porcelain"), "")  # git status itself is fooled
                r = self.check_push()
                self.assertEqual(r.returncode, 1)
                self.assertIn("hide changes", r.stderr)
                self.git("update-index", flag.replace("--", "--no-"), "README.md")
                self.git("checkout", "--", "README.md")

    def test_pushing_a_ref_that_is_not_head(self) -> None:
        self.write("a.txt", "a\n")
        older = self.commit("second")
        self.write("b.txt", "b\n")
        self.commit("third")
        r = self.check_push(self.push_line(local_sha=older))
        self.assertEqual(r.returncode, 1)
        self.assertIn("is not HEAD", r.stderr)
        # another branch checked out while next is pushed (the red team's other-branch case)
        self.git("checkout", "-q", "-b", "other", older)
        self.write("c.txt", "c\n")
        self.commit("on other")
        nxt = self.git("rev-parse", "next").strip()
        self.assertEqual(self.check_push(self.push_line(local_sha=nxt)).returncode, 1)

    def test_a_sha_that_is_not_a_commit_here_and_malformed_input(self) -> None:
        self.assertEqual(self.check_push(self.push_line(local_sha="1" * 40)).returncode, 1)
        self.assertEqual(self.check_push("refs/heads/next HEAD refs/heads/next abc\n").returncode, 1)

    def test_an_annotated_tag_at_head_is_peeled(self) -> None:
        self.git("tag", "-a", "-m", "t", "v9")
        tag = self.git("rev-parse", "v9").strip()
        self.assertNotEqual(tag, self.head())
        r = self.check_push(f"refs/tags/v9 {tag} refs/tags/v9 {ZERO}\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"{self.head()}..{self.head()}", f"tag {tag}"])

    def test_a_deletion_pushes_nothing_to_check(self) -> None:
        r = self.check_push(f"(delete) {ZERO} refs/heads/old {self.remote_next()}\n")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)


class ReplacementsAndMovingTrees(Scratch):
    """S0031 verifier F2, F3: what git shows must be what is pushed, from the first check to the last."""

    def bad_and_decoy(self) -> tuple[str, str, str]:
        base = self.head()
        self.write("main.beancount", "2020-01-01 open Assets:Cash\n")
        bad = self.commit("bad")
        self.git("reset", "-q", "--hard", base)
        self.write("README.md", "decoy\n")
        good = self.commit("decoy")
        self.git("reset", "-q", "--hard", base)
        return base, bad, good

    def test_a_replacement_object_is_refused_and_does_not_hide_the_pushed_blobs(self) -> None:
        base, bad, good = self.bad_and_decoy()
        self.git("update-ref", "refs/heads/next", bad)
        self.git("replace", bad, good)
        self.git("reset", "-q", "--hard", "next")
        self.assertFalse((self.clone / "main.beancount").exists())  # the checkout shows the decoy
        r = self.check_push(self.push_line(local_sha=bad, remote_sha=base))
        self.assertEqual(r.returncode, 1)
        self.assertIn("replacement objects", r.stderr)
        self.assertEqual(self.script("check_staged.py", "--range", f"{base}..{bad}").returncode, 1)  # reads the real blob

    def test_a_grafts_file_is_refused(self) -> None:
        (self.clone / ".git/info").mkdir(exist_ok=True)
        (self.clone / ".git/info/grafts").write_text(self.head() + "\n")
        r = self.check_push()
        self.assertEqual(r.returncode, 1)
        self.assertIn("grafts", r.stderr)

    def test_still_refuses_a_moved_head_or_a_changed_tree(self) -> None:
        head = self.head()
        self.assertEqual(self.script("check_push.py", "--still", head).returncode, 0)
        self.write("README.md", "changed during the checks\n")
        r = self.script("check_push.py", "--still", head)
        self.assertEqual(r.returncode, 1)
        self.assertIn("changed while the checks ran", r.stderr)
        self.git("checkout", "--", "README.md")
        self.write("a.txt", "a\n")
        self.commit("moved")
        self.assertIn("HEAD moved", self.script("check_push.py", "--still", head).stderr)
        self.assertEqual(self.script("check_push.py", "--still", "").returncode, 1)


class Messages(Scratch):
    """S0031 verifier F4: commit and tag messages are published, so the privacy scan reads them."""

    def token(self) -> str:
        return j("gh", "p_", "a1B2c3D4e5F6g7H8i9", "J0k1L2m3N4o5P6q7R8")

    def test_a_commit_message_in_the_range(self) -> None:
        base = self.remote_next()
        self.write("a.txt", "a\n")
        self.git("add", "a.txt")
        self.git("commit", "-q", "-m", f"deploy token {self.token()}")
        self.write("b.txt", "b\n")
        self.commit("a later honest commit")
        p = self.script("privacy_scan.py", "--range", f"{base}..{self.head()}", "--quiet")
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("message", p.stderr)
        self.assertNotIn(self.token(), p.stderr)

    def test_a_pushed_annotated_tag_and_its_message(self) -> None:
        self.git("tag", "-a", "-m", f"release note: {self.token()}", "v9")
        tag = self.git("rev-parse", "v9").strip()
        r = self.check_push(f"refs/tags/v9 {tag} refs/tags/v9 {ZERO}\n")
        self.assertIn(f"tag {tag}", r.stdout.splitlines())
        p = self.script("privacy_scan.py", "--tag-message", tag, "--quiet")
        self.assertEqual(p.returncode, 1, p.stderr)
        self.git("tag", "-a", "-m", "a clean note", "v10")
        self.assertEqual(self.script("privacy_scan.py", "--tag-message", self.git("rev-parse", "v10").strip(),
                                     "--quiet").returncode, 0)
        self.assertNotIn("tag ", self.check_push().stdout)  # a branch push names no tag

    def test_ordinary_slice_subjects_pass_and_values_after_the_slug_do_not(self) -> None:  # S0031 verifier R2-3
        import base64
        import hashlib

        msg = self.clone / "MSG"
        subjects = ["S0016-detect-secrets: round-6 verifier F1-F2 (a docstring fix)",
                    "S0040-password-lock: the app asks for the password at start",
                    "S0041-secret-store: keys in the OS keychain (ADR-0071)",
                    "S0042: rename the secret: field in settings to keychain_ref",
                    "S0043-api-key: the LLM key is read from the keychain",
                    "S0044-token-refresh: refresh tokens rotate daily",
                    "S0045-import: OFX parser handles <BANKACCTFROM>",
                    "S0031-gate-hardening: verifier round 1 F1-F10 (more skip forms refused)"]
        subjects += subprocess.run(["git", "-C", str(REPO), "log", "--format=%s", "--grep=^S0016-"], capture_output=True,
                                   text=True).stdout.splitlines()
        for subject in subjects:
            with self.subTest(subject=subject):
                msg.write_text(f"{subject}\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n")
                r = self.script("privacy_scan.py", "--message", str(msg), "--quiet")
                self.assertEqual(r.returncode, 0, r.stderr)
        value = base64.b64encode(hashlib.sha256(b"a subject test value").digest()).decode()
        for subject in (f"S0016-detect-secrets: {self.token()}", f'S0050-x: x = "{value}"',
                        f'S0016-detect-secrets: "{value}"'):
            with self.subTest(subject=subject[:24]):
                msg.write_text(subject + "\n")
                self.assertEqual(self.script("privacy_scan.py", "--message", str(msg), "--quiet").returncode, 1)

    def test_a_tag_of_a_tag_names_every_tag_object(self) -> None:  # S0031 verifier R2-6
        self.git("tag", "-a", "-m", f"inner note {self.token()}", "inner")
        inner = self.git("rev-parse", "inner").strip()
        self.git("tag", "-a", "-m", "outer clean note", "outer", inner)
        outer = self.git("rev-parse", "outer").strip()
        r = self.check_push(f"refs/tags/outer {outer} refs/tags/outer {ZERO}\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([ln for ln in r.stdout.splitlines() if ln.startswith("tag ")], [f"tag {outer}", f"tag {inner}"])

    def test_the_history_baseline_excuses_only_its_exact_blob_path_and_rule(self) -> None:  # S0031 verifier R2-4
        sys.path.insert(0, str(SCRIPTS))
        import privacy_scan

        base = self.remote_next()
        self.write("known.md", f"see {j('~', 'jdoe/')}\n")
        self.write("other.md", f"see {j('~', 'jdoe/')}\n")
        tip = self.commit("a known finding")
        sha = self.git("rev-parse", f"{tip}:known.md").strip()
        old_cwd, old = os.getcwd(), privacy_scan.HISTORY_BASELINE
        try:
            os.chdir(self.clone)
            with unittest.mock.patch.dict(os.environ, self.env, clear=True):
                spec = f"{base}..{tip}"
                privacy_scan.HISTORY_BASELINE = frozenset({("known.md", sha, "home-path")})
                found = {(f.path.split(" ")[0], f.rule) for f in privacy_scan.run_range(spec, [])[0]}
                self.assertEqual(found, {("other.md", "home-path")})  # the same blob at another path is not excused
                privacy_scan.HISTORY_BASELINE = frozenset({("known.md", sha, "email")})
                found = {(f.path.split(" ")[0], f.rule) for f in privacy_scan.run_range(spec, [])[0]}
                self.assertIn(("known.md", "home-path"), found)  # another rule is not excused
                privacy_scan.HISTORY_BASELINE = frozenset({("commit", tip, "home-path")})
                self.assertEqual(len(privacy_scan.run_range(spec, [])[0]), 2)  # a commit row does not excuse blobs
        finally:
            privacy_scan.HISTORY_BASELINE = old
            os.chdir(old_cwd)

    def test_the_commit_message_file_above_the_scissors(self) -> None:
        msg = self.clone / "MSG"
        msg.write_text(f"subject\n\nbody with {self.token()}\n")
        self.assertEqual(self.script("privacy_scan.py", "--message", str(msg), "--quiet").returncode, 1)
        msg.write_text("subject\n# ------------------------ >8 ------------------------\n"
                       f"diff below the scissors {self.token()}\n")
        self.assertEqual(self.script("privacy_scan.py", "--message", str(msg), "--quiet").returncode, 0)


class IgnoredCodeIsRefused(Scratch):
    """H1: an ignored file is invisible to `git status` but can still be loaded by the checks (a conftest.py)."""

    def test_ignored_code_outside_the_trusted_folders(self) -> None:
        for rel in ("ignore/conftest.py", "data/x/helper.py", "backend/tests/data/conftest.py", "scripts/tests/node_modules/__init__.py", "a/__pycache__/evil.py", "x/__pycache__/sub/y.pyc"):
            with self.subTest(rel=rel):
                self.write(rel, "import pytest\n")
                r = self.check_push()
                self.assertEqual(r.returncode, 1, rel)
                self.assertIn("ignored source or config", r.stderr)
                (self.clone / rel).unlink()

    def test_an_ignored_code_file_outside_an_ignored_folder(self) -> None:
        self.write("backend/tests/test_a.py", "def test_a():\n    pass\n")
        self.commit("a tracked test, so its folder is not collapsed in git status")
        (self.clone / ".git/info/exclude").write_text("conftest.py\nlocal.toml\n")
        for rel in ("backend/tests/conftest.py", "local.toml"):
            with self.subTest(rel=rel):
                self.write(rel, "x = 1\n")
                self.assertIn(f"!! {rel}", self.git("status", "--porcelain", "--ignored"))
                r = self.check_push()
                self.assertEqual(r.returncode, 1, rel)
                self.assertIn("ignored source or config", r.stderr)
                (self.clone / rel).unlink()

    def test_ignored_tool_configuration_is_refused(self) -> None:  # S0031 verifier probe I
        self.write("backend/tests/test_a.py", "def test_a():\n    pass\n")
        self.write("frontend/src/a.ts", "export const a = 1\n")
        self.commit("tracked files, so their folders are listed")
        (self.clone / ".git/info/exclude").write_text(".prettierignore\npyrightconfig.json\n.eslintrc.cjs\n")
        for rel in ("frontend/.prettierignore", "backend/pyrightconfig.json", "frontend/.eslintrc.cjs"):
            with self.subTest(rel=rel):
                self.write(rel, "*\n")
                self.assertEqual(self.check_push().returncode, 1, rel)
                (self.clone / rel).unlink()

    def test_claude_state_passes_but_not_its_hooks_folder(self) -> None:  # S0031 verifier F7
        (self.clone / ".git/info/exclude").write_text(".claude/worktrees/\n.claude/hooks/local.py\nmutants/\nhtmlcov/\n")
        self.write(".claude/settings.json", "{}\n")
        self.write(".claude/hooks/guard.py", "x = 1\n")
        self.write("backend/tests/test_a.py", "def test_a():\n    pass\n")
        self.commit("tracked claude files")
        for rel in (".claude/worktrees/agent-1/backend/tests/conftest.py", "backend/mutants/src/app/clock.py",
                    "backend/htmlcov/coverage_html.js", "htmlcov/x.js"):
            self.write(rel, "x = 1\n")
        r = self.check_push()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.write(".claude/hooks/local.py", "x = 1\n")
        self.assertEqual(self.check_push().returncode, 1)

    def test_ignored_editor_and_mutation_report_output_passes(self) -> None:  # S0031 verifier R2-5
        self.write("frontend/src/a.ts", "export const a = 1\n")
        self.commit("a tracked frontend file")
        (self.clone / ".git/info/exclude").write_text(".vscode/\n.idea/\nreports/\n")
        for rel in (".vscode/settings.json", ".idea/misc.xml", "frontend/reports/stryker/mutation.json",
                    "frontend/reports/stryker/index.js"):
            self.write(rel, "{}\n")
        r = self.check_push()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ignored_caches_dependencies_and_logs_pass(self) -> None:
        for rel in ("run.log", "data/statement.txt", "a/__pycache__/m.cpython-313.pyc", "backend/.venv/lib/x.py",
                    "frontend/node_modules/pkg/index.js", "build/out/app.js", "data/.venv/lib/y.py"):
            self.write(rel, "x\n")
        r = self.check_push()
        self.assertEqual(r.returncode, 0, r.stderr)


class Ranges(Scratch):
    def test_a_new_remote_branch_is_scanned_from_the_remote_next(self) -> None:
        base = self.remote_next()
        self.write("a.txt", "a\n")
        tip = self.commit("second")
        line = self.push_line(remote_sha=ZERO, ref="refs/heads/feature")
        self.assertEqual(self.check_push(line).stdout.split(), [f"{base}..{tip}"])
        # a remote-tracking ref moved in this clone does not shrink the range: the remote's own next is read
        self.git("update-ref", "refs/remotes/origin/next", tip)
        self.assertEqual(self.check_push(line).stdout.split(), [f"{base}..{tip}"])

    def test_no_remote_next_means_the_whole_history(self) -> None:
        self.git_in(self.origin, "update-ref", "-d", "refs/heads/next")
        self.assertEqual(self.check_push(self.push_line(remote_sha=ZERO)).stdout.split(), [self.head()])

    def test_the_remote_next_is_read_at_the_push_url(self) -> None:  # S0031 verifier F5
        base = self.remote_next()
        self.write("a.txt", "a\n")
        tip = self.commit("second")
        other = Path(self.tmp.name) / "fetchside.git"
        self.git_in(Path(self.tmp.name), "clone", "-q", "--bare", str(self.clone), str(other))
        self.git_in(other, "update-ref", "refs/heads/next", tip)  # the fetch side already "has" the tip
        self.git("remote", "set-url", "origin", str(other))
        line = self.push_line(remote_sha=ZERO, ref="refs/heads/feature")
        self.assertEqual(self.check_push(line).stdout.split(), [tip + ".." + tip])  # fetch URL: nothing to scan
        self.assertEqual(self.check_push(line, str(self.origin)).stdout.split(), [f"{base}..{tip}"])

    def test_without_a_remote_next_the_floor_is_the_base(self) -> None:  # S0031 verifier F6
        sys.path.insert(0, str(SCRIPTS))
        import check_push

        floor = self.head()
        self.write("a.txt", "a\n")
        tip = self.commit("second")
        self.git_in(self.origin, "update-ref", "-d", "refs/heads/next")
        old_cwd, old_floor = os.getcwd(), check_push.FLOOR
        try:
            os.chdir(self.clone)
            with unittest.mock.patch.dict(os.environ, self.env, clear=True):
                check_push.FLOOR = floor
                refs, _ = check_push.parse_refs(self.push_line(remote_sha=ZERO))
                self.assertEqual(check_push.ranges_for(refs, "origin"), [f"{floor}..{tip}"])
                check_push.FLOOR = "3" * 40  # not in this history: the whole history
                self.assertEqual(check_push.ranges_for(refs, "origin"), [tip])
        finally:
            check_push.FLOOR = old_floor
            os.chdir(old_cwd)

    def test_a_remote_sha_this_clone_lacks_falls_back_to_the_remote_next_merge_base(self) -> None:
        base = self.remote_next()
        self.write("a.txt", "a\n")
        tip = self.commit("second")
        r = self.check_push(self.push_line(remote_sha="2" * 40))
        self.assertEqual(r.stdout.split(), [f"{base}..{tip}"], r.stderr)


class RangeScans(Scratch):
    """H2: every blob of the pushed range, also those that skipped pre-commit or are gone at the tip."""

    def staged(self, spec: str) -> subprocess.CompletedProcess[str]:
        return self.script("check_staged.py", "--range", spec)

    def test_probe_b_a_ledger_and_a_secret_added_then_removed(self) -> None:
        base = self.remote_next()
        self.write("notes/main.beancount", '2020-01-01 open Assets:Bank:Checking INR\n')
        self.commit("B1: a ledger outside corpora/fixtures")
        token = j("gh", "p_", "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8")
        self.write("notes/creds.txt", f'token = "{token}"\n')
        self.commit("B2a: a secret")
        self.git("rm", "-q", "notes/creds.txt")
        self.commit("B2b: secret removed")
        spec = f"{base}..{self.head()}"
        r = self.staged(spec)
        self.assertEqual(r.returncode, 1)
        self.assertIn("notes/main.beancount", r.stderr)
        p = self.script("privacy_scan.py", "--range", spec, "--quiet")
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("notes/creds.txt (blob", p.stderr)
        self.assertNotIn(token, p.stderr)  # redacted

    def test_two_versions_of_one_path_are_both_checked(self) -> None:
        base = self.remote_next()
        rows = "".join(f"2026-01-{d:02d},Coffee shop,-4.50,1234.56\n" for d in range(1, 8))
        self.write("notes.txt", rows)
        self.commit("statement")
        self.write("notes.txt", "fine now\n")
        self.commit("overwritten")
        self.assertEqual(self.staged(f"{base}..{self.head()}").returncode, 1)
        self.assertEqual(self.script("check_staged.py", "--paths", "notes.txt").returncode, 0)  # the tip is fine

    def test_a_rename_onto_a_refused_name_and_a_symlink_out(self) -> None:
        self.write("notes.txt", "2020-01-01 open Assets:Cash\n")
        base = self.commit("an innocent name")
        self.git("mv", "notes.txt", "main.beancount")
        self.commit("same blob, refused name")
        self.assertEqual(self.staged(f"{base}..{self.head()}").returncode, 1)
        base = self.head()
        os.symlink("/etc/hosts", self.clone / "link")
        self.commit("symlink out")
        self.assertEqual(self.staged(f"{base}..{self.head()}").returncode, 1)

    def test_a_merge_brings_its_branch_blobs_and_its_own_resolution(self) -> None:
        base = self.head()
        self.git("checkout", "-q", "-b", "side")
        self.write("side.beancount", "x\n")
        self.commit("side ledger")
        self.git("checkout", "-q", "next")
        self.write("other.txt", "o\n")
        self.commit("main line")
        self.git("merge", "-q", "--no-ff", "-m", "merge", "side")
        r = self.staged(f"{base}..{self.head()}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("side.beancount", r.stderr)

    def test_a_merge_that_adds_a_file_only_in_its_resolution(self) -> None:
        base = self.head()
        self.git("checkout", "-q", "-b", "side")
        self.write("s.txt", "s\n")
        self.commit("side")
        self.git("checkout", "-q", "next")
        self.git("merge", "-q", "--no-ff", "--no-commit", "side")
        self.write("evil.beancount", "x\n")  # an "evil merge": in no parent, only in the merge commit
        self.git("add", "evil.beancount")
        self.git("commit", "-q", "-m", "merge with an extra file")
        r = self.staged(f"{base}..{self.head()}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("evil.beancount", r.stderr)

    def test_the_secret_entropy_rule_reads_the_range(self) -> None:
        import base64
        import hashlib

        base = self.remote_next()
        value = base64.b64encode(hashlib.sha256(b"a range test value").digest()).decode()
        self.write("conf.py", f'x = "{value}"\n')  # no credential word: only detect-secrets' entropy rule sees it
        self.commit("entropy")
        self.write("later.md", "an innocent later file\n")
        self.commit("later")
        self.git("rm", "-q", "conf.py")
        self.commit("gone")
        p = self.script("privacy_scan.py", "--range", f"{base}..{self.head()}", "--quiet")
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("conf.py (blob", p.stderr)
        self.assertIn("secret-entropy", p.stderr)

    def test_a_clean_range_passes_and_specs_are_shas_only(self) -> None:
        base = self.remote_next()
        self.write("docs.md", "hello\n")
        self.commit("docs")
        self.assertEqual(self.staged(f"{base}..{self.head()}").returncode, 0)
        self.assertEqual(self.script("privacy_scan.py", "--range", f"{base}..{self.head()}", "--quiet").returncode, 0)
        for bad in ("HEAD", "next", f"{base}...{self.head()}", f"{ZERO}..{self.head()}", "", "abc"):
            with self.subTest(bad=bad):
                self.assertEqual(self.staged(bad).returncode, 2)
                self.assertEqual(self.script("privacy_scan.py", "--range", bad, "--quiet").returncode, 2)

    def test_a_file_turned_into_a_symlink_out(self) -> None:  # S0031 verifier F9: diff status T
        base = self.head()
        (self.clone / "README.md").unlink()
        os.symlink("/etc/hosts", self.clone / "README.md")
        self.commit("typechange")
        self.assertIn("T", self.git("diff-tree", "-r", "--no-renames", base, self.head()).split())
        r = self.staged(f"{base}..{self.head()}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("symlink", r.stderr)

    def test_the_whole_history_spec(self) -> None:
        self.write("main.beancount", "x\n")
        self.commit("ledger")
        self.git("rm", "-q", "main.beancount")
        self.commit("gone")
        self.assertEqual(self.staged(self.head()).returncode, 1)


class HookFiles(unittest.TestCase):
    """The hooks call the gate in the right order (the logic itself is tested above)."""

    def setUp(self) -> None:
        self.pre_push = (REPO / ".githooks/pre-push").read_text()

    def test_pre_push_refuses_before_any_check_and_scans_each_range(self) -> None:
        text = self.pre_push
        gate = text.index("scripts/check_push.py")
        self.assertLess(gate, text.index("\nrun "))
        self.assertLess(text.index('refs="$(cat)"'), gate)
        self.assertIn('python3 scripts/check_push.py "$remote_name" "$remote_url")"; then', text)
        self.assertIn("  exit 1\nfi", text[gate:gate + 400])
        self.assertIn('python3 scripts/check_staged.py --range "$range"', text)
        self.assertIn('python3 scripts/privacy_scan.py --range "$range" --quiet', text)
        self.assertIn('done <<< "$ranges"', text)
        self.assertIn('python3 scripts/check_push.py "$remote_name" "$remote_url"', text)
        self.assertIn('python3 scripts/privacy_scan.py --tag-message "${range#tag }" --quiet', text)
        self.assertIn("git ls-files -z | xargs -0 python3 scripts/check_staged.py --paths", text)
        self.assertLess(text.index("export GIT_NO_REPLACE_OBJECTS=1"), gate)
        still = text.index('python3 scripts/check_push.py --still "$head_at_start"')
        self.assertEqual(text[still:].count("\nrun "), 0)  # the last check
        self.assertLess(text.index('head_at_start="$(git rev-parse HEAD)"'), text.index("\nrun "))

    def test_pre_push_lints_the_whole_tree_and_runs_every_oracle_test(self) -> None:
        for line in ('.venv/bin/ruff format --check --config ruff.toml .', '.venv/bin/ruff check --config ruff.toml .',
                     'node_modules/.bin/eslint --max-warnings 0 .', 'node_modules/.bin/prettier --check .',
                     'python -m unittest discover -s scripts/oracle/tests -q\n'):
            self.assertIn(line, self.pre_push)
        self.assertNotIn("-s scripts/oracle/tests -p", self.pre_push)  # no per-file subset

    def test_the_manual_ci_can_run_a_milestone_exit(self) -> None:  # S0031, M6
        ci = (REPO / ".github/workflows/ci.yml").read_text()
        self.assertEqual(ci.count("      milestone_exit:\n"), 2)  # workflow_dispatch and workflow_call
        step = ci[ci.index("- name: Invariants"):]
        step = step[:step.index("\n      - ")]
        self.assertIn("FT_MILESTONE_EXIT: ${{ inputs.milestone_exit }}", step)
        self.assertIn("run: python3 scripts/check_invariants.py", step)

    def test_commit_msg_scans_the_message_and_hooks_ignore_replacements(self) -> None:
        hook = REPO / ".githooks/commit-msg"
        self.assertTrue(os.access(hook, os.X_OK))
        self.assertIn('python3 scripts/privacy_scan.py --message "$1" --quiet', hook.read_text())
        for name in ("commit-msg", "pre-commit", "pre-push"):
            self.assertIn("export GIT_NO_REPLACE_OBJECTS=1", (REPO / ".githooks" / name).read_text(), name)

    def test_pre_merge_commit_runs_pre_commit(self) -> None:
        hook = REPO / ".githooks/pre-merge-commit"
        self.assertTrue(os.access(hook, os.X_OK))
        self.assertIn('exec "$(dirname "$0")/pre-commit" "$@"', hook.read_text())


BACKEND_PYTEST = REPO / "backend/.venv/bin/pytest"
VITEST = REPO / "frontend/node_modules/.bin/vitest"


class RunnersRefuseWhatAStaticScanMisses(unittest.TestCase):
    """S0031 verifier R2-1, R2-2: an empty parameter set and `.only` in any spelling fail the run."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @unittest.skipUnless(BACKEND_PYTEST.is_file(), "needs the backend venv (cd backend && uv sync --locked)")
    def test_an_empty_parameter_set_fails_collection(self) -> None:
        for values in ("[]", "[*[]]", "(*(),)", "list()"):
            with self.subTest(values=values):
                (self.dir / "test_e.py").write_text(
                    "import pytest\n\n\n" + "@" + f"pytest.mark.parametrize('x', {values})\ndef test_x(x):\n    pass\n")
                r = subprocess.run([str(BACKEND_PYTEST), "-q", "-c", str(REPO / "backend/pyproject.toml"), "--rootdir",
                                    str(self.dir), str(self.dir / "test_e.py")], capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0, r.stdout)

    @unittest.skipUnless(VITEST.is_file(), "needs the frontend packages (cd frontend && pnpm install --frozen-lockfile)")
    def test_only_in_any_spelling_fails_the_vitest_run_of_the_pre_push_hook(self) -> None:
        for body in ("test('a', { only: true }, () => {})", "test.only('a', () => {})", "describe.only('d', () => { test('a', () => {}) })",
                     "const t = test\nconst { only: solo } = t\nsolo('a', () => {})"):
            with self.subTest(body=body):
                (self.dir / "x.test.js").write_text(body + "\ntest('b', () => {})\n")
                r = subprocess.run([str(VITEST), "run", "--globals", "--root", str(self.dir), "--allowOnly=false"],
                                   cwd=REPO / "frontend", capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0, r.stdout[-400:])
        (self.dir / "x.test.js").write_text("test('a', () => {})\n")
        r = subprocess.run([str(VITEST), "run", "--globals", "--root", str(self.dir), "--allowOnly=false"],
                           cwd=REPO / "frontend", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-400:])

    def test_the_pre_push_vitest_run_refuses_only(self) -> None:
        self.assertIn("node_modules/.bin/vitest run --allowOnly=false", (REPO / ".githooks/pre-push").read_text())

    def test_the_backend_pytest_config_fails_an_empty_parameter_set(self) -> None:
        import tomllib

        cfg = tomllib.loads((REPO / "backend/pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
        self.assertEqual(cfg.get("empty_parameter_set_mark"), "fail_at_collect")


class Floor(unittest.TestCase):
    """S0031 verifier F6: the floor is the commit that started v0.4 from an empty tree, below HEAD."""

    def test_the_floor_is_the_empty_tree_start_of_next(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        import check_push

        def git(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)

        self.assertEqual(git("log", "-1", "--format=%s", check_push.FLOOR).stdout.strip(),
                         "next: start v0.4 from an empty tree; .gitignore first")
        self.assertEqual(git("ls-tree", "--name-only", check_push.FLOOR).stdout.split(), [".gitignore"])
        self.assertEqual(git("merge-base", "--is-ancestor", check_push.FLOOR, "HEAD").returncode, 0)

    def test_known_history_from_the_floor_passes_the_range_scan(self) -> None:  # S0031 verifier R2-4
        sys.path.insert(0, str(SCRIPTS))
        import check_push

        # 796353ab: the commit the round-2 verifier read; every older finding is in privacy_scan.HISTORY_BASELINE
        r = subprocess.run([sys.executable, str(SCRIPTS / "privacy_scan.py"), "--range",
                            f"{check_push.FLOOR}..796353abefab9870857b4f9bc8c43a951400c953", "--quiet"],
                           cwd=REPO, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])


if __name__ == "__main__":
    unittest.main()
