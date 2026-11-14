# SPDX-License-Identifier: Apache-2.0
"""Clean-room acceptance tests for the clauses added in the revised SPEC.md (4f52dec).

Written from SPEC.md only. Each docstring names the clause. Real git repositories, no mocks;
commands run as black boxes (`python -m git_overlap.<name>`).
"""
import os
import re
import signal
import subprocess
import sys
import time
import unittest

from fixtures import (PACKAGE_ROOT, PAIRMERGE_OK, Repo, Sandbox, assert_error, gherkin_repo,
                      needs_pairmerge_git)

# Signals and closing fd 0/1 in the child (preexec_fn) exist only on POSIX.
POSIX = os.name == "posix"
# One line of printable ASCII and its LF: what an escaped error message must be.
PRINTABLE_LINE = re.compile(rb"\A[\x20-\x7e]+\n\Z")


def unknown(name, ref, record=None):
    """Expected stderr for an unresolvable ref; stdin refs carry `record N: ` (ruling 1).

    Args:
        name: The command name without "git-".
        ref: The ref as it appears in the message (already escaped).
        record: The record number for a stdin ref, or None for BASE.

    Returns:
        The exact stderr bytes, LF included.

    SPEC: "Record errors" and "Resolving a ref".
    """
    where = b"" if record is None else b"record " + str(record).encode() + b": "
    return b"git-" + name.encode() + b": " + where + b"unknown ref or not a commit: " + ref + b"\n"


class RevisedTestCase(unittest.TestCase):
    """Base for the revised-clause tests: a sandbox, process helpers and the pair_repo fixture.

    SPEC: "Common rules".
    """
    def setUp(self):
        """Create a hermetic sandbox, removed after the test."""
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def env(self, extra=None):
        """Return the sandbox environment for running a command with Popen directly.

        Args:
            extra: Variables to add or override, or None.

        Returns:
            A new dict; the sandbox's own is not changed.
        """
        env = dict(self.sb.env)
        # The package under test is imported from the checkout, not an installed copy.
        env["PYTHONPATH"] = PACKAGE_ROOT
        env.update(extra or {})
        return env

    def argv(self, name, args):
        """Return the argv that runs one command as `python -m`.

        Args:
            name: The command name without "git-".
            args: Command-line arguments.

        Returns:
            The argument list for subprocess.
        """
        # sys.executable: the same interpreter as the test run, whatever is on PATH.
        return [sys.executable, "-m", "git_overlap." + name] + list(args)

    def assertExactError(self, proc, expected):
        """Assert stderr is exactly EXPECTED and the exit status is 2.

        Args:
            proc: The completed process.
            expected: The exact stderr bytes.
        """
        self.assertEqual(proc.stderr, expected)
        self.assertEqual(proc.returncode, 2)

    def pair_repo(self, name="repo"):
        """main: c.txt x.py y.py sub/s.txt; a/b conflict on c.txt and sub/s.txt; d edits y.py.

        Args:
            name: The repository directory name inside the sandbox.

        Returns:
            A tuple (Repo, base commit SHA).
        """
        repo = Repo(self.sb, name)
        base = repo.commit(None, {"c.txt": "l\n", "x.py": "x\n", "y.py": "y\n",
                                  "sub/s.txt": "s\n"}, ref="refs/heads/main")
        repo.commit(base, {"c.txt": "l\na\n", "sub/s.txt": "s\na\n"}, ref="refs/heads/a",
                    # A unique subject, so `:/side a edits c` names exactly this commit.
                    message="side a edits c")
        repo.commit(base, {"c.txt": "l\nb\n", "sub/s.txt": "s\nb\n"}, ref="refs/heads/b")
        repo.commit(base, {"y.py": "y2\n"}, ref="refs/heads/d")
        return repo, base


