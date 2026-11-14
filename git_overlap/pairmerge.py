# SPDX-License-Identifier: Apache-2.0
"""git pairmerge [--attributes FILE] [--onto BASE]: report whether each pair of refs conflicts.

Reads records whose first two fields are refs and writes `refA TAB refB TAB clean TAB` or one
`refA TAB refB TAB conflict TAB path` per conflicted path. With --onto BASE a pair is merged as
the two refs would land on BASE, first one then the other, and a ref that no longer merges into
BASE makes its pairs `stale`. Merges run in memory with `git merge-tree --write-tree`; every
object git writes lands in a private temporary object directory, so the repository is never
changed. SPEC: "git pairmerge", "--onto BASE" and "Merge mechanism".
"""

import os
import re
import shutil
import stat
import sys
import tempfile

from ._common import (EXIT_FOUND, EXIT_NOTHING, OID, Fail, check_safe, git, git_error,
                      ignore_signals, install_signal_handlers, parse_args, read_records,
                      resolve, run, show, usage_and_version, write_record,
                      write_stderr_line, write_stdout)

NAME = "pairmerge"
USAGE = """usage: git pairmerge [-z] [--attributes FILE] [--onto BASE] < pairs

For each pair of refs (the first two fields of each record), merge them in memory:
  refA TAB refB TAB clean TAB             (the pair merges cleanly)
  refA TAB refB TAB conflict TAB path     (one record per conflicted path; exit 1)
With --onto BASE, a pair whose refA or refB no longer merges into BASE is written as
  refA TAB refB TAB stale TAB path        (one record per path conflicting with BASE; exit 1)

  --attributes FILE  merge as if FILE were the repository's .gitattributes (replacing it)
  --onto BASE        merge each pair as both refs would land on BASE, refA first
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

# The landing commit M_R of --onto: fixed author, committer and dates, so its id (which git
# embeds in PATH~<id> names) is the same on every run (SPEC "--onto BASE", Mechanism). The
# .invalid domain (RFC 2606) can never be a real address.
LANDING_IDENTITY = {"GIT_AUTHOR_NAME": "git-overlap", "GIT_AUTHOR_EMAIL": "git-overlap@invalid",
                    "GIT_AUTHOR_DATE": "@0 +0000", "GIT_COMMITTER_NAME": "git-overlap",
                    "GIT_COMMITTER_EMAIL": "git-overlap@invalid",
                    "GIT_COMMITTER_DATE": "@0 +0000"}


def check_git_version():
    """Fail unless `git version` reports at least GIT_FLOOR.

    Accepts forms such as "git version 2.50.1 (Apple Git-155)" and
    "git version 2.43.0.windows.1": only the first two numbers after "git version" count.

    Raises:
        Fail: git version fails, prints no version, or is older than GIT_FLOOR.

    SPEC: "Merge mechanism" (version floor for git pairmerge).
    """
    code, out, err = git(["version"])
    if code != 0:
        raise Fail("cannot run git version: %s" % git_error(err, code))
    # search, not match: text before the version number is tolerated.
    match = re.search(rb"git version (\d+)\.(\d+)", out)
    if not match:
        raise Fail("cannot read the git version from %r; git pairmerge needs git %d.%d or later"
                   % (show(out.strip()), GIT_FLOOR[0], GIT_FLOOR[1]))
    found = (int(match.group(1)), int(match.group(2)))
    if found < GIT_FLOOR:
        raise Fail("git %d.%d is too old; git pairmerge needs git %d.%d or later"
                   % (found + GIT_FLOOR))


def read_pairs(zero):
    """Read the pairs from stdin: the first two fields of each record, the rest ignored.

    Args:
        zero: True for -z mode, False for LF mode.

    Returns:
        A list of (record number, refA, refB) tuples, refs as bytes, in input order.

    Raises:
        Fail: A record is malformed or, in LF mode, a ref holds CR.

    SPEC: "git pairmerge" (Input) and "Malformed input".
    """
    pairs = []
    for number, record in read_records(zero):
        # Split on every TAB: fields after the second are ignored, so overlap output pipes in.
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
    """Make relative GIT_DIR-style variables absolute, relative to our (the caller's) cwd.

    Changes os.environ in place, so every later git call inherits the absolute paths.

    SPEC: "Merge mechanism" (merge-tree runs at the repository root).
    """
    for name in _PATH_VARIABLES:
        value = os.environ.get(name)
        if value and not os.path.isabs(value):
            os.environ[name] = os.path.abspath(value)


def c_quote(path):
    """Quote one GIT_ALTERNATE_OBJECT_DIRECTORIES entry C-style in double quotes.

    git splits that variable on the path separator, but an entry starting with '"' is first
    unquoted C-style (object-file.c, parse_alt_odb_entry), so a quoted entry may hold the
    separator. Backslash and '"' get a backslash; control characters become octal escapes.

    Args:
        path: The directory, as str.

    Returns:
        The quoted entry, including its surrounding double quotes.

    SPEC: "Merge mechanism" (GIT_ALTERNATE_OBJECT_DIRECTORIES quoting).
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

    Returns:
        A tuple (objects directory, root directory), both absolute str.

    Raises:
        Fail: Not inside a git repository, or the root cannot be found.

    SPEC: "Merge mechanism" and "Paths are relative to the repository root".
    """
    code, out, err = git(["rev-parse", "--git-path", "objects"])
    if code != 0:
        raise Fail("not a git repository: %s" % git_error(err, code))
    # abspath: git may print a path relative to our cwd, but merge-tree runs elsewhere.
    objects = os.path.abspath(os.fsdecode(path_line(out)))
    code, out, _ = git(["rev-parse", "--show-toplevel"])
    root = path_line(out)
    if code != 0 or not root:
        code, out, err = git(["rev-parse", "--absolute-git-dir"])
        if code != 0:
            raise Fail("cannot find the repository root: %s" % git_error(err, code))
        root = path_line(out)
    return objects, os.fsdecode(root)


def path_line(out):
    """Return a path git printed on one line, without the line end git added.

    Only that one LF is removed: a directory name may itself end in CR or LF on POSIX. On
    Windows, where no path can hold CR, one CR before it is removed too.

    Args:
        out: git's stdout, as bytes.

    Returns:
        The path, as bytes.
    """
    if out.endswith(b"\n"):
        out = out[:-1]
    # CR is legal in POSIX names but impossible in Windows ones, so only strip it there.
    if os.name == "nt" and out.endswith(b"\r"):
        out = out[:-1]
    return out


def private_object_env(tempdir, objects):
    """Return the environment that sends new objects to `tempdir` and reads `objects`.

    `objects` is C-quoted when it holds the path separator or starts with '"'. An alternates
    list the caller already set is kept after it.

    Args:
        tempdir: The private temporary object directory.
        objects: The repository's absolute object directory.

    Returns:
        A dict of the two environment variables to pass to git.

    SPEC: "Merge mechanism" (nothing is written to the repository).
    """
    entry = objects
    # Quote only when needed (SPEC): a plain entry is read verbatim.
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

    Args:
        env: The private object environment from private_object_env.

    Raises:
        Fail: The probe object cannot be read through `env`.

    SPEC: "Merge mechanism" (a set-up failure is never reported as an unknown ref).
    """
    # Ask without `env`, so the probe id comes from the repository as the caller sees it.
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

    Args:
        attributes: FILE's bytes, or None without --attributes.
        env: The private object environment.

    Returns:
        The tree id (bytes).

    Raises:
        Fail: git cannot write the blob or the tree.

    SPEC: "Merge mechanism" (the attribute source is always pinned).
    """
    if attributes is None:
        # The empty tree is hashed from empty input, so it is the same id in any repository.
        code, out, err = git(["hash-object", "-w", "-t", "tree", "--stdin"], env, b"")
    else:
        # --no-filters: FILE is stored as its exact bytes, untouched by clean filters or EOL.
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

    Args:
        sha_a: The first commit id (bytes).
        sha_b: The second commit id (bytes).
        env: The private object environment.
        ref_a: The first ref as written, for messages.
        ref_b: The second ref as written, for messages.

    Raises:
        Fail: No merge base (naming both refs), or git merge-base fails.

    SPEC: "Shallow or unrelated histories".
    """
    code, out, err = git(["merge-base", sha_a, sha_b], env)
    if code == 1 and not out.strip():
        raise Fail("no merge base for %s and %s" % (show(ref_a), show(ref_b)))
    if code != 0:
        raise Fail("git merge-base failed for %s and %s: %s"
                   % (show(ref_a), show(ref_b), git_error(err, code)))


def merge(tree, sha_a, sha_b, env, root, ref_a, ref_b):
    """Merge two commits in memory; return the merged tree id and the conflicted paths.

    Clean/conflict is decided by the exit code and the tree id, never by git's messages. A
    conflict with no listed paths gives [b""], written as one record with an empty path.

    Args:
        tree: The attribute-source tree id (bytes).
        sha_a: The first commit id (bytes).
        sha_b: The second commit id (bytes).
        env: The private object environment.
        root: The repository root, where merge-tree runs.
        ref_a: The first ref as written, for messages.
        ref_b: The second ref as written, for messages.

    Returns:
        A tuple (tree id, paths): paths is None if the pair merges cleanly, else a sorted list
        of conflicted paths (bytes). The tree id is merge-tree's first field, as bytes.

    Raises:
        Fail: merge-tree exits with anything but a clean result or a conflict.

    SPEC: "Merge mechanism" (Per pair).
    """
    # Run at the repository root, so the paths merge-tree prints are root-relative.
    code, out, err = git(list(PINNED_CONFIG) +
                         ["--attr-source=" + os.fsdecode(tree), "merge-tree", "--write-tree",
                          "-z", "--name-only", "--no-messages", "--end-of-options",
                          sha_a, sha_b], env, cwd=root)
    # Output is "TREE NUL PATH NUL PATH NUL ..."; a clean merge prints the tree alone.
    fields = out.split(b"\0")
    if code == 0:
        return fields[0], None
    # A conflict needs a real tree id first.
    if code == 1 and OID.match(fields[0]):
        # set(): never write one path twice, whatever git lists; sorted() is bytewise.
        paths = sorted(set(path for path in fields[1:] if path != b""))
        return fields[0], paths or [b""]
    raise Fail("git merge-tree failed for %s and %s: %s"
               % (show(ref_a), show(ref_b), git_error(err, code)))


def land(tree, base, sha, env, root, onto, ref):
    """Merge one ref onto BASE on its own; return its landing commit, or its stale paths.

    A clean merge's tree becomes the commit M_R = commit-tree TREE -p BASE -p R, the merge
    commit landing R would create, written to the private object directory with a fixed
    identity, date, encoding and no signature, so its id is the same on every run.

    Args:
        tree: The attribute-source tree id (bytes).
        base: BASE's commit id (bytes).
        sha: The ref's commit id (bytes).
        env: The private object environment.
        root: The repository root, where merge-tree runs.
        onto: BASE as the caller wrote it, for messages.
        ref: The ref as written, for messages.

    Returns:
        A tuple (M_R id, None) if the ref merges cleanly into BASE, else (None, paths), the
        sorted paths on which it conflicts with BASE ([b""] if git lists none).

    Raises:
        Fail: No merge base with BASE, or merge-tree or commit-tree fails.

    SPEC: "--onto BASE" (Stale refs, Mechanism).
    """
    check_merge_base(base, sha, env, onto, ref)
    merged, paths = merge(tree, base, sha, env, root, onto, ref)
    if paths is not None:
        return None, paths
    # i18n.commitEncoding and --no-gpg-sign pinned: user configuration cannot change the id.
    code, out, err = git(["-c", "i18n.commitEncoding=UTF-8", "commit-tree", "--no-gpg-sign",
                          merged, "-p", base, "-p", sha, "-m", "pairmerge onto"],
                         dict(env, **LANDING_IDENTITY))
    commit = out.strip()
    if code != 0 or not OID.match(commit):
        raise Fail("git commit-tree failed for %s onto %s: %s"
                   % (show(ref), show(onto), git_error(err, code)))
    return commit, None


def merge_onto(tree, base, landed, sha_a, sha_b, env, root, onto, ref_a, ref_b):
    """Merge a pair as it would land on BASE, refA first; return (status, paths).

    Each distinct commit is landed once (cached in `landed`, keyed by commit id) and reused
    across pairs. If either side is stale the pair is stale, with the union of the stale
    side(s)' paths; otherwise the pair is merge-tree M_A B, with git's own merge base.

    Args:
        tree: The attribute-source tree id (bytes).
        base: BASE's commit id (bytes).
        landed: The cache {commit id: land() result}, filled as refs are first used.
        sha_a: The first ref's commit id (bytes), which lands first.
        sha_b: The second ref's commit id (bytes).
        env: The private object environment.
        root: The repository root, where merge-tree runs.
        onto: BASE as the caller wrote it, for messages.
        ref_a: The first ref as written, for messages.
        ref_b: The second ref as written, for messages.

    Returns:
        A tuple (status, paths): (b"clean", None), or b"conflict" or b"stale" with a sorted
        list of paths ([b""] if there are none to list).

    Raises:
        Fail: A merge-tree, merge-base or commit-tree call fails.

    SPEC: "--onto BASE" (Output per pair, Mechanism, Order).
    """
    for sha, ref in ((sha_a, ref_a), (sha_b, ref_b)):
        if sha not in landed:
            landed[sha] = land(tree, base, sha, env, root, onto, ref)
    # The paths of each stale side (a side that landed has None there).
    stale_sides = [landed[sha][1] for sha in (sha_a, sha_b) if landed[sha][1] is not None]
    if stale_sides:
        # b"" marks "git listed no path"; it is not a path, so it joins no union.
        union = set(path for paths in stale_sides for path in paths if path != b"")
        return b"stale", sorted(union) or [b""]
    # No merge-base check: M_A has BASE as a parent, and B merged with BASE, so they share one.
    _, paths = merge(tree, landed[sha_a][0], sha_b, env, root, ref_a, ref_b)
    if paths is None:
        return b"clean", None
    return b"conflict", paths


def remove_tree(path):
    """Delete `path` recursively, making read-only files writable first (Windows needs it).

    If anything is left, warn on stderr naming the directory; the exit status is unchanged.

    Args:
        path: The directory to remove.

    SPEC: "Merge mechanism" (the temporary directory is removed).
    """
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            except OSError:
                pass
    # ignore_errors: what cannot be removed is reported below, not raised mid-cleanup.
    shutil.rmtree(path, ignore_errors=True)
    if os.path.exists(path):
        # Not fatal (the result stands), but the caller should know what was left behind.
        write_stderr_line("git-%s: warning: could not remove temporary directory %s"
                          % (NAME, show(path)))


def pairmerge(argv):
    """Run git pairmerge.

    Args:
        argv: The arguments, without the program name.

    Returns:
        EXIT_FOUND if any pair conflicts (or, with --onto, is stale), else EXIT_NOTHING.

    Raises:
        Fail: A usage error, git too old, an unreadable FILE, an unknown BASE, a malformed
            record, an unknown ref, no merge base, or a merge-tree or commit-tree failure.
        Interrupted: SIGINT, SIGTERM or SIGHUP arrived (after the cleanup ran).

    SPEC: "git pairmerge", "--onto BASE" and "Merge mechanism".
    """
    opts, positionals = parse_args(argv, ("-z", "-h", "--version"),
                                   ("--attributes", "--onto"))
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
    onto = opts.get("--onto")
    base = None
    if onto is not None:
        # Both spellings, "--onto ''" and "--onto=", reach this usage error.
        if onto == "":
            raise Fail("--onto needs a non-empty BASE (see -h)")
        # BASE is resolved before stdin is read, as every command's BASE is.
        base = resolve(onto)
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
        # --onto only: each distinct commit's land() result, computed once for the whole run.
        landed = {}
        found = False
        for number, ref_a, ref_b in pairs:
            # A pair is the same in either order: process it once, at its first occurrence.
            key = (min(ref_a, ref_b), max(ref_a, ref_b))
            if key in seen:
                continue
            seen.add(key)
            try:
                if base is None:
                    check_merge_base(shas[ref_a], shas[ref_b], env, ref_a, ref_b)
                    _, paths = merge(tree, shas[ref_a], shas[ref_b], env, root, ref_a, ref_b)
                    status = b"clean" if paths is None else b"conflict"
                else:
                    status, paths = merge_onto(tree, base, landed, shas[ref_a], shas[ref_b],
                                               env, root, onto, ref_a, ref_b)
                if paths is None:
                    write_record((ref_a, ref_b, b"clean", b""), zero)
                else:
                    found = True
                    for path in paths:
                        check_safe(path, zero, "path")
                        write_record((ref_a, ref_b, status, path), zero)
                # Hand each finished pair to the reader now: a long run shows progress, and a
                # run that is stopped keeps every pair it completed (SPEC "Output timing").
                write_stdout(None)
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
    """Console-script entry point: run on sys.argv and exit with the SPEC's status.

    Raises:
        SystemExit: Always (0, 1, 2, or 128 + signal number).

    SPEC: "Common rules" (console scripts) and "Exit status".
    """
    run(NAME, lambda: pairmerge(sys.argv[1:]))


if __name__ == "__main__":
    main()
