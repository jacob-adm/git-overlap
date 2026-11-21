# SPDX-License-Identifier: Apache-2.0
"""--json on all three commands: the same records as the default output, as a JSON array.

SPEC: "JSON output".
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from fixtures import needs_pairmerge_git

# The project root, put on PYTHONPATH so `python -m git_overlap.<name>` finds the package.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = dict(os.environ, PYTHONPATH=ROOT, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
           GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@localhost",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@localhost")


def git(repo, *args, data=None):
    """Run git in REPO; return stdout bytes, failing loudly on error."""
    return subprocess.run(["git"] + list(args), cwd=repo, env=ENV, input=data, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def tool(name, repo, args=(), data=b""):
    """Run one command in REPO; return (exit code, stdout, stderr) as bytes."""
    proc = subprocess.run([sys.executable, "-m", "git_overlap." + name] + list(args), cwd=repo,
                          env=ENV, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout, proc.stderr


def as_rows(out, keys):
    """Turn LF records into the objects --json should write for them."""
    return [dict(zip(keys, line.split(b"\t"))) for line in out.splitlines()]


def decoded(rows):
    """Decode every field the way --json does."""
    return [{k: v.decode("utf-8", "surrogateescape") for k, v in row.items()} for row in rows]


class JsonTest(unittest.TestCase):
    """main with a.txt; b1 and b2 edit the same line of it; b3 adds c.txt."""

    def setUp(self):
        """Build the four branches in a temporary repository."""
        # Every test gets its own repository, removed afterwards.
        self.repo = tempfile.mkdtemp(prefix="git-overlap-json-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q", "-b", "main")
        self.commit("main", {"a.txt": b"one\n"})
        # b1 and b2 change the same line, so they conflict; b3 touches nothing they touch.
        for branch, files in (("b1", {"a.txt": b"left\n"}), ("b2", {"a.txt": b"right\n"}),
                              ("b3", {"c.txt": b"new\n"})):
            git(self.repo, "checkout", "-q", "-b", branch, "main")
            self.commit(branch, files)
        git(self.repo, "checkout", "-q", "main")

    def commit(self, message, files):
        """Write FILES ({name: bytes}) and commit them on the current branch."""
        for name, content in files.items():
            with open(os.path.join(self.repo, name), "wb") as handle:
                handle.write(content)
            git(self.repo, "add", "--", name)
        git(self.repo, "commit", "-q", "-m", message)

    def check(self, name, args, data, keys):
        """Run NAME with and without --json; the JSON must hold the same records."""
        # The default output is the reference the JSON is compared with.
        code, out, err = tool(name, self.repo, args, data)
        jcode, jout, jerr = tool(name, self.repo, ["--json"] + list(args), data)
        self.assertEqual((jcode, jerr), (code, err))
        self.assertEqual(json.loads(jout), decoded(as_rows(out, keys)))
        self.assertTrue(jout.endswith(b"]\n"))
        # ASCII only: anything else is escaped, so the output is safe on a terminal.
        jout.decode("ascii")
        return jcode, json.loads(jout)

    def test_footprint(self):
        """footprint --json: one object per changed file."""
        code, rows = self.check("footprint", ["main"], b"b1\nb3\n", ("ref", "status", "path"))
        self.assertEqual(code, 0)
        self.assertEqual(rows, [{"ref": "b1", "status": "M", "path": "a.txt"},
                                {"ref": "b3", "status": "A", "path": "c.txt"}])

    def test_overlap_reads_records_and_writes_json(self):
        """overlap reads footprint records as before and writes the pairs as JSON."""
        # --json changes output only, so the footprint feeding it stays TAB-separated.
        _, footprint, _ = tool("footprint", self.repo, ["main"], b"b1\nb2\nb3\n")
        code, rows = self.check("overlap", [], footprint, ("ref_a", "ref_b", "path"))
        self.assertEqual(code, 1)
        self.assertEqual(rows, [{"ref_a": "b1", "ref_b": "b2", "path": "a.txt"}])

    def test_nothing_found_is_an_empty_array(self):
        """No pair: [] and exit 0."""
        _, footprint, _ = tool("footprint", self.repo, ["main"], b"b1\nb3\n")
        self.assertEqual(tool("overlap", self.repo, ["--json"], footprint), (0, b"[]\n", b""))

    @needs_pairmerge_git
    def test_pairmerge(self):
        """pairmerge --json: conflict records and a clean record with an empty path."""
        code, rows = self.check("pairmerge", [], b"b1\tb2\nb1\tb3\n",
                                ("ref_a", "ref_b", "result", "path"))
        self.assertEqual(code, 1)
        self.assertEqual(rows, [{"ref_a": "b1", "ref_b": "b2", "result": "conflict",
                                 "path": "a.txt"},
                                {"ref_a": "b1", "ref_b": "b3", "result": "clean", "path": ""}])

    def test_name_that_is_not_utf8_is_recoverable(self):
        """A path that is not UTF-8 decodes with surrogateescape and encodes back exactly."""
        # The blob is written once; only its name in the index is unusual.
        oid = git(self.repo, "hash-object", "-w", "--stdin", data=b"x\n").strip()
        git(self.repo, "checkout", "-q", "-b", "raw", "main")
        # Through stdin, not argv: a Windows command line cannot carry bytes that are not UTF-8.
        git(self.repo, "update-index", "-z", "--index-info",
            data=b"100644 " + oid + b"\t\xff.txt\0")
        git(self.repo, "commit", "-q", "-m", "raw")
        git(self.repo, "checkout", "-q", "-f", "main")
        code, out, err = tool("footprint", self.repo, ["--json", "main"], b"raw\n")
        self.assertEqual((code, err), (0, b""))
        path = json.loads(out)[0]["path"]
        self.assertEqual(path.encode("utf-8", "surrogateescape"), b"\xff.txt")

    def test_error_writes_nothing(self):
        """On exit 2 --json writes nothing, though records came before the error."""
        # An orphan branch has no merge base with main, which fails in the second pass.
        git(self.repo, "checkout", "-q", "--orphan", "lone")
        self.commit("lone", {"z.txt": b"z\n"})
        git(self.repo, "checkout", "-q", "-f", "main")
        # Without --json, b1's records are written before lone fails (SPEC "Exit status").
        code, out, _ = tool("footprint", self.repo, ["main"], b"b1\nlone\n")
        self.assertEqual((code, out), (2, b"b1\tM\ta.txt\n"))
        # With --json the array is never started, so stdout stays empty.
        code, out, err = tool("footprint", self.repo, ["--json", "main"], b"b1\nlone\n")
        self.assertEqual((code, out), (2, b""))
        self.assertIn(b"no merge base", err)


if __name__ == "__main__":
    unittest.main()
