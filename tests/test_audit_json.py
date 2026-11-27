# SPDX-License-Identifier: Apache-2.0
"""--audit-json on git overlap and git pairmerge: one audit result object per run.

SPEC: "Audit result output".
"""

import json
import os
import unittest

from fixtures import needs_pairmerge_git
from test_json import ROOT, JsonTest, git, tool

# The version the command reports, read the way a user would.
__version__ = tool("overlap", ROOT, ["--version"])[1].split()[-1].decode("ascii")

TOP = {"format", "tool", "target", "exit", "findings", "errors"}
FINDING = {"rule", "severity", "message", "evidence", "record"}


class AuditJsonTest(unittest.TestCase):
    """The JsonTest repository: b1 and b2 edit the same line of a.txt; b3 adds c.txt."""

    # The same repository as test_json, without running its cases a second time.
    setUp = JsonTest.setUp
    commit = JsonTest.commit

    def audit(self, name, args, data):
        """Run NAME with --audit-json; check the format's rules and return (code, doc, err)."""
        code, out, err = tool(name, self.repo, ["--audit-json"] + list(args), data)
        doc = json.loads(out)
        # Every key the format names, and no other.
        self.assertEqual(set(doc), TOP)
        self.assertEqual(doc["format"], "audit-result/1")
        self.assertEqual(doc["tool"], {"name": "git-overlap", "version": __version__})
        self.assertEqual(doc["target"]["name"], os.path.basename(self.repo))
        for finding in doc["findings"]:
            self.assertEqual(set(finding), FINDING)
            self.assertTrue(finding["evidence"])
        # The exit status follows from the errors and the findings above info.
        found = any(f["severity"] != "info" for f in doc["findings"])
        want = 2 if doc["errors"] else (1 if found else 0)
        self.assertEqual((doc["exit"], code), (want, want))
        # ASCII only, as --json is.
        out.decode("ascii")
        return code, doc, err

    # Footprint records are the input overlap reads; the flag never changes them.
    def footprint(self, refs):
        """The footprint records of REFS against main, TAB-separated."""
        return tool("footprint", self.repo, ["main"], refs)[1]

    def test_overlap(self):
        """Each pair is a low finding naming both refs and the path."""
        data = self.footprint(b"b1\nb2\nb3\n")
        jcode, jout, jerr = tool("overlap", self.repo, ["--json"], data)
        code, doc, err = self.audit("overlap", [], data)
        # The records are the --json records, unchanged, and nothing else changes.
        self.assertEqual([f["record"] for f in doc["findings"]], json.loads(jout))
        self.assertEqual((code, err), (jcode, jerr))
        self.assertEqual(doc["findings"][0]["rule"], "git-overlap.overlap")
        self.assertEqual(doc["findings"][0]["severity"], "low")
        self.assertEqual(doc["findings"][0]["message"], "b1 and b2 both change a.txt")
        self.assertEqual(doc["findings"][0]["evidence"], {"path": "a.txt", "ref": "b1"})

    def test_overlap_with_ref(self):
        """--with REF is the target's ref and the evidence's ref."""
        # b2 is the branch being checked against all the others.
        code, doc, _ = self.audit("overlap", ["--with", "b2"], self.footprint(b"b1\nb2\n"))
        self.assertEqual(doc["target"]["ref"], "b2")
        self.assertEqual(doc["findings"][0]["evidence"]["ref"], "b2")

    def test_overlap_nothing_found(self):
        """No pair: no findings and exit 0."""
        code, doc, err = self.audit("overlap", [], self.footprint(b"b1\nb3\n"))
        self.assertEqual((code, doc["findings"], doc["errors"], err), (0, [], [], b""))

    def test_default_and_json_output_unchanged(self):
        """The flag changes nothing for the other modes."""
        data = self.footprint(b"b1\nb2\n")
        self.assertEqual(tool("overlap", self.repo, [], data), (1, b"b1\tb2\ta.txt\n", b""))
        self.assertNotIn(b"severity", tool("overlap", self.repo, ["--json"], data)[1])

    def test_bad_record_still_writes_the_object(self):
        """A malformed record is the object's one error, with the same stderr line."""
        # A record with no TAB is not a footprint record.
        plain = tool("overlap", self.repo, [], b"nofields\n")
        code, doc, err = self.audit("overlap", [], b"nofields\n")
        self.assertEqual((code, err), (2, plain[2]))
        self.assertEqual(doc["findings"], [])
        self.assertTrue(doc["errors"][0]["message"].startswith("record 1: "))

    def test_json_together_is_a_usage_error(self):
        """--json with --audit-json is an error written as the object."""
        for name in ("overlap", "pairmerge"):
            with self.subTest(name):
                _, doc, _ = self.audit(name, ["--json"], b"")
                self.assertEqual(doc["errors"],
                                 [{"message": "--json and --audit-json cannot be used together"}])

    def test_help_writes_no_object(self):
        """-h prints the usage and exits 0, as without the flag."""
        code, out, _ = tool("overlap", self.repo, ["--audit-json", "-h"], b"")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(b"usage: git overlap"))

    @needs_pairmerge_git
    def test_pairmerge(self):
        """A conflict is high; a clean pair is listed as info and does not set exit 1."""
        # One conflicting pair and one clean pair.
        data = b"b1\tb2\nb1\tb3\n"
        jcode, jout, _ = tool("pairmerge", self.repo, ["--json"], data)
        code, doc, _ = self.audit("pairmerge", [], data)
        self.assertEqual([f["record"] for f in doc["findings"]], json.loads(jout))
        self.assertEqual(code, jcode)
        got = [(f["rule"], f["severity"], f["message"], f["evidence"]) for f in doc["findings"]]
        self.assertEqual(got, [
            ("git-overlap.pairmerge.conflict", "high", "b1 and b2 conflict in a.txt",
             {"path": "a.txt", "ref": "b1"}),
            ("git-overlap.pairmerge.clean", "info", "b1 and b3 merge cleanly", {"ref": "b1"})])
        # Only clean pairs: exit 0.
        code, doc, _ = self.audit("pairmerge", [], b"b1\tb3\n")
        self.assertEqual(code, 0)

    @needs_pairmerge_git
    def test_pairmerge_onto(self):
        """--onto BASE: base in the target; a stale pair is high and names BASE."""
        # main moves on and changes the line b1 changed, so b1 no longer merges into main.
        self.commit("main moves", {"a.txt": b"main\n"})
        code, doc, _ = self.audit("pairmerge", ["--onto", "main"], b"b1\tb3\nb2\tb3\n")
        self.assertEqual(doc["target"]["base"], "main")
        messages = [(f["severity"], f["message"]) for f in doc["findings"]]
        self.assertIn(("high", "b1 or b3 no longer merges into main: conflict in a.txt"),
                      messages)
        self.assertEqual(code, 1)

    @needs_pairmerge_git
    def test_pairmerge_unknown_ref(self):
        """An unknown ref is the object's one error."""
        # The message is the one git pairmerge writes without the flag.
        code, doc, err = self.audit("pairmerge", [], b"b1\tnosuch\n")
        self.assertEqual(doc["errors"],
                         [{"message": "record 1: unknown ref or not a commit: nosuch"}])
        self.assertEqual(err, b"git-pairmerge: record 1: unknown ref or not a commit: nosuch\n")

    @needs_pairmerge_git
    def test_documented_example_is_real_output(self):
        """The example in docs/commands.md is what the command writes."""
        _, out, _ = tool("pairmerge", self.repo, ["--audit-json"], b"b1\tb2\nb1\tb3\n")
        got = json.loads(out)
        # The page names the repository directory demo and shows the version the flag ships in.
        got["target"]["name"] = "demo"
        with open(os.path.join(ROOT, "docs", "commands.md"), encoding="utf-8") as handle:
            text = handle.read()
        block = text.split("## Audit result output", 1)[1].split("```\n", 1)[1]
        documented = json.loads(block.split("```", 1)[0])
        documented["tool"]["version"] = got["tool"]["version"]
        self.assertEqual(documented, got)

if __name__ == "__main__":
    unittest.main()
