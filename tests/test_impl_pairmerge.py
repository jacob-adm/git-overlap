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

# The footprint suite's helpers: one way to build fixtures and run commands.
from test_impl_footprint import ROOT, git, tool, write


def git_version():
    """Return git's (major, minor), e.g. (2, 50) for "git version 2.50.1 (Apple Git-155)".

    SPEC: "Merge mechanism" (version floor).
    """
    out = subprocess.run(["git", "version"], stdout=subprocess.PIPE).stdout
    # search, not match: vendor builds wrap the number in extra text.
    match = re.search(rb"(\d+)\.(\d+)", out)
    return (int(match.group(1)), int(match.group(2)))


# git pairmerge needs git 2.43 (SPEC "Version floor"); older hosts run the floor test only.
HAS_FLOOR = git_version() >= (2, 43)


def object_store(repo):
    """Return {relative path: sha256 of bytes} for every file under .git/objects, packs included.

    SPEC: "Merge mechanism" (nothing is written to the repository).
    """
    base = os.path.join(repo, ".git", "objects")
    snapshot = {}
    for root, _, files in os.walk(base):
        for name in files:
            full = os.path.join(root, name)
            with open(full, "rb") as handle:
                # Hash contents, not just names: a rewritten pack could keep its name.
                snapshot[os.path.relpath(full, base)] = hashlib.sha256(handle.read()).hexdigest()
    return snapshot


