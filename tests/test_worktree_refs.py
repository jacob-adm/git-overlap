# SPDX-License-Identifier: Apache-2.0
"""Tests for worktree:PATH refs, run against real temporary repositories with worktrees."""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from fixtures import PAIRMERGE_OK, needs_pairmerge_git

# The project root, put on PYTHONPATH so `python -m git_overlap.footprint` finds the package.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(repo, *args):
    """Run git in `repo` with a fixed identity and return its stdout as text.

    SPEC: "Determinism" (fixtures do not depend on the user's git config).
    """
    # A fixed identity and no system or global config, as in the other test modules.
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@localhost",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@localhost",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(["git"] + list(args), cwd=repo, env=env, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode()


def tool(name, repo, args=(), data=b"", env=None):
    """Run one of the three commands in `repo`; return (exit code, stdout, stderr) as bytes."""
    # PYTHONPATH is replaced, not extended: only this checkout's package may be imported.
    full = dict(os.environ, PYTHONPATH=ROOT, GIT_CONFIG_NOSYSTEM="1",
                GIT_CONFIG_GLOBAL=os.devnull)
    full.update(env or {})
    proc = subprocess.run([sys.executable, "-m", "git_overlap." + name] + list(args), cwd=repo,
                          env=full, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout, proc.stderr


def write(path, text):
    """Write `text` to the file at `path`, with LF line ends on every platform."""
    with open(path, "w", newline="\n") as handle:
        handle.write(text)


def state(repo, worktrees):
    """Return everything a command must leave alone: objects, refs, and each worktree's
    status, index bytes and file contents."""
    # count-objects covers loose and packed objects; a new unreachable object would show.
    parts = [git(repo, "count-objects", "-v"), git(repo, "for-each-ref")]
    for path in worktrees:
        parts.append(git(path, "status", "--porcelain"))
        index = git(path, "rev-parse", "--path-format=absolute", "--git-path", "index").strip()
        with open(index, "rb") as handle:
            parts.append(hashlib.sha256(handle.read()).hexdigest())
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            if os.path.isfile(full):
                with open(full, "rb") as handle:
                    parts.append(name + hashlib.sha256(handle.read()).hexdigest())
    return parts


class WorktreeRefs(unittest.TestCase):
    """Four worktrees: two change the same line, one adds a file, one is untouched."""

    def setUp(self):
        """Build the repository and its worktrees, with every change left uncommitted."""
        # realpath: the commands print the ref as written, and macOS temp paths are links.
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="git-overlap-wt-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.mkdir(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        write(os.path.join(self.repo, "f.txt"), "one\ntwo\nthree\n")
        write(os.path.join(self.repo, "g.txt"), "x\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        self.wt = {}
        for name in "abcd":
            self.wt[name] = os.path.join(self.tmp, name)
            git(self.repo, "worktree", "add", "-q", self.wt[name], "-b", "agent-" + name)
        write(os.path.join(self.wt["a"], "f.txt"), "one\nTWO-a\nthree\n")
        write(os.path.join(self.wt["b"], "f.txt"), "one\nTWO-b\nthree\n")
        write(os.path.join(self.wt["c"], "new.txt"), "new\n")
        self.paths = [self.repo] + [self.wt[n] for n in "abcd"]

    def ref(self, name):
        """The worktree: ref of worktree `name`, as bytes."""
        return b"worktree:" + os.fsencode(self.wt[name])

    def refs(self, names="abcd"):
        """Input records for the given worktrees."""
        return b"".join(self.ref(n) + b"\n" for n in names)

    def test_footprint_lists_uncommitted_and_untracked_changes(self):
        """Tracked edits and a new untracked file are listed; a clean worktree lists nothing."""
        # d is untouched, so it has no line.
        code, out, err = tool("footprint", self.repo, ["main"], self.refs())
        self.assertEqual((code, err), (0, b""))
        self.assertEqual(out, self.ref("a") + b"\tM\tf.txt\n" + self.ref("b") + b"\tM\tf.txt\n"
                         + self.ref("c") + b"\tA\tnew.txt\n")

    @needs_pairmerge_git
    def test_pipeline_reports_the_conflict_between_uncommitted_edits(self):
        """footprint | overlap | pairmerge reports the two worktrees that change one line."""
        # Each command's output is the next one's input, as in the documented pipeline.
        _, footprint, _ = tool("footprint", self.repo, ["main"], self.refs())
        code, pairs, _ = tool("overlap", self.repo, [], footprint)
        self.assertEqual((code, pairs), (1, self.ref("a") + b"\t" + self.ref("b") + b"\tf.txt\n"))
        code, out, err = tool("pairmerge", self.repo, [], pairs)
        self.assertEqual((code, err), (1, b""))
        self.assertEqual(out, self.ref("a") + b"\t" + self.ref("b") + b"\tconflict\tf.txt\n")

    def test_branches_alone_show_nothing_before_a_commit(self):
        """The same worktrees named by branch have no footprint: the edits are not committed."""
        # This is the gap worktree refs close.
        code, out, _ = tool("footprint", self.repo, ["main"], b"agent-a\nagent-b\nagent-c\n")
        self.assertEqual((code, out), (0, b""))

    @needs_pairmerge_git
    def test_a_worktree_ref_and_a_branch_can_be_paired(self):
        """An uncommitted edit conflicts with a committed one on another branch."""
        # b commits its edit; a's stays uncommitted.
        git(self.wt["b"], "commit", "-qam", "b")
        pair = self.ref("a") + b"\tagent-b\n"
        code, out, _ = tool("pairmerge", self.repo, [], pair)
        self.assertEqual((code, out), (1, self.ref("a") + b"\tagent-b\tconflict\tf.txt\n"))

    @needs_pairmerge_git
    def test_edits_to_different_files_merge_cleanly(self):
        """Uncommitted edits that do not touch the same lines are reported clean."""
        # a edits f.txt, c adds new.txt.
        pair = self.ref("a") + b"\t" + self.ref("c") + b"\n"
        code, out, _ = tool("pairmerge", self.repo, [], pair)
        self.assertEqual((code, out), (0, self.ref("a") + b"\t" + self.ref("c") + b"\tclean\t\n"))

    def test_ignored_files_are_left_out(self):
        """A file matched by .gitignore is not part of the snapshot."""
        # The .gitignore itself is new and untracked, so it is listed.
        write(os.path.join(self.wt["d"], ".gitignore"), "*.log\n")
        write(os.path.join(self.wt["d"], "build.log"), "noise\n")
        code, out, _ = tool("footprint", self.repo, ["main"], self.refs("d"))
        self.assertEqual((code, out), (0, self.ref("d") + b"\tA\t.gitignore\n"))

    def test_nothing_in_the_repository_changes(self):
        """Objects, refs, indexes, statuses and files are the same after the commands ran."""
        # Every command that takes worktree refs is run, with and without --onto.
        before = state(self.repo, self.paths)
        _, footprint, _ = tool("footprint", self.repo, ["main"], self.refs())
        _, pairs, _ = tool("overlap", self.repo, [], footprint)
        # git pairmerge refuses git older than 2.43; footprint is still checked there.
        if PAIRMERGE_OK:
            tool("pairmerge", self.repo, [], pairs)
            tool("pairmerge", self.repo, ["--onto", "main"], pairs)
        self.assertEqual(state(self.repo, self.paths), before)

    @needs_pairmerge_git
    def test_onto_works_with_worktree_refs(self):
        """With --onto, both uncommitted edits land on main and the second conflicts."""
        # a lands first, then b no longer applies.
        pair = self.ref("a") + b"\t" + self.ref("b") + b"\n"
        code, out, err = tool("pairmerge", self.repo, ["--onto", "main"], pair)
        self.assertEqual((code, err), (1, b""))
        self.assertEqual(out, self.ref("a") + b"\t" + self.ref("b") + b"\tconflict\tf.txt\n")

    def test_commands_run_from_inside_a_worktree(self):
        """The commands give the same footprint when run in a linked worktree."""
        # Paths in the output are relative to the repository root either way.
        _, here, _ = tool("footprint", self.repo, ["main"], self.refs())
        code, there, err = tool("footprint", self.wt["d"], ["main"], self.refs())
        self.assertEqual((code, err, there), (0, b"", here))

    def test_path_that_is_not_a_worktree_of_this_repository(self):
        """A missing path, a plain directory, a subdirectory and another repository are errors."""
        # Each bad path is tried through both commands that take worktree refs.
        other = os.path.join(self.tmp, "other")
        os.mkdir(other)
        git(other, "init", "-q", "-b", "main")
        os.mkdir(os.path.join(self.wt["a"], "sub"))
        for path in (os.path.join(self.tmp, "missing"), self.tmp, other,
                     os.path.join(self.wt["a"], "sub")):
            record = b"worktree:" + os.fsencode(path) + b"\n"
            commands = [("footprint", ["main"], record)]
            # git pairmerge refuses git older than 2.43 before it reads any record.
            if PAIRMERGE_OK:
                commands.append(("pairmerge", [], record[:-1] + b"\tmain\n"))
            for name, args, data in commands:
                code, out, err = tool(name, self.repo, args, data)
                self.assertEqual((code, out), (2, b""), path)
                self.assertIn(b"record 1: not the top directory of a worktree of this repository: "
                              b"worktree:", err)
                self.assertEqual(err.count(b"\n"), 1)

    def test_empty_path_is_an_unknown_ref(self):
        """`worktree:` with no path is not a worktree ref and resolves like any other name."""
        # The prefix alone names nothing.
        code, out, err = tool("footprint", self.repo, ["main"], b"worktree:\n")
        self.assertEqual((code, out), (2, b""))
        self.assertIn(b"unknown ref or not a commit: worktree:", err)

    def test_redirecting_environment_is_refused(self):
        """With GIT_INDEX_FILE set, git would not read the named worktree, so it is an error."""
        # The variable would make git read another index than the worktree's.
        code, out, err = tool("footprint", self.repo, ["main"], self.refs("a"),
                              {"GIT_INDEX_FILE": os.path.join(self.tmp, "elsewhere")})
        self.assertEqual((code, out), (2, b""))
        self.assertIn(b"a worktree: ref cannot be used while GIT_INDEX_FILE is set", err)


if __name__ == "__main__":
    unittest.main()
