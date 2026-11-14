# SPDX-License-Identifier: Apache-2.0
"""Every ```sh example in README.md and docs/*.md outside a Recipes section runs, exactly.

Except docs/use-cases.md (NOT_RUN), whose commands are checked against its reproduce script.

SPEC: "Recipes".

The fence rules for README.md and every docs/*.md page, enforced by test_fences:

  ```sh     an example. Outside a Recipes section it must be followed, with only blank lines
            between, by a ```text block holding its exact standard output. (Recipes keep their
            expected output in test_recipes.py, which also defines a Recipes section.)
  ```text   expected output; only directly after a ```sh block outside Recipes.
  ```       not executed: install commands, syntax.

Any other info string (```bash, ```console, ...), an indented fence or an unclosed fence fails,
so no shell example on any page can escape the check.

The examples run with `sh -c` in the fixture repository of test_recipes.py (main and agent/a..e)
plus:

  odd                                a branch, outside refs/heads/agent/, adding a file whose
                                     name holds a TAB (the -z example)
  ../snapshots/2026-11-13.bundle     a snapshot bundle in pr-merge-audit's layout
                                     (refs/audit/base, refs/audit/pr/N), whose refs are not in
                                     the repository until an example fetches them

The commands resolve through the same `git-<name>` wrappers, so `git footprint` runs the code of
this checkout. Everything is real git; nothing is mocked.
"""

import os
import subprocess
import unittest

from fixtures import PAIRMERGE_OK, Sandbox
from test_recipes import ROOT, SH, build_fixture, doc_fences, doc_files, write_wrapper

# The only fence info strings the docs may use (see the module docstring).
FENCE_INFOS = ("", "sh", "text")

# Pages whose examples are not run here. docs/use-cases.md records real runs on public GitHub
# repositories: its commands need the network and minutes per run, and its output depends on
# those repositories, so scripts/reproduce-use-cases.sh re-runs it by hand instead, and
# UseCasesScriptTest keeps the page's commands identical to that script's.
NOT_RUN = ("docs/use-cases.md",)
USE_CASES_SCRIPT = "scripts/reproduce-use-cases.sh"

# pr-merge-audit snapshot refs in the bundle: name -> fixture branch it points at.
AUDIT_REFS = (("refs/audit/base", "main"), ("refs/audit/pr/12", "agent/a"),
              ("refs/audit/pr/15", "agent/c"), ("refs/audit/pr/17", "agent/e"))


def adjacent(first, second, lines):
    """Tell whether only blank lines separate two fences.

    Args:
        first: the earlier fence dict from doc_fences.
        second: the later fence dict.
        lines: the file's lines from doc_fences.

    Returns:
        True if every line between first's closing fence and second's opening fence is blank.
    """
    # Line numbers are 1-based: lines[end] is the line after the closing fence.
    between = lines[first["end"]:second["start"] - 1]
    return all(line.strip() == "" for line in between)


def doc_examples():
    """Collect the examples of README.md and docs/*.md outside Recipes, checking the fence rules.

    Returns:
        [(file, line number, sh source, expected stdout)], one per ```sh block outside Recipes,
        file relative to ROOT.

    Raises:
        AssertionError: a page breaks a fence rule of the module docstring.
    """
    examples = []
    for relpath in doc_files():
        if relpath in NOT_RUN:
            continue
        fences, lines = doc_fences(relpath)
        for index, fence in enumerate(fences):
            where = "%s line %d" % (relpath, fence["start"])
            if fence["info"] not in FENCE_INFOS:
                raise AssertionError("%s: fence ```%s; use ```sh (run and checked), ```text "
                                     "(its output) or ``` (not run)" % (where, fence["info"]))
            in_recipes = fence["recipe"] is not None
            # Shown output must belong to an example, or it is a claim no test checks.
            if fence["info"] == "text":
                previous = fences[index - 1] if index else None
                if in_recipes or previous is None or previous["info"] != "sh" \
                        or not adjacent(previous, fence, lines):
                    raise AssertionError("%s: a ```text block must directly follow a ```sh "
                                         "example outside Recipes" % where)
            # Recipes are run by test_recipes.py, against its own expected output.
            if fence["info"] != "sh" or in_recipes:
                continue
            following = fences[index + 1] if index + 1 < len(fences) else None
            if following is None or following["info"] != "text" \
                    or not adjacent(fence, following, lines):
                raise AssertionError("%s: ```sh example without its expected output (a ```text "
                                     "block right after it)" % where)
            examples.append((relpath, fence["start"], fence["body"], following["body"]))
    return examples


