# SPDX-License-Identifier: Apache-2.0
"""Shared plumbing for the git-overlap commands: records, arguments, errors and git.

Everything that crosses a process boundary (stdin, stdout, git's output) is bytes. Nothing is
decoded except to build a human-readable error message for stderr.
"""

import errno
import os
import re
import signal
import subprocess
import sys

from . import __version__

# Exit codes, as grep and diff use them (SPEC "Exit status").
EXIT_NOTHING = 0  # ran, nothing found
EXIT_FOUND = 1  # ran, something found
EXIT_ERROR = 2  # error

# Bytes that cannot appear inside a field of an LF-terminated record (SPEC "Unsafe names").
_UNSAFE_LF = (b"\t", b"\n", b"\r")

# One object id: 40 hex digits (SHA-1) or 64 (SHA-256).
OID = re.compile(rb"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")

# Escapes for error messages (SPEC "Errors"); other unprintable bytes become \xHH.
_ESCAPES = {0x09: "\\t", 0x0A: "\\n", 0x0D: "\\r", 0x5C: "\\\\"}


class Fail(Exception):
    """A user-facing error: `run` prints `git-<name>: <message>` and exits 2."""


class PipeClosed(Exception):
    """Raised when a write to stdout finds the reader gone; `run` exits 0 quietly."""


class Interrupted(BaseException):
    """Raised by a signal handler so `finally` blocks run; `run` exits 128 + signum."""

    def __init__(self, signum):
        """Remember which signal arrived."""
        BaseException.__init__(self, signum)
        self.signum = signum


def show(data):
    """Escape a ref or path (bytes or str) for an error message, so it stays on one line.

    TAB, LF, CR and backslash become backslash escapes; any other byte that is not printable
    ASCII becomes \\xHH. Used for stderr only, never for records.
    """
    if not isinstance(data, bytes):
        # Command-line text: os.fsencode gives back the bytes the caller typed.
        data = os.fsencode(data)
    out = []
    for byte in bytearray(data):
        if byte in _ESCAPES:
            out.append(_ESCAPES[byte])
        elif 0x20 <= byte < 0x7F:
            out.append(chr(byte))
        else:
            out.append("\\x%02x" % byte)
    return "".join(out)


def parse_args(argv, flags, valued):
    """Split argv into ({option: value}, [positionals]) without treating refs as options.

    `flags` are options without a value (e.g. "-z"); `valued` take one (e.g. "--with REF" or
    "--with=REF"). Any other argument, even one starting with "-", is a positional: a ref
    starting with "-" is a ref, never an option (SPEC "Refs"). "--" ends option parsing.
    """
    opts = {}
    positionals = []
    missing = None
    i = 0
    # Walk the arguments by index, because a valued option consumes the next one.
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            # Everything after "--" is positional, whatever it looks like.
            positionals.extend(argv[i + 1:])
            break
        if arg in flags:
            opts[arg] = True
        elif arg in valued:
            # The value is the next argument verbatim, so "--with -x" means ref "-x".
            if i + 1 >= len(argv):
                # Raised after the loop, so a -h or --version anywhere still wins.
                missing = arg
                break
            opts[arg] = argv[i + 1]
            i += 1
        elif "=" in arg and arg.split("=", 1)[0] in valued:
            # The "--with=REF" spelling of a valued option.
            key, value = arg.split("=", 1)
            opts[key] = value
        else:
            positionals.append(arg)
        i += 1
    if missing and not (opts.get("-h") or opts.get("--version")):
        raise Fail("option %s needs a value (see -h)" % missing)
    return opts, positionals


