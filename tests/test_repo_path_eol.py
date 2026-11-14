# SPDX-License-Identifier: Apache-2.0
"""git pairmerge works in a repository whose directory name ends in CR or LF.

SPEC: "Merge mechanism" (paths are relative to the repository root). Real directories and real
repositories only; nothing is mocked.
"""
import os
import unittest

# Imported as a top-level module: unittest discovery puts tests/ on sys.path.
from fixtures import Repo, Sandbox

# Endings git may print back as part of the path; each must survive exactly.
EOL_NAMES = ["repo\n", "repo\r", "repo\r\n", "repo\n\n", "repo\r\r"]
# Other awkward names that must keep working alongside the fix.
OTHER_NAMES = ["re po", "re\tpo", 're"po', "re'po"]


@unittest.skipIf(os.name == "nt", "Windows cannot hold CR, LF, TAB or quotes in a name")
class TestRepositoryPathEndings(unittest.TestCase):
    """Each directory name gets a clean pair and a conflicting pair, and nothing is written."""

    def setUp(self):
        """Create a fresh sandbox per test."""
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def check(self, name):
        """Build a repo named NAME and assert pairmerge reports clean and conflict correctly."""
        repo = Repo(self.sb, name)
        base = repo.commit(None, {"c.txt": "l\n", "y.py": "y\n"}, ref="refs/heads/main")
        # a and b edit the same line of c.txt; d edits another file.
        repo.commit(base, {"c.txt": "l\na\n"}, ref="refs/heads/a")
        repo.commit(base, {"c.txt": "l\nb\n"}, ref="refs/heads/b")
        repo.commit(base, {"y.py": "y2\n"}, ref="refs/heads/d")
        before = repo.state()
        proc = repo.run("pairmerge", [], stdin=b"a\td\na\tb\n")
        self.assertEqual(proc.returncode, 1, "name=%r stderr=%r" % (name, proc.stderr))
        self.assertEqual(proc.stdout, b"a\td\tclean\t\na\tb\tconflict\tc.txt\n",
                         "name=%r stderr=%r" % (name, proc.stderr))
        # Read-only: refs, index, worktree and object store are exactly as before.
        self.assertEqual(repo.state(), before, "name=%r" % (name,))

    def test_line_endings_in_the_directory_name(self):
        """LF, CR, CRLF and repeated endings are part of the name, not git's line end."""
        for name in EOL_NAMES:
            with self.subTest(name=name):
                self.check(name)

    def test_other_awkward_names_still_work(self):
        """Spaces, TABs and quotes in the directory name keep working."""
        for name in OTHER_NAMES:
            with self.subTest(name=name):
                self.check(name)


if __name__ == "__main__":
    unittest.main()