class TestRefResolution(RevisedTestCase):
    """Single-revision check, BASE resolved first, and refs printed as written.

    SPEC: "Resolving a ref" and "Refs".
    """
    def test_footprint_rejects_non_single_revisions(self):
        """SPEC Resolving a ref + Errors (record prefix): `^a` and `a..b` are not one object id:
        exit 2, `record 2: unknown ref or not a commit: <ref>`, before any diff runs."""
        repo, _ = gherkin_repo(self.sb)
        # `^a` and `a..b` are valid revision syntax, but not a single object id.
        for ref in (b"^a", b"a..b", b"no-such"):
            proc = repo.run("footprint", ["main"], stdin=b"a\n" + ref + b"\n")
            self.assertExactError(proc, unknown("footprint", ref, 2))
            self.assertEqual(proc.stdout, b"", "resolution happens before any diff runs")

    def test_footprint_rejects_bad_base(self):
        """SPEC Resolving a ref (including BASE): `^main` as BASE is exit 2 with the message."""
        repo, _ = gherkin_repo(self.sb)
        self.assertExactError(repo.run("footprint", ["^main"], stdin=b"a\n"),
                              unknown("footprint", b"^main"))
        self.assertExactError(repo.run("footprint", ["main..a"], stdin=b"a\n"),
                              unknown("footprint", b"main..a"))

    def test_footprint_base_resolved_before_stdin_is_read(self):
        """SPEC Resolving a ref: BASE is resolved before stdin is read (exits 2 while stdin is
        still open and unwritten)."""
        repo, _ = gherkin_repo(self.sb)
        # Popen with stdin left open: a command that reads stdin first would block here.
        proc = subprocess.Popen(self.argv("footprint", ["no-such-base"]), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=repo.path, env=self.env())
        try:
            code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            # Kill rather than leave a blocked child behind for the next test.
            proc.kill()
            proc.wait()
            self.fail("footprint blocked on stdin instead of resolving BASE first")
        finally:
            # Closed in finally, so the pipe is released whether or not the wait timed out.
            proc.stdin.close()
        err = proc.stderr.read()
        proc.stdout.close()
        proc.stderr.close()
        self.assertEqual(code, 2)
        self.assertEqual(err, unknown("footprint", b"no-such-base"))

    def test_colon_slash_message_accepted(self):
        """SPEC Refs: `:/message` is a commit-ish, accepted and printed as written."""
        repo, base = gherkin_repo(self.sb)
        # A unique subject, so `:/unique marker 7f3` matches this commit only.
        repo.commit(base, {"w.py": "w\n"}, ref="refs/heads/m", message="unique marker 7f3")
        proc = repo.run("footprint", ["main"], stdin=b":/unique marker 7f3\n")
        self.assertEqual(proc.stdout, b":/unique marker 7f3\tA\tw.py\n", proc.stderr)
        self.assertEqual(proc.returncode, 0)

    def test_repeated_means_same_spelling(self):
        """SPEC Resolving a ref: "Repeated" means the same spelling, not the same commit."""
        repo, _ = gherkin_repo(self.sb)
        sha = repo.rev("a").encode()
        # a and its SHA are one commit but two spellings: both print; the second `a` does not.
        proc = repo.run("footprint", ["main"], stdin=b"a\n" + sha + b"\na\n")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\n" + sha + b"\tM\tx.py\n", proc.stderr)
        self.assertEqual(proc.returncode, 0)

    @needs_pairmerge_git
    def test_pairmerge_ref_resolution(self):
        """SPEC Resolving a ref (all commands): pairmerge rejects `^a` and `a..b` with the message
        before any merge (no records); accepts `:/message`."""
        repo, _ = self.pair_repo()
        for ref in (b"^a", b"a..b"):
            # The valid pair a/d comes first: no record for it proves resolution precedes merging.
            proc = repo.run("pairmerge", [], stdin=b"a\td\n" + ref + b"\td\n")
            self.assertExactError(proc, unknown("pairmerge", ref, 2))
            self.assertEqual(proc.stdout, b"")
        proc = repo.run("pairmerge", [], stdin=b":/side a edits c\tb\n")
        self.assertEqual(proc.stdout, b":/side a edits c\tb\tconflict\tc.txt\n"
                                      b":/side a edits c\tb\tconflict\tsub/s.txt\n", proc.stderr)
        self.assertEqual(proc.returncode, 1)


