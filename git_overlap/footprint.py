# SPDX-License-Identifier: Apache-2.0
"""git footprint BASE: list every file each input ref changes since its merge base with BASE.

Reads one ref per record on stdin and writes `ref TAB status TAB path` records, the same
set of files a pull request's "files changed" tab shows. Contract: SPEC.md.
"""

import sys

from ._common import (EXIT_NOTHING, Fail, check_safe, git, git_error, parse_args,
                      read_records, resolve, run, show, usage_and_version, write_record)

NAME = "footprint"
USAGE = """usage: git footprint [-z] BASE < refs

For each ref on stdin, list the files it changes compared with its merge base with BASE:
  ref TAB status TAB path      (status: A, M, D or T; renames are D + A)

  -z          records end with NUL instead of LF, on input and output
  -h          show this help
  --version   show the version"""


def merge_base(base_sha, sha, ref):
    """Return the merge base (bytes) of two commit SHAs; Fail if they share no history."""
    code, out, err = git(["merge-base", base_sha, sha])
    # merge-base exits 1 with no output when there is no common ancestor.
    if code == 1 and not out.strip():
        raise Fail("no merge base between BASE and %s" % show(ref))
    if code != 0:
        raise Fail("git merge-base failed for %s: %s" % (show(ref), git_error(err)))
    return out.split(b"\n")[0].strip()


def changed_files(mb, sha):
    """Return [(status, path)] of what `sha` changes relative to `mb`, sorted by path bytewise.

    Renames and copies are off, so a rename is D of the old path plus A of the new one.
    External diff drivers and textconv are off: only names are wanted.
    """
    code, out, err = git(["diff", "--name-status", "-z", "--no-renames", "--no-ext-diff",
                          "--no-textconv", "--no-relative", mb, sha])
    if code != 0:
        raise Fail("git diff failed: %s" % git_error(err))
    # -z name-status output is "STATUS NUL PATH NUL" repeated; drop the final empty tail.
    parts = out.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) % 2:
        raise Fail("unexpected git diff output")
    pairs = [(parts[i], parts[i + 1]) for i in range(0, len(parts), 2)]
    # Sort by path bytewise (SPEC "Determinism"); git's own order may follow diff.orderFile.
    pairs.sort(key=lambda pair: pair[1])
    return pairs


def footprint(argv):
    """Run the command on argv (without the program name); return the exit code."""
    opts, positionals = parse_args(argv, ("-z", "-h", "--version"), ())
    if usage_and_version(opts, NAME, USAGE):
        return EXIT_NOTHING
    if len(positionals) != 1:
        raise Fail("expected exactly one BASE argument (see -h)")
    zero = bool(opts.get("-z"))
    # Resolve BASE before reading stdin, so a bad BASE fails even with no input.
    base_sha = resolve(positionals[0])
    seen = set()
    for number, ref in read_records(zero):
        # A record is the whole ref; in LF mode a TAB or CR in it cannot round-trip.
        try:
            check_safe(ref, zero, "ref")
        except Fail as exc:
            raise Fail("record %d: %s" % (number, exc))
        # A repeated ref is processed once, at its first occurrence.
        if ref in seen:
            continue
        seen.add(ref)
        try:
            sha = resolve(ref)
        except Fail as exc:
            raise Fail("record %d: %s" % (number, exc))
        mb = merge_base(base_sha, sha, ref)
        for status, path in changed_files(mb, sha):
            check_safe(path, zero, "path")
            # The ref is printed exactly as the caller wrote it.
            write_record((ref, status, path), zero)
    # A footprint is not a finding: success is always 0.
    return EXIT_NOTHING


def main():
    """Console-script entry point: run on sys.argv and exit with the SPEC's status."""
    run(NAME, lambda: footprint(sys.argv[1:]))


if __name__ == "__main__":
    main()
