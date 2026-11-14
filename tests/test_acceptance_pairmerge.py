"""Clean-room acceptance tests for `git pairmerge`, written from SPEC.md only.

Each docstring names the SPEC.md clause it checks. Real git repositories, no mocks; the
command is run as a black box (`python -m git_overlap.pairmerge`).

Fixture (base_repo): main has c.txt, x.py, y.py.
  a: appends "a" to c.txt         b: appends "b" to c.txt   -> a/b conflict on c.txt only
  d: edits y.py                                              -> a/d and b/d clean
"""
import os
import re
import unittest

from fixtures import (Repo, Sandbox, assert_error, closed_stdout_run, needs_pairmerge_git,
                      PAIRMERGE_OK)

NAME = "pairmerge"


def recno(n):
    return re.compile(rb"(?<![0-9])" + str(n).encode() + rb"(?![0-9])")


@needs_pairmerge_git
class PairmergeTestCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def base_repo(self, gitattributes=None):
        repo = Repo(self.sb)
        files = {"c.txt": "line1\n", "x.py": "x\n", "y.py": "y\n"}
        if gitattributes is not None:
            files[".gitattributes"] = gitattributes
        base = repo.commit(None, files, ref="refs/heads/main")
        repo.commit(base, {"c.txt": "line1\na\n"}, ref="refs/heads/a")
        repo.commit(base, {"c.txt": "line1\nb\n"}, ref="refs/heads/b")
        repo.commit(base, {"y.py": "y changed\n"}, ref="refs/heads/d")
        return repo, base

    def pm(self, repo, args, stdin, env_extra=None):
        return repo.run(NAME, args, stdin=stdin, env_extra=env_extra)

    def write(self, name, data):
        path = os.path.join(self.sb.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def assertResult(self, proc, stdout, code):
        self.assertEqual(proc.stdout, stdout, "stderr=%r" % (proc.stderr,))
        self.assertEqual(proc.returncode, code, "stderr=%r" % (proc.stderr,))


@needs_pairmerge_git
class TestOutcomes(PairmergeTestCase):
    def test_clean_pair(self):
        """SPEC pairmerge Output: clean pair is `refA TAB refB TAB clean TAB` (empty 4th field);
        exit 0 when no pair conflicts."""
        repo, _ = self.base_repo()
        proc = self.pm(repo, [], b"a\td\n")
        self.assertResult(proc, b"a\td\tclean\t\n", 0)
        self.assertEqual(proc.stderr, b"")

    def test_conflicting_pair(self):
        """SPEC pairmerge Output: a conflicting pair writes `refA TAB refB TAB conflict TAB path`;
        exit 1 if any pair conflicts."""
        repo, _ = self.base_repo()
        proc = self.pm(repo, [], b"a\tb\n")
        self.assertResult(proc, b"a\tb\tconflict\tc.txt\n", 1)

    def test_input_order_and_refs_as_given(self):
        """SPEC pairmerge Output: in input order, refs as given (no reordering of the pair, no
        sorting of pairs)."""
        repo, _ = self.base_repo()
        proc = self.pm(repo, [], b"d\ta\nb\ta\n")
        self.assertResult(proc, b"d\ta\tclean\t\nb\ta\tconflict\tc.txt\n", 1)

    def test_conflicted_paths_sorted_bytewise(self):
        """SPEC pairmerge Output: one record per conflicted path, paths sorted bytewise (incl. a
        modify/delete conflict), locale-independent."""
        repo = Repo(self.sb)
        names = ["a.txt", "B.txt", "é.txt", "_u.txt", "keep.txt"]
        base = repo.commit(None, dict((n, "base\n") for n in names), ref="refs/heads/main")
        repo.commit(base, {"a.txt": "p\n", "B.txt": "p\n", "é.txt": "p\n", "_u.txt": "p\n"},
                    ref="refs/heads/p")
        repo.commit(base, {"a.txt": "q\n", "B.txt": "q\n", "é.txt": "q\n", "_u.txt": None},
                    ref="refs/heads/q")
        proc = self.pm(repo, [], b"q\tp\n", env_extra={"LC_ALL": "en_US.UTF-8"})
        self.assertResult(proc, b"q\tp\tconflict\tB.txt\nq\tp\tconflict\t_u.txt\n"
                                b"q\tp\tconflict\ta.txt\nq\tp\tconflict\t\xc3\xa9.txt\n", 1)

    def test_repeated_pair_either_order_once(self):
        """SPEC pairmerge Input: a repeated pair (in either order) is processed once, at its first
        occurrence (printed as first given)."""
        repo, _ = self.base_repo()
        proc = self.pm(repo, [], b"b\ta\na\tb\nb\ta\nd\ta\na\td\n")
        self.assertResult(proc, b"b\ta\tconflict\tc.txt\nd\ta\tclean\t\n", 1)

    def test_extra_fields_ignored(self):
        """SPEC pairmerge Input: further fields after the first two are ignored."""
        repo, _ = self.base_repo()
        proc = self.pm(repo, [], b"a\tb\tc.txt\na\td\tfoo\tbar\n")
        self.assertResult(proc, b"a\tb\tconflict\tc.txt\na\td\tclean\t\n", 1)

    def test_overlap_output_pipes_in(self):
        """SPEC pairmerge Input: `git overlap` output pipes straight in."""
        repo, _ = self.base_repo()
        ov = repo.run("overlap", [], stdin=b"a\tM\tc.txt\nb\tM\tc.txt\nd\tM\ty.py\n")
        self.assertEqual(ov.returncode, 1, ov.stderr)
        proc = self.pm(repo, [], ov.stdout)
        self.assertResult(proc, b"a\tb\tconflict\tc.txt\n", 1)

    def test_refs_printed_as_given(self):
        """SPEC common Refs: any commit-ish (HEAD~0, a full SHA, `-x`) accepted, printed as written."""
        repo, _ = self.base_repo()
        repo.git("symbolic-ref", "HEAD", "refs/heads/a")
        sha_b = repo.rev("b").encode()
        repo.git("update-ref", "refs/heads/-x", repo.rev("d"))
        proc = self.pm(repo, [], b"HEAD~0\t" + sha_b + b"\n-x\tb\n")
        self.assertResult(proc, b"HEAD~0\t" + sha_b + b"\tconflict\tc.txt\n-x\tb\tclean\t\n", 1)

    def test_empty_input(self):
        """SPEC pairmerge Exit: no pair conflicts (none given) -> empty stdout, exit 0."""
        repo, _ = self.base_repo()
        self.assertResult(self.pm(repo, [], b""), b"", 0)


@needs_pairmerge_git
class TestErrors(PairmergeTestCase):
    def test_unknown_ref_exit_2_before_any_merge(self):
        """SPEC pairmerge Exit / Merge mechanism: unknown ref is exit 2, checked before any merge
        runs, so no record for the earlier valid pair is written."""
        repo, _ = self.base_repo()
        proc = self.pm(repo, [], b"a\td\nnope\ta\n")
        assert_error(self, proc, NAME)
        self.assertEqual(proc.stdout, b"")

    def test_non_commit_ref_is_error(self):
        """SPEC Merge mechanism: each ref must resolve as REF^{commit}; a tree SHA is exit 2."""
        repo, _ = self.base_repo()
        tree = repo.rev("a^{tree}").encode()
        assert_error(self, self.pm(repo, [], b"a\t" + tree + b"\n"), NAME)

    def test_unrelated_histories_is_error(self):
        """SPEC Merge mechanism: merge-tree failing (no common history) is exit 2."""
        repo, _ = self.base_repo()
        repo.commit(None, {"o.txt": "o\n"}, ref="refs/heads/orphan")
        assert_error(self, self.pm(repo, [], b"a\torphan\n"), NAME)

    def test_too_few_fields_is_error_with_record_number(self):
        """SPEC common Malformed input: fewer than 2 fields is an error naming the record number
        (counting skipped empty records); exit 2."""
        repo, _ = self.base_repo()
        assert_error(self, self.pm(repo, [], b"a\td\n\nlonely\n"), NAME, must_contain=recno(3))

    def test_empty_ref_is_error(self):
        """SPEC common Malformed input: an empty ref field is an error naming the record; exit 2."""
        repo, _ = self.base_repo()
        assert_error(self, self.pm(repo, [], b"a\t\n"), NAME, must_contain=recno(1))
        assert_error(self, self.pm(repo, [], b"a\td\n\tb\n"), NAME, must_contain=recno(2))

    def test_unreadable_attributes_file_is_error(self):
        """SPEC pairmerge --attributes: an unreadable FILE (missing; a directory) is exit 2."""
        repo, _ = self.base_repo()
        missing = os.path.join(self.sb.tmp, "no-such-attributes")
        assert_error(self, self.pm(repo, ["--attributes", missing], b"a\tb\n"), NAME)
        assert_error(self, self.pm(repo, ["--attributes", self.sb.tmp], b"a\tb\n"), NAME)

    def test_outside_repository_is_error(self):
        """SPEC common: runs in the repository of the current directory; none -> exit 2."""
        empty = self.sb.mkdir("not-a-repo")
        assert_error(self, self.sb.run_cmd(NAME, [], stdin=b"a\tb\n", cwd=empty), NAME)


@needs_pairmerge_git
class TestAttributes(PairmergeTestCase):
    UNION = b"c.txt merge=union\n"

    def test_checked_out_gitattributes_is_not_used(self):
        """SPEC Merge mechanism: the attribute source is always pinned; without --attributes it is
        the empty tree, so a committed AND checked-out `.gitattributes` with merge=union must not
        make a conflicting pair clean."""
        repo, _ = self.base_repo(gitattributes=self.UNION)
        repo.git("reset", "-q", "--hard", "main")
        self.assertTrue(os.path.exists(os.path.join(repo.path, ".gitattributes")))
        self.assertResult(self.pm(repo, [], b"a\tb\n"), b"a\tb\tconflict\tc.txt\n", 1)

    def test_attributes_file_makes_union_pair_clean(self):
        """SPEC pairmerge --attributes FILE: merge as if FILE were the root .gitattributes; with
        `c.txt merge=union` the a/b pair is clean, and no repository file changes."""
        repo, _ = self.base_repo()
        attrs = self.write("attrs", self.UNION)
        before = repo.state()
        self.assertResult(self.pm(repo, ["--attributes", attrs], b"a\tb\n"),
                          b"a\tb\tclean\t\n", 0)
        self.assertEqual(before, repo.state())
        self.assertFalse(os.path.exists(os.path.join(repo.path, ".gitattributes")))

    def test_attributes_file_replaces_committed_ones(self):
        """SPEC Merge mechanism: FILE replaces the repository's own .gitattributes; it is not
        combined with them (committed union rule ignored when FILE lacks it)."""
        repo, _ = self.base_repo(gitattributes=self.UNION)
        repo.git("reset", "-q", "--hard", "main")
        attrs = self.write("attrs", b"*.bin binary\n")
        self.assertResult(self.pm(repo, ["--attributes", attrs], b"a\tb\n"),
                          b"a\tb\tconflict\tc.txt\n", 1)

    def test_info_attributes_still_honoured(self):
        """SPEC Merge mechanism: $GIT_DIR/info/attributes is still honoured."""
        repo, _ = self.base_repo()
        info = os.path.join(repo.path, ".git", "info")
        if not os.path.isdir(info):
            os.makedirs(info)
        with open(os.path.join(info, "attributes"), "wb") as fh:
            fh.write(self.UNION)
        self.assertResult(self.pm(repo, [], b"a\tb\n"), b"a\tb\tclean\t\n", 0)

    def test_config_merge_driver_honoured(self):
        """SPEC Merge mechanism: merge drivers defined in git config are honoured (driver named in
        the --attributes FILE)."""
        repo, _ = self.base_repo()
        repo.git("config", "merge.keepours.name", "keep ours")
        repo.git("config", "merge.keepours.driver", "true")
        attrs = self.write("attrs", b"c.txt merge=keepours\n")
        self.assertResult(self.pm(repo, ["--attributes", attrs], b"a\tb\n"),
                          b"a\tb\tclean\t\n", 0)


@needs_pairmerge_git
class TestRecordsAndModes(PairmergeTestCase):
    def test_z_mode(self):
        """SPEC common Records: with -z input and output records end with NUL."""
        repo, _ = self.base_repo()
        proc = self.pm(repo, ["-z"], b"a\tb\0a\td\0")
        self.assertResult(proc, b"a\tb\tconflict\tc.txt\0a\td\tclean\t\0", 1)

    def test_z_mode_conflict_path_with_tab_and_lf(self):
        """SPEC common Unsafe names: in -z mode conflicted paths with TAB or LF are written raw."""
        repo = Repo(self.sb)
        base = repo.commit(None, {b"t\tab": "o\n", b"l\nf": "o\n"}, ref="refs/heads/main")
        repo.commit(base, {b"t\tab": "p\n", b"l\nf": "p\n"}, ref="refs/heads/p")
        repo.commit(base, {b"t\tab": "q\n", b"l\nf": "q\n"}, ref="refs/heads/q")
        proc = self.pm(repo, ["-z"], b"p\tq\0")
        self.assertResult(proc, b"p\tq\tconflict\tl\nf\0p\tq\tconflict\tt\tab\0", 1)
        proc = self.pm(repo, [], b"p\tq\n")
        assert_error(self, proc, NAME, must_contain=b"-z")

    def test_crlf_and_empty_records(self):
        """SPEC common Input tolerance: LF mode strips a trailing CR and skips empty records."""
        repo, _ = self.base_repo()
        proc = self.pm(repo, [], b"\r\n\na\tb\r\n\n\na\td\r\n")
        self.assertResult(proc, b"a\tb\tconflict\tc.txt\na\td\tclean\t\n", 1)

    def test_never_changes_repository_or_object_store(self):
        """SPEC common + Merge mechanism: nothing is written to the repository: refs, index,
        worktree and every file under .git/objects identical before/after (clean, conflict,
        --attributes and error runs)."""
        repo, _ = self.base_repo(gitattributes=self.ATTRS)
        repo.git("reset", "-q", "--hard", "main")
        with open(os.path.join(repo.path, "x.py"), "wb") as fh:
            fh.write(b"dirty\n")
        repo.git("update-index", "-q", "--refresh", check=False)
        attrs = self.write("attrs", b"c.txt merge=union\n")
        before = repo.state()
        self.assertTrue(before["objects"])
        self.assertEqual(self.pm(repo, [], b"a\tb\na\td\n").returncode, 1)
        self.assertEqual(self.pm(repo, ["--attributes", attrs], b"a\tb\n").returncode, 0)
        self.assertEqual(self.pm(repo, [], b"a\tnope\n").returncode, 2)
        after = repo.state()
        for key in sorted(before):
            self.assertEqual(before[key], after[key], "%s changed" % key)

    ATTRS = "*.bin binary\n"


@needs_pairmerge_git
class TestCliContract(PairmergeTestCase):
    def test_dash_h_exits_0(self):
        """SPEC common: -h prints usage and exits 0."""
        repo, _ = self.base_repo()
        proc = self.pm(repo, ["-h"], b"")
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(proc.stdout.strip())
        self.assertNotIn(b"Traceback", proc.stderr)

    def test_version(self):
        """SPEC common: --version prints `git-pairmerge <version>`."""
        repo, _ = self.base_repo()
        proc = self.pm(repo, ["--version"], b"")
        self.assertEqual(proc.returncode, 0)
        self.assertRegex(proc.stdout, re.compile(rb"\Agit-pairmerge \S+\n\Z"))

    def test_unexpected_positional_is_error(self):
        """SPEC common Options: pairmerge takes no positional (no BASE); exit 2, one line."""
        repo, _ = self.base_repo()
        assert_error(self, self.pm(repo, ["main"], b"a\tb\n"), NAME)
        assert_error(self, self.pm(repo, ["--no-such-option"], b"a\tb\n"), NAME)

    def test_double_dash_accepted(self):
        """SPEC common Options: `--` is accepted."""
        repo, _ = self.base_repo()
        self.assertResult(self.pm(repo, ["-z", "--"], b"a\td\0"), b"a\td\tclean\t\0", 0)

    def test_deterministic(self):
        """SPEC common Determinism: byte-identical output across runs, locales and time zones."""
        repo, _ = self.base_repo()
        stdin = b"a\tb\nd\ta\nb\td\n"
        runs = [self.pm(repo, [], stdin, env_extra=e) for e in (
            {"LC_ALL": "C", "TZ": "UTC"}, {"LC_ALL": "C", "TZ": "UTC"},
            {"LC_ALL": "en_US.UTF-8", "LANG": "fr_FR.UTF-8", "TZ": "America/Denver"})]
        self.assertEqual(runs[0].returncode, 1)
        self.assertTrue(runs[0].stdout)
        self.assertEqual(runs[0].stderr, b"")
        for other in runs[1:]:
            self.assertEqual(runs[0].stdout, other.stdout)
            self.assertEqual(runs[0].returncode, other.returncode)

    def test_closed_stdout_exits_0_quietly(self):
        """SPEC common: a closed stdout ends the command quietly with exit 0 (not 1, no traceback)."""
        repo = Repo(self.sb)
        names = ["dir/file-%05d.txt" % i for i in range(20000)]
        base = repo.commit(None, dict((n, "base\n") for n in names), ref="refs/heads/main")
        repo.commit(base, dict((n, "p\n") for n in names), ref="refs/heads/p")
        repo.commit(base, dict((n, "q\n") for n in names), ref="refs/heads/q")
        closed_stdout_run(self, self.sb, NAME, [], b"p\tq\n", repo.path)


@unittest.skipIf(PAIRMERGE_OK, "only meaningful on git older than 2.43")
class TestVersionFloor(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def test_git_older_than_floor_is_error_naming_2_43(self):
        """SPEC Merge mechanism / Exit: on git older than 2.43 pairmerge reads `git version` and
        exits 2 with a message naming the floor."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"c.txt": "l\n"}, ref="refs/heads/main")
        repo.commit(base, {"c.txt": "l\na\n"}, ref="refs/heads/a")
        repo.commit(base, {"c.txt": "l\nb\n"}, ref="refs/heads/b")
        proc = repo.run(NAME, [], stdin=b"a\tb\n")
        assert_error(self, proc, NAME, must_contain=b"2.43")
        self.assertEqual(proc.stdout, b"")


if __name__ == "__main__":
    unittest.main()
