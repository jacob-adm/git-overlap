# SPDX-License-Identifier: Apache-2.0
"""Clean-room fixture helpers for the acceptance suite (written from SPEC.md only).

Every fixture is a REAL git repository built with subprocess git. Commits are
built with plumbing (hash-object / update-index / write-tree / commit-tree)
through a private index file, so paths that a filesystem may refuse (TAB, LF,
non-UTF-8 bytes) and symlinks work on every platform without touching a
worktree. Nothing here is mocked.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# The package under test: GIT_OVERLAP_ROOT if set, else the checkout these tests live in.
PACKAGE_ROOT = os.environ.get(
    "GIT_OVERLAP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Fixed dates make commit ids, and so every output, identical from run to run.
FIXED_DATE = "2026-01-01T00:00:00+0000"
# update-index --index-info reads mode 0 with the null id as "remove this path".
ZERO_SHA = b"0" * 40


def installed_git_version():
    """(major, minor, patch) parsed from `git version`, e.g. "git version 2.50.1 (Apple Git-155)"."""
    out = subprocess.run(["git", "version"], stdout=subprocess.PIPE, check=True).stdout
    # The first dotted number, so vendor suffixes such as "(Apple Git-155)" are ignored.
    match = re.search(rb"(\d+)\.(\d+)(?:\.(\d+))?", out)
    return tuple(int(g or 0) for g in match.groups())


GIT_VERSION = installed_git_version()
# SPEC "Merge mechanism": git pairmerge needs git 2.43; on older git those tests skip.
PAIRMERGE_FLOOR = (2, 43, 0)
PAIRMERGE_OK = GIT_VERSION >= PAIRMERGE_FLOOR
needs_pairmerge_git = unittest.skipUnless(
    PAIRMERGE_OK, "git pairmerge needs git >= 2.43 (installed %s)" % (GIT_VERSION,))


def _b(value):
    """Return `value` as bytes, encoding str as UTF-8.

    Args:
        value: A str or bytes path, content or ref.

    Returns:
        The bytes.
    """
    return value if isinstance(value, bytes) else value.encode("utf-8")


class Sandbox(object):
    """A temp directory with a hermetic environment for git and the commands."""

    def __init__(self):
        """Make the temporary directory and the scrubbed environment."""
        # realpath: on macOS the temp dir sits behind a symlink, and git reports resolved paths.
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="go-accept-"))
        self.empty_config = os.path.join(self.tmp, "empty-gitconfig")
        # An empty global config file, so the user's ~/.gitconfig never leaks in.
        open(self.empty_config, "wb").close()
        env = {}
        for key, value in os.environ.items():
            # Drop the caller's git, Python and locale settings: tests must not depend on them.
            if key.startswith("GIT_") or key.startswith("PYTHON") or key.startswith("LC_"):
                continue
            if key in ("LANG", "LANGUAGE", "TZ"):
                continue
            env[key] = value
        env.update({
            "HOME": self.tmp,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": self.empty_config,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_AUTHOR_DATE": FIXED_DATE,
            "GIT_COMMITTER_DATE": FIXED_DATE,
            # Stop repository discovery at the sandbox, so a parent checkout is never found.
            "GIT_CEILING_DIRECTORIES": self.tmp,
        })
        self.env = env

    def mkdir(self, name):
        """Create a directory inside the sandbox.

        Args:
            name: The directory name.

        Returns:
            Its absolute path.
        """
        path = os.path.join(self.tmp, name)
        os.mkdir(path)
        return path

    def cleanup(self):
        """Remove the sandbox; leftovers are ignored, as a test result must not depend on them."""
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cmd(self, name, args, stdin=b"", cwd=None, env_extra=None):
        """Run `python -m git_overlap.<name> ARGS` as a black box."""
        env = dict(self.env)
        env["PYTHONPATH"] = PACKAGE_ROOT
        if env_extra:
            env.update(env_extra)
        argv = [sys.executable, "-m", "git_overlap." + name] + list(args)
        # A timeout, so a command that hangs fails its test instead of the whole run.
        return subprocess.run(argv, input=stdin, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, cwd=cwd or self.tmp,
                              env=env, timeout=120)

    def popen_cmd(self, name, args, cwd):
        """Start `python -m git_overlap.<name> ARGS` with all three streams piped.

        Args:
            name: The command module, e.g. "overlap".
            args: Command-line arguments.
            cwd: The directory to run in.

        Returns:
            The running subprocess.Popen, for tests that close its pipes early.
        """
        env = dict(self.env)
        env["PYTHONPATH"] = PACKAGE_ROOT
        argv = [sys.executable, "-m", "git_overlap." + name] + list(args)
        return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=cwd, env=env)


class Repo(object):
    """A real git repository inside a Sandbox."""

    def __init__(self, sandbox, name="repo"):
        """Create and initialise the repository, with main as its initial branch.

        Args:
            sandbox: The Sandbox to create it in.
            name: The directory name.
        """
        self.sb = sandbox
        self.path = sandbox.mkdir(name)
        # Content -> blob id, so repeated contents are hashed once.
        self._blob_cache = {}
        self.git("init", "-q", "-b", "main")

    def git(self, *args, **kw):
        """Run git in the repository and return its stdout.

        Args:
            *args: git's arguments.
            **kw: env_extra (dict), input (bytes), check (default True), quiet (default False).

        Returns:
            git's stdout, as bytes.

        Raises:
            RuntimeError: git failed (with check) or wrote to stderr (with quiet).
        """
        env = dict(self.sb.env)
        env.update(kw.get("env_extra") or {})
        # An identity in config as well as in the environment, so no command fails for want of one.
        proc = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
                              + [a for a in args],
                              input=kw.get("input"), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, cwd=self.path, env=env, timeout=120)
        if kw.get("check", True) and proc.returncode != 0:
            raise RuntimeError("git %r failed: %r" % (args, proc.stderr))
        if kw.get("quiet") and proc.stderr:
            # e.g. update-index "Ignoring path": a fixture that silently lost a path is a bug.
            raise RuntimeError("git %r warned: %r" % (args, proc.stderr))
        return proc.stdout

    def blob(self, content):
        """Write `content` as a blob and return its id.

        Args:
            content: The file content, str or bytes.

        Returns:
            The blob id, as bytes.
        """
        content = _b(content)
        if content not in self._blob_cache:
            self._blob_cache[content] = self.git("hash-object", "-w", "--stdin", input=content).strip()
        return self._blob_cache[content]

    def commit(self, parent, changes, ref=None, message="c"):
        """Commit on top of PARENT (None = root commit).

        CHANGES maps path (str/bytes) to file content (str/bytes), to None for
        a deletion, to ("link", target) for a symlink, or to ("gitlink", sha) for a submodule
        pointer. Returns the SHA (str).
        """
        # A private index file, so the repository's own index is never touched by a fixture.
        index = os.path.join(self.sb.tmp, "plumbing-index")
        if os.path.exists(index):
            os.remove(index)
        extra = {"GIT_INDEX_FILE": index}
        # Git for Windows' core.protectNTFS (on by default) refuses, in any index, paths that
        # NTFS cannot hold (TAB, LF, CR, '"', backslash, ...); update-index then drops them with
        # "Ignoring path" and exit 0. These commits never touch a worktree, so build the trees
        # every platform can hold; the commands under test still run with git's defaults.
        ntfs = ("-c", "core.protectNTFS=false")
        if parent:
            self.git(*ntfs + ("read-tree", parent), env_extra=extra)
        else:
            self.git(*ntfs + ("read-tree", "--empty"), env_extra=extra)
        # --index-info lines: "mode SP id TAB path NUL", one per change.
        info = []
        for path, value in changes.items():
            path = _b(path)
            if value is None:
                info.append(b"0 " + ZERO_SHA + b"\t" + path + b"\0")
            elif isinstance(value, tuple) and value[0] == "gitlink":
                info.append(b"160000 " + _b(value[1]) + b"\t" + path + b"\0")
            elif isinstance(value, tuple) and value[0] == "link":
                info.append(b"120000 " + self.blob(value[1]) + b"\t" + path + b"\0")
            else:
                info.append(b"100644 " + self.blob(value) + b"\t" + path + b"\0")
        self.git(*ntfs + ("update-index", "-z", "--index-info"), input=b"".join(info),
                 env_extra=extra, quiet=True)
        tree = self.git(*ntfs + ("write-tree",), env_extra=extra).strip().decode()
        cmd = ["commit-tree", tree, "-m", message]
        if parent:
            cmd += ["-p", parent]
        # commit-tree builds the commit without moving any ref; set_ref does that below.
        sha = self.git(*cmd).strip().decode()
        os.remove(index)
        if ref:
            self.set_ref(ref, sha)
        return sha

    def set_ref(self, ref, sha):
        """Point REF at SHA. On Windows a ref holding a character NTFS forbids in a file name
        (e.g. '|') cannot be a loose ref file, so it goes into packed-refs, where git reads it
        on every platform."""
        if os.name == "nt" and re.search(r'[\x00-\x1f"*:<>?|]', ref):
            packed = os.path.join(self.path, ".git", "packed-refs")
            # No "sorted" header: git sorts an unheaded packed-refs file when it reads it.
            with open(packed, "ab") as fh:
                fh.write(_b(sha) + b" " + _b(ref) + b"\n")
        else:
            self.git("update-ref", ref, sha)

    def rev(self, spec):
        """Resolve `spec` to an object id.

        Args:
            spec: Any revision git accepts.

        Returns:
            The id, as str.
        """
        # --end-of-options, so a spec starting with "-" is not read as an option.
        return self.git("rev-parse", "--verify", "--end-of-options", spec).strip().decode()

    def run(self, name, args, stdin=b"", env_extra=None):
        """Run a command under test inside this repository.

        Args:
            name: The command module, e.g. "footprint".
            args: Command-line arguments.
            stdin: Input bytes.
            env_extra: Extra environment variables, or None.

        Returns:
            The finished subprocess.CompletedProcess.
        """
        return self.sb.run_cmd(name, args, stdin=stdin, cwd=self.path, env_extra=env_extra)

    def state(self):
        """Snapshot of refs, HEAD, index bytes and worktree status (read-only git calls)."""
        index_path = os.path.join(self.path, ".git", "index")
        index = None
        if os.path.exists(index_path):
            with open(index_path, "rb") as fh:
                index = fh.read()
        # No optional locks: status would otherwise refresh the index, changing what is measured.
        ro = {"GIT_OPTIONAL_LOCKS": "0"}
        return {
            "index": index,
            "refs": self.git("for-each-ref", "--format=%(refname) %(objectname)", env_extra=ro),
            "head": self.git("symbolic-ref", "-q", "HEAD", check=False, env_extra=ro),
            "head_sha": self.git("rev-parse", "HEAD", env_extra=ro),
            "status": self.git("--no-optional-locks", "status", "--porcelain=v1", "-z",
                               "--untracked-files=all", "--ignored", env_extra=ro),
            "files": self._worktree_bytes(),
            "objects": self.object_files(),
        }

    def object_files(self):
        """Every file under .git/objects (loose objects, packs, info), sorted."""
        top = os.path.join(self.path, ".git", "objects")
        found = []
        for root, _dirs, files in os.walk(top):
            for name in files:
                found.append(os.path.relpath(os.path.join(root, name), top))
        return sorted(found)

    def _worktree_bytes(self):
        """Map every worktree file (outside .git) to its bytes.

        Returns:
            A dict of relative path -> content.
        """
        out = {}
        for root, dirs, files in os.walk(self.path):
            # Pruning dirs in place stops os.walk from descending into .git.
            if ".git" in dirs:
                dirs.remove(".git")
            for name in files:
                full = os.path.join(root, name)
                with open(full, "rb") as fh:
                    out[os.path.relpath(full, self.path)] = fh.read()
        return out


def gherkin_repo(sandbox):
    """main: x.py, z.py; a edits x.py; b adds y.py and deletes z.py."""
    repo = Repo(sandbox)
    base = repo.commit(None, {"x.py": "x\n", "z.py": "z\n"}, ref="refs/heads/main")
    repo.commit(base, {"x.py": "x changed\n"}, ref="refs/heads/a")
    repo.commit(base, {"y.py": "y\n", "z.py": None}, ref="refs/heads/b")
    return repo, base


# SPEC "Errors": exactly one line, `git-<name>: <message>`, ending in LF.
ERR_LINE = re.compile(rb"\Agit-(footprint|overlap|pairmerge): [^\n]+\n\Z")


def assert_error(test, proc, name, must_contain=None):
    """Exit 2, one stderr line `git-<name>: <message>`, never a traceback."""
    test.assertEqual(proc.returncode, 2, "exit status; stderr=%r" % (proc.stderr,))
    test.assertNotIn(b"Traceback", proc.stderr)
    test.assertTrue(proc.stderr.startswith(("git-%s: " % name).encode()),
                    "stderr prefix: %r" % (proc.stderr,))
    test.assertRegex(proc.stderr, ERR_LINE, "stderr must be exactly one line")
    if must_contain is not None:
        if isinstance(must_contain, bytes):
            test.assertIn(must_contain, proc.stderr)
        else:
            test.assertRegex(proc.stderr, must_contain)


def closed_stdout_run(test, sandbox, name, args, stdin, cwd):
    """Start the command, read a few bytes, close the pipe, expect quiet exit 0."""
    # The commands read all of stdin before writing, so writing it all first cannot deadlock.
    proc = sandbox.popen_cmd(name, args, cwd)
    try:
        proc.stdin.write(stdin)
        proc.stdin.close()
    # The command may already have exited; that is judged by the checks below.
    except BrokenPipeError:
        pass
    # Read a little, then close: the command meets EPIPE on its next write.
    first = proc.stdout.read(16)
    proc.stdout.close()
    err = proc.stderr.read()
    proc.stderr.close()
    code = proc.wait(timeout=120)
    test.assertTrue(first, "command produced no output before the close")
    test.assertNotIn(b"Traceback", err)
    test.assertEqual(err, b"", "a closed stdout must end the command quietly")
    test.assertEqual(code, 0)
