# SPDX-License-Identifier: Apache-2.0
"""HNET-216: every ```sh block under "## Recipes" in README.md is executed and its stdout checked.

The fixture repository reproduces the night described by Xuchen Yao, "Rethinking Merging and
Continous Integration for Agentic Workflows" (4 November 2026): five agent branches off `main`,
most of which add to the same shared files, one file of each typed-path kind:

  tools/registry.py   set       a Python list, one entry per line (with trailing comma)
  CHANGELOG.md        log       each branch adds a line under "## Unreleased"
  openapi.json        derived   generated; each branch regenerates it with its own endpoint
  migrations/NNNN_*   sequence  numbered files; two branches both pick 0002

Branches (all from one `main` commit):

  agent/a  registry +fetch, CHANGELOG, openapi +/fetch, src/app.py TIMEOUT=20, adds src/fetch.py
  agent/b  registry +lint,  CHANGELOG, openapi +/lint,  src/app.py TIMEOUT=30, adds src/lint.py
  agent/c  registry +users, CHANGELOG, adds migrations/0002_users.sql
  agent/d  CHANGELOG, openapi +/orders, adds migrations/0002_orders.sql
  agent/e  adds docs/guide.md only (touches nothing shared)

so that: CHANGELOG conflicts in 6 pairs, openapi.json and the registry in 3 each, src/app.py in 1
(the one real conflict left after union attributes and the derived filter); p = 6/10; c and d
collide on migration 0002 although git merges them cleanly; agent/e is clear for the pre-work
check while agent/a collides.

The commands run as `python -m git_overlap.<name>` through small `git-<name>` sh wrappers in a
temporary bin directory prepended to PATH, so `git footprint` resolves exactly as it would for an
installed console script. Everything is real git; nothing is mocked.
"""

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from fixtures import PAIRMERGE_OK, Repo, Sandbox

# This checkout's root (not fixtures.PACKAGE_ROOT, which defaults to one fixed path): the code
# under test is the code next to this README.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")

# `sh` runs the recipes. On Windows it exists only under Git Bash (or similar); without it the
# recipe tests are skipped rather than failed, since the recipes are documented as POSIX sh.
SH = shutil.which("sh")
needs_sh = unittest.skipUnless(SH, "the recipes are POSIX sh and no `sh` is on PATH")

T = "\t"


def lines(*records):
    """Join TAB-separated records into the exact stdout text a recipe must print."""
    return "".join(T.join(fields) + "\n" for fields in records)


# --- Expected output of every recipe, keyed by the NAME of its "### NAME: question?" heading. ---
# A recipe in the README without an entry here (or an entry without a recipe) fails
# test_every_recipe_is_checked, so no documented command can go unchecked.

FOOTPRINT = lines(
    ("agent/a", "M", "CHANGELOG.md"), ("agent/a", "M", "openapi.json"),
    ("agent/a", "M", "src/app.py"), ("agent/a", "A", "src/fetch.py"),
    ("agent/a", "M", "tools/registry.py"),
    ("agent/b", "M", "CHANGELOG.md"), ("agent/b", "M", "openapi.json"),
    ("agent/b", "M", "src/app.py"), ("agent/b", "A", "src/lint.py"),
    ("agent/b", "M", "tools/registry.py"),
    ("agent/c", "M", "CHANGELOG.md"), ("agent/c", "A", "migrations/0002_users.sql"),
    ("agent/c", "M", "tools/registry.py"),
    ("agent/d", "M", "CHANGELOG.md"), ("agent/d", "A", "migrations/0002_orders.sql"),
    ("agent/d", "M", "openapi.json"),
    ("agent/e", "A", "docs/guide.md"),
)

# Every pair among a..d shares CHANGELOG.md; agent/e shares nothing.
OVERLAP_PAIRS = lines(
    ("agent/a", "agent/b"), ("agent/a", "agent/c"), ("agent/a", "agent/d"),
    ("agent/b", "agent/c"), ("agent/b", "agent/d"), ("agent/c", "agent/d"),
)

