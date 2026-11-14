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
    """Run git in `repo` with a fixed identity and return its stdout as text.

    SPEC: "Determinism" (fixtures do not depend on the user's git config).
    """
    # A fixed identity and no system or global config: fixtures build the same way on
    # every machine, whatever the developer's own git settings.
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@localhost",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@localhost",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git"] + list(args), cwd=repo, env=env, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode()


def write(repo, path, text):
    """Write `text` to `path` inside `repo`, creating directories as needed."""
    full = os.path.join(repo, path)
    # dirname is "" for a top-level file, and makedirs("") would raise.
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, "w") as handle:
        handle.write(text)


def tool(module, repo, args, stdin, extra_env=None):
    """Run `python -m git_overlap.<module>` in `repo`; return (exit code, stdout, stderr).

    SPEC: "Common rules" (runnable as `python3 -m git_overlap.<name>`).
    """
    env = dict(os.environ, PYTHONPATH=ROOT)
    # PYTHONPATH, not an install: the code under test is this checkout's.
    env.update(extra_env or {})
    proc = subprocess.run([sys.executable, "-m", "git_overlap." + module] + list(args),
                          # input= always, so the command never waits on the test runner's own stdin.
                          cwd=repo, env=env, input=stdin,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout, proc.stderr


class FootprintTest(unittest.TestCase):
    """Behaviour of git footprint against a small fixture repository.

    SPEC: "git footprint BASE".
    """

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
        # git mv stages the rename itself; the edits below are staged by name.
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
        """A rename shows as D + A; records are sorted by path; main's own change is absent.

        SPEC: "git footprint BASE" (Output: renames, sorting).
        """
        # BASE is main, which has moved on: m.txt must not show, only feat's own work.
        code, out, err = tool("footprint", self.repo, ["main"], b"feat\n")
        self.assertEqual((code, err), (0, b""))
        # The rename a.txt -> z.txt is D + A, and d/c.txt sorts between b.txt and z.txt.
        self.assertEqual(out, b"feat\tD\ta.txt\nfeat\tM\tb.txt\nfeat\tA\td/c.txt\n"
                              b"feat\tA\tz.txt\n")

    def test_repeats_crlf_and_empty_records(self):
        """A repeated ref is processed once; CR is stripped; empty lines are skipped.

        SPEC: "git footprint BASE" (Input) and "Input tolerance".
        """
        code, out, _ = tool("footprint", self.repo, ["main"], b"main\nfeat\r\n\nfeat\n")
        self.assertEqual(code, 0)
        # main relative to itself changes nothing, so only feat's four records appear.
        self.assertEqual(out.count(b"\n"), 4)
        self.assertTrue(out.startswith(b"feat\tD\ta.txt\n"))

    def test_ref_starting_with_dash_is_a_ref(self):
        """A ref named "-x" is resolved as a ref, both on stdin and as BASE after "--".

        SPEC: "Refs" and "Options".
        """
        # git branch refuses a name starting with -, update-ref does not.
        git(self.repo, "update-ref", "refs/heads/-x", "feat")
        code, out, _ = tool("footprint", self.repo, ["main"], b"-x\n")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(b"-x\tD\ta.txt\n"))
        # "--" is accepted though not needed: -x would be a positional anyway.
        code, out, _ = tool("footprint", self.repo, ["--", "-x"], b"main\n")
        self.assertEqual((code, out), (0, b"main\tA\tm.txt\n"))

    def test_z_mode(self):
        """With -z, input and output records end with NUL.

        SPEC: "Records".
        """
        code, out, _ = tool("footprint", self.repo, ["-z", "main"], b"feat\0")
        self.assertEqual(code, 0)
        self.assertEqual(out.split(b"\0")[0], b"feat\tD\ta.txt")
        self.assertTrue(out.endswith(b"\0"))

    @unittest.skipIf(os.name == "nt", "Windows file names cannot contain TAB")
    def test_tab_in_path_needs_z(self):
        """A path with TAB is an error naming -z in LF mode, and written as-is with -z.

        SPEC: "Unsafe names".
        """
        git(self.repo, "switch", "-q", "feat")
        # A real file with a TAB in its name, which POSIX filesystems allow.
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
        """Unknown BASE, unknown ref, a non-commit and unrelated history all exit 2.

        SPEC: "Errors", "Record errors" and "Shallow or unrelated histories".
        """
        # A tree id is a valid object but not a commit: it must be rejected as a ref.
        tree = git(self.repo, "rev-parse", "main^{tree}").strip().encode()
        # An orphan branch shares no history with main: no merge base.
        git(self.repo, "switch", "-q", "--orphan", "lonely")
        write(self.repo, "o.txt", "o\n")
        git(self.repo, "add", "o.txt")
        git(self.repo, "commit", "-q", "-m", "orphan")
        cases = [(["nosuch"], b"feat\n"), (["main"], b"nosuch\n"),
                 (["main"], tree + b"\n"), (["main"], b"lonely\n")]
        # Errors about a record carry its number; errors about BASE do not.
        code, _, err = tool("footprint", self.repo, ["main"], b"feat\nlonely\n")
        self.assertEqual(err, b"git-footprint: record 2: no merge base between main and lonely\n")
        code, _, err = tool("footprint", self.repo, ["nosuch"], b"feat\n")
        self.assertEqual(err, b"git-footprint: unknown ref or not a commit: nosuch\n")
        for args, stdin in cases:
            code, _, err = tool("footprint", self.repo, args, stdin)
            self.assertEqual(code, 2, (args, stdin))
            self.assertTrue(err.startswith(b"git-footprint: "), err)
            self.assertNotIn(b"Traceback", err)
            self.assertEqual(err.count(b"\n"), 1, err)

    def test_submodule_pointer_change_counts(self):
        """A gitlink change is listed even when the user's config ignores submodules.

        SPEC: "git footprint BASE" (the pinned diff).
        """
        sha = git(self.repo, "rev-parse", "main").strip()
        # Mode 160000 is a gitlink: a submodule pointer, no .gitmodules needed.
        git(self.repo, "update-index", "--add", "--cacheinfo", "160000,%s,sub" % sha)
        git(self.repo, "commit", "-q", "-m", "add gitlink")
        git(self.repo, "switch", "-q", "-c", "bump")
        feat = git(self.repo, "rev-parse", "feat").strip()
        git(self.repo, "update-index", "--cacheinfo", "160000,%s,sub" % feat)
        git(self.repo, "commit", "-q", "-m", "bump gitlink")
        # The user's config would hide the change; the tool pins --ignore-submodules=none.
        git(self.repo, "config", "diff.ignoreSubmodules", "all")
        code, out, _ = tool("footprint", self.repo, ["main"], b"bump\n")
        self.assertEqual((code, out), (0, b"bump\tM\tsub\n"))

    def test_ref_must_be_a_single_revision(self):
        """"^a" and ranges are rejected, as BASE and on stdin; ":/message" is accepted.

        SPEC: "Resolving a ref".
        """
        for args, stdin in ((["main"], b"^feat\n"), (["main"], b"main..feat\n"),
                            (["^main"], b"feat\n")):
            code, _, err = tool("footprint", self.repo, args, stdin)
            self.assertEqual(code, 2, (args, stdin))
            self.assertIn(b"unknown ref or not a commit", err)
        # :/^feat searches commit messages: still one revision, so it is a valid ref.
        code, out, _ = tool("footprint", self.repo, ["main"], b":/^feat\n")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(b":/^feat\tD\ta.txt\n"))

    def test_error_names_are_escaped_to_one_line(self):
        """A ref holding LF or a non-UTF-8 byte is escaped in the one-line error message
        (on Windows such a ref cannot reach git's argv, and the message says so).

        SPEC: "Errors" and "Resolving a ref" (Windows).
        """
        # -z, so the LF reaches the tool inside the ref instead of splitting the record.
        code, _, err = tool("footprint", self.repo, ["-z", "main"], b"no\nsuch\\\xff\0")
        self.assertEqual(code, 2)
        message = (b"cannot pass a name that is not valid UTF-8 to git on this platform: "
                   if os.name == "nt" else b"unknown ref or not a commit: ")
        self.assertEqual(err, b"git-footprint: record 1: " + message
                              + b"no\\nsuch\\\\\\xff\n")

    def test_unterminated_last_record(self):
        """A final record without its LF is accepted.

        SPEC: "Unterminated last record".
        """
        code, out, _ = tool("footprint", self.repo, ["main"], b"main\nfeat")
        self.assertEqual(code, 0)
        self.assertEqual(out.count(b"\n"), 4)

    @unittest.skipIf(os.name == "nt", "closing a descriptor with sh redirection is POSIX only")
    def test_closed_standard_streams(self):
        """fd 0 or fd 1 not open at all is exit 2 with the SPEC's message.

        SPEC: "Standard streams".
        """
        env = dict(os.environ, PYTHONPATH=ROOT)
        base = [sys.executable, "-m", "git_overlap.footprint", "main"]
        for redirect, message in (("<&-", b"standard input is not open"),
                                  (">&-", b"standard output is not open")):
            # sh closes the descriptor itself; "$@" passes the argv through without re-quoting.
            proc = subprocess.run(["sh", "-c", '"$@" ' + redirect, "sh"] + base,
                                  cwd=self.repo, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(proc.returncode, 2, redirect)
            self.assertEqual(proc.stderr, b"git-footprint: " + message + b"\n")

    def test_help_and_version_take_precedence(self):
        """-h and --version win over any other argument, even invalid ones.

        SPEC: "Standard streams".
        """
        # Neither extra positionals nor an unknown BASE are looked at once -h or --version is seen.
        for args in (["--version", "extra", "more"], ["nosuch", "-h"]):
            code, out, err = tool("footprint", self.repo, args, b"")
            self.assertEqual((code, err), (0, b""), args)
            self.assertTrue(out)

    def test_help_and_version(self):
        """-h prints usage and exits 0; --version prints the name and version.

        SPEC: "Common rules" (`-h` and `--version`).
        """
        code, out, _ = tool("footprint", self.repo, ["-h"], b"")
        self.assertEqual(code, 0)
        self.assertIn(b"-z", out)
        code, out, _ = tool("footprint", self.repo, ["--version"], b"")
        self.assertEqual((code, out), (0, b"git-footprint 0.1.0\n"))


if __name__ == "__main__":
    unittest.main()
