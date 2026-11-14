# SPDX-License-Identifier: Apache-2.0
"""Every ```sh example in README.md outside "## Recipes" runs, and its output is exact.

SPEC: "Recipes".

The README's fence rules, enforced by test_fences:

  ```sh     an example. Outside "## Recipes" it must be followed, with only blank lines between,
            by a ```text block holding its exact standard output. (Recipes keep their expected
            output in test_recipes.py.)
  ```text   expected output; only directly after a ```sh block outside Recipes.
  ```       not executed: install commands, syntax.

Any other info string (```bash, ```console, ...), an indented fence or an unclosed fence fails,
so no shell example can escape the check.

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
from test_recipes import README, SH, build_fixture, write_wrapper

# The only fence info strings the README may use (see the module docstring).
FENCE_INFOS = ("", "sh", "text")

# pr-merge-audit snapshot refs in the bundle: name -> fixture branch it points at.
AUDIT_REFS = (("refs/audit/base", "main"), ("refs/audit/pr/12", "agent/a"),
              ("refs/audit/pr/15", "agent/c"), ("refs/audit/pr/17", "agent/e"))


def readme_fences():
    """Split README.md into its fenced blocks.

    Returns:
        (fences, lines): `lines` is the README split on LF; `fences` is a list of dicts, in
        order, with keys info (the text after ```), section (the nearest "## " heading above,
        or None), start and end (1-based line numbers of the opening and closing fence) and
        body (the lines between, each ending in LF).

    Raises:
        AssertionError: a fence is indented or never closed.
    """
    with open(README, "r", encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    fences, section, open_fence = [], None, None
    for number, line in enumerate(lines, 1):
        if open_fence is not None:
            # Inside a fence: collect lines until the closing ```.
            if line.startswith("```"):
                body = open_fence.pop("lines")
                open_fence.update(end=number, body="".join(text + "\n" for text in body))
                fences.append(open_fence)
                open_fence = None
            else:
                open_fence["lines"].append(line)
            continue
        # An indented fence (e.g. inside a list item) would slip past the checks below.
        if line.lstrip().startswith("```") and not line.startswith("```"):
            raise AssertionError("README line %d: indented fence; examples must start in "
                                 "column 1 to be checked" % number)
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith("```"):
            open_fence = {"info": line[3:].strip(), "section": section, "start": number,
                          "lines": []}
    if open_fence is not None:
        raise AssertionError("README line %d: fence never closed" % open_fence["start"])
    return fences, lines


def adjacent(first, second, lines):
    """Tell whether only blank lines separate two fences.

    Args:
        first: the earlier fence dict from readme_fences.
        second: the later fence dict.
        lines: the README lines from readme_fences.

    Returns:
        True if every line between first's closing fence and second's opening fence is blank.
    """
    # Line numbers are 1-based: lines[end] is the line after the closing fence.
    between = lines[first["end"]:second["start"] - 1]
    return all(line.strip() == "" for line in between)


def readme_examples():
    """Collect the README examples outside Recipes, checking the fence rules on the way.

    Returns:
        [(line number, sh source, expected stdout)], one per ```sh block outside Recipes.

    Raises:
        AssertionError: the README breaks a fence rule of the module docstring.
    """
    fences, lines = readme_fences()
    examples = []
    for index, fence in enumerate(fences):
        where = "README line %d" % fence["start"]
        if fence["info"] not in FENCE_INFOS:
            raise AssertionError("%s: fence ```%s; use ```sh (run and checked), ```text "
                                 "(its output) or ``` (not run)" % (where, fence["info"]))
        in_recipes = fence["section"] == "Recipes"
        # Shown output must belong to an example, or it is a claim no test checks.
        if fence["info"] == "text":
            previous = fences[index - 1] if index else None
            if in_recipes or previous is None or previous["info"] != "sh" \
                    or not adjacent(previous, fence, lines):
                raise AssertionError("%s: a ```text block must directly follow a ```sh example "
                                     "outside Recipes" % where)
        # Recipes are run by test_recipes.py, against its own expected output.
        if fence["info"] != "sh" or in_recipes:
            continue
        following = fences[index + 1] if index + 1 < len(fences) else None
        if following is None or following["info"] != "text" \
                or not adjacent(fence, following, lines):
            raise AssertionError("%s: ```sh example without its expected output (a ```text "
                                 "block right after it)" % where)
        examples.append((fence["start"], fence["body"], following["body"]))
    return examples


def add_audit_bundle(sandbox, repo):
    """Write sandbox/snapshots/2026-11-13.bundle the way pr-merge-audit's snapshot lays it out.

    The bundle holds refs/audit/base and refs/audit/pr/N (AUDIT_REFS). The refs are created only
    to build it and deleted again, so the README's `git fetch` is what brings them in.

    Args:
        sandbox: the Sandbox holding the fixture repository.
        repo: the fixture Repo, whose directory is sandbox/repo.
    """
    # pr-merge-audit keeps snapshots in snapshots/DATE.bundle; the README fetches ../snapshots/.
    os.mkdir(os.path.join(sandbox.tmp, "snapshots"))
    for ref, branch in AUDIT_REFS:
        repo.set_ref(ref, repo.rev(branch))
    bundle = os.path.join(sandbox.tmp, "snapshots", "2026-11-13.bundle")
    repo.git("bundle", "create", "-q", bundle, *[ref for ref, _ in AUDIT_REFS])
    for ref, _ in AUDIT_REFS:
        repo.git("update-ref", "-d", ref)


class FenceRulesTest(unittest.TestCase):
    """The README's fences follow the module docstring's rules; needs no sh, so runs everywhere."""

    def test_fences(self):
        """Acceptance: "a block without an expected output fails the suite"."""
        examples = readme_examples()
        self.assertTrue(examples, "README has no ```sh examples outside Recipes")


@unittest.skipUnless(SH, "the README examples are POSIX sh and no `sh` is on PATH")
class ReadmeExamplesTest(unittest.TestCase):
    """Each README example's stdout equals the ```text block after it, and stderr is empty."""

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
        for line, source, expected in readme_examples():
            with self.subTest(readme_line=line):
                if "pairmerge" in source and not PAIRMERGE_OK:
                    self.skipTest("git pairmerge needs git >= 2.43")
                proc = subprocess.run([SH, "-c", source], cwd=self.repo.path, env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      timeout=300)
                # Anything on stderr means a command in the example failed unseen.
                self.assertEqual(proc.stderr, b"", "example at README line %d wrote to stderr"
                                 % line)
                self.assertEqual(proc.stdout.decode("utf-8"), expected)


if __name__ == "__main__":
    unittest.main()
