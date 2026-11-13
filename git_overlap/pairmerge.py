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

from ._common import (EXIT_FOUND, EXIT_NOTHING, OID, Fail, check_safe, git, git_error,
                      ignore_signals, install_signal_handlers, parse_args, read_records,
                      resolve, run, show, usage_and_version, write_record)

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

# Merge settings pinned to git's documented defaults, so user configuration cannot flip a
# verdict (SPEC "Merge settings are pinned"). From Documentation/config/merge.txt (identical
# in git 2.43 and 2.50):
#   merge.renames          "Defaults to the value of diff.renames", and diff.renames
#                          (Documentation/config/diff.txt) "Defaults to true".
#   merge.directoryRenames "Defaults to conflict."
#   merge.renameLimit      "If not specified, defaults to the value of diff.renameLimit. If
#                          neither merge.renameLimit nor diff.renameLimit are specified,
#                          currently defaults to 7000." Setting merge.renameLimit explicitly
#                          ends that fallback chain, so diff.renameLimit (whose own default,
#                          1000, applies to diff only) needs no pin.
PINNED_CONFIG = ("-c", "merge.renames=true", "-c", "merge.directoryRenames=conflict",
                 "-c", "merge.renameLimit=7000")


def check_git_version():
    """Fail unless `git version` reports at least GIT_FLOOR.

    Accepts forms such as "git version 2.50.1 (Apple Git-155)" and
    "git version 2.43.0.windows.1": only the first two numbers after "git version" count.
    """
    code, out, err = git(["version"])
    if code != 0:
        raise Fail("cannot run git version: %s" % git_error(err, code))
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


# Variables a caller may set to relative paths, which git resolves against its working
# directory. merge-tree runs at the repository root, so they are made absolute first.
_PATH_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                   "GIT_OBJECT_DIRECTORY")


def absolutise_git_paths():
    """Make relative GIT_DIR-style variables absolute, relative to our (the caller's) cwd."""
    for name in _PATH_VARIABLES:
        value = os.environ.get(name)
        if value and not os.path.isabs(value):
            os.environ[name] = os.path.abspath(value)


def c_quote(path):
    """Quote one GIT_ALTERNATE_OBJECT_DIRECTORIES entry C-style in double quotes.

    git splits that variable on the path separator, but an entry starting with '"' is first
    unquoted C-style (object-file.c, parse_alt_odb_entry), so a quoted entry may hold the
    separator. Backslash and '"' get a backslash; control characters become octal escapes.
    """
    out = []
    for char in path:
        if char in '\\"':
            out.append("\\" + char)
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append("\\%03o" % ord(char))
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def repository_layout():
    """Return (objects dir, root dir), both absolute, asked before any environment override.

    The objects dir is `git rev-parse --git-path objects` (it honours a caller's own
    GIT_OBJECT_DIRECTORY). The root is the top of the worktree, or the git directory when
    there is no worktree (a bare repository, or running inside .git), so that merge-tree,
    run there, prints root-relative paths.
    """
    code, out, err = git(["rev-parse", "--git-path", "objects"])
    if code != 0:
        raise Fail("not a git repository: %s" % git_error(err, code))
    objects = os.path.abspath(os.fsdecode(out.rstrip(b"\r\n")))
    code, out, _ = git(["rev-parse", "--show-toplevel"])
    root = out.rstrip(b"\r\n")
    if code != 0 or not root:
        code, out, err = git(["rev-parse", "--absolute-git-dir"])
        if code != 0:
            raise Fail("cannot find the repository root: %s" % git_error(err, code))
        root = out.rstrip(b"\r\n")
    return objects, os.fsdecode(root)


def private_object_env(tempdir, objects):
    """Return the environment that sends new objects to `tempdir` and reads `objects`.

    `objects` is C-quoted when it holds the path separator or starts with '"'. An alternates
    list the caller already set is kept after it.
    """
    entry = objects
    if os.pathsep in entry or entry.startswith('"'):
        entry = c_quote(entry)
    existing = os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES")
    if existing:
        entry = entry + os.pathsep + existing
    return {"GIT_OBJECT_DIRECTORY": tempdir, "GIT_ALTERNATE_OBJECT_DIRECTORIES": entry}


def check_object_access(env):
    """Fail, naming the set-up, if the repository's objects are unreadable through `env`.

    Probes one object a ref points at (read without the override) through the private
    environment, so a broken alternates entry is never reported as an unknown ref. A
    repository with no refs has nothing to probe.
    """
    code, out, _ = git(["for-each-ref", "--count=1", "--format=%(objectname)"])
    oid = out.strip()
    if code != 0 or not OID.match(oid):
        return
    code, _, err = git(["cat-file", "-e", oid], env)
    if code != 0:
        raise Fail("cannot set up the private object directory (repository objects not "
                   "readable through GIT_ALTERNATE_OBJECT_DIRECTORIES): %s" % git_error(err, code))


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
        raise Fail("cannot build the attribute tree: %s" % git_error(err, code))
    return out.strip()