def read_records(zero):
    """Read all of stdin and return [(record number, record bytes)], empty records skipped.

    LF mode splits on LF and removes one trailing CR; -z mode splits on NUL and removes
    nothing. Record numbers count every record, skipped ones included, from 1 (like line
    numbers), so an error message points at the line the caller sees in their file.
    """
    # Python sets sys.stdin to None when file descriptor 0 is not open at all.
    if sys.stdin is None:
        raise Fail("standard input is not open")
    data = sys.stdin.buffer.read()
    terminator = b"\0" if zero else b"\n"
    parts = data.split(terminator)
    # A final terminator leaves one empty tail, which is not a record. Without a final
    # terminator the last part is the unterminated last record, which is accepted.
    if parts and parts[-1] == b"":
        parts.pop()
    records = []
    for number, record in enumerate(parts, 1):
        # Input tolerance applies to LF mode only (SPEC "Input tolerance").
        if not zero and record.endswith(b"\r"):
            record = record[:-1]
        # Empty records are skipped in both modes.
        if record == b"":
            continue
        records.append((number, record))
    return records


def check_safe(field, zero, what):
    """Fail if `field` cannot be written in an LF record; in -z mode every field is safe."""
    if zero:
        return
    for byte in _UNSAFE_LF:
        if byte in field:
            # Never quote or escape: point the caller at -z instead.
            raise Fail("%s contains TAB, LF or CR and cannot be written as a line; "
                       "use -z: %s" % (what, show(field)))


def write_stdout(data):
    """Write bytes to stdout; a reader that went away becomes PipeClosed.

    EPIPE is the closed pipe; Windows reports the same as EINVAL. Only here, on stdout
    writes and flushes, are those errors read as "the reader left". (Not covered by a test:
    EINVAL from a closed pipe happens only on Windows, and provoking EINVAL from another
    call, to show it is not misread, would need a mock, which the test rules forbid.)
    """
    try:
        if data is None:
            sys.stdout.flush()
        else:
            sys.stdout.buffer.write(data)
    except OSError as exc:
        if isinstance(exc, BrokenPipeError) or exc.errno in (errno.EPIPE, errno.EINVAL):
            raise PipeClosed()
        raise


def write_record(fields, zero):
    """Write one record of bytes fields to stdout, TAB-separated, LF- or NUL-terminated."""
    write_stdout(b"\t".join(fields) + (b"\0" if zero else b"\n"))


def git(args, extra_env=None, data=None, cwd=None):
    """Run git with list args (never a shell) and LC_ALL=C; return (code, stdout, stderr) bytes.

    Arguments may be str or bytes; bytes are converted with os.fsdecode, which gives the
    original bytes back to git on POSIX and UTF-8 on Windows. `extra_env` adds environment
    variables; `data` (bytes) is fed to git's stdin, which is otherwise empty; `cwd` is the
    directory git runs in (default: ours).
    """
    env = dict(os.environ)
    env.update(extra_env or {})
    # A fixed locale keeps git's behaviour and messages independent of the user's settings.
    env["LC_ALL"] = "C"
    argv = ["git"]
    for arg in args:
        if isinstance(arg, bytes):
            try:
                arg = os.fsdecode(arg)
            except UnicodeDecodeError:
                # Windows passes arguments as UTF-16, so other bytes cannot reach git there.
                raise Fail("cannot pass a name that is not valid UTF-8 to git on this "
                           "platform: %s" % show(arg))
        argv.append(arg)
    # subprocess.run refuses stdin= together with input=, so pick exactly one.
    stdin_kw = {"input": data} if data is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                              cwd=cwd, **stdin_kw)
    except OSError as exc:
        # git missing from PATH, or not executable.
        raise Fail("cannot run git: %s" % exc)
    return proc.returncode, proc.stdout, proc.stderr


def resolve(ref, extra_env=None):
    """Return the commit id (bytes) that `ref` names; Fail if it is unknown or not a commit.

    Two steps (SPEC "Resolving a ref"): `rev-parse --verify` must print exactly one object
    id, which rejects "^a", ranges and other non-single revisions, and that id must then
    peel to a commit. --end-of-options makes a ref starting with "-" a ref, never an option.
    """
    error = Fail("unknown ref or not a commit: %s" % show(ref))
    code, out, _ = git(["rev-parse", "--verify", "--quiet", "--end-of-options", ref],
                       extra_env)
    oid = out[:-1] if out.endswith(b"\n") else out
    if code != 0 or not OID.match(oid):
        raise error
    code, out, _ = git(["rev-parse", "--verify", "--quiet", oid + b"^{commit}"], extra_env)
    sha = out.strip()
    if code != 0 or not OID.match(sha):
        raise error
    return sha


