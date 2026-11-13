# SPDX-License-Identifier: Apache-2.0
"""Shared plumbing for the git-overlap commands: records, arguments, errors and git.

Everything that crosses a process boundary (stdin, stdout, git's output) is bytes. Nothing is
decoded except to build a human-readable error message for stderr.
"""

import errno
import os
import subprocess
import sys

from . import __version__

# Exit codes, as grep and diff use them (SPEC "Exit status").
EXIT_NOTHING = 0  # ran, nothing found
EXIT_FOUND = 1  # ran, something found
EXIT_ERROR = 2  # error

# Bytes that cannot appear inside a field of an LF-terminated record (SPEC "Unsafe names").
_UNSAFE_LF = (b"\t", b"\n", b"\r")


class Fail(Exception):
    """A user-facing error: `run` prints `git-<name>: <message>` and exits 2."""


def show(data):
    """Turn bytes (a ref or path) into text for an error message only; never for output."""
    # Replacement characters are fine here: stderr is for humans, records stay bytes.
    return data.decode("utf-8", "replace")


def parse_args(argv, flags, valued):
    """Split argv into ({option: value}, [positionals]) without treating refs as options.

    `flags` are options without a value (e.g. "-z"); `valued` take one (e.g. "--with REF" or
    "--with=REF"). Any other argument, even one starting with "-", is a positional: a ref
    starting with "-" is a ref, never an option (SPEC "Refs"). "--" ends option parsing.
    """
    opts = {}
    positionals = []
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
                raise Fail("option %s needs a value (see -h)" % arg)
            opts[arg] = argv[i + 1]
            i += 1
        elif "=" in arg and arg.split("=", 1)[0] in valued:
            # The "--with=REF" spelling of a valued option.
            key, value = arg.split("=", 1)
            opts[key] = value
        else:
            positionals.append(arg)
        i += 1
    return opts, positionals


def read_records(zero):
    """Read all of stdin and return [(record number, record bytes)], empty records skipped.

    LF mode splits on LF and removes one trailing CR; -z mode splits on NUL and removes
    nothing. Record numbers count every record, skipped ones included, from 1 (like line
    numbers), so an error message points at the line the caller sees in their file.
    """
    data = sys.stdin.buffer.read()
    terminator = b"\0" if zero else b"\n"
    parts = data.split(terminator)
    # A final terminator leaves one empty tail, which is not a record.
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
                       "use -z: %r" % (what, show(field)))


def write_record(fields, zero):
    """Write one record of bytes fields to stdout, TAB-separated, LF- or NUL-terminated."""
    sys.stdout.buffer.write(b"\t".join(fields) + (b"\0" if zero else b"\n"))


def git(args):
    """Run git with list args (never a shell) and LC_ALL=C; return (code, stdout, stderr) bytes.

    Arguments may be str or bytes; bytes are converted with os.fsdecode, which gives the
    original bytes back to git on POSIX and UTF-8 on Windows.
    """
    env = dict(os.environ)
    # A fixed locale keeps git's behaviour and messages independent of the user's settings.
    env["LC_ALL"] = "C"
    argv = ["git"] + [os.fsdecode(a) if isinstance(a, bytes) else a for a in args]
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env)
    except OSError as exc:
        # git missing from PATH, or not executable.
        raise Fail("cannot run git: %s" % exc)
    return proc.returncode, proc.stdout, proc.stderr


def git_error(stderr):
    """Return the first line of git's stderr as text, for use inside an error message."""
    lines = show(stderr).strip().splitlines()
    return lines[0] if lines else "git failed"


def usage_and_version(opts, name, usage):
    """Handle -h and --version: print to stdout and return True if the command should stop."""
    if opts.get("-h"):
        sys.stdout.write(usage + "\n")
        return True
    if opts.get("--version"):
        sys.stdout.write("git-%s %s\n" % (name, __version__))
        return True
    return False


def _is_closed_pipe(exc):
    """True if `exc` means stdout's reader went away (EPIPE; EINVAL is how Windows says it)."""
    return isinstance(exc, BrokenPipeError) or (
        isinstance(exc, OSError) and exc.errno in (errno.EPIPE, errno.EINVAL))


def run(name, body):
    """Call body() and exit with its code; map every failure to the SPEC's exit and message.

    Fail -> one stderr line and exit 2. A closed stdout -> quiet exit 0. Anything else
    unexpected -> one stderr line and exit 2, never a traceback.
    """
    try:
        code = body()
        # Flush inside the try, so a pipe closed during the final flush is caught here.
        sys.stdout.flush()
    except Fail as exc:
        sys.stderr.write("git-%s: %s\n" % (name, exc))
        code = EXIT_ERROR
    except KeyboardInterrupt:
        sys.stderr.write("git-%s: interrupted\n" % name)
        code = EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - the SPEC forbids tracebacks
        if _is_closed_pipe(exc):
            # Point stdout at the null device so Python's exit-time flush stays quiet.
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            code = EXIT_NOTHING
        else:
            sys.stderr.write("git-%s: internal error: %s\n" % (name, exc))
            code = EXIT_ERROR
    sys.exit(code)
