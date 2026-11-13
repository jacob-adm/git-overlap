# SPDX-License-Identifier: Apache-2.0
"""Implementer's tests for git overlap, run outside any repository and fed by real footprints."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_impl_footprint import ROOT, git, tool, write


class OverlapTest(unittest.TestCase):
    """Behaviour of git overlap on hand-written records and on real git footprint output."""

    def setUp(self):
        """Work in an empty directory that is not a git repository."""
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def run_overlap(self, args, stdin):
        """Run git overlap in the empty directory; return (exit code, stdout, stderr)."""
        return tool("overlap", self.dir, args, stdin)

    def test_pairs_sorted_deduplicated_and_never_self(self):
        """Pairs are ordered, sorted and written once; the status field is ignored."""
        stdin = (b"b\tM\tx\n"
                 b"a\t?\tx\n"     # any status value is accepted
                 b"a\tA\ty\r\n"  # CR is stripped
                 b"\n"            # empty records are skipped
                 b"b\tA\ty\n"
                 b"a\tD\tx\n"     # repeat of (a, x): still one record
                 b"c\tA\tz\n"
                 b"c\tM\tz\n")    # c alone on z: no self-pair
        code, out, err = self.run_overlap([], stdin)
        self.assertEqual((code, err), (1, b""))
        self.assertEqual(out, b"a\tb\tx\na\tb\ty\n")

    def test_nothing_found_is_exit_0(self):
        """No shared path writes nothing and exits 0."""
        self.assertEqual(self.run_overlap([], b"a\tA\tx\nb\tA\ty\n"), (0, b"", b""))

    def test_with_filters_pairs(self):
        """--with keeps only pairs that include REF, in either position; dash refs work."""
        stdin = b"a\tA\tp\nb\tA\tp\n-c\tA\tp\n"
        code, out, _ = self.run_overlap(["--with", "b"], stdin)
        self.assertEqual((code, out), (1, b"-c\tb\tp\na\tb\tp\n"))
        code, out, _ = self.run_overlap(["--with", "-c"], stdin)
        self.assertEqual((code, out), (1, b"-c\ta\tp\n-c\tb\tp\n"))
        self.assertEqual(self.run_overlap(["--with=zzz"], stdin)[:2], (0, b""))

    def test_malformed_records_are_exit_2(self):
        """2 fields, an empty ref or an empty path is an error naming the record number."""
        for bad in (b"a\tx\n", b"\tA\tx\n", b"a\tA\t\n"):
            code, out, err = self.run_overlap([], b"a\tA\tx\n\n" + bad)
            self.assertEqual((code, out), (2, b""), bad)
            self.assertTrue(err.startswith(b"git-overlap: record 3: "), err)
            self.assertEqual(err.count(b"\n"), 1)

    def test_path_is_everything_after_second_tab(self):
        """The path keeps its TABs in -z mode; in LF mode a TAB or CR in a path is an error."""
        stdin = b"a\tA\tt\tab\0b\tM\tt\tab\0"
        code, out, _ = self.run_overlap(["-z"], stdin)
        self.assertEqual((code, out), (1, b"a\tb\tt\tab\0"))
        for path in (b"t\tab", b"p\rq"):
            code, _, err = self.run_overlap([], b"a\tA\t" + path + b"\nb\tA\t" + path + b"\n")
            self.assertEqual(code, 2, path)
            self.assertIn(b"-z", err)

    def test_pipeline_from_real_footprint(self):
        """git footprint output pipes straight into git overlap."""
        repo = os.path.join(self.dir, "repo")
        os.mkdir(repo)
        git(repo, "init", "-q", "-b", "main")
        write(repo, "f", "0\n")
        git(repo, "add", "f")
        git(repo, "commit", "-q", "-m", "base")
        for branch in ("one", "two"):
            git(repo, "switch", "-q", "-c", branch, "main")
            write(repo, "f", branch + "\n")
            write(repo, branch, "x\n")
            git(repo, "add", "f", branch)
            git(repo, "commit", "-q", "-m", branch)
        _, footprint, _ = tool("footprint", repo, ["main"], b"two\none\n")
        self.assertEqual(self.run_overlap([], footprint), (1, b"one\ttwo\tf\n", b""))

    def test_closed_stdout_is_quiet_exit_0(self):
        """Closing the reading end early ends the command with exit 0 and no traceback."""
        stdin = b"".join(b"r%d\tA\tp%d\n" % (r, p) for r in range(60) for p in range(60))
        env = dict(os.environ, PYTHONPATH=ROOT)
        proc = subprocess.Popen([sys.executable, "-m", "git_overlap.overlap"], cwd=self.dir,
                                env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        proc.stdin.write(stdin)
        proc.stdin.close()
        proc.stdout.read(10)
        proc.stdout.close()
        err = proc.stderr.read()
        proc.stderr.close()
        self.assertEqual((proc.wait(), err), (0, b""))

    def test_help_version_and_stray_argument(self):
        """-h exits 0, --version names the command, a positional argument is exit 2."""
        self.assertEqual(self.run_overlap(["-h"], b"")[0], 0)
        self.assertEqual(self.run_overlap(["--version"], b"")[1], b"git-overlap 0.1.0\n")
        self.assertEqual(self.run_overlap(["main"], b"")[0], 2)


if __name__ == "__main__":
    unittest.main()
