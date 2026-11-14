# SPDX-License-Identifier: Apache-2.0
"""git overlap [--with REF]: report pairs of refs that touch the same path.

Reads footprint records (`ref TAB status TAB path`) on stdin and writes
`refA TAB refB TAB path` records with refA < refB bytewise. Needs no repository.
Contract: SPEC.md.
"""

import os
import sys

from ._common import (EXIT_FOUND, EXIT_NOTHING, Fail, check_safe, parse_args, read_records,
                      run, show, usage_and_version, write_record)

NAME = "overlap"
USAGE = """usage: git overlap [-z] [--with REF] < footprint

Read git footprint records (ref TAB status TAB path) and report each pair of refs that
touch the same path:
  refA TAB refB TAB path       (refA < refB bytewise; exit 1 if any, else 0)

  --with REF  only pairs that include REF
  -z          records end with NUL instead of LF, on input and output
  -h          show this help
  --version   show the version"""

def split_record(record):
    """Return (ref, path) of one `ref TAB status TAB path` record, or None if malformed.

    One rule in both modes: the ref ends at the first TAB, the status (ignored) at the
    second, and the path is everything after it, so a -z path may itself contain TAB.
    Fewer than 3 fields, or an empty ref or path, is malformed.
    """
    fields = record.split(b"\t", 2)
    if len(fields) < 3 or fields[0] == b"" or fields[2] == b"":
        return None
    return fields[0], fields[2]


def overlap(argv):
    """Run the command on argv (without the program name); return the exit code."""
    opts, positionals = parse_args(argv, ("-z", "-h", "--version"), ("--with",))
    if usage_and_version(opts, NAME, USAGE):
        return EXIT_NOTHING
    if positionals:
        raise Fail("unexpected argument: %s (see -h)" % show(positionals[0]))
    zero = bool(opts.get("-z"))
    # The command line is text; os.fsencode gives back the bytes the caller typed.
    with_ref = os.fsencode(opts["--with"]) if "--with" in opts else None
    # An empty REF could never match a ref: it is a usage error, not "nothing found".
    if with_ref == b"":
        raise Fail("--with needs a non-empty REF (see -h)")
    # path -> {ref: number of the first record naming that ref and path}.
    touched = {}
    for number, record in read_records(zero):
        parsed = split_record(record)
        if parsed is None:
            raise Fail("record %d: expected ref TAB status TAB path, with a non-empty ref and path"
                       % number)
        ref, path = parsed
        touched.setdefault(path, {}).setdefault(ref, number)
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
        ref_a, ref_b, path = pair
        try:
            for field in pair:
                check_safe(field, zero, "ref or path")
        except Fail as exc:
            # Name the earliest input record behind this pair (SPEC "Record errors").
            number = min(touched[path][ref_a], touched[path][ref_b])
            raise Fail("record %d: %s" % (number, exc))
        write_record(pair, zero)
    return EXIT_FOUND if pairs else EXIT_NOTHING


def main():
    """Console-script entry point: run on sys.argv and exit with the SPEC's status."""
    run(NAME, lambda: overlap(sys.argv[1:]))


if __name__ == "__main__":
    main()