def git_error(stderr, code):
    """Describe a git failure for an error message: the first line of stderr, escaped.

    With nothing on stderr, the exit status is the only clue; a negative status from
    subprocess means git was killed by that signal number.
    """
    lines = stderr.strip().splitlines()
    if lines:
        return show(lines[0])
    if code < 0:
        return "git was killed by signal %d" % -code
    return "git exited with status %d" % code


def usage_and_version(opts, name, usage):
    """Handle -h and --version: print to stdout and return True if the command should stop."""
    if opts.get("-h"):
        write_stdout((usage + "\n").encode("ascii"))
        return True
    if opts.get("--version"):
        write_stdout(("git-%s %s\n" % (name, __version__)).encode("ascii"))
        return True
    return False


def install_signal_handlers():
    """Turn SIGINT, SIGTERM and SIGHUP into Interrupted, so cleanup in `finally` runs.

    SIGHUP does not exist on Windows. SIGPIPE stays as Python sets it (ignored), so a reader
    closing the pipe arrives as EPIPE, which the SPEC treats as a quiet exit 0.
    """
    def handler(signum, _frame):
        """Unwind the stack; `run` turns this into exit 128 + signum."""
        raise Interrupted(signum)
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, handler)


def ignore_signals():
    """Ignore the signals install_signal_handlers catches, so cleanup is not cut short."""
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, signal.SIG_IGN)


def write_stderr_line(text):
    """Write `text` and one LF to stderr as bytes, if stderr exists.

    Bytes, because a text-mode stderr on Windows turns LF into CR LF, and the SPEC's error
    is one line ending in LF on every platform.
    """
    if sys.stderr is None:
        return
    sys.stderr.flush()
    stream = getattr(sys.stderr, "buffer", None)
    if stream is None:
        # A replaced stderr without a byte buffer: text is all it takes.
        sys.stderr.write(text + "\n")
        sys.stderr.flush()
        return
    encoding = getattr(sys.stderr, "encoding", None) or "utf-8"
    stream.write((text + "\n").encode(encoding, "backslashreplace"))
    stream.flush()


def _error(name, message):
    """Write `git-<name>: <message>` to stderr as exactly one line, if stderr exists."""
    # Belt and braces: messages are built escaped, but never let one span two lines.
    message = str(message).replace("\r", "\\r").replace("\n", "\\n")
    write_stderr_line("git-%s: %s" % (name, message))


def _discard_stdout():
    """Point file descriptor 1 at the null device, so leftover buffered output goes nowhere."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stdout.fileno())


def run(name, body):
    """Call body() and exit with its code; map every failure to the SPEC's exit and message.

    Fail -> one stderr line and exit 2. A closed stdout pipe -> quiet exit 0. A signal ->
    exit 128 + signum. Anything else unexpected -> one stderr line and exit 2, never a
    traceback.
    """
    # Python sets sys.stdout to None when file descriptor 1 is not open at all.
    if sys.stdout is None:
        _error(name, "standard output is not open")
        sys.exit(EXIT_ERROR)
    try:
        code = body()
        # Flush inside the try, so a pipe closed during the final flush is caught here.
        write_stdout(None)
    except Fail as exc:
        _error(name, exc)
        code = EXIT_ERROR
    except Interrupted as exc:
        # Drop unwritten output: the exit-time flush must not block on a reader that stalls.
        _discard_stdout()
        code = 128 + exc.signum
    except KeyboardInterrupt:
        # SIGINT where no handler was installed: same exit status as a handled one.
        code = 128 + 2
    except PipeClosed:
        # Python's exit-time flush would hit the closed pipe again; send it nowhere instead.
        _discard_stdout()
        code = EXIT_NOTHING
    except Exception as exc:  # noqa: BLE001 - the SPEC forbids tracebacks
        _error(name, "internal error: %s" % exc)
        code = EXIT_ERROR
    sys.exit(code)