class TestErrorEscaping(RevisedTestCase):
    """Names quoted in error messages are escaped so stderr stays one line.

    SPEC: "Errors".
    """
    def test_footprint_escapes_names_in_messages(self):
        """SPEC Errors: a ref quoted in a message is backslash-escaped (TAB, LF, CR, backslash),
        so stderr is exactly one line (-z mode lets the ref hold those bytes)."""
        repo, _ = gherkin_repo(self.sb)
        # (raw ref, how it must appear in the message); -z lets the ref carry these bytes.
        cases = [(b"a\nb", b"a\\nb"), (b"t\tb", b"t\\tb"), (b"c\rd", b"c\\rd"),
                 (b"e\\f", b"e\\\\f")]
        for raw, shown in cases:
            proc = repo.run("footprint", ["-z", "main"], stdin=raw + b"\0")
            self.assertExactError(proc, unknown("footprint", shown, 1))

    def test_non_printable_bytes_escaped(self):
        """SPEC Errors: any byte that is not printable ASCII is escaped; one printable line.
        SPEC Resolving a ref: on Windows a non-UTF-8 ref cannot be passed to git, and says so."""
        repo, _ = gherkin_repo(self.sb)
        # Invalid UTF-8, control bytes, ESC and a valid UTF-8 é: all escaped byte by byte.
        proc = repo.run("footprint", ["-z", "main"], stdin=b"\xff\x01\x1bz\xc3\xa9\0")
        self.assertEqual(proc.returncode, 2)
        self.assertRegex(proc.stderr, PRINTABLE_LINE)
        message = (b"cannot pass a name that is not valid UTF-8 to git on this platform: "
                   # Windows cannot pass non-UTF-8 bytes to git, so its message differs by design.
                   if os.name == "nt" else b"unknown ref or not a commit: ")
        self.assertEqual(proc.stderr, b"git-footprint: record 1: " + message
                         + b"\\xff\\x01\\x1bz\\xc3\\xa9\n")

    @needs_pairmerge_git
    def test_pairmerge_escapes_names(self):
        """SPEC Errors: pairmerge -z with a ref holding LF: exactly one escaped stderr line."""
        repo, _ = self.pair_repo()
        # In -z mode the LF is part of the second ref, not a record end.
        proc = repo.run("pairmerge", ["-z"], stdin=b"a\tx\ny\0")
        self.assertExactError(proc, unknown("pairmerge", b"x\\ny", 1))


class TestUnterminatedLastRecord(RevisedTestCase):
    """A final record without its terminator is accepted by every command.

    SPEC: "Unterminated last record".
    """
    def test_footprint(self):
        """SPEC Unterminated last record: a final record without LF or NUL is accepted."""
        repo, _ = gherkin_repo(self.sb)
        # b has no LF: it must still be read as a record, not dropped as a partial line.
        proc = repo.run("footprint", ["main"], stdin=b"a\nb")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\nb\tA\ty.py\nb\tD\tz.py\n", proc.stderr)
        self.assertEqual(proc.returncode, 0)
        proc = repo.run("footprint", ["-z", "main"], stdin=b"a\0b")
        self.assertEqual(proc.stdout, b"a\tM\tx.py\0b\tA\ty.py\0b\tD\tz.py\0", proc.stderr)

    def test_overlap(self):
        """SPEC Unterminated last record: overlap, LF and -z."""
        # overlap needs no repository, so it runs in a plain directory.
        empty = self.sb.mkdir("empty")
        proc = self.sb.run_cmd("overlap", [], stdin=b"a\tM\tx\nb\tM\tx", cwd=empty)
        self.assertEqual(proc.stdout, b"a\tb\tx\n", proc.stderr)
        self.assertEqual(proc.returncode, 1)
        proc = self.sb.run_cmd("overlap", ["-z"], stdin=b"a\tM\tx\0b\tM\tx", cwd=empty)
        self.assertEqual(proc.stdout, b"a\tb\tx\0", proc.stderr)

    @needs_pairmerge_git
    def test_pairmerge(self):
        """SPEC Unterminated last record: pairmerge."""
        repo, _ = self.pair_repo()
        # The repeated a/d is processed once; the unterminated d/b is still processed.
        proc = repo.run("pairmerge", [], stdin=b"a\td\na\td\nd\tb")
        self.assertEqual(proc.stdout, b"a\td\tclean\t\nd\tb\tclean\t\n", proc.stderr)
        self.assertEqual(proc.returncode, 0)


