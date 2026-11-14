# SPDX-License-Identifier: Apache-2.0
"""Clean-room acceptance tests for `git pairmerge --onto BASE`, written from SPEC.md only.

Each test docstring names the clause of SPEC.md "### --onto BASE" (or the common rule) it
checks. Real git repositories built with subprocess git, no mocks; the command runs as a black
box (`python -m git_overlap.pairmerge`).

Fixture (onto_repo), history O -> m -> main (BASE):
  O:    c.txt, f.txt, g.txt, Z.txt, _z.txt, t.txt, m.txt, y.py
  m:    m.txt "m1" (so m, alias main~1, is already merged into BASE)
  main: f.txt, g.txt, Z.txt, _z.txt, t.txt "t1", m.txt "m2" changed
  fresh, forked from main:  a, b (conflict on c.txt), d (y.py only), q (t.txt "t2"),
                            e (m.txt "m3")
  stale, forked from O:     s1 (f.txt, g.txt), s2 (g.txt, Z.txt, _z.txt, and c.txt)
  behind, forked from O:    p (t.txt "t1", the same change main made: not stale)
"""
import re
import unittest

from fixtures import Repo, Sandbox, assert_error, needs_pairmerge_git

NAME = "pairmerge"
# A conflicted name for a directory/file conflict: PATH~<commit id> (SHA-1 or SHA-256).
DF_NAME = re.compile(rb"\Ad~([0-9a-f]{40}|[0-9a-f]{64})\Z")


@needs_pairmerge_git
class OntoTestCase(unittest.TestCase):
    """Base for --onto tests: a fresh sandbox per test and the fixtures above.

    SPEC: "--onto BASE".
    """

    def setUp(self):
        """Create a hermetic sandbox, removed after the test."""
        self.sb = Sandbox()
        # addCleanup, not tearDown, so the sandbox goes even if setUp fails later.
        self.addCleanup(self.sb.cleanup)

    def onto_repo(self):
        """Build the module fixture described in the module docstring.

        Returns:
            The Repo.
        """
        repo = Repo(self.sb)
        names = ["c.txt", "f.txt", "g.txt", "Z.txt", "_z.txt"]
        files = dict((n, n + " base\n") for n in names)
        files.update({"c.txt": "line1\n", "t.txt": "t0\n", "m.txt": "m0\n", "y.py": "y\n"})
        old = repo.commit(None, files, ref="refs/heads/old")
        # m lands on main before main moves on: an ancestor of BASE.
        merged = repo.commit(old, {"m.txt": "m1\n"}, ref="refs/heads/m")
        main = repo.commit(merged, {"f.txt": "f main\n", "g.txt": "g main\n", "Z.txt": "Z main\n",
                                    "_z.txt": "_z main\n", "t.txt": "t1\n", "m.txt": "m2\n"},
                           ref="refs/heads/main")
        # a and b append different lines at the same spot of c.txt: a line merge cannot settle it.
        repo.commit(main, {"c.txt": "line1\na\n"}, ref="refs/heads/a")
        repo.commit(main, {"c.txt": "line1\nb\n"}, ref="refs/heads/b")
        repo.commit(main, {"y.py": "y changed\n"}, ref="refs/heads/d")
        repo.commit(main, {"t.txt": "t2\n"}, ref="refs/heads/q")
        repo.commit(main, {"m.txt": "m3\n"}, ref="refs/heads/e")
        # Stale refs edit, from the old commit, files main has changed since.
        repo.commit(old, {"f.txt": "f s1\n", "g.txt": "g s1\n"}, ref="refs/heads/s1")
        repo.commit(old, {"g.txt": "g s2\n", "Z.txt": "Z s2\n", "_z.txt": "_z s2\n",
                          "c.txt": "line1\ns2\n"}, ref="refs/heads/s2")
        # p makes exactly main's change to t.txt, so it lands cleanly although it is behind.
        repo.commit(old, {"t.txt": "t1\n"}, ref="refs/heads/p")
        return repo

    def df_repo(self):
        """main plus a (adds file d) and b (adds directory d/): a directory/file conflict.

        Returns:
            The Repo.
        """
        repo = Repo(self.sb)
        main = repo.commit(None, {"base.txt": "base\n"}, ref="refs/heads/main")
        repo.commit(main, {"d": "file\n"}, ref="refs/heads/a")
        repo.commit(main, {"d/x": "x\n"}, ref="refs/heads/b")
        return repo

    def pm(self, repo, args, stdin, env_extra=None):
        """Run git pairmerge in REPO as a black box.

        Args:
            repo: The fixture repository; the command runs at its root.
            args: Command-line arguments.
            stdin: Input bytes.
            env_extra: Extra environment variables, or None.

        Returns:
            The subprocess.CompletedProcess.
        """
        return repo.run(NAME, args, stdin=stdin, env_extra=env_extra)

    def assertResult(self, proc, stdout, code):
        """Assert exact stdout bytes and exit status, showing stderr on failure.

        Args:
            proc: The completed process.
            stdout: The expected stdout, byte for byte.
            code: The expected exit status.
        """
        # stdout first: a wrong record explains a wrong exit status better than the reverse.
        self.assertEqual(proc.stdout, stdout, "stderr=%r" % (proc.stderr,))
        self.assertEqual(proc.returncode, code, "stderr=%r" % (proc.stderr,))


