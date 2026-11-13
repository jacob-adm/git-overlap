"""Clean-room acceptance tests for `git footprint BASE`, written from SPEC.md only.

Each docstring names the SPEC.md clause it checks. Real git repositories, no mocks;
the command is run as a black box (`python -m git_overlap.footprint`).
"""
import os
import re
import unittest

from fixtures import (Repo, Sandbox, assert_error, closed_stdout_run, gherkin_repo)

NAME = "footprint"


class FootprintTestCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def fp(self, repo, args, stdin):
        return repo.run(NAME, args, stdin=stdin)


class TestGherkinAndOutput(FootprintTestCase):
    def test_gherkin_case(self):
        """SPEC footprint: Given a edits x.py and b adds y.py and deletes z.py, When footprint main
        reads `a` and `b`, Then stdout is exactly a M x.py / b A y.py / b D z.py and exit is 0
        (a footprint is not a finding)."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, ["main"], b"a\nb\n")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\nb\tA\ty.py\nb\tD\tz.py\n")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, b"")

    def test_merge_base_semantics(self):
        """SPEC footprint: compared with the merge base of BASE and the ref (PR "files changed"):
        changes made on BASE after the branch point are NOT in the ref's footprint."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"x.py": "x\n", "w.py": "w\n"}, ref="refs/heads/main")
        repo.commit(base, {"x.py": "x2\n"}, ref="refs/heads/a")
        repo.commit(base, {"w.py": "w moved on\n", "only-main.py": "m\n"}, ref="refs/heads/main")
        proc = self.fp(repo, ["main"], b"a\n")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\n")
        self.assertEqual(proc.returncode, 0)

    def test_rename_is_delete_plus_add(self):
        """SPEC footprint: renames are not detected; a rename is a D of the old path and an A of the
        new one (even with diff.renames=true in the repository config); sorted by path."""
        repo = Repo(self.sb)
        repo.git("config", "diff.renames", "true")
        base = repo.commit(None, {"old.py": "same content\n" * 20}, ref="refs/heads/main")
        repo.commit(base, {"old.py": None, "new.py": "same content\n" * 20}, ref="refs/heads/r")
        proc = self.fp(repo, ["main"], b"r\n")
        self.assertEqual(proc.stdout, b"r\tA\tnew.py\nr\tD\told.py\n")
        self.assertEqual(proc.returncode, 0)

    def test_type_change_is_T(self):
        """SPEC footprint: status is git's letter A, M, D or T; a file replaced by a symlink is T.
        (Built with plumbing, so no filesystem symlink support is needed.)"""
        repo = Repo(self.sb)
        base = repo.commit(None, {"x.py": "x\n"}, ref="refs/heads/main")
        repo.commit(base, {"x.py": ("link", "target.py")}, ref="refs/heads/t")
        proc = self.fp(repo, ["main"], b"t\n")
        self.assertEqual(proc.stdout, b"t\tT\tx.py\n")
        self.assertEqual(proc.returncode, 0)

    def test_records_sorted_bytewise_by_path_refs_keep_input_order(self):
        """SPEC footprint: within one ref records are sorted by path (bytewise, locale-independent);
        refs keep input order."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"a.py": "a\n", "keep": "k\n"}, ref="refs/heads/main")
        repo.commit(base, {"a.py": None, "B.py": "b\n", "_m.py": "m\n",
                           "é.py": "e\n", "d/x": "dx\n", "d.py": "d\n"},
                    ref="refs/heads/z")
        repo.commit(base, {"keep": "k2\n"}, ref="refs/heads/A")
        proc = repo.run(NAME, ["main"], stdin=b"z\nA\n",
                        env_extra={"LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"})
        expected = (b"z\tA\tB.py\n" b"z\tA\t_m.py\n" b"z\tD\ta.py\n" b"z\tA\td.py\n"
                    b"z\tA\td/x\n" b"z\tA\t\xc3\xa9.py\n" b"A\tM\tkeep\n")
        self.assertEqual(proc.stdout, expected)
        self.assertEqual(proc.returncode, 0)

    def test_paths_written_raw_not_quoted(self):
        """SPEC common: data is bytes end to end, never quoted or escaped (no core.quotePath
        octal escapes, no C-style quoting of `"` or backslash) in LF mode."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"k": "k\n"}, ref="refs/heads/main")
        repo.commit(base, {b"q\"uo\\te.py": "q\n", "ü.py": "u\n"}, ref="refs/heads/q")
        proc = self.fp(repo, ["main"], b"q\n")
        self.assertEqual(proc.stdout, b"q\tA\tq\"uo\\te.py\nq\tA\t\xc3\xbc.py\n")
        self.assertEqual(proc.returncode, 0)

    def test_non_utf8_path_round_trips(self):
        """SPEC common: no decoding, no re-encoding: a non-UTF-8 path byte sequence is written
        unchanged, in LF mode and in -z mode."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"k": "k\n"}, ref="refs/heads/main")
        repo.commit(base, {b"caf\xe9.py": b"latin1\n"}, ref="refs/heads/r")
        proc = self.fp(repo, ["main"], b"r\n")
        self.assertEqual(proc.stdout, b"r\tA\tcaf\xe9.py\n")
        self.assertEqual(proc.returncode, 0)
        proc = self.fp(repo, ["-z", "main"], b"r\0")
        self.assertEqual(proc.stdout, b"r\tA\tcaf\xe9.py\0")
        self.assertEqual(proc.returncode, 0)

    def test_git_runs_without_shell(self):
        """SPEC common: git runs without a shell: shell metacharacters in a ref are inert and the
        ref is printed exactly as written."""
        repo, base = gherkin_repo(self.sb)
        name = "a;touch${IFS}PWNED`touch${IFS}PWNED2`|x"
        repo.commit(base, {"x.py": "x3\n"}, ref="refs/heads/" + name)
        proc = self.fp(repo, ["main"], name.encode() + b"\n")
        self.assertEqual(proc.stdout, name.encode() + b"\tM\tx.py\n")
        self.assertEqual(proc.returncode, 0)
        for leftover in ("PWNED", "PWNED2"):
            self.assertFalse(os.path.exists(os.path.join(repo.path, leftover)))


class TestRefHandling(FootprintTestCase):
    def test_repeated_ref_processed_once_at_first_occurrence(self):
        """SPEC footprint: a repeated ref is processed once, at its first occurrence."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, ["main"], b"b\na\nb\na\n")
        self.assertEqual(proc.stdout, b"b\tA\ty.py\nb\tD\tz.py\na\tM\tx.py\n")
        self.assertEqual(proc.returncode, 0)

    def test_ref_without_changes_writes_nothing(self):
        """SPEC footprint: a ref with no changes writes nothing (BASE itself, and an ancestor of
        BASE whose merge base is itself); exit stays 0."""
        repo, base = gherkin_repo(self.sb)
        proc = self.fp(repo, ["main"], b"main\n" + base.encode() + b"\na\n")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\n")
        self.assertEqual(proc.returncode, 0)
        proc = self.fp(repo, ["a"], b"main\n")
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.returncode, 0)

    def test_ref_printed_exactly_as_written(self):
        """SPEC common Refs: any commit-ish (HEAD~0, a full SHA, an annotated tag) is accepted and
        printed exactly as the caller wrote it."""
        repo, _ = gherkin_repo(self.sb)
        repo.git("symbolic-ref", "HEAD", "refs/heads/a")
        sha_b = repo.rev("b")
        repo.git("tag", "-a", "-m", "annotated", "tb", "b")
        stdin = b"HEAD~0\n" + sha_b.encode() + b"\ntb\nrefs/heads/a\n"
        proc = self.fp(repo, ["main"], stdin)
        expected = (b"HEAD~0\tM\tx.py\n"
                    + sha_b.encode() + b"\tA\ty.py\n" + sha_b.encode() + b"\tD\tz.py\n"
                    + b"tb\tA\ty.py\ntb\tD\tz.py\n"
                    + b"refs/heads/a\tM\tx.py\n")
        self.assertEqual(proc.stdout, expected)
        self.assertEqual(proc.returncode, 0)

    def test_ref_starting_with_dash_is_a_ref(self):
        """SPEC common Refs: a ref starting with `-` is treated as a ref, never as an option."""
        repo, base = gherkin_repo(self.sb)
        repo.git("update-ref", "refs/heads/-x", repo.rev("a"))
        proc = self.fp(repo, ["main"], b"-x\n")
        self.assertEqual(proc.stdout, b"-x\tM\tx.py\n")
        self.assertEqual(proc.returncode, 0)

    def test_unknown_ref_is_error(self):
        """SPEC footprint: exit 2 on error: unknown ref (one-line `git-footprint:` message)."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, ["main"], b"no-such-branch\n")
        assert_error(self, proc, NAME)

    def test_unknown_base_is_error(self):
        """SPEC footprint: exit 2 on error: unknown BASE."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, ["no-such-base"], b"a\n")
        assert_error(self, proc, NAME)

    def test_tree_ref_is_error(self):
        """SPEC footprint: exit 2 on error: a ref that is not a commit (a tree SHA)."""
        repo, _ = gherkin_repo(self.sb)
        tree = repo.rev("a^{tree}")
        proc = self.fp(repo, ["main"], tree.encode() + b"\n")
        assert_error(self, proc, NAME)

    def test_blob_ref_is_error(self):
        """SPEC footprint: exit 2 on error: a ref that is not a commit (a blob SHA)."""
        repo, _ = gherkin_repo(self.sb)
        blob = repo.rev("a:x.py")
        proc = self.fp(repo, ["main"], blob.encode() + b"\n")
        assert_error(self, proc, NAME)

    def test_non_commit_base_is_error(self):
        """SPEC footprint: exit 2 on error: BASE naming a blob is not a commit (no merge base)."""
        repo, _ = gherkin_repo(self.sb)
        blob = repo.rev("main:x.py")
        proc = self.fp(repo, [blob], b"a\n")
        assert_error(self, proc, NAME)

    def test_no_merge_base_is_error(self):
        """SPEC footprint: exit 2 on error: no merge base (an orphan branch)."""
        repo, _ = gherkin_repo(self.sb)
        repo.commit(None, {"o.py": "o\n"}, ref="refs/heads/orphan")
        proc = self.fp(repo, ["main"], b"orphan\n")
        assert_error(self, proc, NAME)

    def test_outside_repository_is_error(self):
        """SPEC common: runs in the repository of the current directory; with no repository the
        BASE cannot be resolved: exit 2, one-line message, no traceback."""
        empty = self.sb.mkdir("not-a-repo")
        proc = self.sb.run_cmd(NAME, ["main"], stdin=b"a\n", cwd=empty)
        assert_error(self, proc, NAME)


