"""Clean-room acceptance tests for `git overlap [--with REF]`, written from SPEC.md only.

Each docstring names the SPEC.md clause it checks. The command is run as a black box
(`python -m git_overlap.overlap`); by default from a directory that is NOT a git repository,
because SPEC says overlap needs no repository. Input records are `ref TAB status TAB path`.
"""
import re
import unittest

from fixtures import Repo, Sandbox, assert_error, closed_stdout_run, gherkin_repo

NAME = "overlap"


def recno(n):
    """Regex matching record number N as a whole number in the error line."""
    return re.compile(rb"(?<![0-9])" + str(n).encode() + rb"(?![0-9])")


class OverlapTestCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.cwd = self.sb.mkdir("no-repo-here")

    def ov(self, args, stdin, env_extra=None):
        return self.sb.run_cmd(NAME, args, stdin=stdin, cwd=self.cwd, env_extra=env_extra)

    def assertFound(self, proc, expected):
        self.assertEqual(proc.stdout, expected, "stderr=%r" % (proc.stderr,))
        self.assertEqual(proc.returncode, 1, "exit 1 when any record is written; stderr=%r"
                         % (proc.stderr,))

    def assertNothing(self, proc):
        self.assertEqual(proc.stdout, b"", "stderr=%r" % (proc.stderr,))
        self.assertEqual(proc.returncode, 0, "exit 0 when nothing is written; stderr=%r"
                         % (proc.stderr,))


class TestPairs(OverlapTestCase):
    def test_gherkin_case(self):
        """SPEC overlap: Given a and b both touch x.py and c touches only w.py, Then stdout is exactly
        `a TAB b TAB x.py` and exit is 1."""
        proc = self.ov([], b"a\tM\tx.py\nb\tM\tx.py\nc\tA\tw.py\n")
        self.assertFound(proc, b"a\tb\tx.py\n")
        self.assertEqual(proc.stderr, b"")

    def test_status_is_ignored(self):
        """SPEC overlap Input: the status field may hold any value (even empty) and is ignored."""
        proc = self.ov([], b"a\tWHATEVER\tx.py\nb\t\tx.py\nc\tM\tw.py\n")
        self.assertFound(proc, b"a\tb\tx.py\n")

    def test_pair_order_bytewise_and_output_sorted(self):
        """SPEC overlap Output: refA < refB bytewise; records sorted by (refA, refB, path) bytewise,
        regardless of input order and locale."""
        stdin = (b"a\tA\t\xc3\xa9.py\n" b"a\tM\tX.py\n" b"_\tD\ta.py\n" b"B\tM\ta.py\n"
                 b"a\tT\ta.py\n" b"B\tA\tX.py\n" b"_\tM\t\xc3\xa9.py\n")
        expected = (b"B\t_\ta.py\n" b"B\ta\tX.py\n" b"B\ta\ta.py\n"
                    b"_\ta\ta.py\n" b"_\ta\t\xc3\xa9.py\n")
        proc = self.ov([], stdin, env_extra={"LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"})
        self.assertFound(proc, expected)

    def test_three_refs_share_one_path(self):
        """SPEC overlap Output: one record per shared path for every pair of distinct refs."""
        proc = self.ov([], b"c\tM\tx\nb\tM\tx\na\tM\tx\n")
        self.assertFound(proc, b"a\tb\tx\na\tc\tx\nb\tc\tx\n")

    def test_never_self_pair(self):
        """SPEC overlap Output: a ref never pairs with itself."""
        proc = self.ov([], b"a\tM\tx.py\na\tD\tx.py\na\tA\tx.py\nb\tM\ty.py\n")
        self.assertNothing(proc)

    def test_duplicates_written_once(self):
        """SPEC overlap Output: duplicates are written once (same pair and path seen many times)."""
        proc = self.ov([], b"a\tM\tx\nb\tM\tx\na\tA\tx\nb\tM\tx\nb\tD\tx\n")
        self.assertFound(proc, b"a\tb\tx\n")

    def test_nothing_shared(self):
        """SPEC overlap Exit: nothing shared gives empty stdout and exit 0; empty input likewise."""
        self.assertNothing(self.ov([], b"a\tM\tx\nb\tM\ty\n"))
        self.assertNothing(self.ov([], b""))

    def test_ref_starting_with_dash(self):
        """SPEC common Refs: a ref starting with `-` is a ref, printed as written."""
        proc = self.ov([], b"-x\tM\tf\nb\tM\tf\n")
        self.assertFound(proc, b"-x\tb\tf\n")

    def test_works_outside_any_repository(self):
        """SPEC overlap: needs no repository (cwd is an empty dir, discovery stopped by
        GIT_CEILING_DIRECTORIES, GIT_DIR pointing nowhere) and still finds pairs."""
        proc = self.sb.run_cmd(NAME, [], stdin=b"a\tM\tx\nb\tM\tx\n", cwd=self.cwd,
                               env_extra={"GIT_DIR": self.cwd + "/nonexistent-git-dir"})
        self.assertFound(proc, b"a\tb\tx\n")


