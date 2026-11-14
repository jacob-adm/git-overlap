# SPDX-License-Identifier: Apache-2.0
"""git overlap [--with REF]: report pairs of refs that touch the same path.

Reads footprint records (`ref TAB status TAB path`) on stdin and writes
`refA TAB refB TAB path` records with refA < refB bytewise. Needs no repository.
SPEC: "git overlap [--with REF]".
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

    Args:
        record: One input record, as bytes.

    Returns:
        A (ref, path) bytes tuple, or None if the record is malformed.

    SPEC: "git overlap" (Input).
    """
    # maxsplit=2: any TAB after the second belongs to the path.
    fields = record.split(b"\t", 2)
    if len(fields) < 3 or fields[0] == b"" or fields[2] == b"":
        return None
    return fields[0], fields[2]


def overlap(argv):
    """Run git overlap.

    Args:
        argv: The arguments, without the program name.

    Returns:
        EXIT_FOUND if any pair was written, else EXIT_NOTHING.

    Raises:
        Fail: A usage error, a malformed record or an unsafe name.

    SPEC: "git overlap [--with REF]".
    """
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
    # (refA, refB, path) -> earliest input record behind it. Keys write each duplicate once.
    pairs = {}

    def add(ref_x, num_x, ref_y, num_y, path):
        """Record one pair, refA < refB bytewise; a ref never pairs with itself."""
        if ref_x == ref_y or (with_ref is not None and with_ref not in (ref_x, ref_y)):
            return
        key = (min(ref_x, ref_y), max(ref_x, ref_y), path)
        pairs[key] = min(pairs.get(key, num_x), num_x, num_y)

    for path, refs in touched.items():
        for ref_a, num_a in refs.items():
            for ref_b, num_b in refs.items():
                if ref_a < ref_b:
                    add(ref_a, num_a, ref_b, num_b, path)
        # A path under a touched path collides with it (a file `d` against `d/x`): pair them
        # on the ancestor. Only '/' boundaries count, so `foo` never matches `foobar/x`.
        parts = path.split(b"/")
        for depth in range(1, len(parts)):
            ancestor = b"/".join(parts[:depth])
            for ref_a, num_a in touched.get(ancestor, {}).items():
                for ref_b, num_b in refs.items():
                    add(ref_a, num_a, ref_b, num_b, ancestor)
    # Tuples of bytes sort bytewise by (refA, refB, path), as the SPEC requires.
    for pair in sorted(pairs):
        try:
            for field in pair:
                check_safe(field, zero, "ref or path")
        except Fail as exc:
            # Name the earliest input record behind this pair (SPEC "Record errors").
            raise Fail("record %d: %s" % (pairs[pair], exc))
        write_record(pair, zero)
    return EXIT_FOUND if pairs else EXIT_NOTHING


def main():
    """Console-script entry point: run on sys.argv and exit with the SPEC's status.

    Raises:
        SystemExit: Always (0, 1 or 2).

    SPEC: "Common rules" (console scripts) and "Exit status".
    """
    run(NAME, lambda: overlap(sys.argv[1:]))


if __name__ == "__main__":
    main()