class TestRecordsAndModes(FootprintTestCase):
    def test_path_with_tab_or_lf_or_cr_in_lf_mode_is_error_naming_z(self):
        """SPEC common Unsafe names: in LF mode a path containing TAB, LF or CR cannot be written;
        error naming `-z` on stderr, exit 2, never quoted."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"k": "k\n"}, ref="refs/heads/main")
        repo.commit(base, {b"ta\tb.py": "t\n"}, ref="refs/heads/tab")
        repo.commit(base, {b"l\nf.py": "l\n"}, ref="refs/heads/lf")
        repo.commit(base, {b"c\rr.py": "c\n"}, ref="refs/heads/cr")
        for ref in (b"tab", b"lf", b"cr"):
            proc = self.fp(repo, ["main"], ref + b"\n")
            assert_error(self, proc, NAME, must_contain=b"-z")
            self.assertNotIn(b"\\t", proc.stdout)
            self.assertNotIn(b'"', proc.stdout)

    def test_z_mode_writes_tab_and_lf_paths_as_nul_records(self):
        """SPEC common Records/Unsafe names: with -z records end with NUL on input and output; a path
        may contain TAB or LF and is written raw."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"k": "k\n"}, ref="refs/heads/main")
        repo.commit(base, {b"ta\tb.py": "t\n", b"l\nf.py": "l\n", "k": "k2\n"},
                    ref="refs/heads/w")
        proc = self.fp(repo, ["-z", "main"], b"w\0")
        self.assertEqual(proc.stdout, b"w\tM\tk\0w\tA\tl\nf.py\0w\tA\tta\tb.py\0")
        self.assertEqual(proc.returncode, 0)

    def test_z_mode_input(self):
        """SPEC common Records: with -z, input records end with NUL; Gherkin result as NUL records."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, ["-z", "main"], b"a\0b\0")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\0b\tA\ty.py\0b\tD\tz.py\0")
        self.assertEqual(proc.returncode, 0)

    def test_crlf_input_accepted(self):
        """SPEC common Input tolerance: in LF mode a trailing CR on a record is removed (the ref is
        printed without it)."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, ["main"], b"a\r\nb\r\n")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\nb\tA\ty.py\nb\tD\tz.py\n")
        self.assertEqual(proc.returncode, 0)

    def test_empty_records_skipped_lf(self):
        """SPEC common Input tolerance: empty records are skipped (LF mode, incl. a CR-only line)."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, ["main"], b"\n\na\n\r\n\nb\n\n")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\nb\tA\ty.py\nb\tD\tz.py\n")
        self.assertEqual(proc.returncode, 0)

    def test_empty_records_skipped_z(self):
        """SPEC common Input tolerance: in -z mode empty records are skipped too."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, ["-z", "main"], b"\0a\0\0\0b\0\0")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\0b\tA\ty.py\0b\tD\tz.py\0")
        self.assertEqual(proc.returncode, 0)

    def test_z_mode_does_not_strip_cr(self):
        """SPEC common Input tolerance: in -z mode nothing is removed, so `a<CR>` is a different
        (unknown) ref: exit 2."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, ["-z", "main"], b"a\r\0")
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn(b"Traceback", proc.stderr)
        self.assertTrue(proc.stderr.startswith(b"git-footprint: "))
        self.assertNotEqual(proc.stdout, b"a\tM\tx.py\0")


class TestCliContract(FootprintTestCase):
    def test_dash_h_exits_0(self):
        """SPEC common: -h prints usage and exits 0."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, ["-h"], b"")
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(proc.stdout.strip(), "usage expected on stdout")
        self.assertNotIn(b"Traceback", proc.stderr)

    def test_version(self):
        """SPEC common: --version prints `git-footprint <version>`."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, ["--version"], b"")
        self.assertEqual(proc.returncode, 0)
        self.assertRegex(proc.stdout, re.compile(rb"\Agit-footprint \S+\n\Z"))

    def test_missing_base_is_error(self):
        """SPEC common Errors: a usage error (BASE missing) is an error: exit 2, one stderr line
        `git-footprint: <message>`."""
        repo, _ = gherkin_repo(self.sb)
        proc = self.fp(repo, [], b"a\n")
        assert_error(self, proc, NAME)

    def test_unexpected_positional_is_error(self):
        """SPEC common Options: an undocumented argument (even `--no-such-option`) is a positional;
        a second positional is unexpected: exit 2, one stderr line."""
        repo, _ = gherkin_repo(self.sb)
        assert_error(self, self.fp(repo, ["main", "a"], b"a\n"), NAME)
        assert_error(self, self.fp(repo, ["--no-such-option", "main"], b"a\n"), NAME)

    def test_dash_ref_as_base(self):
        """SPEC common Options: a ref named `-x` works as BASE (any undocumented argument is a
        positional)."""
        repo, base = gherkin_repo(self.sb)
        repo.git("update-ref", "refs/heads/-x", base)
        proc = self.fp(repo, ["-x"], b"a\n")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\n", proc.stderr)
        self.assertEqual(proc.returncode, 0)

    def test_double_dash_accepted(self):
        """SPEC common Options: `--` is accepted (before BASE, also with -z before it)."""
        repo, base = gherkin_repo(self.sb)
        repo.git("update-ref", "refs/heads/-x", base)
        proc = self.fp(repo, ["--", "main"], b"a\n")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\n", proc.stderr)
        self.assertEqual(proc.returncode, 0)
        proc = self.fp(repo, ["-z", "--", "-x"], b"a\0")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\0", proc.stderr)
        self.assertEqual(proc.returncode, 0)

    def test_closed_stdout_exits_0_quietly(self):
        """SPEC common: a closed stdout (piping into `head`) ends the command quietly, exit 0."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"k": "k\n"}, ref="refs/heads/main")
        many = dict(("dir/file-%05d.py" % i, "same\n") for i in range(20000))
        repo.commit(base, many, ref="refs/heads/big")
        closed_stdout_run(self, self.sb, NAME, ["main"], b"big\n", repo.path)

    def test_deterministic_across_runs_locale_and_tz(self):
        """SPEC common Determinism: same state and input give byte-identical output, independent
        of locale and time zone."""
        repo, base = gherkin_repo(self.sb)
        repo.commit(base, {"B.py": "b\n", "é.py": "e\n", "a.py": "a\n"},
                    ref="refs/heads/c")
        stdin = b"c\na\nb\n"
        one = repo.run(NAME, ["main"], stdin, env_extra={"LC_ALL": "C", "TZ": "UTC"})
        two = repo.run(NAME, ["main"], stdin, env_extra={"LC_ALL": "C", "TZ": "UTC"})
        three = repo.run(NAME, ["main"], stdin,
                         env_extra={"LC_ALL": "en_US.UTF-8", "LANG": "de_DE.UTF-8",
                                    "TZ": "Asia/Tokyo"})
        self.assertEqual(one.returncode, 0)
        self.assertTrue(one.stdout)
        self.assertEqual(one.stdout, two.stdout)
        self.assertEqual(one.stdout, three.stdout)
        self.assertEqual(one.returncode, three.returncode)

    def test_never_changes_refs_index_or_worktree(self):
        """SPEC common: never changes a ref, the index, the worktree or the object store (dirty
        state preserved; every file under .git/objects listed)."""
        repo, _ = gherkin_repo(self.sb)
        repo.git("reset", "-q", "--hard", "main")
        with open(os.path.join(repo.path, "x.py"), "wb") as fh:
            fh.write(b"dirty worktree\n")
        with open(os.path.join(repo.path, "staged.py"), "wb") as fh:
            fh.write(b"staged\n")
        repo.git("add", "staged.py")
        with open(os.path.join(repo.path, "untracked.txt"), "wb") as fh:
            fh.write(b"u\n")
        repo.git("update-index", "-q", "--refresh", check=False)
        before = repo.state()
        ok = self.fp(repo, ["main"], b"a\nb\nHEAD\n")
        self.assertEqual(ok.returncode, 0)
        bad = self.fp(repo, ["main"], b"a\nno-such-ref\n")
        self.assertEqual(bad.returncode, 2)
        after = repo.state()
        for key in sorted(before):
            self.assertEqual(before[key], after[key], "%s changed" % key)


if __name__ == "__main__":
    unittest.main()
