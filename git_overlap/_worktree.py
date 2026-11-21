# SPDX-License-Identifier: Apache-2.0
"""`worktree:PATH` refs: the files of a worktree as they are now, as a commit.

The commit is built in a private object directory with a private index, so the repository's
refs, index, worktree and object store are left alone. SPEC: "Worktree refs".
"""

import os
import shutil

from ._common import OID, Fail, git, git_error, resolve, show

PREFIX = b"worktree:"

# A fixed identity and date: the snapshot commit never depends on who runs the command or when.
_IDENTITY = {
    "GIT_AUTHOR_NAME": "git-overlap", "GIT_AUTHOR_EMAIL": "git-overlap@localhost",
    "GIT_AUTHOR_DATE": "@0 +0000",
    "GIT_COMMITTER_NAME": "git-overlap", "GIT_COMMITTER_EMAIL": "git-overlap@localhost",
    "GIT_COMMITTER_DATE": "@0 +0000",
}
# Variables that point git somewhere other than the worktree named in the ref.
_REDIRECTS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def is_worktree_ref(ref):
    """Return whether `ref` (bytes or str) is spelled worktree:PATH.

    SPEC: "Worktree refs".
    """
    # Records are bytes; BASE and option values are str.
    data = ref if isinstance(ref, bytes) else os.fsencode(ref)
    return data.startswith(PREFIX) and len(data) > len(PREFIX)


def _line(args, cwd, env=None):
    """Return the first output line of a git command run in `cwd`, or None if git fails there.

    SPEC: "Worktree refs".
    """
    # A directory git cannot run in is reported by the caller, in its own words.
    try:
        code, out, _ = git(args, env, cwd=cwd)
    except Fail:
        return None
    line = out.split(b"\n")[0].strip()
    return line if code == 0 and line else None


def _same(path_a, path_b):
    """Return whether two paths (bytes or str) name the same place, links resolved.

    SPEC: "Worktree refs".
    """
    # realpath: a worktree is often reached through a symbolic link (macOS /tmp, for one).
    return os.path.realpath(os.fsdecode(path_a)) == os.path.realpath(os.fsdecode(path_b))


def snapshot(ref, env, tempdir, counter):
    """Return the id of a commit holding the worktree's current files.

    Tracked changes and untracked files that are not ignored are included, as `git add -A`
    would stage them. A worktree with nothing uncommitted resolves to its HEAD commit.

    Args:
        ref: The ref as the caller wrote it, worktree:PATH (bytes or str).
        env: The private object directory environment for git.
        tempdir: A private directory for the temporary index.
        counter: A one-item list, incremented to name each temporary index.

    Returns:
        The commit id, as hex bytes.

    Raises:
        Fail: PATH is not the top directory of a worktree of this repository, the worktree has
            no commit checked out, the environment redirects git, or a git command fails.

    SPEC: "Worktree refs".
    """
    data = ref if isinstance(ref, bytes) else os.fsencode(ref)
    path = os.fsdecode(data[len(PREFIX):])
    # With these set, git run in PATH would read some other repository, worktree or index.
    set_vars = [name for name in _REDIRECTS if os.environ.get(name)]
    if set_vars:
        raise Fail("a worktree: ref cannot be used while %s is set" % " or ".join(set_vars))
    not_ours = Fail("not the top directory of a worktree of this repository: %s" % show(ref))
    if not os.path.isdir(path):
        raise not_ours
    # Same repository: both must share one common git directory. Top directory: so that
    # `add -A` covers the whole worktree and paths come out relative to its root.
    common = ["rev-parse", "--path-format=absolute", "--git-common-dir"]
    ours, theirs = _line(common, None), _line(common, path)
    top = _line(["rev-parse", "--show-toplevel"], path)
    if not ours or not theirs or not top or not _same(ours, theirs) or not _same(top, path):
        raise not_ours
    # The snapshot's parent, which gives it the worktree's history and so a merge base.
    head = _line(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], path)
    if not head or not OID.match(head):
        raise Fail("worktree has no commit checked out: %s" % show(ref))
    # A copy of the worktree's own index keeps its cached file states, so only changed files
    # are read again. The copy is what git updates; the real index is only read.
    counter[0] += 1
    index = os.path.join(tempdir, "index-%d" % counter[0])
    real = _line(["rev-parse", "--path-format=absolute", "--git-path", "index"], path)
    if real and os.path.isfile(os.fsdecode(real)):
        shutil.copyfile(os.fsdecode(real), index)
    private = dict(env, GIT_INDEX_FILE=index, **_IDENTITY)
    # No index yet (nothing was ever staged there): start from the checked-out commit.
    if not os.path.isfile(index):
        code, _, err = git(["read-tree", head], private, cwd=path)
        if code != 0:
            raise Fail("cannot read the worktree %s: %s" % (show(ref), git_error(err, code)))
    # Stage everything a commit of the whole worktree would hold, into the private index:
    # changed and deleted tracked files, and untracked files that are not ignored.
    code, _, err = git(["-c", "core.fsmonitor=false", "-c", "advice.addEmbeddedRepo=false",
                        "add", "-A", "--", "."], private, cwd=path)
    if code != 0:
        raise Fail("cannot read the worktree %s: %s" % (show(ref), git_error(err, code)))
    tree = _line(["write-tree"], path, private)
    if not tree or not OID.match(tree):
        raise Fail("cannot read the worktree %s: git write-tree failed" % show(ref))
    # Nothing uncommitted: the worktree is its HEAD commit, and no object is needed.
    if tree == _line(["rev-parse", "--verify", "--quiet", head + b"^{tree}"], path, private):
        return head
    code, out, err = git(["commit-tree", tree, "-p", head, "-m", "worktree snapshot"],
                         private, cwd=path)
    sha = out.strip()
    if code != 0 or not OID.match(sha):
        raise Fail("cannot read the worktree %s: %s" % (show(ref), git_error(err, code)))
    return sha


def resolve_any(ref, env, tempdir, counter):
    """Return the commit id for a normal ref, or for the snapshot of a worktree: ref.

    Args and errors are those of `snapshot` and of `_common.resolve`.

    SPEC: "Worktree refs" and "Resolving a ref".
    """
    # Only the worktree: spelling is special; every other ref goes to git unchanged.
    if is_worktree_ref(ref):
        return snapshot(ref, env, tempdir, counter)
    return resolve(ref, env)
