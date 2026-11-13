# SPDX-License-Identifier: Apache-2.0
"""git pairmerge [--attributes FILE]: report whether merging each pair of refs conflicts.

Reads records whose first two fields are refs and writes `refA TAB refB TAB clean TAB` or one
`refA TAB refB TAB conflict TAB path` per conflicted path. Merges run in memory with
`git merge-tree --write-tree`; every object git writes lands in a private temporary object
directory, so the repository is never changed. Contract: SPEC.md, "Merge mechanism".
"""

import os
import re
import shutil
import stat
import sys
import tempfile

from ._common import (EXIT_FOUND, EXIT_NOTHING, Fail, check_safe, git, git_error, parse_args,
                      read_records, resolve, run, show, usage_and_version, write_record)

NAME = "pairmerge"
USAGE = """usage: git pairmerge [-z] [--attributes FILE] < pairs

For each pair of refs (the first two fields of each record), merge them in memory:
  refA TAB refB TAB clean TAB             (the pair merges cleanly)
  refA TAB refB TAB conflict TAB path     (one record per conflicted path; exit 1)

  --attributes FILE  merge as if FILE were the repository's .gitattributes (replacing it)
  -z                 records end with NUL instead of LF, on input and output
  -h                 show this help
  --version          show the version
Without --attributes, the repository's .gitattributes files are ignored (a plain line merge);
$GIT_DIR/info/attributes, core.attributesFile and configured merge drivers still apply."""

# --attr-source arrived in git 2.41; merge-tree crashed with it until 2.43.
GIT_FLOOR = (2, 43)