class TestWith(OverlapTestCase):
    STDIN = b"c\tM\tx\nb\tM\tx\na\tM\tx\nb\tM\ty\nc\tM\ty\n"

    def test_with_ref_as_refA_and_refB(self):
        """SPEC overlap --with REF: only pairs that include REF; REF prints as refA or refB per the
        ordering rule (here both)."""
        proc = self.ov(["--with", "b"], self.STDIN)
        self.assertFound(proc, b"a\tb\tx\nb\tc\tx\nb\tc\ty\n")

    def test_with_ref_only_as_refA(self):
        """SPEC overlap --with REF: REF smallest, so always refA."""
        proc = self.ov(["--with", "a"], self.STDIN)
        self.assertFound(proc, b"a\tb\tx\na\tc\tx\n")

    def test_with_ref_only_as_refB(self):
        """SPEC overlap --with REF: REF largest, so always refB."""
        proc = self.ov(["--with", "c"], self.STDIN)
        self.assertFound(proc, b"a\tc\tx\nb\tc\tx\nb\tc\ty\n")

    def test_with_absent_ref_finds_nothing(self):
        """SPEC overlap --with / Exit: no pair includes REF, so nothing written and exit 0."""
        self.assertNothing(self.ov(["--with", "zz"], self.STDIN))

    def test_with_dash_ref(self):
        """SPEC common Refs + Options: a REF starting with `-` after --with is a ref, not an option."""
        stdin = b"-x\tM\tf\nb\tM\tf\nc\tM\tg\nb\tM\tg\n"
        self.assertFound(self.ov(["--with", "-x"], stdin), b"-x\tb\tf\n")
        self.assertFound(self.ov(["--with=-x"], stdin), b"-x\tb\tf\n")