# git's plain line merge: each pair conflicts on every shared file (all edits hit the same lines);
# the two migrations 0002 are different files, so c/d conflicts on CHANGELOG.md only.
REAL_CONFLICTS = lines(
    ("agent/a", "agent/b", "conflict", "CHANGELOG.md"),
    ("agent/a", "agent/b", "conflict", "openapi.json"),
    ("agent/a", "agent/b", "conflict", "src/app.py"),
    ("agent/a", "agent/b", "conflict", "tools/registry.py"),
    ("agent/a", "agent/c", "conflict", "CHANGELOG.md"),
    ("agent/a", "agent/c", "conflict", "tools/registry.py"),
    ("agent/a", "agent/d", "conflict", "CHANGELOG.md"),
    ("agent/a", "agent/d", "conflict", "openapi.json"),
    ("agent/b", "agent/c", "conflict", "CHANGELOG.md"),
    ("agent/b", "agent/c", "conflict", "tools/registry.py"),
    ("agent/b", "agent/d", "conflict", "CHANGELOG.md"),
    ("agent/b", "agent/d", "conflict", "openapi.json"),
    ("agent/c", "agent/d", "conflict", "CHANGELOG.md"),
)

# Ranked by pair count; the tie between openapi.json and tools/registry.py breaks bytewise.
HOTSPOTS = lines(("6", "CHANGELOG.md"), ("3", "openapi.json"), ("3", "tools/registry.py"),
                 ("1", "src/app.py"))

# With merge=union on the set and the log, only openapi.json (derived) and src/app.py remain.
WHAT_IF = lines(
    ("agent/a", "agent/b", "conflict", "openapi.json"),
    ("agent/a", "agent/b", "conflict", "src/app.py"),
    ("agent/a", "agent/c", "clean", ""),
    ("agent/a", "agent/d", "conflict", "openapi.json"),
    ("agent/b", "agent/c", "clean", ""),
    ("agent/b", "agent/d", "conflict", "openapi.json"),
    ("agent/c", "agent/d", "clean", ""),
)

EXPECTED = {
    "Footprint": FOOTPRINT,
    "Who overlaps whom": OVERLAP_PAIRS,
    "Real conflicts": REAL_CONFLICTS,
    "Hotspots": HOTSPOTS,
    # 5 branches -> 10 pairs; the 6 pairs among a..d conflict (CHANGELOG.md), the 4 with e do not.
    "Measured p": "conflicting pairs 6 of 10, p = 0.60\n",
    "What-if merge drivers": WHAT_IF,
    # Union clears the set and log, the pattern list drops openapi.json: one human conflict left.
    "Derived files": lines(("agent/a", "agent/b", "conflict", "src/app.py")),
    # Both c and d add a migration numbered 0002; no pair above reports it as a conflict.
    "Sequence collisions": lines(
        ("migrations/0002", "agent/c", "migrations/0002_users.sql"),
        ("migrations/0002", "agent/d", "migrations/0002_orders.sql")),
    "Pre-work check": "agent/a: collision\nagent/e: clear\n",
}