def add_audit_bundle(sandbox, repo):
    """Write sandbox/snapshots/2026-11-13.bundle the way pr-merge-audit's snapshot lays it out.

    The bundle holds refs/audit/base and refs/audit/pr/N (AUDIT_REFS). The refs are created only
    to build it and deleted again, so the docs' `git fetch` is what brings them in.

    Args:
        sandbox: the Sandbox holding the fixture repository.
        repo: the fixture Repo, whose directory is sandbox/repo.
    """
    # pr-merge-audit keeps snapshots in snapshots/DATE.bundle; the docs fetch ../snapshots/.
    os.mkdir(os.path.join(sandbox.tmp, "snapshots"))
    for ref, branch in AUDIT_REFS:
        repo.set_ref(ref, repo.rev(branch))
    bundle = os.path.join(sandbox.tmp, "snapshots", "2026-11-13.bundle")
    repo.git("bundle", "create", "-q", bundle, *[ref for ref, _ in AUDIT_REFS])
    for ref, _ in AUDIT_REFS:
        repo.git("update-ref", "-d", ref)


class FenceRulesTest(unittest.TestCase):
    """Every page's fences follow the module docstring's rules; needs no sh, so runs everywhere."""

    def test_fences(self):
        """Acceptance: "a block without an expected output fails the suite", on any page."""
        examples = doc_examples()
        self.assertTrue(examples, "the docs have no ```sh examples outside Recipes")
        # The README keeps a checked example of its own (the install check and the quick start).
        self.assertIn("README.md", [relpath for relpath, _, _, _ in examples])


class UseCasesScriptTest(unittest.TestCase):
    """docs/use-cases.md and scripts/reproduce-use-cases.sh cannot drift apart."""

    def test_every_command_is_in_the_script(self):
        """Every ```sh block of each NOT_RUN page appears verbatim in the reproduce script, so
        re-running the script re-runs exactly the commands the page shows."""
        with open(os.path.join(ROOT, *USE_CASES_SCRIPT.split("/")), "r",
                  encoding="utf-8") as handle:
            script = handle.read()
        for relpath in NOT_RUN:
            fences, _ = doc_fences(relpath)
            blocks = [fence for fence in fences if fence["info"] == "sh"]
            self.assertTrue(blocks, "%s has no ```sh blocks" % relpath)
            for fence in blocks:
                with self.subTest(file=relpath, line=fence["start"]):
                    self.assertIn(fence["body"], script, "%s line %d: command not in %s"
                                  % (relpath, fence["start"], USE_CASES_SCRIPT))


@unittest.skipUnless(SH, "the documented examples are POSIX sh and no `sh` is on PATH")
class ReadmeExamplesTest(unittest.TestCase):
    """Each documented example's stdout equals the ```text block after it, and stderr is empty."""

    @classmethod
    def setUpClass(cls):
        """Build one hermetic sandbox: the recipes' fixture, branch `odd`, the snapshot bundle
        and the git-<name> wrappers."""
        cls.sb = Sandbox()
        cls.repo = build_fixture(cls.sb)
        # A TAB in a path: only -z can carry it (SPEC: "Unsafe names").
        cls.repo.commit(cls.repo.rev("main"), {"notes\tdraft.txt": "draft\n"},
                        ref="refs/heads/odd")
        add_audit_bundle(cls.sb, cls.repo)
        cls.bin = cls.sb.mkdir("bin")
        for name in ("footprint", "overlap", "pairmerge"):
            write_wrapper(cls.bin, name, name)

    @classmethod
    def tearDownClass(cls):
        """Remove the sandbox and everything in it."""
        cls.sb.cleanup()

    def test_examples(self):
        """Run every example with `sh -c` in the fixture; pairmerge ones need git >= 2.43.

        SPEC: "Recipes".
        """
        # The sandbox environment, with the git-<name> wrappers found first on PATH.
        env = dict(self.sb.env)
        env["PATH"] = self.bin + os.pathsep + env.get("PATH", "")
        env["TMPDIR"] = self.sb.tmp            # mktemp files land in the sandbox
        for relpath, line, source, expected in doc_examples():
            with self.subTest(file=relpath, line=line):
                if "pairmerge" in source and not PAIRMERGE_OK:
                    self.skipTest("git pairmerge needs git >= 2.43")
                proc = subprocess.run([SH, "-c", source], cwd=self.repo.path, env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      timeout=300)
                # Anything on stderr means a command in the example failed unseen.
                self.assertEqual(proc.stderr, b"", "example at %s line %d wrote to stderr"
                                 % (relpath, line))
                self.assertEqual(proc.stdout.decode("utf-8"), expected)


if __name__ == "__main__":
    unittest.main()