class TestRecordsAndModes(OverlapTestCase):
    def test_two_field_input_is_error(self):
        """SPEC overlap Input: there is no 2-field form; fewer than 3 fields is an error naming the
        record number (2); exit 2."""
        proc = self.ov([], b"a\tM\tx.py\nb\tx.py\n")
        assert_error(self, proc, NAME, must_contain=recno(2))

    def test_single_field_is_error(self):
        """SPEC common Malformed input: a one-field record is an error naming record 3; exit 2."""
        proc = self.ov([], b"a\tM\tx\nb\tM\tx\nlonely\n")
        assert_error(self, proc, NAME, must_contain=recno(3))

    def test_record_number_counts_skipped_empty_records(self):
        """SPEC common Malformed input: record numbers count every record from 1, skipped empty
        ones included (LF: record 4; -z: record 3)."""
        proc = self.ov([], b"\n\na\tM\tx\nb\tx\n")
        assert_error(self, proc, NAME, must_contain=recno(4))
        proc = self.ov(["-z"], b"\0a\tM\tx\0b\tx\0")
        assert_error(self, proc, NAME, must_contain=recno(3))

    def test_empty_ref_is_error(self):
        """SPEC common/overlap Malformed input: an empty ref field is an error (record 2), exit 2."""
        proc = self.ov([], b"a\tM\tx\n\tM\tx\n")
        assert_error(self, proc, NAME, must_contain=recno(2))

    def test_empty_path_is_error(self):
        """SPEC common/overlap Malformed input: an empty path field is an error (record 1), exit 2."""
        proc = self.ov([], b"a\tM\t\nb\tM\t\n")
        assert_error(self, proc, NAME, must_contain=recno(1))

    def test_too_few_fields_z_mode(self):
        """SPEC common Malformed input applies in -z mode as well (record 2)."""
        proc = self.ov(["-z"], b"a\tM\tx\0b\tx\0")
        assert_error(self, proc, NAME, must_contain=recno(2))

    def test_path_is_everything_after_second_tab_z(self):
        """SPEC overlap Input: the path is everything after the second TAB, so in -z mode a path may
        itself contain TAB (and is written raw)."""
        proc = self.ov(["-z"], b"a\tM\tp\tq.py\0b\tA\tp\tq.py\0c\tM\tq.py\0")
        self.assertFound(proc, b"a\tb\tp\tq.py\0")

    def test_path_with_tab_in_lf_mode_is_unsafe(self):
        """SPEC overlap Input + common Unsafe names: in LF mode a shared path holding a TAB (after
        the second TAB) cannot be written: error naming -z, exit 2."""
        proc = self.ov([], b"a\tM\tp\tq.py\nb\tM\tp\tq.py\n")
        assert_error(self, proc, NAME, must_contain=b"-z")

    def test_crlf_input_accepted(self):
        """SPEC common Input tolerance: LF mode removes one trailing CR per record."""
        proc = self.ov([], b"a\tM\tx.py\r\nb\tA\tx.py\r\n")
        self.assertFound(proc, b"a\tb\tx.py\n")

    def test_empty_records_skipped(self):
        """SPEC common Input tolerance: empty records are skipped (LF incl. CR-only, and -z)."""
        self.assertFound(self.ov([], b"\n\na\tM\tx\n\n\r\nb\tM\tx\n\n"), b"a\tb\tx\n")
        self.assertFound(self.ov(["-z"], b"\0a\tM\tx\0\0b\tM\tx\0\0"), b"a\tb\tx\0")

    def test_z_mode_records(self):
        """SPEC common Records: with -z, input and output records end with NUL; fields still TAB."""
        proc = self.ov(["-z"], b"a\tM\tx.py\0b\tA\tx.py\0c\tM\tw.py\0")
        self.assertFound(proc, b"a\tb\tx.py\0")

    def test_z_mode_path_with_lf(self):
        """SPEC common Unsafe names: in -z mode a path may contain LF; written raw."""
        proc = self.ov(["-z"], b"a\tA\tl\nf.py\0b\tM\tl\nf.py\0")
        self.assertFound(proc, b"a\tb\tl\nf.py\0")

    def test_z_mode_does_not_strip_cr(self):
        """SPEC common Input tolerance: in -z mode nothing is removed, so `x<CR>` != `x`."""
        self.assertNothing(self.ov(["-z"], b"a\tM\tx\r\0b\tM\tx\0"))

    def test_lf_mode_unsafe_path_is_error_naming_z(self):
        """SPEC common Unsafe names: in LF mode a shared path containing CR cannot be written; error
        naming `-z`, exit 2, never quoted or escaped."""
        proc = self.ov([], b"a\tM\tx\ry.py\nb\tM\tx\ry.py\n")
        assert_error(self, proc, NAME, must_contain=b"-z")
        proc = self.ov([], b"a\tM\tx.py\r\r\nb\tM\tx.py\r\r\n")
        assert_error(self, proc, NAME, must_contain=b"-z")

    def test_lf_mode_unsafe_ref_is_error_naming_z(self):
        """SPEC common Unsafe names: in LF mode a ref containing CR cannot be written; exit 2."""
        proc = self.ov([], b"a\rb\tM\tx\nc\tM\tx\n")
        assert_error(self, proc, NAME, must_contain=b"-z")

    def test_bytes_round_trip(self):
        """SPEC common Records: bytes end to end, non-UTF-8 bytes unchanged, nothing quoted."""
        proc = self.ov([], b"r\xff\tM\tcaf\xe9 \"q\\.py\nb\tA\tcaf\xe9 \"q\\.py\n")
        self.assertFound(proc, b"b\tr\xff\tcaf\xe9 \"q\\.py\n")