@needs_pairmerge_git
class TestBaseOption(OntoTestCase):
    """How BASE is given and resolved.

    SPEC: "--onto BASE" (BASE is resolved once, before stdin is read).
    """

    def test_both_spellings(self):
        """Clause: `--onto BASE` takes its value as the next argument or as `--onto=BASE`.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        # Same input both ways: the stale ref proves --onto is in effect.
        for args in (["--onto", "main"], ["--onto=main"]):
            with self.subTest(args=args):
                proc = self.pm(repo, args, b"s1\td\n")
                self.assertResult(proc, b"s1\td\tstale\tf.txt\ns1\td\tstale\tg.txt\n", 1)

    def test_unknown_base_exits_2_before_stdin(self):
        """Clause: BASE is resolved before stdin is read; an unknown BASE is exit 2.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        # Empty input: nothing to merge, yet the bad BASE is still an error.
        proc = self.pm(repo, ["--onto", "nosuch"], b"")
        assert_error(self, proc, NAME, must_contain=b"unknown ref or not a commit: nosuch")
        self.assertEqual(proc.stdout, b"")
        # A malformed first record: the BASE error comes first, without a record prefix.
        proc = self.pm(repo, ["--onto=nosuch"], b"onlyone\n")
        assert_error(self, proc, NAME, must_contain=b"unknown ref or not a commit: nosuch")
        self.assertNotIn(b"record", proc.stderr)

    def test_base_must_be_a_single_commit(self):
        """Clause: BASE follows "Resolving a ref": a tree or a range is not a commit, exit 2.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        for base in ("main^{tree}", "old..main"):
            with self.subTest(base=base):
                proc = self.pm(repo, ["--onto", base], b"a\td\n")
                assert_error(self, proc, NAME,
                             must_contain=b"unknown ref or not a commit: " + base.encode())
                self.assertEqual(proc.stdout, b"")

    def test_empty_or_missing_value_exits_2(self):
        """Clause: an empty BASE (`--onto=`, `--onto ""`) or a missing one is a usage error.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        for args in (["--onto="], ["--onto", ""], ["--onto"]):
            with self.subTest(args=args):
                proc = self.pm(repo, args, b"a\td\n")
                assert_error(self, proc, NAME)
                self.assertEqual(proc.stdout, b"")

    def test_base_may_start_with_dash_even_h(self):
        """Clause: BASE is a ref; `-h` as the value of --onto is BASE, not help (common rules).

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        # Branches named -h and -x, both pointing at main.
        repo.git("update-ref", "refs/heads/-h", repo.rev("main"))
        repo.git("update-ref", "refs/heads/-x", repo.rev("main"))
        for args in (["--onto", "-h"], ["--onto=-x"]):
            with self.subTest(args=args):
                proc = self.pm(repo, args, b"s1\td\n")
                self.assertResult(proc, b"s1\td\tstale\tf.txt\ns1\td\tstale\tg.txt\n", 1)

    def test_help_wins_and_unexpected_positional(self):
        """Clause: -h still wins over --onto; an extra positional with --onto is exit 2.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        proc = self.pm(repo, ["--onto", "nosuch", "-h"], b"")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout, "usage expected on stdout")
        proc = self.pm(repo, ["--onto", "main", "extra"], b"a\td\n")
        assert_error(self, proc, NAME)

    def test_no_merge_base_with_base(self):
        """Clause: a ref unrelated to BASE has no merge base: exit 2 naming the ref.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        # A second root commit: no history in common with main.
        repo.commit(None, {"other.txt": "o\n"}, ref="refs/heads/unrelated-root")
        proc = self.pm(repo, ["--onto", "main"], b"unrelated-root\td\n")
        assert_error(self, proc, NAME, must_contain=b"no merge base")
        self.assertIn(b"unrelated-root", proc.stderr)


@needs_pairmerge_git
class TestStale(OntoTestCase):
    """Stale refs: a ref that does not merge cleanly into BASE on its own.

    SPEC: "--onto BASE" (Stale refs, Output per pair).
    """

    def test_one_stale_ref_lists_its_paths(self):
        """Clause: either ref stale -> `refA TAB refB TAB stale TAB path` per path on which the
        stale ref conflicts with BASE, in input order, refs as given; either position.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        proc = self.pm(repo, ["--onto", "main"], b"s1\td\na\ts1\n")
        self.assertResult(proc, b"s1\td\tstale\tf.txt\ns1\td\tstale\tg.txt\n"
                                b"a\ts1\tstale\tf.txt\na\ts1\tstale\tg.txt\n", 1)

    def test_stale_paths_match_single_ref_check(self):
        """Clause: the stale paths are those of `printf 'BASE\\tREF\\n' | git pairmerge`.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        single = self.pm(repo, [], b"main\ts2\n")
        # Fixture check: s2 conflicts with main on exactly these three paths.
        self.assertResult(single, b"main\ts2\tconflict\tZ.txt\nmain\ts2\tconflict\t_z.txt\n"
                                  b"main\ts2\tconflict\tg.txt\n", 1)
        # Turn each "main TAB s2 TAB conflict TAB path" into the stale record expected.
        expected = single.stdout.replace(b"main\ts2\tconflict\t", b"s2\td\tstale\t")
        proc = self.pm(repo, ["--onto", "main"], b"s2\td\n")
        self.assertResult(proc, expected, 1)

    def test_only_the_stale_sides_paths(self):
        """Clause: stale records list where the stale ref conflicts with BASE, not where the
        pair would conflict (s2 and a both edit c.txt, which main never touched).

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        proc = self.pm(repo, ["--onto", "main"], b"s2\ta\n")
        # Bytewise: "Z" (0x5A) < "_" (0x5F) < "g" (0x67); a collating locale would differ.
        self.assertResult(proc, b"s2\ta\tstale\tZ.txt\ns2\ta\tstale\t_z.txt\n"
                                b"s2\ta\tstale\tg.txt\n", 1)

    def test_both_stale_sorted_and_deduplicated(self):
        """Clause: both refs stale -> the union of their paths, sorted bytewise, a path on which
        both conflict listed once; a stale pair is never reported as `conflict`.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        # s1 and s2 also conflict with each other on g.txt: still only `stale`.
        proc = self.pm(repo, ["--onto", "main"], b"s2\ts1\n",
                       env_extra={"LC_ALL": "en_US.UTF-8"})
        self.assertResult(proc, b"s2\ts1\tstale\tZ.txt\ns2\ts1\tstale\t_z.txt\n"
                                b"s2\ts1\tstale\tf.txt\ns2\ts1\tstale\tg.txt\n", 1)
        self.assertNotIn(b"\tconflict\t", proc.stdout)

    def test_stale_without_paths_writes_empty_path(self):
        """Clause: a stale ref on which git lists no path gives one record with an empty path.

        SPEC: "--onto BASE"
        """
        repo = Repo(self.sb)
        body = dict((p, p * 20 + "\n") for p in ("a", "b"))
        old = repo.commit(None, {"dir/a": body["a"], "dir/b": body["b"], "keep": "k\n"},
                          ref="refs/heads/old")
        # main splits dir/ in two with no majority: a directory rename conflict with no path.
        main = repo.commit(old, {"dir/a": None, "dir/b": None, "x/a": body["a"],
                                 "y/b": body["b"]}, ref="refs/heads/main")
        repo.commit(old, {"dir/c": "c\n"}, ref="refs/heads/r")
        repo.commit(main, {"keep": "k2\n"}, ref="refs/heads/k")
        # Fixture check (no --onto): r alone against main conflicts with no path listed.
        single = self.pm(repo, [], b"main\tr\n")
        self.assertResult(single, b"main\tr\tconflict\t\n", 1)
        proc = self.pm(repo, ["--onto", "main"], b"r\tk\n")
        self.assertResult(proc, b"r\tk\tstale\t\n", 1)

    def test_stale_path_in_z_mode(self):
        """Clause: stale records follow -z like any record (a path with LF written raw); in LF
        mode the same path is the unsafe-name error naming -z.

        SPEC: "--onto BASE"
        """
        repo = Repo(self.sb)
        old = repo.commit(None, {"new\nline.txt": "0\n"}, ref="refs/heads/old")
        main = repo.commit(old, {"new\nline.txt": "main\n"}, ref="refs/heads/main")
        repo.commit(old, {"new\nline.txt": "s\n"}, ref="refs/heads/s")
        repo.commit(main, {"other": "o\n"}, ref="refs/heads/f")
        proc = self.pm(repo, ["-z", "--onto", "main"], b"s\tf\0")
        self.assertResult(proc, b"s\tf\tstale\tnew\nline.txt\0", 1)
        # LF mode cannot write that record.
        proc = self.pm(repo, ["--onto", "main"], b"s\tf\n")
        assert_error(self, proc, NAME, must_contain=b"-z")


@needs_pairmerge_git
class TestPairs(OntoTestCase):
    """Non-stale pairs: both refs land on BASE, first field first.

    SPEC: "--onto BASE" (Output per pair, Mechanism, Edge cases, Order, Exit status).
    """

    def test_fresh_refs_conflicting_with_each_other(self):
        """Clause: otherwise `clean` or `conflict` exactly as without --onto: two refs that each
        merge into BASE but conflict with each other -> `conflict` on that path.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        # a and b are both fresh: neither is stale, so the pair itself is merged.
        proc = self.pm(repo, ["--onto", "main"], b"a\tb\n")
        self.assertResult(proc, b"a\tb\tconflict\tc.txt\n", 1)

    def test_behind_ref_clean_once_both_land(self):
        """Clause: the question is whether the refs conflict once both land on BASE, first one
        then the other: p (behind BASE, same t.txt change as BASE) and q conflict at their own
        merge base but not once p has landed on BASE (M_p) and q follows.

        With q first, Mechanism gives merge-tree M_q p, whose merge base is the old commit p
        forked from, where t0 -> t1 (p) meets t0 -> t2 (M_q): a conflict on t.txt.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        # Without --onto the pair is merged against its own merge base: a conflict.
        plain = self.pm(repo, [], b"p\tq\n")
        self.assertResult(plain, b"p\tq\tconflict\tt.txt\n", 1)
        proc = self.pm(repo, ["--onto", "main"], b"p\tq\n")
        self.assertResult(proc, b"p\tq\tclean\t\n", 0)
        # The reverse order lands q first, then merges p as it is (see the docstring).
        proc = self.pm(repo, ["--onto", "main"], b"q\tp\n")
        self.assertResult(proc, b"q\tp\tconflict\tt.txt\n", 1)

    def test_edge_cases_are_clean(self):
        """Clause (Edge cases): A equal to B, A an ancestor of B, B already containing A (a
        merge), and a ref already merged into BASE are clean, in either order.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        a = repo.rev("a")
        # a2 descends from a and edits c.txt again; am is a merge commit of d and a.
        repo.commit(a, {"c.txt": "line1\na\na2\n"}, ref="refs/heads/a2")
        am = repo.git("commit-tree", repo.git("merge-tree", "--write-tree", "d", "a")
                      .split(b"\n")[0].decode(), "-p", "d", "-p", "a", "-m", "am").strip().decode()
        repo.set_ref("refs/heads/am", am)
        # One run per order: a reversed pair in the same input would be a repeat, skipped.
        forward = [b"a\ta", b"a\t" + a.encode(), b"p\tp", b"a\ta2", b"a\tam", b"m\te",
                   b"main~1\tb", b"main\ta"]
        backward = [b"a2\ta", b"am\ta", b"e\tm", b"b\tmain~1", b"a\tmain"]
        for pairs in (forward, backward):
            with self.subTest(pairs=pairs):
                stdin = b"".join(p + b"\n" for p in pairs)
                proc = self.pm(repo, ["--onto", "main"], stdin)
                self.assertResult(proc, b"".join(p + b"\tclean\t\n" for p in pairs), 0)

    def test_criss_cross_uses_recursive_base(self):
        """Clause (Edge cases): criss-cross histories use git's recursive merge base. Against
        one of the two merge bases alone this pair conflicts; recursively it is clean.

        SPEC: "--onto BASE"
        """
        repo = Repo(self.sb)
        old = repo.commit(None, {"f": "o\n", "g": "g\n", "h": "h\n"}, ref="refs/heads/main")
        left1 = repo.commit(old, {"f": "x\n"})
        right1 = repo.commit(old, {"g": "g2\n"})
        # Each side merges the other: two merge bases, left1 and right1.
        merges = []
        for first, second in ((left1, right1), (right1, left1)):
            tree = repo.git("merge-tree", "--write-tree", first, second).split(b"\n")[0].decode()
            merges.append(repo.git("commit-tree", tree, "-p", first, "-p", second,
                                   "-m", "cross").strip().decode())
        repo.commit(merges[0], {"f": "y\n"}, ref="refs/heads/l3")
        repo.commit(merges[1], {"h": "h2\n"}, ref="refs/heads/r3")
        # Fixture check with git itself: right1 alone as the base conflicts on f.
        alone = repo.git("merge-tree", "--write-tree", "--merge-base", right1, "l3", "r3",
                         check=False)
        self.assertIn(b"CONFLICT", alone)
        proc = self.pm(repo, ["--onto", "main"], b"l3\tr3\n")
        self.assertResult(proc, b"l3\tr3\tclean\t\n", 0)
        proc = self.pm(repo, ["--onto", "main"], b"r3\tl3\n")
        self.assertResult(proc, b"r3\tl3\tclean\t\n", 0)

    def test_repeated_pair_once_at_first_occurrence(self):
        """Clause (Order): a repeated pair in either order is computed once, at its first
        occurrence, printed as first given; stale pairs too.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        stdin = b"b\ta\na\tb\ns1\td\nb\ta\nd\ts1\na\td\nd\ta\n"
        proc = self.pm(repo, ["--onto", "main"], stdin)
        self.assertResult(proc, b"b\ta\tconflict\tc.txt\n"
                                b"s1\td\tstale\tf.txt\ns1\td\tstale\tg.txt\n"
                                b"a\td\tclean\t\n", 1)

    def test_input_order_and_refs_as_given(self):
        """Clause: output per pair in input order, refs as given (SHA, refs/heads/ spelling).

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        sha_b = repo.rev("b").encode()
        stdin = b"refs/heads/d\ta\n" + sha_b + b"\trefs/heads/a\ns2\tq\n"
        proc = self.pm(repo, ["--onto", "main"], stdin)
        self.assertResult(proc, b"refs/heads/d\ta\tclean\t\n"
                                + sha_b + b"\trefs/heads/a\tconflict\tc.txt\n"
                                b"s2\tq\tstale\tZ.txt\ns2\tq\tstale\t_z.txt\n"
                                b"s2\tq\tstale\tg.txt\n", 1)

    def test_first_field_lands_first(self):
        """Clause (Order, Mechanism): the first field lands first; the pair is merge-tree M_A B,
        so a directory/file name carries the id of the side holding the file: B's own id when
        B holds it, M_A's (neither A nor B) when A does.

        SPEC: "--onto BASE"
        """
        repo = self.df_repo()
        a, b = repo.rev("a").encode(), repo.rev("b").encode()
        # b lands first; a, merged as itself, holds the file: d~<a's id>.
        proc = self.pm(repo, ["--onto", "main"], b"b\ta\n")
        self.assertResult(proc, b"b\ta\tconflict\td~" + a + b"\n", 1)
        # a lands first: the file side is M_a, a commit of its own.
        proc = self.pm(repo, ["--onto", "main"], b"a\tb\n")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        fields = proc.stdout.rstrip(b"\n").split(b"\t")
        # Four fields, the last one PATH~<id> for an id other than a's and b's.
        self.assertEqual(fields[:3], [b"a", b"b", b"conflict"], proc.stdout)
        self.assertRegex(fields[3], DF_NAME)
        self.assertNotIn(fields[3][2:], (a, b))

    def test_deterministic_output(self):
        """Clause (Mechanism, Order): M_R has a fixed author, committer and date, so its id and
        every `PATH~<id>` name are the same on every run: byte-identical output.

        SPEC: "--onto BASE"
        """
        repo = self.df_repo()
        runs = [self.pm(repo, ["--onto", "main"], b"a\tb\nb\tmain\n") for _ in range(2)]
        # A different time zone and locale must not change a byte either.
        runs.append(self.pm(repo, ["--onto", "main"], b"a\tb\nb\tmain\n",
                            env_extra={"TZ": "Asia/Kolkata", "LC_ALL": "en_US.UTF-8"}))
        for proc in runs:
            self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertRegex(runs[0].stdout, rb"\Aa\tb\tconflict\td~[0-9a-f]{40,64}\nb\tmain\tclean\t\n\Z")
        self.assertEqual(runs[0].stdout, runs[1].stdout)
        self.assertEqual(runs[0].stdout, runs[2].stdout)


