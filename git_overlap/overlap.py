# SPDX-License-Identifier: Apache-2.0
"""git overlap [--with REF]: report pairs of refs that touch the same path.

Reads footprint records (`ref TAB path` or `ref TAB status TAB path`) on stdin and writes
`refA TAB refB TAB path` records with refA < refB bytewise. Needs no repository.
Contract: SPEC.md.
"""

import os
import sys

from ._common import (EXIT_FOUND, EXIT_NOTHING, Fail, check_safe, parse_args, read_records,
                      run, usage_and_version, write_record)

NAME = "overlap"
USAGE = """usage: git overlap [-z] [--with REF] < footprint

Read ref/path records (the first field is the ref, the last the path) and report each pair
of refs that touch the same path:
  refA TAB refB TAB path       (refA < refB bytewise; exit 1 if any, else 0)

  --with REF  only pairs that include REF
  -z          records end with NUL instead of LF, on input and output
  -h          show this help
  --version   show the version"""

# Status letters git footprint writes; used to find the path field of a -z record.
_STATUSES = (b"A", b"M", b"D", b"T")


def split_record(record, zero):
    """Return (ref, path) of one record, or None if it has fewer than 2 fields.

    In LF mode the path is the last TAB field (SPEC). In -z mode a path may itself contain
    TAB, so "last field" would cut it: the path is everything after the ref, or after the
    status too when the second field is one of git footprint's status letters.
    """
    fields = record.split(b"\t")
    if len(fields) < 2:
        return None
    if not zero:
        return fields[0], fields[-1]
    # Refs cannot contain TAB, so the ref always ends at the first TAB.
    if len(fields) >= 3 and fields[1] in _STATUSES:
        return fields[0], record.split(b"\t", 2)[2]
    return fields[0], record.split(b"\t", 1)[1]


def overlap(argv):
    """Run the command on argv (without the program name); return the exit code."""
    opts, positionals = parse_args(argv, ("-z", "-h", "--version"), ("--with",))
    if usage_and_version(opts, NAME, USAGE):
        return EXIT_NOTHING
    if positionals:
        raise Fail("unexpected argument: %s (see -h)" % positionals[0])
    zero = bool(opts.get("-z"))
    # The command line is text; os.fsencode gives back the bytes the caller typed.
    with_ref = os.fsencode(opts["--with"]) if "--with" in opts else None
    # path -> set of refs that touch it.
    touched = {}
    for number, record in read_records(zero):
        parsed = split_record(record, zero)
        if parsed is None:
            raise Fail("record %d: expected at least 2 TAB-separated fields" % number)
        ref, path = parsed
        touched.setdefault(path, set()).add(ref)
    # A set of (refA, refB, path) writes each duplicate once; a ref never pairs with itself.
    pairs = set()
    for path, refs in touched.items():
        ordered = sorted(refs)
        for i, ref_a in enumerate(ordered):
            for ref_b in ordered[i + 1:]:
                if with_ref is None or with_ref in (ref_a, ref_b):
                    pairs.add((ref_a, ref_b, path))
    # Tuples of bytes sort bytewise by (refA, refB, path), as the SPEC requires.
    for pair in sorted(pairs):
        for field in pair:
            check_safe(field, zero, "ref or path")
        write_record(pair, zero)
    return EXIT_FOUND if pairs else EXIT_NOTHING


def main():
    """Console-script entry point: run on sys.argv and exit with the SPEC's status."""
    run(NAME, lambda: overlap(sys.argv[1:]))


if __name__ == "__main__":
    main()