class TestCliContract(OverlapTestCase):
    def test_dash_h_exits_0(self):
        """SPEC common: -h prints usage and exits 0."""
        proc = self.ov(["-h"], b"")
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(proc.stdout.strip())
        self.assertNotIn(b"Traceback", proc.stderr)

    def test_version(self):
        """SPEC common: --version prints `git-overlap <version>`."""
        proc = self.ov(["--version"], b"")
        self.assertEqual(proc.returncode, 0)
        self.assertRegex(proc.stdout, re.compile(rb"\Agit-overlap \S+\n\Z"))

    def test_unexpected_positional_is_error(self):
        """SPEC common Options: an undocumented argument (even `--no-such-option`) is a positional;
        overlap takes none, so exit 2 with one `git-overlap:` line."""
        assert_error(self, self.ov(["extra"], b"a\tM\tx\n"), NAME)
        assert_error(self, self.ov(["--no-such-option"], b"a\tM\tx\n"), NAME)

    def test_double_dash_accepted(self):
        """SPEC common Options: `--` is accepted."""
        proc = self.ov(["--with", "a", "--"], b"a\tM\tx\nb\tM\tx\n")
        self.assertFound(proc, b"a\tb\tx\n")

    def test_closed_stdout_exits_0_quietly(self):
        """SPEC common: a closed stdout ends the command quietly with exit 0, not exit 1, not a
        traceback."""
        stdin = b"".join(b"%s\tM\tdir/file-%05d.py\n" % (ref, i)
                         for ref in (b"a", b"b") for i in range(20000))
        closed_stdout_run(self, self.sb, NAME, [], stdin, self.cwd)

    def test_deterministic(self):
        """SPEC common Determinism: byte-identical output across runs, locales and time zones."""
        stdin = (b"z\tM\tB\nA\tM\tB\na\tM\tB\nz\tM\t\xc3\xa9\na\tM\t\xc3\xa9\n"
                 b"A\tM\ta\nz\tM\ta\n")
        runs = [self.ov([], stdin, env_extra=e) for e in (
            {"LC_ALL": "C", "TZ": "UTC"}, {"LC_ALL": "C", "TZ": "UTC"},
            {"LC_ALL": "en_US.UTF-8", "LANG": "sv_SE.UTF-8", "TZ": "Pacific/Auckland"})]
        self.assertEqual(runs[0].returncode, 1)
        self.assertTrue(runs[0].stdout)
        self.assertEqual(runs[0].stderr, b"")
        for other in runs[1:]:
            self.assertEqual(runs[0].stdout, other.stdout)
            self.assertEqual(runs[0].returncode, other.returncode)

    def test_never_changes_repository(self):
        """SPEC common: never changes a ref, the index, the worktree or the object store (run inside
        a repository)."""
        repo, _ = gherkin_repo(self.sb)
        repo.git("reset", "-q", "--hard", "main")
        repo.git("update-index", "-q", "--refresh", check=False)
        before = repo.state()
        proc = repo.run(NAME, ["--with", "a"], stdin=b"a\tM\tx.py\nb\tM\tx.py\n")
        self.assertFound(proc, b"a\tb\tx.py\n")
        after = repo.state()
        for key in sorted(before):
            self.assertEqual(before[key], after[key], "%s changed" % key)

    def test_pipes_from_footprint(self):
        """SPEC overlap Input: footprint records (footprint output) pipe straight in, -z too."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"x.py": "x\n", "w.py": "w\n"}, ref="refs/heads/main")
        repo.commit(base, {"x.py": "a\n", b"t\tab": "t\n"}, ref="refs/heads/a")
        repo.commit(base, {"x.py": None, b"t\tab": "u\n"}, ref="refs/heads/b")
        repo.commit(base, {"w.py": "c\n"}, ref="refs/heads/c")
        fp = repo.run("footprint", ["-z", "main"], stdin=b"b\0c\0a\0")
        self.assertEqual(fp.returncode, 0, fp.stderr)
        proc = repo.run(NAME, ["-z"], stdin=fp.stdout)
        self.assertFound(proc, b"a\tb\tt\tab\0a\tb\tx.py\0")


if __name__ == "__main__":
    unittest.main()