class TestStandardStreams(RevisedTestCase):
    """-h and --version precedence, and fd 0 or 1 not open at all.

    SPEC: "Standard streams".
    """
    def test_help_and_version_take_precedence(self):
        """SPEC Standard streams: -h and --version print to stdout and exit 0, taking precedence
        over any other argument (bad BASE, junk positionals, unreadable --attributes, empty --with)."""
        repo, _ = gherkin_repo(self.sb)
        # Each case would be an error without -h, so exit 0 proves -h wins.
        cases = [("footprint", ["no-such-base", "extra", "-h"]),
                 ("overlap", ["junk", "-h"]),
                 ("pairmerge", ["--attributes", "/nonexistent/attrs", "-h"])]
        for name, args in cases:
            # pairmerge refuses old git, which would hide what these cases check.
            if name == "pairmerge" and not PAIRMERGE_OK:
                continue
            proc = repo.run(name, args, stdin=b"")
            self.assertEqual(proc.returncode, 0, (name, proc.stderr))
            self.assertTrue(proc.stdout.strip(), name)
            self.assertEqual(proc.stderr, b"", name)
        # The same with --version, placed first, last and among bad arguments.
        for name, args in (("footprint", ["--version", "x", "y"]),
                           ("overlap", ["junk", "--version"]),
                           ("pairmerge", ["--attributes", "/nonexistent/attrs", "--version"])):
            if name == "pairmerge" and not PAIRMERGE_OK:
                continue
            proc = repo.run(name, args, stdin=b"")
            self.assertEqual(proc.returncode, 0, (name, proc.stderr))
            self.assertRegex(proc.stdout, re.compile(rb"\Agit-" + name.encode() + rb" \S+\n\Z"))
            self.assertEqual(proc.stderr, b"", name)

    @unittest.skipUnless(POSIX, "closing fd 0/1 in the child needs preexec_fn")
    def test_fd_not_open(self):
        """SPEC Standard streams: fd 0 or 1 not open at all (`<&-`, `>&-`) is exit 2 with
        `standard input is not open` / `standard output is not open`."""
        repo, _ = self.pair_repo()
        cases = [("footprint", ["main"], b"a\n"), ("overlap", [], b"a\tM\tx\nb\tM\tx\n"),
                 ("pairmerge", [], b"a\tb\n")]
        for name, args, data in cases:
            if name == "pairmerge" and not PAIRMERGE_OK:
                continue
            for fd, what in ((0, b"standard input is not open"),
                             (1, b"standard output is not open")):
                proc = subprocess.Popen(
                    self.argv(name, args),
                    # The fd is first given something valid, then closed in the child before exec.
                    stdin=subprocess.DEVNULL if fd == 0 else subprocess.PIPE,
                    stdout=subprocess.DEVNULL if fd == 1 else subprocess.PIPE,
                    stderr=subprocess.PIPE, cwd=repo.path, env=self.env(),
                    # fd=fd binds the loop value now; a late-binding closure would see only the last one.
                    preexec_fn=lambda fd=fd: os.close(fd))
                _out, err = proc.communicate(input=None if fd == 0 else data, timeout=60)
                self.assertEqual(err, b"git-" + name.encode() + b": " + what + b"\n",
                                 (name, fd))
                self.assertEqual(proc.returncode, 2, (name, fd))