@unittest.skipUnless(HAS_FLOOR, "git pairmerge needs git 2.43 or later")
class PairmergeTest(unittest.TestCase):
    """main has CHANGELOG.md and lib.txt; a and b each append to CHANGELOG.md; c edits lib.txt.

    SPEC: "git pairmerge" and "Merge mechanism".
    """

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
        # Packed objects must be readable through the alternates entry as well as loose ones.
        git(repo, "repack", "-adq")
        # Outside the repository, so FILE cannot be mistaken for a committed attributes file.
        self.attrs = os.path.join(repo, "..", os.path.basename(repo) + "-attrs")
        with open(self.attrs, "wb") as handle:
            handle.write(b"CHANGELOG.md merge=union\n")
        self.addCleanup(os.remove, self.attrs)

    def pairmerge(self, args, stdin):
        """Run git pairmerge in the fixture; return (exit code, stdout, stderr)."""
        return tool("pairmerge", self.repo, args, stdin)

    def test_clean_conflict_dedup_and_input_order(self):
        """Pairs keep input order and refs as given; reversed repeats and extra fields are ignored.

        SPEC: "git pairmerge" (Input, Output).
        """
        # c/a stays as given (not sorted); b/a repeats a/b; c/c is a self-pair; field 3 is ignored.
        code, out, err = self.pairmerge([], b"c\ta\tCHANGELOG.md\na\tb\nb\ta\nc\tc\n")
        self.assertEqual((code, err), (1, b""))
        self.assertEqual(out, b"c\ta\tclean\t\na\tb\tconflict\tCHANGELOG.md\nc\tc\tclean\t\n")

    def test_attributes_fix_union_conflict(self):
        """CHANGELOG.md merge=union in --attributes turns the conflict clean; nothing found = 0.

        SPEC: "git pairmerge" (`--attributes FILE`) and "Exit status".
        """
        code, out, _ = self.pairmerge(["--attributes", self.attrs], b"a\tb\n")
        self.assertEqual((code, out), (0, b"a\tb\tclean\t\n"))

    def test_worktree_gitattributes_do_not_apply(self):
        """A worktree .gitattributes with merge=union does not change the default result.

        SPEC: "Merge mechanism" (the attribute source is always pinned).
        """
        # Uncommitted, in the worktree: without the pinned empty attribute tree git would use it.
        write(self.repo, ".gitattributes", "CHANGELOG.md merge=union\n")
        code, out, _ = self.pairmerge([], b"a\tb\n")
        self.assertEqual((code, out), (1, b"a\tb\tconflict\tCHANGELOG.md\n"))

    def test_object_store_unchanged(self):
        """Merging, with and without --attributes, leaves .git/objects byte-identical.

        SPEC: "Merge mechanism" (nothing is written to the repository).
        """
        before = object_store(self.repo)
        self.pairmerge([], b"a\tb\na\tc\n")
        # --attributes writes a blob and a tree: they must land in the private directory only.
        self.pairmerge(["--attributes", self.attrs], b"a\tb\n")
        self.assertEqual(object_store(self.repo), before)

    def test_errors_exit_2(self):
        """Unknown ref, malformed record and unreadable FILE exit 2 before any output.

        SPEC: "git pairmerge", "Malformed input" and "Errors".
        """
        for args, stdin in (([], b"a\tb\na\tnosuch\n"), ([], b"a\n"),
                            # The first case fails on record 2, after record 1 parsed: still no output at all.
                            (["--attributes", self.attrs + ".missing"], b"a\tb\n")):
            code, out, err = self.pairmerge(args, stdin)
            self.assertEqual((code, out), (2, b""), (args, stdin))
            self.assertTrue(err.startswith(b"git-pairmerge: "), err)
            self.assertEqual(err.count(b"\n"), 1, err)

    def test_dash_ref_and_z_mode(self):
        """A ref named -x is a ref; -z ends input and output records with NUL.

        SPEC: "Refs" and "Records".
        """
        # update-ref, because git branch refuses a name starting with -.
        git(self.repo, "update-ref", "refs/heads/-x", "b")
        code, out, _ = self.pairmerge(["-z"], b"a\t-x\0")
        self.assertEqual((code, out), (1, b"a\t-x\tconflict\tCHANGELOG.md\0"))

    def test_footprint_overlap_pairmerge_pipe(self):
        """git footprint | git overlap | git pairmerge works end to end.

        SPEC: "git pairmerge" (Input: overlap output pipes in).
        """
        # Each stage's real output is the next stage's input: the SPEC's documented pipeline.
        _, footprint, _ = tool("footprint", self.repo, ["main"], b"a\nb\nc\n")
        code, overlap, _ = tool("overlap", self.repo, [], footprint)
        self.assertEqual((code, overlap), (1, b"a\tb\tCHANGELOG.md\n"))
        self.assertEqual(self.pairmerge([], overlap)[:2], (1, b"a\tb\tconflict\tCHANGELOG.md\n"))


    def test_paths_are_root_relative_from_a_subdirectory_and_bare(self):
        """Run from a subdirectory, and in a bare clone, conflicted paths stay root-relative.

        SPEC: "Paths are relative to the repository root" and "Merge mechanism".
        """
        repo = self.repo
        for branch in ("d1", "d2"):
            git(repo, "switch", "-q", "-c", branch, "main")
            write(repo, "dir/f", branch + "\n")
            git(repo, "add", "dir/f")
            git(repo, "commit", "-q", "-m", branch)
        git(repo, "switch", "-q", "main")
        # An empty subdirectory to run from: merge-tree must still be run at the root.
        os.makedirs(os.path.join(repo, "other"))
        expected = (1, b"d1\td2\tconflict\tdir/f\n")
        self.assertEqual(tool("pairmerge", os.path.join(repo, "other"), [], b"d1\td2\n")[:2],
                         expected)
        bare = repo + "-bare.git"
        self.addCleanup(shutil.rmtree, bare, ignore_errors=True)
        # A bare clone has no worktree, so the root is the git directory itself.
        git(repo, "clone", "-q", "--bare", repo, bare)
        self.assertEqual(tool("pairmerge", bare, [], b"d1\td2\n")[:2], expected)

    def test_user_merge_config_does_not_flip_verdict(self):
        """merge.renames=false would make rename-vs-edit a conflict; the pin keeps it clean.

        SPEC: "Merge mechanism" (merge settings are pinned).
        """
        repo = self.repo
        # Large enough that git sees the edited, moved file as a rename (similarity >= 50%).
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
        # Every setting that could turn rename detection off or starve it, set hostile.
        for key, value in (("merge.renames", "false"), ("diff.renames", "false"),
                           ("merge.directoryRenames", "false"), ("merge.renameLimit", "1")):
            git(repo, "config", key, value)
        self.assertEqual(self.pairmerge([], b"mv\ted\n")[:2], (0, b"mv\ted\tclean\t\n"))

    @unittest.skipIf(os.name == "nt", "Windows paths cannot contain ':' after the drive")
    def test_repository_path_with_separator(self):
        """A repository directory whose name holds ':' still works (C-quoted alternates).

        SPEC: "Merge mechanism" (GIT_ALTERNATE_OBJECT_DIRECTORIES quoting).
        """
        parent = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        # ":" is the path separator in GIT_ALTERNATE_OBJECT_DIRECTORIES on POSIX.
        repo = os.path.join(parent, "col:on")
        git(self.repo, "clone", "-q", self.repo, repo)
        for branch in ("a", "b"):
            # A clone has only main locally; the pair needs local branches a and b.
            git(repo, "branch", "-q", branch, "origin/" + branch)
        self.assertEqual(tool("pairmerge", repo, [], b"a\tb\n")[:2],
                         (1, b"a\tb\tconflict\tCHANGELOG.md\n"))

    def test_single_revision_refs_only(self):
        """"^a" and ranges are exit 2 before any merge; ":/message" is a ref.

        SPEC: "Resolving a ref".
        """
        for stdin in (b"^a\tb\n", b"a\ta..b\n"):
            code, out, err = self.pairmerge([], stdin)
            self.assertEqual((code, out), (2, b""), stdin)
            self.assertIn(b"unknown ref or not a commit", err)
        # :/^c names the commit whose message starts with c: a single revision.
        self.assertEqual(self.pairmerge([], b":/^c\ta\n")[:2], (0, b":/^c\ta\tclean\t\n"))

    def test_malformed_unterminated_and_self_pair(self):
        """Empty first or second field is malformed; an unterminated record and a self-pair work.

        SPEC: "git pairmerge", "Malformed input" and "Unterminated last record".
        """
        for stdin in (b"a\t\n", b"\tb\n", b"a\n"):
            code, _, err = self.pairmerge([], stdin)
            self.assertEqual(code, 2, stdin)
            self.assertTrue(err.startswith(b"git-pairmerge: record 1: "), err)
        # No final LF, and a ref paired with itself: merged like any pair, clean.
        self.assertEqual(self.pairmerge([], b"a\ta")[:2], (0, b"a\ta\tclean\t\n"))

    def test_temp_object_dir_removed_on_success(self):
        """With TMPDIR/TEMP/TMP pointed at a private dir, that dir is empty afterwards.

        SPEC: "Merge mechanism" (the temporary directory is removed).
        """
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # tempfile.mkdtemp reads TMPDIR on POSIX and TEMP or TMP on Windows: set all three.
        env = {"TMPDIR": tmp, "TEMP": tmp, "TMP": tmp}
        self.assertEqual(tool("pairmerge", self.repo, [], b"a\tb\na\tc\n", env)[0], 1)
        self.assertEqual(os.listdir(tmp), [])

    def test_relative_git_environment_from_subdirectory(self):
        """Relative GIT_DIR, GIT_WORK_TREE and GIT_OBJECT_DIRECTORY still work from a subdir.

        SPEC: "Merge mechanism" (merge-tree runs at the repository root).
        """
        sub = os.path.join(self.repo, "other")
        os.makedirs(sub)
        before = object_store(self.repo)
        # Relative to the subdirectory: wrong if merge-tree, run at the root, read them as given.
        env = {"GIT_DIR": "../.git", "GIT_WORK_TREE": "..",
               "GIT_OBJECT_DIRECTORY": os.path.join("..", ".git", "objects")}
        code, out, err = tool("pairmerge", sub, [], b"a\tb\na\tc\n", env)
        self.assertEqual((code, out, err),
                         (1, b"a\tb\tconflict\tCHANGELOG.md\na\tc\tclean\t\n", b""))
        self.assertEqual(object_store(self.repo), before)

    def test_no_merge_base_names_both_refs(self):
        """Unrelated histories: exit 2, `record N: no merge base for A and B`.

        SPEC: "Shallow or unrelated histories".
        """
        git(self.repo, "switch", "-q", "--orphan", "orphan")
        write(self.repo, "o.txt", "o\n")
        git(self.repo, "add", "o.txt")
        git(self.repo, "commit", "-q", "-m", "orphan")
        code, out, err = self.pairmerge([], b"a\tb\na\torphan\n")
        self.assertEqual(code, 2)
        self.assertEqual(err, b"git-pairmerge: record 2: no merge base for a and orphan\n")

    def test_reader_closing_early_is_exit_0_and_cleans_up(self):
        """EPIPE (the reader left) is a quiet exit 0, and the private temp dir is still removed.

        SPEC: "Standard streams" and "Merge mechanism".
        """
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
        # communicate feeds stdin and collects stderr without risking a pipe deadlock.
        _, err = proc.communicate(b"a\tb\na\tc\nb\tc\n")
        self.assertEqual((proc.returncode, err), (0, b""))
        self.assertEqual(os.listdir(tmp), [])


