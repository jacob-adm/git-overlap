# SPDX-License-Identifier: Apache-2.0
"""A file in one ref and a path under it in another ref are an overlap.

Each docstring names the SPEC.md clause it checks. The pipeline test builds a real repository
and runs footprint -> overlap -> pairmerge as black boxes; nothing is mocked.
"""
import unittest

# Imported as a top-level module: unittest discovery puts tests/ on sys.path.
from fixtures import Repo, Sandbox


class DirCollisionCase(unittest.TestCase):
    """Base case: a fresh sandbox and a directory that is not a repository."""

    def setUp(self):
        """Create the sandbox and the no-repository directory overlap runs in."""
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        # Overlap needs no repository: run it where repository discovery finds nothing.
        self.cwd = self.sb.mkdir("no-repo-here")

    def ov(self, args, stdin):
        """Run git overlap outside any repository and return the finished process."""
        return self.sb.run_cmd("overlap", args, stdin=stdin, cwd=self.cwd)

    def assertOut(self, proc, stdout, code):
        """Assert exact stdout and exit status, showing stderr on failure."""
        # One tuple, so a failure shows both values side by side.
        self.assertEqual((proc.stdout, proc.returncode), (stdout, code), proc.stderr)


class TestAncestorRule(DirCollisionCase):
    """SPEC: "git overlap" (a path and a path under it are an overlap)."""

    def test_file_and_path_under_it(self):
        """`flat` touches `dir`, `nested` touches `dir/child`: one record, on the ancestor."""
        proc = self.ov([], b"flat\tA\tdir\nnested\tA\tdir/child\n")
        self.assertOut(proc, b"flat\tnested\tdir\n", 1)

    def test_deep_ancestor(self):
        """The ancestor may be several levels up: `a/b` and `a/b/c/d` overlap on `a/b`."""
        proc = self.ov([], b"x\tA\ta/b\ny\tA\ta/b/c/d\n")
        self.assertOut(proc, b"x\ty\ta/b\n", 1)

    def test_name_prefix_is_not_an_ancestor(self):
        """Only `/` boundaries count: `foo` and `foobar/x` (or `foo.txt`) are not an overlap."""
        # A plain string-prefix test would pair all three refs here.
        proc = self.ov([], b"a\tA\tfoo\nb\tA\tfoobar/x\nc\tA\tfoo.txt\n")
        self.assertOut(proc, b"", 0)

    def test_many_descendants_one_record(self):
        """Several paths under the same ancestor still give one record per pair and path."""
        proc = self.ov([], b"a\tA\td\nb\tA\td/x\nb\tA\td/y/z\n")
        self.assertOut(proc, b"a\tb\td\n", 1)

    def test_same_ref_never_pairs_with_itself(self):
        """One ref touching both `d` and `d/x` (a file turned into a directory) is no pair."""
        proc = self.ov([], b"a\tD\td\na\tA\td/x\n")
        self.assertOut(proc, b"", 0)

    def test_exact_and_ancestor_merge_sorted(self):
        """Exact and ancestor records share one bytewise order and are written once each."""
        # Exact: a and c share p, a and b share z. Ancestor: b's p/q sits under a's and c's p.
        stdin = b"b\tA\tp/q\na\tA\tp\nb\tM\tz\na\tM\tz\nc\tA\tp\n"
        proc = self.ov([], stdin)
        self.assertOut(proc, b"a\tb\tp\na\tb\tz\na\tc\tp\nb\tc\tp\n", 1)

    def test_with_ref(self):
        """`--with REF` keeps only ancestor pairs that include REF."""
        stdin = b"a\tA\td\nb\tA\td/x\nc\tA\td/y\n"
        self.assertOut(self.ov(["--with", "c"], stdin), b"a\tc\td\n", 1)

    def test_z_mode(self):
        """-z: the rule is the same, and a path may hold LF or TAB."""
        # The ancestor `l<LF>f` holds an LF; the descendant also holds a TAB.
        stdin = b"a\tA\tl\nf\0b\tA\tl\nf/x\ty\0"
        self.assertOut(self.ov(["-z"], stdin), b"a\tb\tl\nf\0", 1)


class TestPipeline(DirCollisionCase):
    """The audit's case end to end: footprint -> overlap -> pairmerge reaches the conflict.

    SPEC: "git footprint", "git overlap" and "git pairmerge".
    """

    def test_file_versus_directory_reaches_pairmerge(self):
        """`flat` adds file `dir`, `nested` adds `dir/child`: pairmerge reports the conflict."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"README": "base\n"}, ref="refs/heads/main")
        repo.commit(base, {"dir": "flat\n"}, ref="refs/heads/flat")
        repo.commit(base, {"dir/child": "nested\n"}, ref="refs/heads/nested")
        # A footprint is not a finding: exit 0 on success.
        footprint = repo.run("footprint", ["main"], stdin=b"flat\nnested\n")
        self.assertEqual(footprint.returncode, 0, footprint.stderr)
        overlap = repo.run("overlap", [], stdin=footprint.stdout)
        self.assertOut(overlap, b"flat\tnested\tdir\n", 1)
        # pairmerge reads the first two fields; the conflict is what the old pipeline missed.
        merged = repo.run("pairmerge", [], stdin=overlap.stdout)
        self.assertEqual(merged.returncode, 1, merged.stderr)
        self.assertTrue(merged.stdout.startswith(b"flat\tnested\tconflict\t"), merged.stdout)


if __name__ == "__main__":
    unittest.main()