class TestRootRelativePaths(RevisedTestCase):
    """Output paths are relative to the repository root whatever the working directory.

    SPEC: "Paths are relative to the repository root".
    """
    def test_footprint_from_subdirectory(self):
        """SPEC Paths are relative to the repository root, whatever subdirectory the command runs
        in; footprint diff pinned with --no-relative (even with diff.relative=true)."""
        repo, _ = self.pair_repo()
        # The fixture commits sub/ without a worktree, so the directory must be made.
        cwd = os.path.join(repo.path, "sub")
        os.mkdir(cwd)
        for relative in (None, "true"):
            if relative:
                # diff.relative=true would make git print paths relative to sub/ without --no-relative.
                repo.git("config", "diff.relative", relative)
            proc = self.sb.run_cmd("footprint", ["main"], stdin=b"a\n", cwd=cwd)
            self.assertEqual(proc.stdout, b"a\tM\tc.txt\na\tM\tsub/s.txt\n", proc.stderr)
            self.assertEqual(proc.returncode, 0)

    @needs_pairmerge_git
    def test_pairmerge_from_subdirectory(self):
        """SPEC Paths are relative to the repository root (merge-tree runs at the root)."""
        repo, _ = self.pair_repo()
        cwd = os.path.join(repo.path, "sub")
        os.mkdir(cwd)
        # From sub/, merge-tree would print sub-relative names unless run at the root.
        proc = self.sb.run_cmd("pairmerge", [], stdin=b"a\tb\n", cwd=cwd)
        self.assertEqual(proc.stdout, b"a\tb\tconflict\tc.txt\na\tb\tconflict\tsub/s.txt\n",
                         proc.stderr)
        self.assertEqual(proc.returncode, 1)

    def test_overlap_from_subdirectory(self):
        """SPEC Paths are relative to the repository root: overlap passes paths through unchanged
        when run in a repository subdirectory."""
        repo, _ = self.pair_repo()
        cwd = os.path.join(repo.path, "sub")
        os.mkdir(cwd)
        # overlap never rewrites paths: what comes in, root-relative, goes out.
        proc = self.sb.run_cmd("overlap", [], stdin=b"a\tM\tsub/s.txt\nb\tM\tsub/s.txt\n", cwd=cwd)
        self.assertEqual(proc.stdout, b"a\tb\tsub/s.txt\n", proc.stderr)
        self.assertEqual(proc.returncode, 1)


class TestMergeBase(RevisedTestCase):
    """A missing merge base is an error that says so.

    SPEC: "Shallow or unrelated histories".
    """
    def test_missing_merge_base_says_so(self):
        """SPEC Shallow or unrelated histories: a missing merge base is exit 2 with a message that
        says so (footprint and pairmerge, orphan branch); pairmerge's names `no merge base` and
        both refs (ruling 2)."""
        repo, _ = self.pair_repo()
        # A second root commit: no history shared with main or a.
        repo.commit(None, {"o.txt": "o\n"}, ref="refs/heads/orphan")
        for name, stdin in (("footprint", b"orphan\n"), ("pairmerge", b"a\torphan\n")):
            if name == "pairmerge" and not PAIRMERGE_OK:
                continue
            proc = repo.run(name, ["main"] if name == "footprint" else [], stdin=stdin)
            # Loose for footprint, whose wording the SPEC leaves open; strict for pairmerge below.
            assert_error(self, proc, name, must_contain=re.compile(rb"(?i)merge[ -]base"))
            if name == "pairmerge":
                self.assertIn(b"no merge base", proc.stderr)
                self.assertIn(b"orphan", proc.stderr)
                # Ref a as a word of its own, not the a inside other words of the message.
                self.assertRegex(proc.stderr, re.compile(rb"(?<![\w-])a(?![\w-])"))