def readme_recipes():
    """List the ```sh blocks of README.md's "## Recipes" section with their headings.

    The section runs from "## Recipes" to the next "## " heading. Each block is attributed to the
    nearest "### NAME: QUESTION" heading above it. test_every_recipe_is_checked rejects a block
    without a heading and a heading without a question.

    SPEC: "Recipes"

    Returns:
        [(name, question, sh source)]: name is None for a block before any "### " heading;
        question is None if the heading has no ": ".
    """
    with open(README, "r", encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r"^## Recipes\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match:
        return []
    recipes, name, question, block = [], None, None, None
    for line in match.group(1).split("\n"):
        if block is not None:                 # inside a fence: collect until the closing ```
            if line.startswith("```"):
                recipes.append((name, question, "\n".join(block) + "\n"))
                block = None
            else:
                block.append(line)
        elif line.startswith("### "):
            name, _, question = line[4:].strip().partition(": ")
            question = question or None
        elif line.startswith("```sh"):
            block = []
    return recipes


def write_wrapper(bin_dir, name, module):
    """Write `bin_dir/git-<name>`: an sh script running `python -m git_overlap.<module>`.

    git finds `git-<name>` on PATH for `git <name>`; the wrapper pins PYTHONPATH to this
    checkout so the README is checked against the code beside it, not an installed copy.
    """
    path = os.path.join(bin_dir, "git-" + name)
    with open(path, "w", newline="\n") as handle:
        handle.write("#!/bin/sh\nPYTHONPATH='%s' exec '%s' -m git_overlap.%s \"$@\"\n"
                     % (ROOT, sys.executable, module))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_fixture(sandbox):
    """Create the article's night as a real repository: `main` plus agent/a..agent/e (see top)."""
    repo = Repo(sandbox)
    registry = 'TOOLS = [\n    "search",\n]\n'
    changelog = "# Changelog\n\n## Unreleased\n\n## 1.0.0\n- First release.\n"
    openapi = '{\n  "paths": [\n    "/search"\n  ]\n}\n'
    base = repo.commit(None, {
        "tools/registry.py": registry,
        "CHANGELOG.md": changelog,
        "openapi.json": openapi,
        "migrations/0001_init.sql": "CREATE TABLE tools (name TEXT);\n",
        "src/app.py": "TIMEOUT = 10\n",
    }, ref="refs/heads/main")

    def add_tool(name):
        """Return the registry with tool NAME added after "search"."""
        # Set: one entry per line with a trailing comma, so a union merge stays valid Python.
        return registry.replace('"search",\n', '"search",\n    "%s",\n' % name)

    def add_log(entry):
        """Return the changelog with ENTRY as the first line under "## Unreleased"."""
        # Log: every branch writes its line straight under "## Unreleased" (same spot).
        return changelog.replace("## Unreleased\n", "## Unreleased\n- %s\n" % entry)

    def add_path(path):
        """Return openapi.json as the generator writes it with PATH added."""
        # Derived: the generator rewrites the "/search" line (adds a comma) and appends a path.
        return openapi.replace('"/search"\n', '"/search",\n    "%s"\n' % path)

    repo.commit(base, {"tools/registry.py": add_tool("fetch"), "CHANGELOG.md": add_log("Add fetch."),
                       "openapi.json": add_path("/fetch"), "src/app.py": "TIMEOUT = 20\n",
                       "src/fetch.py": "def fetch():\n    pass\n"}, ref="refs/heads/agent/a")
    repo.commit(base, {"tools/registry.py": add_tool("lint"), "CHANGELOG.md": add_log("Add lint."),
                       "openapi.json": add_path("/lint"), "src/app.py": "TIMEOUT = 30\n",
                       "src/lint.py": "def lint():\n    pass\n"}, ref="refs/heads/agent/b")
    repo.commit(base, {"tools/registry.py": add_tool("users"), "CHANGELOG.md": add_log("Add users."),
                       "migrations/0002_users.sql": "CREATE TABLE users (id INT);\n"},
                ref="refs/heads/agent/c")
    repo.commit(base, {"CHANGELOG.md": add_log("Add orders."), "openapi.json": add_path("/orders"),
                       "migrations/0002_orders.sql": "CREATE TABLE orders (id INT);\n"},
                ref="refs/heads/agent/d")
    repo.commit(base, {"docs/guide.md": "# Guide\n"}, ref="refs/heads/agent/e")
    return repo


@needs_sh
class RecipesTest(unittest.TestCase):
    """README "Recipes" run against the fixture night; stdout must match EXPECTED exactly."""

    @classmethod
    def setUpClass(cls):
        """One hermetic sandbox (empty global config, no system config, fixed identity and dates),
        one fixture repository and one bin directory of git-<name> wrappers for all recipes."""
        cls.sb = Sandbox()
        cls.repo = build_fixture(cls.sb)
        cls.bin = cls.sb.mkdir("bin")
        for name in ("footprint", "overlap", "pairmerge"):
            write_wrapper(cls.bin, name, name)
        cls.recipes = dict((name, source) for name, _, source in readme_recipes())

    @classmethod
    def tearDownClass(cls):
        """Remove the sandbox and everything in it."""
        cls.sb.cleanup()

    def run_recipe(self, heading):
        """Run the README recipe under HEADING with `sh -c` in the fixture; return its stdout.

        Recipes that call `git pairmerge` are skipped below its git 2.43 floor. A recipe must
        print nothing on stderr: a failing command inside a pipeline would otherwise go unseen.
        """
        source = self.recipes.get(heading)
        self.assertIsNotNone(source, "README has no recipe under '### %s'" % heading)
        if "pairmerge" in source and not PAIRMERGE_OK:
            self.skipTest("git pairmerge needs git >= 2.43")
        env = dict(self.sb.env)
        env["PATH"] = self.bin + os.pathsep + env.get("PATH", "")
        env["TMPDIR"] = self.sb.tmp            # mktemp files land in the sandbox
        proc = subprocess.run([SH, "-c", source], cwd=self.repo.path, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        self.assertEqual(proc.stderr, b"", "recipe '%s' wrote to stderr" % heading)
        return proc.stdout.decode("utf-8")

    def check(self, heading):
        """Assert the recipe's exact stdout against EXPECTED[heading]."""
        self.assertEqual(self.run_recipe(heading), EXPECTED[heading])

    def test_every_recipe_is_checked(self):
        """Acceptance: "no documented command can drift from the code". Every ```sh block under
        Recipes sits under its own ### heading and has an expected output here, and vice versa."""
        found = readme_recipes()
        headings = [name for name, _, _ in found]
        self.assertNotIn(None, headings, "a ```sh block in Recipes has no ### heading")
        for name, question, _ in found:
            # The owner's rule: each recipe heading says which question it answers.
            self.assertTrue(question and question.endswith("?"),
                            "heading '### %s' must read '### NAME: question?'" % name)
        self.assertEqual(len(headings), len(set(headings)), "one ```sh block per ### heading")
        self.assertEqual(sorted(headings), sorted(EXPECTED))

    def test_footprint(self):
        """Recipe "Footprint": the files each agent/* branch changes (the night's raw input)."""
        self.check("Footprint")

    def test_who_overlaps_whom(self):
        """Recipe "Who overlaps whom": a..d all pair up through CHANGELOG.md; e pairs with none."""
        self.check("Who overlaps whom")

    def test_real_conflicts(self):
        """Recipe "Real conflicts": overlap | pairmerge; the 0002 migrations are no conflict to git."""
        self.check("Real conflicts")

    def test_hotspots(self):
        """Recipe "Hotspots". Acceptance: "hotspots rank the registry, changelog and openapi.json"."""
        self.check("Hotspots")

    def test_measured_p(self):
        """Recipe "Measured p": conflicting pairs over all n(n-1)/2 pairs, generated by awk."""
        self.check("Measured p")

    def test_what_if_merge_drivers(self):
        """Recipe "What-if merge drivers". Acceptance: "union attributes clear the set and log
        conflicts": registry and CHANGELOG.md vanish, a/c, b/c and c/d turn clean."""
        self.check("What-if merge drivers")

    def test_derived_files(self):
        """Recipe "Derived files". Acceptance: "derived paths are filtered out as
        regenerate-not-merge": openapi.json is dropped, the src/app.py conflict stays."""
        self.check("Derived files")

    def test_sequence_collisions(self):
        """Recipe "Sequence collisions". Acceptance: "two branches adding different files with the
        same migration number are reported although git sees no conflict"."""
        self.check("Sequence collisions")
        # "although git sees no conflict": no Real conflicts record names a migration.
        if PAIRMERGE_OK:
            self.assertNotIn("migrations/", self.run_recipe("Real conflicts"))

    def test_pre_work_check(self):
        """Recipe "Pre-work check": overlap --with's exit status tells agent/a (collision) from
        agent/e (clear)."""
        self.check("Pre-work check")


if __name__ == "__main__":
    unittest.main()
