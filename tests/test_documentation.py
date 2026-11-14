# SPDX-License-Identifier: Apache-2.0
"""Documentation floor: every file is licensed, every definition documented, the code explained.

Scope: every .py file in git_overlap/ and tests/. The docstring style is the one stated in
git_overlap/__init__.py (PEP 257, Google-style sections); these tests enforce its presence and
the comment density, not its wording, which is left to review.
"""

import ast
import io
import os
import re
import tokenize
import unittest

# The checkout these tests live in: the files checked are the ones being tested.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# One comment line per this many code lines, at least (house rule, as in pr-merge-audit).
CODE_LINES_PER_COMMENT = 10

# Tokens that carry no code of their own: layout, comments and the end of the file.
_NOT_CODE = (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
             tokenize.DEDENT, tokenize.ENDMARKER)

# Nodes that PEP 257 says must carry a docstring.
_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def python_files():
    """List every .py file under git_overlap/ and tests/, as paths relative to ROOT.

    Returns:
        A sorted list of relative paths, so failures are reported in a stable order.
    """
    found = []
    for folder in ("git_overlap", "tests"):
        for name in os.listdir(os.path.join(ROOT, folder)):
            if name.endswith(".py"):
                found.append(os.path.join(folder, name))
    return sorted(found)


# Ticket keys and pull request numbers belong in commit messages and PR descriptions, never in
# the shipped files. A key is two or more capitals, a hyphen and digits; standard
# names of the same shape (SHA-256, UTF-8, ISO-8601, RFC-3339) are not tickets.
_TRACKER_REF = re.compile(
    r"\b(?!(?:SHA|UTF|ISO|RFC)-)[A-Z][A-Z]+-[0-9]+\b"   # issue-tracker key
    r"|\bPR ?#[0-9]+"                                     # "PR", optional space, "#", digits
    r"|/pull/[0-9]+")                                      # a pull request URL


def shipped_text_files():
    """List the text files that ship in the repository: docs, code, tests and configuration.

    Returns:
        Sorted paths relative to ROOT: README.md, SPEC.md, pyproject.toml, every file under
        git_overlap/, tests/ and .github/ (compiled caches excluded). LICENSE is excluded: it is
        the unmodified Apache text.
    """
    found = [name for name in ("README.md", "SPEC.md", "pyproject.toml")
             if os.path.exists(os.path.join(ROOT, name))]
    for folder in ("git_overlap", "tests", ".github"):
        for parent, dirs, names in os.walk(os.path.join(ROOT, folder)):
            # Bytecode caches are build output, not shipped text.
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in names:
                if not name.endswith(".pyc"):
                    found.append(os.path.relpath(os.path.join(parent, name), ROOT))
    return sorted(found)


def read_source(relpath):
    """Return the text of one file under ROOT.

    Args:
        relpath: The path relative to ROOT.

    Returns:
        The file's text, decoded as UTF-8 (the encoding Python assumes for source).
    """
    with open(os.path.join(ROOT, relpath), encoding="utf-8") as handle:
        return handle.read()


def docstring_lines(tree):
    """Return the line numbers that docstrings occupy in a parsed module.

    Args:
        tree: The module's ast.

    Returns:
        A set of 1-based line numbers, every line of every docstring included.
    """
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, _DOCUMENTED) and node.body:
            first = node.body[0]
            # A docstring is a bare string constant as the first statement (PEP 257).
            # ast.Constant and end_lineno both exist from Python 3.8, the floor.
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                lines.update(range(first.lineno, first.end_lineno + 1))
    return lines