class TestFootprintPinnedDiff(RevisedTestCase):
    """The footprint diff ignores user configuration that would change the file list.

    SPEC: "git footprint BASE" (the diff is pinned against user configuration).
    """
    def test_submodule_pointer_change_is_a_change(self):
        """SPEC footprint: --ignore-submodules=none: a gitlink change is in the footprint even with
        diff.ignoreSubmodules=all."""
        repo = Repo(self.sb)
        # Gitlinks need no real submodule: the pointer id alone is the change.
        base = repo.commit(None, {"mod": ("gitlink", "1" * 40), "k": "k\n"},
                           ref="refs/heads/main")
        repo.commit(base, {"mod": ("gitlink", "2" * 40), "mod2": ("gitlink", "3" * 40)},
                    ref="refs/heads/r")
        # Without --ignore-submodules=none this config would hide both gitlink changes.
        repo.git("config", "diff.ignoreSubmodules", "all")
        proc = repo.run("footprint", ["main"], stdin=b"r\n")
        self.assertEqual(proc.stdout, b"r\tM\tmod\nr\tA\tmod2\n", proc.stderr)
        self.assertEqual(proc.returncode, 0)

    def test_order_file_ignored(self):
        """SPEC footprint: paths are sorted by the tool, not by diff.orderFile."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"a.py": "a\n", "z.py": "z\n"}, ref="refs/heads/main")
        repo.commit(base, {"a.py": "a2\n", "z.py": "z2\n"}, ref="refs/heads/r")
        order = os.path.join(self.sb.tmp, "order")
        with open(order, "wb") as fh:
            # The reverse of bytewise order, so obeying orderFile would show.
            fh.write(b"z.py\na.py\n")
        repo.git("config", "diff.orderFile", order)
        proc = repo.run("footprint", ["main"], stdin=b"r\n")
        self.assertEqual(proc.stdout, b"r\tM\ta.py\nr\tM\tz.py\n", proc.stderr)

    def test_tab_in_input_ref_lf_mode(self):
        """SPEC footprint: the input record is the whole ref: in LF mode a TAB in it is the
        unsafe-name error (naming -z, exit 2)."""
        repo, _ = gherkin_repo(self.sb)
        # A footprint record fed back as a ref: the TAB makes it unsafe, not a two-field record.
        assert_error(self, repo.run("footprint", ["main"], stdin=b"a\tM\n"), "footprint",
                     must_contain=b"-z")


class TestOverlapWithEmpty(RevisedTestCase):
    """Bytewise, case-sensitive comparison and the empty --with REF.

    SPEC: "git overlap" (Known limit, --with REF).
    """
    def test_case_sensitive_comparison(self):
        """SPEC overlap Known limit: paths and refs compare bytewise, case-sensitively; `Foo.txt`
        and `foo.txt` are not an overlap, and refs `A`/`a` are distinct refs."""
        empty = self.sb.mkdir("case")
        proc = self.sb.run_cmd("overlap", [], stdin=b"a\tM\tFoo.txt\nb\tM\tfoo.txt\n", cwd=empty)
        self.assertEqual((proc.stdout, proc.returncode), (b"", 0), proc.stderr)
        # Refs differing only in case are distinct; A sorts before a bytewise.
        proc = self.sb.run_cmd("overlap", [], stdin=b"A\tM\tx\na\tM\tx\n", cwd=empty)
        self.assertEqual((proc.stdout, proc.returncode), (b"A\ta\tx\n", 1), proc.stderr)

    def test_empty_with_is_usage_error(self):
        """SPEC overlap --with: an empty REF is a usage error (exit 2), `--with=` and `--with ''`."""
        empty = self.sb.mkdir("empty")
        # Both spellings of the empty value must reach the same usage error.
        for args in (["--with="], ["--with", ""]):
            proc = self.sb.run_cmd("overlap", args, stdin=b"a\tM\tx\nb\tM\tx\n", cwd=empty)
            assert_error(self, proc, "overlap")


@needs_pairmerge_git
class TestPairmergeRevised(RevisedTestCase):
    """Self pairs, malformed fields, directory/file names, pinned settings, quoted alternates.

    SPEC: "git pairmerge" and "Merge mechanism".
    """
    def test_self_pair_is_clean(self):
        """SPEC pairmerge: a ref paired with itself is merged like any pair (and is clean)."""
        repo, _ = self.pair_repo()
        proc = repo.run("pairmerge", [], stdin=b"a\ta\n")
        self.assertEqual(proc.stdout, b"a\ta\tclean\t\n", proc.stderr)
        self.assertEqual(proc.returncode, 0)

    def test_empty_second_field_malformed(self):
        """SPEC pairmerge: an empty first or second field is a malformed record (record number)."""
        repo, _ = self.pair_repo()
        # An empty second field with a third present: the ref slot is empty, not shifted.
        assert_error(self, repo.run("pairmerge", [], stdin=b"a\td\na\t\tx\n"), "pairmerge",
                     must_contain=re.compile(rb"(?<![0-9])2(?![0-9])"))
        assert_error(self, repo.run("pairmerge", [], stdin=b"\ta\n"), "pairmerge",
                     must_contain=re.compile(rb"(?<![0-9])1(?![0-9])"))

    def test_directory_file_conflict_name_unchanged(self):
        """SPEC pairmerge: a directory/file conflict is printed as git reports it,
        `PATH~<commit id>`, unchanged (deterministic because merges are given commit ids)."""
        repo = Repo(self.sb)
        base = repo.commit(None, {"keep": "k\n"}, ref="refs/heads/main")
        # df is a file on one side and a directory on the other.
        file_side = repo.commit(base, {"df": "file\n"}, ref="refs/heads/a")
        repo.commit(base, {"df/x": "in dir\n"}, ref="refs/heads/b")
        # Both orders: the generated name uses a's commit id either way.
        for stdin, pair in ((b"a\tb\n", b"a\tb"), (b"b\ta\n", b"b\ta")):
            proc = repo.run("pairmerge", [], stdin=stdin)
            self.assertEqual(proc.stdout, pair + b"\tconflict\tdf~" + file_side.encode() + b"\n",
                             proc.stderr)
            self.assertEqual(proc.returncode, 1)

    def test_merge_settings_pinned(self):
        """SPEC Merge mechanism: merge.renames etc. pinned with -c, so a rename-vs-edit pair stays
        clean even with merge.renames=false in GIT_CONFIG_COUNT/KEY/VALUE or repository config."""
        repo = Repo(self.sb)
        # Enough identical lines that rename detection pairs f.txt with g.txt.
        body = "".join("line %02d of the renamed file\n" % i for i in range(30))
        edited = body.replace("line 10 of", "line 10 EDITED of")
        base = repo.commit(None, {"f.txt": body}, ref="refs/heads/main")
        repo.commit(base, {"f.txt": None, "g.txt": body}, ref="refs/heads/ren")
        repo.commit(base, {"f.txt": edited}, ref="refs/heads/edit")
        # Config from the environment, which outranks repository config.
        hostile = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "merge.renames",
                   "GIT_CONFIG_VALUE_0": "false"}
        # First prove plain git conflicts under this config, or the test shows nothing.
        probe = subprocess.run(["git", "merge-tree", "--write-tree", "ren", "edit"],
                               cwd=repo.path, env=self.env(hostile), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
        # Some git versions (seen on 2.43 and 2.47) still detect the rename under
        # merge.renames=false, so plain git does not conflict and pinning cannot be shown here.
        if probe.returncode == 0:
            self.skipTest("this git ignores merge.renames=false in merge-tree; nothing to pin against")
        self.assertEqual(probe.returncode, 1, "fixture must conflict for plain git under the env")
        proc = repo.run("pairmerge", [], stdin=b"ren\tedit\n", env_extra=hostile)
        self.assertEqual(proc.stdout, b"ren\tedit\tclean\t\n", proc.stderr)
        self.assertEqual(proc.returncode, 0)
        repo.git("config", "merge.renames", "false")
        # A limit of 1 would also disable rename detection if not pinned.
        repo.git("config", "merge.renameLimit", "1")
        proc = repo.run("pairmerge", [], stdin=b"ren\tedit\n")
        self.assertEqual(proc.stdout, b"ren\tedit\tclean\t\n", proc.stderr)
        self.assertEqual(proc.returncode, 0)

    @unittest.skipIf(os.name == "nt", "':' is not allowed in Windows directory names")
    def test_repository_path_with_colon(self):
        """SPEC Merge mechanism: GIT_ALTERNATE_OBJECT_DIRECTORIES entry containing the separator
        (`:`) is C-quoted, so a repository whose directory name holds ':' works; nothing written."""
        # ':' is os.pathsep on POSIX, so an unquoted alternates entry would be split in two.
        repo, _ = self.pair_repo(name="re:po")
        before = repo.state()
        proc = repo.run("pairmerge", [], stdin=b"a\tb\na\td\n")
        self.assertEqual(proc.stdout, b"a\tb\tconflict\tc.txt\na\tb\tconflict\tsub/s.txt\n"
                                      b"a\td\tclean\t\n", proc.stderr)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(before, repo.state())


@needs_pairmerge_git
class TestTempObjectDirectory(RevisedTestCase):
    """The private temporary object directory is removed on every exit path.

    SPEC: "Merge mechanism" (Nothing is written to the repository).
    """
    def private_tmp(self):
        """Make an empty directory and the variables that send tempfile there.

        Returns:
            A tuple (directory, environment additions for TMPDIR, TEMP and TMP).
        """
        tmp = self.sb.mkdir("private-tmp")
        # tempfile reads TMPDIR on POSIX and TEMP or TMP on Windows: set all three.
        return tmp, {"TMPDIR": tmp, "TEMP": tmp, "TMP": tmp}

    def test_removed_after_success_conflict_and_error(self):
        """SPEC Merge mechanism: the temporary object directory (tempfile.mkdtemp) is removed on
        normal exit and on error: TMPDIR/TEMP/TMP point at a private dir that stays empty."""
        repo, _ = self.pair_repo()
        tmp, extra = self.private_tmp()
        attrs = os.path.join(self.sb.tmp, "attrs")
        with open(attrs, "wb") as fh:
            fh.write(b"c.txt merge=union\n")
        # Clean, conflict, --attributes, unknown ref, malformed record, unknown ref again.
        for args, stdin, code in (([], b"a\td\n", 0), ([], b"a\tb\n", 1),
                                  (["--attributes", attrs], b"a\tb\n", 1),
                                  ([], b"a\tnope\n", 2), ([], b"a\n", 2),
                                  ([], b"a\torphan-missing\n", 2)):
            proc = repo.run("pairmerge", args, stdin=stdin, env_extra=extra)
            self.assertEqual(proc.returncode, code, (args, stdin, proc.stderr))
            self.assertEqual(os.listdir(tmp), [], (args, stdin))

    @unittest.skipUnless(POSIX, "signals are Unix-only here")
    def test_removed_on_signal_while_blocked_on_stdin(self):
        """SPEC Merge mechanism: the temporary directory is removed on SIGINT, SIGTERM and SIGHUP,
        and the exit status is 128 + signal number (sent while blocked on stdin)."""
        repo, _ = self.pair_repo()
        # A fresh temp root per signal, so a leftover is pinned on the right one.
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            tmp = os.path.join(self.sb.tmp, "tmp-%d" % sig)
            os.mkdir(tmp)
            proc = subprocess.Popen(self.argv("pairmerge", []), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=repo.path,
                                    env=self.env({"TMPDIR": tmp, "TEMP": tmp, "TMP": tmp}))
            # Give the interpreter time to start and install its handlers.
            time.sleep(1.0)
            deadline = time.time() + 4
            # Wait until the temporary directory exists, i.e. the command is past set-up.
            while time.time() < deadline and not os.listdir(tmp) and proc.poll() is None:
                time.sleep(0.05)
            # Let it reach the blocking read of stdin before the signal arrives.
            time.sleep(0.3)
            proc.send_signal(sig)
            try:
                code = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                # A hang means the signal was not handled; the exit status assertion then fails.
                code = proc.wait()
            proc.stdin.close()
            err = proc.stderr.read()
            proc.stdout.close()
            proc.stderr.close()
            self.assertEqual(code, 128 + int(sig), (sig, err))
            self.assertNotIn(b"Traceback", err)
            self.assertEqual(os.listdir(tmp), [], sig)

    def test_removed_after_early_closing_reader(self):
        """SPEC Standard streams + Merge mechanism: a reader closing the pipe early (EPIPE) is a
        quiet exit 0, and the private temporary object directory is still removed."""
        repo = Repo(self.sb)
        # Enough conflicts to overflow the pipe buffer, so the write after the close fails.
        names = ["dir/file-%05d.txt" % i for i in range(20000)]
        base = repo.commit(None, dict((n, "base\n") for n in names), ref="refs/heads/main")
        repo.commit(base, dict((n, "p\n") for n in names), ref="refs/heads/p")
        repo.commit(base, dict((n, "q\n") for n in names), ref="refs/heads/q")
        tmp, extra = self.private_tmp()
        proc = subprocess.Popen(self.argv("pairmerge", []), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=repo.path, env=self.env(extra))
        proc.stdin.write(b"p\tq\n")
        proc.stdin.close()
        # Read a little, then close: the command is mid-output when the pipe goes.
        first = proc.stdout.read(16)
        proc.stdout.close()
        err = proc.stderr.read()
        proc.stderr.close()
        code = proc.wait(timeout=120)
        self.assertTrue(first)
        self.assertEqual(err, b"")
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(tmp), [])


if __name__ == "__main__":
    unittest.main()