def check_merge_base(sha_a, sha_b, env, ref_a, ref_b):
    """Fail with "no merge base" if two commits share no history (decided by exit status).

    `git merge-base` exits 1 with no output when there is no common ancestor; checking first
    means git's own refusal message from merge-tree is never parsed.
    """
    code, out, err = git(["merge-base", sha_a, sha_b], env)
    if code == 1 and not out.strip():
        raise Fail("no merge base for %s and %s" % (show(ref_a), show(ref_b)))
    if code != 0:
        raise Fail("git merge-base failed for %s and %s: %s"
                   % (show(ref_a), show(ref_b), git_error(err, code)))


def merge(tree, sha_a, sha_b, env, root, ref_a, ref_b):
    """Merge two commits in memory; return None if clean, else the sorted conflicted paths.

    Clean/conflict is decided by the exit code and the tree id, never by git's messages. A
    conflict with no listed paths returns [b""], written as one record with an empty path.
    """
    # Run at the repository root, so the paths merge-tree prints are root-relative.
    code, out, err = git(list(PINNED_CONFIG) +
                         ["--attr-source=" + os.fsdecode(tree), "merge-tree", "--write-tree",
                          "-z", "--name-only", "--no-messages", "--end-of-options",
                          sha_a, sha_b], env, cwd=root)
    if code == 0:
        return None
    # Output is "TREE NUL PATH NUL PATH NUL ..."; a conflict needs a real tree id first.
    fields = out.split(b"\0")
    if code == 1 and OID.match(fields[0]):
        paths = sorted(set(path for path in fields[1:] if path != b""))
        return paths or [b""]
    raise Fail("git merge-tree failed for %s and %s: %s"
               % (show(ref_a), show(ref_b), git_error(err, code)))


def remove_tree(path):
    """Delete `path` recursively, making read-only files writable first (Windows needs it).

    If anything is left, warn on stderr naming the directory; the exit status is unchanged.
    """
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)
    if os.path.exists(path):
        # Not fatal (the result stands), but the caller should know what was left behind.
        if sys.stderr is not None:
            sys.stderr.write("git-%s: warning: could not remove temporary directory %s\n"
                             % (NAME, show(path)))


def pairmerge(argv):
    """Run the command on argv (without the program name); return the exit code."""
    opts, positionals = parse_args(argv, ("-z", "-h", "--version"), ("--attributes",))
    if usage_and_version(opts, NAME, USAGE):
        return EXIT_NOTHING
    if positionals:
        raise Fail("unexpected argument: %s (see -h)" % show(positionals[0]))
    zero = bool(opts.get("-z"))
    # From here on SIGINT/SIGTERM/SIGHUP unwind through the cleanup below (exit 128 + n).
    install_signal_handlers()
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
    # Before any git call that may run elsewhere: relative paths from the caller must not
    # change meaning when merge-tree runs at the repository root.
    absolutise_git_paths()
    objects, root = repository_layout()
    tempdir = None
    try:
        tempdir = tempfile.mkdtemp(prefix="git-pairmerge-")
        env = private_object_env(tempdir, objects)
        check_object_access(env)
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
        for number, ref_a, ref_b in pairs:
            # A pair is the same in either order: process it once, at its first occurrence.
            key = (min(ref_a, ref_b), max(ref_a, ref_b))
            if key in seen:
                continue
            seen.add(key)
            try:
                check_merge_base(shas[ref_a], shas[ref_b], env, ref_a, ref_b)
                paths = merge(tree, shas[ref_a], shas[ref_b], env, root, ref_a, ref_b)
                if paths is None:
                    write_record((ref_a, ref_b, b"clean", b""), zero)
                    continue
                found = True
                for path in paths:
                    check_safe(path, zero, "path")
                    write_record((ref_a, ref_b, b"conflict", path), zero)
            except Fail as exc:
                # Errors about this pair name its record (SPEC "Record errors").
                raise Fail("record %d: %s" % (number, exc))
    finally:
        # The private object directory goes on every exit path, errors and signals included;
        # further signals are ignored so they cannot cut the cleanup short.
        ignore_signals()
        if tempdir is not None:
            remove_tree(tempdir)
    return EXIT_FOUND if found else EXIT_NOTHING


def main():
    """Console-script entry point: run on sys.argv and exit with the SPEC's status."""
    run(NAME, lambda: pairmerge(sys.argv[1:]))


if __name__ == "__main__":
    main()