def count_lines(source):
    """Count the comment lines and code lines of one file.

    The counting rule, chosen to be honest in both directions:

    * A comment line is a line holding a COMMENT token, whether alone or after code. The
      SPDX licence header is not counted: it is required boilerplate, not an explanation.
    * A code line is a line covered by any token other than a comment, a line break, an
      indent or dedent, minus every line of a docstring. So blank lines, comment-only lines
      and docstring lines are not code; a statement spread over three lines is three lines,
      because each of them is something a reader has to read.

    Args:
        source: The file's text.

    Returns:
        A tuple (comment lines, code lines).
    """
    comments = set()
    code = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            if not token.string.startswith("# SPDX-"):
                comments.add(token.start[0])
        elif token.type not in _NOT_CODE:
            # A multi-line token (a long string) covers every line it spans.
            code.update(range(token.start[0], token.end[0] + 1))
    code -= docstring_lines(ast.parse(source))
    return len(comments), len(code)


class TestDocumentation(unittest.TestCase):
    """Every file in git_overlap/ and tests/ meets the documentation floor."""

    def test_every_file_starts_with_an_spdx_header(self):
        """The first line of every file names its licence."""
        for relpath in python_files():
            with self.subTest(file=relpath):
                self.assertTrue(read_source(relpath).startswith("# SPDX-License-Identifier: "))

    def test_every_module_class_and_function_has_a_docstring(self):
        """Modules, classes and functions (nested and methods included) all have docstrings."""
        for relpath in python_files():
            tree = ast.parse(read_source(relpath))
            # "<module>" for the module itself, which has no name of its own.
            missing = ["%s (line %d)" % (getattr(node, "name", "<module>"),
                                         getattr(node, "lineno", 1))
                       for node in ast.walk(tree)
                       if isinstance(node, _DOCUMENTED) and not ast.get_docstring(node)]
            with self.subTest(file=relpath):
                self.assertEqual(missing, [], "missing docstrings")

    def test_every_file_has_one_comment_line_per_ten_code_lines(self):
        """Comment lines times ten reach at least the code line count, file by file."""
        for relpath in python_files():
            comments, code = count_lines(read_source(relpath))
            with self.subTest(file=relpath):
                self.assertGreaterEqual(
                    comments * CODE_LINES_PER_COMMENT, code,
                    "%d comment lines for %d code lines" % (comments, code))

    def test_the_counter_is_not_fooled(self):
        """The counter excludes docstrings, blank lines and the SPDX line, and sees inline comments."""
        source = ('# SPDX-License-Identifier: Apache-2.0\n'
                  '"""Module docstring,\n\nover three lines."""\n'
                  '\n'
                  'x = 1  # inline comment\n'
                  '# own-line comment\n'
                  'y = (1,\n'
                  '     2)\n')
        # Code: "x = 1" and both lines of the tuple; comments: the inline one and the own-line one.
        self.assertEqual(count_lines(source), (2, 3))

    def test_no_ticket_or_pull_request_references_in_shipped_files(self):
        """Fail if any shipped file names an issue-tracker ticket or a pull request.

        Owner's rule: ticket keys and PR numbers live in commit messages and PR descriptions,
        never in docs, code or tests, which outlive the tracker.
        """
        found = []
        for relpath in shipped_text_files():
            with open(os.path.join(ROOT, relpath), encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, 1):
                    # Report every hit with its location, so one run shows them all.
                    for match in _TRACKER_REF.finditer(line):
                        found.append("%s:%d: %s" % (relpath, number, match.group(0)))
        self.assertEqual(found, [], "ticket or PR references in shipped files")

    def test_the_reference_pattern_is_not_fooled(self):
        """The pattern catches real references and ignores standard names of the same shape."""
        # Built from pieces so this file does not itself contain a reference.
        for text in ("see ABC" + "-12", "PR " + "#4", "PR" + "#4", "github.com/o/r/pull" + "/7"):
            self.assertTrue(_TRACKER_REF.search(text), text)
        for text in ("SHA-256", "UTF-8", "ISO-8601", "RFC-3339", "git-overlap", "Python 3.8"):
            self.assertIsNone(_TRACKER_REF.search(text), text)


if __name__ == "__main__":
    unittest.main()