@needs_pairmerge_git
class TestExitAndContract(OntoTestCase):
    """Exit status, -z, the read-only guarantee and the behaviour without --onto.

    SPEC: "--onto BASE" (Exit status) and common rules.
    """

    def test_exit_status(self):
        """Clause (Exit status): 1 if any pair is stale or conflicts, else 0.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        cases = [(b"a\td\np\tq\n", 0), (b"", 0), (b"s1\td\n", 1), (b"a\tb\n", 1),
                 (b"a\td\ns1\td\n", 1), (b"a\tb\na\td\n", 1)]
        for stdin, code in cases:
            with self.subTest(stdin=stdin):
                proc = self.pm(repo, ["--onto", "main"], stdin)
                self.assertEqual(proc.returncode, code, proc.stderr)
                self.assertEqual(proc.stderr, b"")

    def test_unknown_ref_in_input_exits_2(self):
        """Clause: every input ref is resolved as in "Resolving a ref"; unknown is exit 2.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        # a is fine; the second field names nothing.
        proc = self.pm(repo, ["--onto", "main"], b"a\tnosuch\n")
        assert_error(self, proc, NAME, must_contain=b"unknown ref or not a commit: nosuch")

    def test_z_mode(self):
        """Clause: with -z, --onto input and output records end with NUL.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        # The last record is unterminated, which -z accepts as well.
        proc = self.pm(repo, ["--onto=main", "-z"], b"a\tb\0s1\td\0a\td")
        self.assertResult(proc, b"a\tb\tconflict\tc.txt\0s1\td\tstale\tf.txt\0"
                                b"s1\td\tstale\tg.txt\0a\td\tclean\t\0", 1)

    def test_object_store_and_repository_unchanged(self):
        """Clause (Mechanism): every object goes to the run's private object directory: refs,
        index, worktree and object store are unchanged.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        before = repo.state()
        proc = self.pm(repo, ["--onto", "main"], b"a\tb\np\tq\ns2\ts1\na\td\n")
        # The run must have done real work (conflict, clean, stale) for the check to mean much.
        self.assertResult(proc, b"a\tb\tconflict\tc.txt\np\tq\tclean\t\n"
                                b"s2\ts1\tstale\tZ.txt\ns2\ts1\tstale\t_z.txt\n"
                                b"s2\ts1\tstale\tf.txt\ns2\ts1\tstale\tg.txt\n"
                                b"a\td\tclean\t\n", 1)
        self.assertEqual(repo.state(), before)

    def test_df_object_store_unchanged(self):
        """Clause (Mechanism): M_R commits are never written to the repository; the id named in
        a directory/file conflict is not an object of the repository.

        SPEC: "--onto BASE"
        """
        repo = self.df_repo()
        before = repo.state()
        proc = self.pm(repo, ["--onto", "main"], b"a\tb\n")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(repo.state(), before)
        # One record, a TAB b TAB conflict TAB d~<id>.
        self.assertRegex(proc.stdout, rb"\Aa\tb\tconflict\t[^\t\n]+\n\Z")
        name = proc.stdout.rstrip(b"\n").split(b"\t")[3]
        self.assertRegex(name, DF_NAME)
        # cat-file prints nothing (and fails) for an object the repository does not hold.
        self.assertEqual(repo.git("cat-file", "-t", name[2:].decode(), check=False), b"")

    def test_without_onto_differs_only_where_spec_says(self):
        """Clause: without --onto the behaviour is unchanged: the same input gives the same
        output for fresh pairs, and differs for stale refs and refs behind BASE.

        SPEC: "--onto BASE"
        """
        repo = self.onto_repo()
        same = b"a\tb\na\td\nb\td\n"
        self.assertResult(self.pm(repo, [], same), self.pm(repo, ["--onto", "main"], same).stdout, 1)
        self.assertResult(self.pm(repo, [], same), b"a\tb\tconflict\tc.txt\na\td\tclean\t\n"
                                                   b"b\td\tclean\t\n", 1)
        # Stale: without --onto s1 meets d (which holds main's edits) at the old commit, so
        # the paths come out as `conflict`; s2/s1 conflict with each other on g.txt only.
        self.assertResult(self.pm(repo, [], b"s1\td\ns2\ts1\n"),
                          b"s1\td\tconflict\tf.txt\ns1\td\tconflict\tg.txt\n"
                          b"s2\ts1\tconflict\tg.txt\n", 1)
        self.assertResult(self.pm(repo, ["--onto", "main"], b"s1\td\n"),
                          b"s1\td\tstale\tf.txt\ns1\td\tstale\tg.txt\n", 1)


if __name__ == "__main__":
    unittest.main()
