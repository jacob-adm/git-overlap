# SPDX-License-Identifier: Apache-2.0
"""Implementer's tests for git pairmerge, run against real temporary git repositories."""

import hashlib
import os
import shutil
import tempfile
import unittest

from test_impl_footprint import git, tool, write


def object_store(repo):
    """Return {relative path: sha256 of bytes} for every file under .git/objects, packs included."""
    base = os.path.join(repo, ".git", "objects")
    snapshot = {}
    for root, _, files in os.walk(base):
        for name in files:
            full = os.path.join(root, name)
            with open(full, "rb") as handle:
                snapshot[os.path.relpath(full, base)] = hashlib.sha256(handle.read()).hexdigest()
    return snapshot


class PairmergeTest(unittest.TestCase):
    """main has CHANGELOG.md and lib.txt; a and b each append to CHANGELOG.md; c edits lib.txt."""

    def setUp(self):
        """Build the fixture and pack it, so the object store has loose and packed objects."""
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        repo = self.repo
        git(repo, "init", "-q", "-b", "main")
        write(repo, "CHANGELOG.md", "# log\n")
        write(repo, "lib.txt", "lib\n")
        git(repo, "add", "CHANGELOG.md", "lib.txt")
        git(repo, "commit", "-q", "-m", "base")
        for branch in ("a", "b"):
            git(repo, "switch", "-q", "-c", branch, "main")
            write(repo, "CHANGELOG.md", "# log\n- %s\n" % branch)
            git(repo, "add", "CHANGELOG.md")
            git(repo, "commit", "-q", "-m", branch)
        git(repo, "switch", "-q", "-c", "c", "main")
        write(repo, "lib.txt", "lib c\n")
        git(repo, "add", "lib.txt")
        git(repo, "commit", "-q", "-m", "c")
        git(repo, "switch", "-q", "main")
        git(repo, "repack", "-adq")
        self.attrs = os.path.join(repo, "..", os.path.basename(repo) + "-attrs")
        with open(self.attrs, "wb") as handle:
            handle.write(b"CHANGELOG.md merge=union\n")
        self.addCleanup(os.remove, self.attrs)

    def pairmerge(self, args, stdin):
        """Run git pairmerge in the fixture; return (exit code, stdout, stderr)."""
        return tool("pairmerge", self.repo, args, stdin)

    def test_clean_conflict_dedup_and_input_order(self):
        """Pairs keep input order and refs as given; a reversed repeat and extra fields are ignored."""
        code, out, err = self.pairmerge([], b"c\ta\tCHANGELOG.md\na\tb\nb\ta\nc\tc\n")
        self.assertEqual((code, err), (1, b""))
        self.assertEqual(out, b"c\ta\tclean\t\na\tb\tconflict\tCHANGELOG.md\nc\tc\tclean\t\n")

    def test_attributes_fix_union_conflict(self):
        """CHANGELOG.md merge=union in --attributes turns the conflict clean; nothing found = 0."""
        code, out, _ = self.pairmerge(["--attributes", self.attrs], b"a\tb\n")
        self.assertEqual((code, out), (0, b"a\tb\tclean\t\n"))

    def test_worktree_gitattributes_do_not_apply(self):
        """A worktree .gitattributes with merge=union does not change the default result."""
        write(self.repo, ".gitattributes", "CHANGELOG.md merge=union\n")
        code, out, _ = self.pairmerge([], b"a\tb\n")
        self.assertEqual((code, out), (1, b"a\tb\tconflict\tCHANGELOG.md\n"))

    def test_object_store_unchanged(self):
        """Merging, with and without --attributes, leaves .git/objects byte-identical."""
        before = object_store(self.repo)
        self.pairmerge([], b"a\tb\na\tc\n")
        self.pairmerge(["--attributes", self.attrs], b"a\tb\n")
        self.assertEqual(object_store(self.repo), before)

    def test_errors_exit_2(self):
        """Unknown ref, malformed record and unreadable FILE exit 2 before any output."""
        for args, stdin in (([], b"a\tb\na\tnosuch\n"), ([], b"a\n"),
                            (["--attributes", self.attrs + ".missing"], b"a\tb\n")):
            code, out, err = self.pairmerge(args, stdin)
            self.assertEqual((code, out), (2, b""), (args, stdin))
            self.assertTrue(err.startswith(b"git-pairmerge: "), err)
            self.assertEqual(err.count(b"\n"), 1, err)

    def test_dash_ref_and_z_mode(self):
        """A ref named -x is a ref; -z ends input and output records with NUL."""
        git(self.repo, "update-ref", "refs/heads/-x", "b")
        code, out, _ = self.pairmerge(["-z"], b"a\t-x\0")
        self.assertEqual((code, out), (1, b"a\t-x\tconflict\tCHANGELOG.md\0"))

    def test_footprint_overlap_pairmerge_pipe(self):
        """git footprint | git overlap | git pairmerge works end to end."""
        _, footprint, _ = tool("footprint", self.repo, ["main"], b"a\nb\nc\n")
        code, overlap, _ = tool("overlap", self.repo, [], footprint)
        self.assertEqual((code, overlap), (1, b"a\tb\tCHANGELOG.md\n"))
        self.assertEqual(self.pairmerge([], overlap)[:2], (1, b"a\tb\tconflict\tCHANGELOG.md\n"))


if __name__ == "__main__":
    unittest.main()