# A tree object id: 40 hex digits (SHA-1) or 64 (SHA-256).
_OID = re.compile(rb"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def check_git_version():
    """Fail unless `git version` reports at least GIT_FLOOR.

    Accepts forms such as "git version 2.50.1 (Apple Git-155)" and
    "git version 2.43.0.windows.1": only the first two numbers after "git version" count.
    """
    code, out, err = git(["version"])
    if code != 0:
        raise Fail("cannot run git version: %s" % git_error(err))
    match = re.search(rb"git version (\d+)\.(\d+)", out)
    if not match:
        raise Fail("cannot read the git version from %r; git pairmerge needs git %d.%d or later"
                   % (show(out.strip()), GIT_FLOOR[0], GIT_FLOOR[1]))
    found = (int(match.group(1)), int(match.group(2)))
    if found < GIT_FLOOR:
        raise Fail("git %d.%d is too old; git pairmerge needs git %d.%d or later"
                   % (found + GIT_FLOOR))


def read_pairs(zero):
    """Return [(record number, refA, refB)] from stdin: first two fields, the rest ignored."""
    pairs = []
    for number, record in read_records(zero):
        fields = record.split(b"\t")
        # Fewer than 2 fields, or an empty ref, is malformed input.
        if len(fields) < 2 or fields[0] == b"" or fields[1] == b"":
            raise Fail("record %d: expected two TAB-separated refs" % number)
        # In LF mode a CR inside a ref could not be written back out as given.
        for ref in fields[:2]:
            try:
                check_safe(ref, zero, "ref")
            except Fail as exc:
                raise Fail("record %d: %s" % (number, exc))
        pairs.append((number, fields[0], fields[1]))
    return pairs


def private_object_env(tempdir):
    """Return the environment that sends new objects to `tempdir` and reads the repo's own.

    The repository's object directory comes from `git rev-parse --git-path objects`, asked
    before the override so it names the real one, and is made absolute. An alternates list
    the caller already set is kept after it.
    """
    code, out, err = git(["rev-parse", "--git-path", "objects"])
    if code != 0:
        raise Fail(git_error(err))
    objects = os.path.abspath(os.fsdecode(out.rstrip(b"\r\n")))
    alternates = objects
    existing = os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES")
    if existing:
        alternates = objects + os.pathsep + existing
    return {"GIT_OBJECT_DIRECTORY": tempdir, "GIT_ALTERNATE_OBJECT_DIRECTORIES": alternates}


def attribute_tree(attributes, env):
    """Write the pinned attribute-source tree into the private object dir; return its id.

    Without FILE it is the empty tree (written explicitly: it need not exist as an object).
    With FILE it is a tree holding FILE's bytes as its root .gitattributes.
    """
    if attributes is None:
        code, out, err = git(["hash-object", "-w", "-t", "tree", "--stdin"], env, b"")
    else:
        code, out, err = git(["hash-object", "-w", "--no-filters", "--stdin"], env, attributes)
        if code == 0:
            # mktree -z reads one NUL-terminated "mode type oid TAB name" entry.
            entry = b"100644 blob " + out.strip() + b"\t.gitattributes\0"
            code, out, err = git(["mktree", "-z"], env, entry)
    if code != 0:
        raise Fail("cannot build the attribute tree: %s" % git_error(err))
    return out.strip()


def merge(tree, sha_a, sha_b, env, ref_a, ref_b):
    """Merge two commits in memory; return None if clean, else the sorted conflicted paths.

    Clean/conflict is decided by the exit code and the tree id, never by git's messages. A
    conflict with no listed paths returns [b""], written as one record with an empty path.
    """
    code, out, err = git(["--attr-source=" + os.fsdecode(tree), "merge-tree", "--write-tree",
                          "-z", "--name-only", "--no-messages", "--end-of-options",
                          sha_a, sha_b], env)
    if code == 0:
        return None
    # Output is "TREE NUL PATH NUL PATH NUL ..."; a conflict needs a real tree id first.
    fields = out.split(b"\0")
    if code == 1 and _OID.match(fields[0]):
        paths = sorted(set(path for path in fields[1:] if path != b""))
        return paths or [b""]
    raise Fail("git merge-tree failed for %s and %s: %s"
               % (show(ref_a), show(ref_b), git_error(err)))


def remove_tree(path):
    """Delete `path` recursively, first making read-only files writable (Windows needs it)."""
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def pairmerge(argv):
    """Run the command on argv (without the program name); return the exit code."""
    opts, positionals = parse_args(argv, ("-z", "-h", "--version"), ("--attributes",))
    if usage_and_version(opts, NAME, USAGE):
        return EXIT_NOTHING
    if positionals:
        raise Fail("unexpected argument: %s (see -h)" % positionals[0])
    zero = bool(opts.get("-z"))
    check_git_version()
    # FILE is read as bytes, once, before anything else touches git objects.
    attributes = None
    if "--attributes" in opts:
        try:
            with open(opts["--attributes"], "rb") as handle:
                attributes = handle.read()
        except OSError as exc:
            raise Fail("cannot read --attributes file: %s" % exc)
    pairs = read_pairs(zero)
    tempdir = tempfile.mkdtemp(prefix="git-pairmerge-")
    try:
        env = private_object_env(tempdir)
        # Check every ref before any merge runs; a ref is resolved once however often used.
        shas = {}
        for number, ref_a, ref_b in pairs:
            for ref in (ref_a, ref_b):
                if ref not in shas:
                    try:
                        shas[ref] = resolve(ref, env)
                    except Fail as exc:
                        raise Fail("record %d: %s" % (number, exc))
        tree = attribute_tree(attributes, env)
        seen = set()
        found = False
        for _, ref_a, ref_b in pairs:
            # A pair is the same in either order: process it once, at its first occurrence.
            key = (min(ref_a, ref_b), max(ref_a, ref_b))
            if key in seen:
                continue
            seen.add(key)
            paths = merge(tree, shas[ref_a], shas[ref_b], env, ref_a, ref_b)
            if paths is None:
                write_record((ref_a, ref_b, b"clean", b""), zero)
                continue
            found = True
            for path in paths:
                check_safe(path, zero, "path")
                write_record((ref_a, ref_b, b"conflict", path), zero)
    finally:
        # The private object directory goes on every exit path, errors included.
        remove_tree(tempdir)
    return EXIT_FOUND if found else EXIT_NOTHING


def main():
    """Console-script entry point: run on sys.argv and exit with the SPEC's status."""
    run(NAME, lambda: pairmerge(sys.argv[1:]))


if __name__ == "__main__":
    main()
