# SPDX-License-Identifier: Apache-2.0
"""Implementer's tests for git footprint, run against real temporary git repositories."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# The project root, put on PYTHONPATH so `python -m git_overlap.footprint` finds the package.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(repo, *args):
    """Run git in `repo` with a fixed identity and return its stdout as text."""
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@localhost",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@localhost",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git"] + list(args), cwd=repo, env=env, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode()


def write(repo, path, text):
    """Write `text` to `path` inside `repo`, creating directories as needed."""
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, "w") as handle:
        handle.write(text)


def tool(module, repo, args, stdin):
    """Run `python -m git_overlap.<module>` in `repo`; return (exit code, stdout, stderr)."""
    env = dict(os.environ, PYTHONPATH=ROOT)
    proc = subprocess.run([sys.executable, "-m", "git_overlap." + module] + list(args),
                          cwd=repo, env=env, input=stdin,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout, proc.stderr


class FootprintTest(unittest.TestCase):
    """Behaviour of git footprint against a small fixture repository."""

    def setUp(self):
        """Build: main with a.txt, b.txt; branch feat renames a->z, edits b, adds d/c."""
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        git(self.repo, "init", "-q", "-b", "main")
        write(self.repo, "a.txt", "a\n")
        write(self.repo, "b.txt", "b\n")
        git(self.repo, "add", "a.txt", "b.txt")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "switch", "-q", "-c", "feat")
        git(self.repo, "mv", "a.txt", "z.txt")
        write(self.repo, "b.txt", "b2\n")
        write(self.repo, "d/c.txt", "c\n")
        git(self.repo, "add", "b.txt", "d/c.txt")
        git(self.repo, "commit", "-q", "-m", "feat")
        git(self.repo, "switch", "-q", "main")
        # main moves on after feat forked: the footprint must still use the merge base.
        write(self.repo, "m.txt", "m\n")
        git(self.repo, "add", "m.txt")
        git(self.repo, "commit", "-q", "-m", "main moves")

    def test_rename_is_delete_plus_add_sorted_by_path(self):
        """A rename shows as D + A; records are sorted by path; main's own change is absent."""
        code, out, err = tool("footprint", self.repo, ["main"], b"feat\n")
        self.assertEqual((code, err), (0, b""))
        self.assertEqual(out, b"feat\tD\ta.txt\nfeat\tM\tb.txt\nfeat\tA\td/c.txt\n"
                              b"feat\tA\tz.txt\n")

    def test_repeats_crlf_and_empty_records(self):
        """A repeated ref is processed once; CR is stripped; empty lines are skipped."""
        code, out, _ = tool("footprint", self.repo, ["main"], b"main\nfeat\r\n\nfeat\n")
        self.assertEqual(code, 0)
        # main relative to itself changes nothing, so only feat's four records appear.
        self.assertEqual(out.count(b"\n"), 4)
        self.assertTrue(out.startswith(b"feat\tD\ta.txt\n"))

    def test_ref_starting_with_dash_is_a_ref(self):
        """A ref named "-x" is resolved as a ref, both on stdin and as BASE after "--"."""
        git(self.repo, "update-ref", "refs/heads/-x", "feat")
        code, out, _ = tool("footprint", self.repo, ["main"], b"-x\n")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(b"-x\tD\ta.txt\n"))
        code, out, _ = tool("footprint", self.repo, ["--", "-x"], b"main\n")
        self.assertEqual((code, out), (0, b"main\tA\tm.txt\n"))

    def test_z_mode(self):
        """With -z, input and output records end with NUL."""
        code, out, _ = tool("footprint", self.repo, ["-z", "main"], b"feat\0")
        self.assertEqual(code, 0)
        self.assertEqual(out.split(b"\0")[0], b"feat\tD\ta.txt")
        self.assertTrue(out.endswith(b"\0"))

    @unittest.skipIf(os.name == "nt", "Windows file names cannot contain TAB")
    def test_tab_in_path_needs_z(self):
        """A path with TAB is an error naming -z in LF mode, and written as-is with -z."""
        git(self.repo, "switch", "-q", "feat")
        write(self.repo, "t\tab", "x\n")
        git(self.repo, "add", "t\tab")
        git(self.repo, "commit", "-q", "-m", "tab")
        code, _, err = tool("footprint", self.repo, ["main"], b"feat\n")
        self.assertEqual(code, 2)
        self.assertIn(b"-z", err)
        self.assertTrue(err.startswith(b"git-footprint: "))
        code, out, _ = tool("footprint", self.repo, ["-z", "main"], b"feat\0")
        self.assertEqual(code, 0)
        self.assertIn(b"feat\tA\tt\tab\0", out)

    def test_errors_exit_2_without_traceback(self):
        """Unknown BASE, unknown ref, a non-commit and unrelated history all exit 2."""
        tree = git(self.repo, "rev-parse", "main^{tree}").strip().encode()
        git(self.repo, "switch", "-q", "--orphan", "lonely")
        write(self.repo, "o.txt", "o\n")
        git(self.repo, "add", "o.txt")
        git(self.repo, "commit", "-q", "-m", "orphan")
        cases = [(["nosuch"], b"feat\n"), (["main"], b"nosuch\n"),
                 (["main"], tree + b"\n"), (["main"], b"lonely\n")]
        for args, stdin in cases:
            code, _, err = tool("footprint", self.repo, args, stdin)
            self.assertEqual(code, 2, (args, stdin))
            self.assertTrue(err.startswith(b"git-footprint: "), err)
            self.assertNotIn(b"Traceback", err)
            self.assertEqual(err.count(b"\n"), 1, err)

    def test_help_and_version(self):
        """-h prints usage and exits 0; --version prints the name and version."""
        code, out, _ = tool("footprint", self.repo, ["-h"], b"")
        self.assertEqual(code, 0)
        self.assertIn(b"-z", out)
        code, out, _ = tool("footprint", self.repo, ["--version"], b"")
        self.assertEqual((code, out), (0, b"git-footprint 0.1.0\n"))


if __name__ == "__main__":
    unittest.main()
