# SPDX-License-Identifier: Apache-2.0
"""Implementer's tests for git pairmerge, run against real temporary git repositories."""

import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from test_impl_footprint import ROOT, git, tool, write


def git_version():
    """Return git's (major, minor), e.g. (2, 50) for "git version 2.50.1 (Apple Git-155)"."""
    out = subprocess.run(["git", "version"], stdout=subprocess.PIPE).stdout
    match = re.search(rb"(\d+)\.(\d+)", out)
    return (int(match.group(1)), int(match.group(2)))


# git pairmerge needs git 2.43 (SPEC "Version floor"); older hosts run the floor test only.
HAS_FLOOR = git_version() >= (2, 43)


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


@unittest.skipUnless(HAS_FLOOR, "git pairmerge needs git 2.43 or later")
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
        """Pairs keep input order and refs as given; reversed repeats and extra fields are ignored."""
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


    def test_paths_are_root_relative_from_a_subdirectory_and_bare(self):
        """Run from a subdirectory, and in a bare clone, conflicted paths stay root-relative."""
        repo = self.repo
        for branch in ("d1", "d2"):
            git(repo, "switch", "-q", "-c", branch, "main")
            write(repo, "dir/f", branch + "\n")
            git(repo, "add", "dir/f")
            git(repo, "commit", "-q", "-m", branch)
        git(repo, "switch", "-q", "main")
        os.makedirs(os.path.join(repo, "other"))
        expected = (1, b"d1\td2\tconflict\tdir/f\n")
        self.assertEqual(tool("pairmerge", os.path.join(repo, "other"), [], b"d1\td2\n")[:2],
                         expected)
        bare = repo + "-bare.git"
        self.addCleanup(shutil.rmtree, bare, ignore_errors=True)
        git(repo, "clone", "-q", "--bare", repo, bare)
        self.assertEqual(tool("pairmerge", bare, [], b"d1\td2\n")[:2], expected)

    def test_user_merge_config_does_not_flip_verdict(self):
        """merge.renames=false would make rename-vs-edit a conflict; the pin keeps it clean."""
        repo = self.repo
        write(repo, "big.txt", "".join("line %d\n" % i for i in range(40)))
        git(repo, "add", "big.txt")
        git(repo, "commit", "-q", "-m", "big")
        git(repo, "switch", "-q", "-c", "mv", "main")
        git(repo, "mv", "big.txt", "moved.txt")
        git(repo, "commit", "-q", "-m", "mv")
        git(repo, "switch", "-q", "-c", "ed", "main")
        write(repo, "big.txt", "".join("line %d\n" % i for i in range(39)) + "edited\n")
        git(repo, "commit", "-q", "-am", "ed")
        git(repo, "switch", "-q", "main")
        for key, value in (("merge.renames", "false"), ("diff.renames", "false"),
                           ("merge.directoryRenames", "false"), ("merge.renameLimit", "1")):
            git(repo, "config", key, value)
        self.assertEqual(self.pairmerge([], b"mv\ted\n")[:2], (0, b"mv\ted\tclean\t\n"))

    @unittest.skipIf(os.name == "nt", "Windows paths cannot contain ':' after the drive")
    def test_repository_path_with_separator(self):
        """A repository directory whose name holds ':' still works (C-quoted alternates)."""
        parent = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        repo = os.path.join(parent, "col:on")
        git(self.repo, "clone", "-q", self.repo, repo)
        for branch in ("a", "b"):
            git(repo, "branch", "-q", branch, "origin/" + branch)
        self.assertEqual(tool("pairmerge", repo, [], b"a\tb\n")[:2],
                         (1, b"a\tb\tconflict\tCHANGELOG.md\n"))

    def test_single_revision_refs_only(self):
        """"^a" and ranges are exit 2 before any merge; ":/message" is a ref."""
        for stdin in (b"^a\tb\n", b"a\ta..b\n"):
            code, out, err = self.pairmerge([], stdin)
            self.assertEqual((code, out), (2, b""), stdin)
            self.assertIn(b"unknown ref or not a commit", err)
        self.assertEqual(self.pairmerge([], b":/^c\ta\n")[:2], (0, b":/^c\ta\tclean\t\n"))

    def test_malformed_unterminated_and_self_pair(self):
        """Empty first or second field is malformed; an unterminated record and a self-pair work."""
        for stdin in (b"a\t\n", b"\tb\n", b"a\n"):
            code, _, err = self.pairmerge([], stdin)
            self.assertEqual(code, 2, stdin)
            self.assertTrue(err.startswith(b"git-pairmerge: record 1: "), err)
        self.assertEqual(self.pairmerge([], b"a\ta")[:2], (0, b"a\ta\tclean\t\n"))

    def test_temp_object_dir_removed_on_success(self):
        """With TMPDIR/TEMP/TMP pointed at a private dir, that dir is empty afterwards."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = {"TMPDIR": tmp, "TEMP": tmp, "TMP": tmp}
        self.assertEqual(tool("pairmerge", self.repo, [], b"a\tb\na\tc\n", env)[0], 1)
        self.assertEqual(os.listdir(tmp), [])

    @unittest.skipIf(os.name == "nt", "SIGTERM delivery to a child is POSIX only")
    def test_sigterm_exits_143_and_leaves_no_temp_dir(self):
        """SIGTERM while waiting on stdin: exit 128 + 15, private temp dir empty."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = dict(os.environ, PYTHONPATH=ROOT, TMPDIR=tmp, TEMP=tmp, TMP=tmp)
        proc = subprocess.Popen([sys.executable, "-m", "git_overlap.pairmerge"], cwd=self.repo,
                                env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        proc.stdin.write(b"a\tb\n")
        proc.stdin.flush()
        # Give the interpreter time to start and install its handlers before signalling.
        time.sleep(1.5)
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate()
        self.assertEqual((proc.returncode, out, err), (143, b"", b""))
        self.assertEqual(os.listdir(tmp), [])


    def test_no_merge_base_names_both_refs(self):
        """Unrelated histories: exit 2, `record N: no merge base for A and B`."""
        git(self.repo, "switch", "-q", "--orphan", "orphan")
        write(self.repo, "o.txt", "o\n")
        git(self.repo, "add", "o.txt")
        git(self.repo, "commit", "-q", "-m", "orphan")
        code, out, err = self.pairmerge([], b"a\tb\na\torphan\n")
        self.assertEqual(code, 2)
        self.assertEqual(err, b"git-pairmerge: record 2: no merge base for a and orphan\n")

    def test_reader_closing_early_is_exit_0_and_cleans_up(self):
        """EPIPE (the reader left) is a quiet exit 0, and the private temp dir is still removed."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = dict(os.environ, PYTHONPATH=ROOT, TMPDIR=tmp, TEMP=tmp, TMP=tmp)
        read_end, write_end = os.pipe()
        # Close the reading end before git pairmerge writes anything: every write is EPIPE.
        os.close(read_end)
        proc = subprocess.Popen([sys.executable, "-m", "git_overlap.pairmerge"], cwd=self.repo,
                                env=env, stdin=subprocess.PIPE, stdout=write_end,
                                stderr=subprocess.PIPE)
        os.close(write_end)
        _, err = proc.communicate(b"a\tb\na\tc\nb\tc\n")
        self.assertEqual((proc.returncode, err), (0, b""))
        self.assertEqual(os.listdir(tmp), [])


@unittest.skipIf(HAS_FLOOR, "only meaningful on git older than 2.43")
class PairmergeFloorTest(unittest.TestCase):
    """On git older than 2.43, git pairmerge refuses with exit 2 naming the floor."""

    def test_old_git_is_exit_2_naming_243(self):
        """Exit 2 and a one-line message naming 2.43."""
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        git(repo, "init", "-q")
        git(repo, "commit", "-q", "--allow-empty", "-m", "x")
        code, out, err = tool("pairmerge", repo, [], b"HEAD\tHEAD\n")
        self.assertEqual((code, out), (2, b""))
        self.assertIn(b"2.43", err)
        self.assertEqual(err.count(b"\n"), 1)


class GitErrorTest(unittest.TestCase):
    """A git failure with empty stderr still says what happened."""

    def test_exit_status_and_signal_are_named(self):
        """Empty stderr gives the exit status, or the signal for a negative status."""
        sys.path.insert(0, ROOT)
        from git_overlap._common import git_error
        self.assertEqual(git_error(b"", 3), "git exited with status 3")
        self.assertEqual(git_error(b"", -9), "git was killed by signal 9")
        self.assertEqual(git_error(b"fatal: x\nmore\n", 128), "fatal: x")


if __name__ == "__main__":
    unittest.main()
