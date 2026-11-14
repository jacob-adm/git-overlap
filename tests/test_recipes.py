# SPDX-License-Identifier: Apache-2.0
"""Every recipe (a ```sh block in a "Recipes" section of README.md or docs/*.md) runs, checked.

A "Recipes" section starts at a heading reading exactly "Recipes" (any level, e.g. "## Recipes"
in README.md or "# Recipes" in docs/recipes.md) and runs to the next heading of the same or a
higher level. Each recipe sits under its own deeper heading, "NAME: question?".

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

Then the stale case: agent/f (from the same commit) edits the DEBUG line of src/config.py; main
moves on, changing that line too; agent/g, cut from the moved main, edits only the LOG line. f no
longer merges into main, and f/g conflict at their own merge base although g's own edit does not
touch f's line.

so that: agent/f is the one stale branch; among the fresh branches, CHANGELOG conflicts in 6
pairs, openapi.json and the registry in 3 each, src/app.py in 1 (the one real conflict left after
union attributes and the derived filter); p = 6/15; c and d collide on migration 0002 although
git merges them cleanly; agent/e is clear for the pre-work check while agent/a collides.

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
# under test is the code next to these docs.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = "docs"

# A Markdown ATX heading: its level (number of #) and its text.
HEADING = re.compile(r"^(#{1,6}) (.*)$")

# `sh` runs the recipes. On Windows it exists only under Git Bash (or similar); without it the
# recipe tests are skipped rather than failed, since the recipes are documented as POSIX sh.
SH = shutil.which("sh")
needs_sh = unittest.skipUnless(SH, "the recipes are POSIX sh and no `sh` is on PATH")

T = "\t"


def lines(*records):
    """Join TAB-separated records into the exact stdout text a recipe must print."""
    return "".join(T.join(fields) + "\n" for fields in records)


# --- Expected output of every recipe, keyed by the NAME of its "NAME: question?" heading. -----
# A documented recipe without an entry here (or an entry without a recipe) fails
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
    ("agent/f", "M", "src/config.py"),
    ("agent/g", "M", "src/config.py"),
)

# Every pair among a..d shares CHANGELOG.md; agent/e shares nothing; f and g share src/config.py.
OVERLAP_PAIRS = lines(
    ("agent/a", "agent/b"), ("agent/a", "agent/c"), ("agent/a", "agent/d"),
    ("agent/b", "agent/c"), ("agent/b", "agent/d"), ("agent/c", "agent/d"),
    ("agent/f", "agent/g"),
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
    # agent/f edits the DEBUG line that main changed later; every other branch merges cleanly.
    "Stale branches": lines(("agent/f", "src/config.py")),
    "Footprint": FOOTPRINT,
    "Who overlaps whom": OVERLAP_PAIRS,
    "Real conflicts": REAL_CONFLICTS,
    "Hotspots": HOTSPOTS,
    # 6 fresh branches (stale f dropped) -> 15 pairs; the 6 pairs among a..d conflict
    # (CHANGELOG.md); the pairs with e or g do not. With f, f/g would count as a 7th.
    "Measured p": "conflicting pairs 6 of 15, p = 0.40\n",
    "What-if merge drivers": WHAT_IF,
    # Union clears the set and log, the pattern list drops openapi.json: one human conflict left.
    "Derived files": lines(("agent/a", "agent/b", "conflict", "src/app.py")),
    # Both c and d add a migration numbered 0002; no pair above reports it as a conflict.
    "Sequence collisions": lines(
        ("migrations/0002", "agent/c", "migrations/0002_users.sql"),
        ("migrations/0002", "agent/d", "migrations/0002_orders.sql")),
    "Pre-work check": "agent/a: collision\nagent/e: clear\n",
}


def doc_files():
    """List the documentation files whose examples are checked: README.md, then docs/*.md.

    Returns:
        Paths relative to ROOT, with "/" as separator (they are only displayed and joined).
    """
    folder = os.path.join(ROOT, DOCS)
    names = sorted(n for n in os.listdir(folder) if n.endswith(".md")) \
        if os.path.isdir(folder) else []
    return ["README.md"] + [DOCS + "/" + name for name in names]


def doc_fences(relpath):
    """Split one documentation file into its fenced blocks.

    Args:
        relpath: The file, relative to ROOT, as doc_files lists it.

    Returns:
        (fences, lines): `lines` is the file split on LF; `fences` is a list of dicts, in order,
        with keys info (the text after ```), start and end (1-based line numbers of the opening
        and closing fence), body (the lines between, each ending in LF) and recipe: None outside
        a Recipes section, else (name, question) of the nearest heading above the block inside
        that section, (None, None) if there is none and question None if the heading has no
        ": ".

    Raises:
        AssertionError: a fence is indented or never closed.
    """
    with open(os.path.join(ROOT, *relpath.split("/")), "r", encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    fences, open_fence = [], None
    recipes_level, recipe = None, None     # the open Recipes section's level; current heading
    for number, line in enumerate(lines, 1):
        if open_fence is not None:
            # Inside a fence: collect lines until the closing ```; a "#" here is sh, not Markdown.
            if line.startswith("```"):
                body = open_fence.pop("lines")
                open_fence.update(end=number, body="".join(text + "\n" for text in body))
                fences.append(open_fence)
                open_fence = None
            else:
                open_fence["lines"].append(line)
            continue
        # An indented fence (e.g. inside a list item) would slip past the checks.
        if line.lstrip().startswith("```") and not line.startswith("```"):
            raise AssertionError("%s line %d: indented fence; examples must start in column 1 "
                                 "to be checked" % (relpath, number))
        heading = HEADING.match(line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip()
            # A heading of the same or a higher level closes the Recipes section.
            if recipes_level is not None and level <= recipes_level:
                recipes_level = None
            if recipes_level is None:
                if title == "Recipes":
                    recipes_level, recipe = level, (None, None)
            else:
                name, _, question = title.partition(": ")
                recipe = (name, question or None)
        elif line.startswith("```"):
            open_fence = {"info": line[3:].strip(), "start": number, "lines": [],
                          "recipe": recipe if recipes_level is not None else None}
    if open_fence is not None:
        raise AssertionError("%s line %d: fence never closed" % (relpath, open_fence["start"]))
    return fences, lines


def doc_recipes():
    """List the recipes of README.md and docs/*.md: every ```sh block in a Recipes section.

    test_every_recipe_is_checked rejects a block without a heading and a heading without a
    question.

    SPEC: "Recipes".

    Returns:
        [(name, question, sh source)]: name is None for a block before any heading inside the
        section; question is None if the heading has no ": ".
    """
    recipes = []
    for relpath in doc_files():
        fences, _ = doc_fences(relpath)
        # Only ```sh blocks are recipes; any other fence is checked by test_readme's rules.
        recipes.extend(fence["recipe"] + (fence["body"],) for fence in fences
                       if fence["info"] == "sh" and fence["recipe"] is not None)
    return recipes


def write_wrapper(bin_dir, name, module):
    """Write `bin_dir/git-<name>`: an sh script running `python -m git_overlap.<module>`.

    git finds `git-<name>` on PATH for `git <name>`; the wrapper pins PYTHONPATH to this
    checkout so the docs are checked against the code beside it, not an installed copy.

    SPEC: "Common rules" (`git footprint` found as `git-footprint` on PATH).
    """
    path = os.path.join(bin_dir, "git-" + name)
    with open(path, "w", newline="\n") as handle:
        handle.write("#!/bin/sh\nPYTHONPATH='%s' exec '%s' -m git_overlap.%s \"$@\"\n"
                     % (ROOT, sys.executable, module))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_fixture(sandbox):
    """Create the article's night as a real repository: `main` plus agent/a..agent/g (see top).

    SPEC: "Recipes".
    """
    repo = Repo(sandbox)
    registry = 'TOOLS = [\n    "search",\n]\n'
    changelog = "# Changelog\n\n## Unreleased\n\n## 1.0.0\n- First release.\n"
    openapi = '{\n  "paths": [\n    "/search"\n  ]\n}\n'
    # Five settings, so an edit to the first line and one to the last merge cleanly.
    config = "DEBUG = False\nPORT = 8000\nHOST = 'localhost'\nWORKERS = 1\nLOG = 'info'\n"
    base = repo.commit(None, {
        "tools/registry.py": registry,
        "CHANGELOG.md": changelog,
        "openapi.json": openapi,
        "migrations/0001_init.sql": "CREATE TABLE tools (name TEXT);\n",
        "src/app.py": "TIMEOUT = 10\n",
        "src/config.py": config,
    }, ref="refs/heads/main")

    def add_tool(name):
        """Return the registry with NAME added as a set entry.

        Args:
            name: The tool name to register.

        Returns:
            The new tools/registry.py text.
        """
        # Set: one entry per line with a trailing comma, so a union merge stays valid Python.
        return registry.replace('"search",\n', '"search",\n    "%s",\n' % name)

    def add_log(entry):
        """Return the changelog with ENTRY added under "## Unreleased".

        Args:
            entry: The changelog line, without its leading "- ".

        Returns:
            The new CHANGELOG.md text.
        """
        # Log: every branch writes its line straight under "## Unreleased" (same spot).
        return changelog.replace("## Unreleased\n", "## Unreleased\n- %s\n" % entry)

    def add_path(path):
        """Return openapi.json regenerated with PATH added.

        Args:
            path: The endpoint to add, e.g. "/fetch".

        Returns:
            The new openapi.json text.
        """
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
    # Stale: agent/f edits the DEBUG line that main then changes too, so f no longer merges.
    moved = config.replace("DEBUG = False", "DEBUG = True")
    repo.commit(base, {"src/config.py": config.replace("DEBUG = False", "DEBUG = None")},
                ref="refs/heads/agent/f")
    later = repo.commit(base, {"src/config.py": moved}, ref="refs/heads/main")
    # Fresh: agent/g, cut from the later main, edits only the LOG line. Merged with agent/f at
    # their own merge base (the first commit), g carries main's DEBUG change, so f/g conflict.
    repo.commit(later, {"src/config.py": moved.replace("LOG = 'info'", "LOG = 'debug'")},
                ref="refs/heads/agent/g")
    return repo


@needs_sh
class RecipesTest(unittest.TestCase):
    """The documented recipes run against the fixture night; stdout must match EXPECTED exactly.

    SPEC: "Recipes".
    """

    @classmethod
    def setUpClass(cls):
        """One hermetic sandbox (empty global config, no system config, fixed identity and dates),
        one fixture repository and one bin directory of git-<name> wrappers for all recipes."""
        cls.sb = Sandbox()
        cls.repo = build_fixture(cls.sb)
        cls.bin = cls.sb.mkdir("bin")
        for name in ("footprint", "overlap", "pairmerge"):
            write_wrapper(cls.bin, name, name)
        cls.recipes = dict((name, source) for name, _, source in doc_recipes())

    @classmethod
    def tearDownClass(cls):
        """Remove the sandbox, fixture repository and wrappers included."""
        cls.sb.cleanup()

    def run_recipe(self, heading):
        """Run the documented recipe under HEADING with `sh -c` in the fixture; return its stdout.

        Recipes that call `git pairmerge` are skipped below its git 2.43 floor. A recipe must
        print nothing on stderr: a failing command inside a pipeline would otherwise go unseen.

        SPEC: "Recipes".
        """
        source = self.recipes.get(heading)
        self.assertIsNotNone(source, "no documented recipe under the heading '%s'" % heading)
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
        """Assert the recipe's exact stdout against EXPECTED[heading].

        SPEC: "Recipes".
        """
        self.assertEqual(self.run_recipe(heading), EXPECTED[heading])

    def test_every_recipe_is_checked(self):
        """Acceptance: "no documented command can drift from the code". Every ```sh block under
        Recipes sits under its own heading and has an expected output here, and vice versa.

        SPEC: "Recipes".
        """
        found = doc_recipes()
        headings = [name for name, _, _ in found]
        self.assertNotIn(None, headings, "a ```sh block in Recipes has no heading of its own")
        for name, question, _ in found:
            # The owner's rule: each recipe heading says which question it answers.
            self.assertTrue(question and question.endswith("?"),
                            "heading '%s' must read 'NAME: question?'" % name)
        self.assertEqual(len(headings), len(set(headings)), "one ```sh block per recipe heading")
        self.assertEqual(sorted(headings), sorted(EXPECTED))

    def test_stale_branches(self):
        """Recipe "Stale branches": main paired with each branch; only agent/f conflicts.

        SPEC: "git pairmerge".
        """
        self.check("Stale branches")
        # The filter matters: without it, the stale f/g pair is a conflict like any other.
        if PAIRMERGE_OK:
            self.assertNotIn("agent/f", self.run_recipe("Real conflicts"))

    def test_footprint(self):
        """Recipe "Footprint": the files each agent/* branch changes (the night's raw input).

        SPEC: "git footprint BASE".
        """
        self.check("Footprint")

    def test_who_overlaps_whom(self):
        """Recipe "Who overlaps whom": a..d all pair up through CHANGELOG.md; e pairs with none.

        SPEC: "git overlap [--with REF]".
        """
        self.check("Who overlaps whom")

    def test_real_conflicts(self):
        """Recipe "Real conflicts": overlap | pairmerge; the 0002 migrations are no conflict to git.

        SPEC: "git pairmerge".
        """
        self.check("Real conflicts")

    def test_hotspots(self):
        """Recipe "Hotspots". Acceptance: "hotspots rank the registry, changelog and openapi.json".

        SPEC: "Recipes".
        """
        self.check("Hotspots")

    def test_measured_p(self):
        """Recipe "Measured p": conflicting pairs over all n(n-1)/2 pairs of fresh branches.

        SPEC: "Recipes".
        """
        self.check("Measured p")

    def test_what_if_merge_drivers(self):
        """Recipe "What-if merge drivers". Acceptance: "union attributes clear the set and log
        conflicts": registry and CHANGELOG.md vanish, a/c, b/c and c/d turn clean.

        SPEC: "Merge mechanism" (`--attributes FILE`).
        """
        self.check("What-if merge drivers")

    def test_derived_files(self):
        """Recipe "Derived files". Acceptance: "derived paths are filtered out as
        regenerate-not-merge": openapi.json is dropped, the src/app.py conflict stays.

        SPEC: "Recipes".
        """
        self.check("Derived files")

    def test_sequence_collisions(self):
        """Recipe "Sequence collisions". Acceptance: "two branches adding different files with the
        same migration number are reported although git sees no conflict".

        SPEC: "Recipes".
        """
        self.check("Sequence collisions")
        # "although git sees no conflict": no Real conflicts record names a migration.
        if PAIRMERGE_OK:
            self.assertNotIn("migrations/", self.run_recipe("Real conflicts"))

    def test_pre_work_check(self):
        """Recipe "Pre-work check": overlap --with's exit status tells agent/a (collision) from
        agent/e (clear).

        SPEC: "git overlap [--with REF]" and "Exit status".
        """
        self.check("Pre-work check")


if __name__ == "__main__":
    unittest.main()