@unittest.skipUnless(HAS_FLOOR, "git pairmerge needs git 2.43 or later")
@unittest.skipIf(os.name == "nt", "signals and blocked pipes are tested on POSIX only")
class BlockedWriterTest(unittest.TestCase):
    """A pair conflicting in many long paths: its output overfills the pipe, so git pairmerge
    blocks writing while its private object directory exists.

    SPEC: "Merge mechanism" (removal on SIGINT, SIGTERM and SIGHUP).
    """

    COUNT = 1500

    def setUp(self):
        """Build branches big1 and big2 that edit the same COUNT files differently."""
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        repo = self.repo
        git(repo, "init", "-q", "-b", "main")
        # About 190 KB of conflict records, far more than a pipe buffer holds (64 KB on Linux).
        names = ["p/%s%04d" % ("n" * 100, i) for i in range(self.COUNT)]
        for name in names:
            write(repo, name, "base\n")
        git(repo, "add", "p")
        git(repo, "commit", "-q", "-m", "base")
        for branch in ("big1", "big2"):
            git(repo, "switch", "-q", "-c", branch, "main")
            for name in names:
                write(repo, name, branch + "\n")
            git(repo, "commit", "-q", "-am", branch)
        git(repo, "switch", "-q", "main")
        # The private TMPDIR whose contents are checked; restored writable before removal.
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Runs before rmtree (cleanups are LIFO), undoing the chmod 555 of the leftover test.
        self.addCleanup(os.chmod, self.tmp, 0o700)

    def start_blocked(self):
        """Start git pairmerge on big1/big2, never reading stdout; wait for its temp dir."""
        env = dict(os.environ, PYTHONPATH=ROOT, TMPDIR=self.tmp, TEMP=self.tmp, TMP=self.tmp)
        proc = subprocess.Popen([sys.executable, "-m", "git_overlap.pairmerge"], cwd=self.repo,
                                env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.addCleanup(proc.stderr.close)
        self.addCleanup(proc.stdout.close)
        proc.stdin.write(b"big1\tbig2\n")
        proc.stdin.close()
        deadline = time.time() + 30
        # Poll: the directory appearing is the only outside sign the merge has begun.
        while not [n for n in os.listdir(self.tmp) if n.startswith("git-pairmerge-")]:
            self.assertLess(time.time(), deadline, "no git-pairmerge-* directory appeared")
            time.sleep(0.05)
        # Let it get past the merge and fill the pipe: it is then blocked writing.
        time.sleep(1.0)
        self.assertIsNone(proc.poll())
        return proc

    def check_signal(self, name):
        """Signal the blocked writer: exit 128 + signum, private TMPDIR left empty."""
        signum = getattr(signal, name)
        proc = self.start_blocked()
        # 128 + signum is the shell convention the SPEC adopts for a signal exit.
        proc.send_signal(signum)
        self.assertEqual(proc.wait(timeout=30), 128 + signum)
        self.assertEqual(proc.stderr.read(), b"")
        self.assertEqual(os.listdir(self.tmp), [])

    def test_sigterm(self):
        """SIGTERM unwinds through the cleanup.

        SPEC: "Merge mechanism".
        """
        self.check_signal("SIGTERM")

    def test_sigint(self):
        """SIGINT unwinds through the cleanup.

        SPEC: "Merge mechanism".
        """
        self.check_signal("SIGINT")

    @unittest.skipUnless(hasattr(signal, "SIGHUP"), "no SIGHUP on this platform")
    def test_sighup(self):
        """SIGHUP unwinds through the cleanup.

        SPEC: "Merge mechanism".
        """
        self.check_signal("SIGHUP")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions")
    def test_leftover_temp_dir_is_a_warning(self):
        """If the temp dir cannot be removed: one warning line naming it, exit status kept.

        SPEC: "Merge mechanism" (only SIGKILL can leave the directory behind).
        """
        proc = self.start_blocked()
        leftover = os.path.join(self.tmp, os.listdir(self.tmp)[0])
        # The temp dir's contents can still go, but not its own entry in TMPDIR.
        os.chmod(self.tmp, 0o555)
        out = proc.stdout.read()
        self.assertEqual(proc.wait(timeout=30), 1)
        self.assertEqual(out.count(b"\tconflict\t"), self.COUNT)
        self.assertEqual(proc.stderr.read(),
                         b"git-pairmerge: warning: could not remove temporary directory "
                         + os.fsencode(leftover) + b"\n")


@unittest.skipIf(HAS_FLOOR, "only meaningful on git older than 2.43")
class PairmergeFloorTest(unittest.TestCase):
    """On git older than 2.43, git pairmerge refuses with exit 2 naming the floor.

    SPEC: "Merge mechanism" (version floor).
    """

    def test_old_git_is_exit_2_naming_243(self):
        """Exit 2 and a one-line message naming 2.43."""
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        git(repo, "init", "-q")
        # One commit is enough: the version check runs before any ref is read.
        git(repo, "commit", "-q", "--allow-empty", "-m", "x")
        code, out, err = tool("pairmerge", repo, [], b"HEAD\tHEAD\n")
        self.assertEqual((code, out), (2, b""))
        self.assertIn(b"2.43", err)
        self.assertEqual(err.count(b"\n"), 1)


class GitErrorTest(unittest.TestCase):
    """A git failure with empty stderr still says what happened.

    SPEC: "Errors".
    """

    def test_exit_status_and_signal_are_named(self):
        """Empty stderr gives the exit status, or the signal for a negative status.

        SPEC: "Errors" (one line).
        """
        # Imported directly: an empty git stderr cannot be provoked reliably from outside.
        sys.path.insert(0, ROOT)
        from git_overlap._common import git_error
        self.assertEqual(git_error(b"", 3), "git exited with status 3")
        self.assertEqual(git_error(b"", -9), "git was killed by signal 9")
        self.assertEqual(git_error(b"fatal: x\nmore\n", 128), "fatal: x")


if __name__ == "__main__":
    unittest.main()
