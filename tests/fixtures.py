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
FIXED_DATE = "2026-01-01T00:00:00+0000"
ZERO_SHA = b"0" * 40


def installed_git_version():
    """(major, minor, patch) parsed from `git version`, e.g. "git version 2.50.1 (Apple Git-155)"."""
    out = subprocess.run(["git", "version"], stdout=subprocess.PIPE, check=True).stdout
    match = re.search(rb"(\d+)\.(\d+)(?:\.(\d+))?", out)
    return tuple(int(g or 0) for g in match.groups())


GIT_VERSION = installed_git_version()
PAIRMERGE_FLOOR = (2, 43, 0)
PAIRMERGE_OK = GIT_VERSION >= PAIRMERGE_FLOOR
needs_pairmerge_git = unittest.skipUnless(
    PAIRMERGE_OK, "git pairmerge needs git >= 2.43 (installed %s)" % (GIT_VERSION,))


def _b(value):
    return value if isinstance(value, bytes) else value.encode("utf-8")


class Sandbox(object):
    """A temp directory with a hermetic environment for git and the commands."""

    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="go-accept-"))
        self.empty_config = os.path.join(self.tmp, "empty-gitconfig")
        open(self.empty_config, "wb").close()
        env = {}
        for key, value in os.environ.items():
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
            "GIT_CEILING_DIRECTORIES": self.tmp,
        })
        self.env = env

    def mkdir(self, name):
        path = os.path.join(self.tmp, name)
        os.mkdir(path)
        return path

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cmd(self, name, args, stdin=b"", cwd=None, env_extra=None):
        """Run `python -m git_overlap.<name> ARGS` as a black box."""
        env = dict(self.env)
        env["PYTHONPATH"] = PACKAGE_ROOT
        if env_extra:
            env.update(env_extra)
        argv = [sys.executable, "-m", "git_overlap." + name] + list(args)
        return subprocess.run(argv, input=stdin, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, cwd=cwd or self.tmp,
                              env=env, timeout=120)

    def popen_cmd(self, name, args, cwd):
        env = dict(self.env)
        env["PYTHONPATH"] = PACKAGE_ROOT
        argv = [sys.executable, "-m", "git_overlap." + name] + list(args)
        return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=cwd, env=env)


class Repo(object):
    """A real git repository inside a Sandbox."""

    def __init__(self, sandbox, name="repo"):
        self.sb = sandbox
        self.path = sandbox.mkdir(name)
        self._blob_cache = {}
        self.git("init", "-q", "-b", "main")

    def git(self, *args, **kw):
        env = dict(self.sb.env)
        env.update(kw.get("env_extra") or {})
        proc = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
                              + [a for a in args],
                              input=kw.get("input"), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, cwd=self.path, env=env, timeout=120)
        if kw.get("check", True) and proc.returncode != 0:
            raise RuntimeError("git %r failed: %r" % (args, proc.stderr))
        return proc.stdout

    def blob(self, content):
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
        index = os.path.join(self.sb.tmp, "plumbing-index")
        if os.path.exists(index):
            os.remove(index)
        extra = {"GIT_INDEX_FILE": index}
        if parent:
            self.git("read-tree", parent, env_extra=extra)
        else:
            self.git("read-tree", "--empty", env_extra=extra)
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
        self.git("update-index", "-z", "--index-info", input=b"".join(info), env_extra=extra)
        tree = self.git("write-tree", env_extra=extra).strip().decode()
        cmd = ["commit-tree", tree, "-m", message]
        if parent:
            cmd += ["-p", parent]
        sha = self.git(*cmd).strip().decode()
        os.remove(index)
        if ref:
            self.git("update-ref", ref, sha)
        return sha

    def rev(self, spec):
        return self.git("rev-parse", "--verify", "--end-of-options", spec).strip().decode()

    def run(self, name, args, stdin=b"", env_extra=None):
        return self.sb.run_cmd(name, args, stdin=stdin, cwd=self.path, env_extra=env_extra)

    def state(self):
        """Snapshot of refs, HEAD, index bytes and worktree status (read-only git calls)."""
        index_path = os.path.join(self.path, ".git", "index")
        index = None
        if os.path.exists(index_path):
            with open(index_path, "rb") as fh:
                index = fh.read()
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
        out = {}
        for root, dirs, files in os.walk(self.path):
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
    proc = sandbox.popen_cmd(name, args, cwd)
    try:
        proc.stdin.write(stdin)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    first = proc.stdout.read(16)
    proc.stdout.close()
    err = proc.stderr.read()
    proc.stderr.close()
    code = proc.wait(timeout=120)
    test.assertTrue(first, "command produced no output before the close")
    test.assertNotIn(b"Traceback", err)
    test.assertEqual(err, b"", "a closed stdout must end the command quietly")
    test.assertEqual(code, 0)
