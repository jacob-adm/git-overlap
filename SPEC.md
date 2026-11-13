# git-overlap: interface contract

Three git subcommands that read records on stdin and write records on stdout. This file is the
contract: the implementation and the tests are both written from it, separately.

## Common rules (all three commands)

- Python 3.8 or later, standard library only. git 2.38 or later.
- Installed as console scripts `git-footprint`, `git-overlap`, `git-pairmerge`, so `git footprint`
  works on Linux, macOS and Windows. Also runnable as `python3 -m git_overlap.footprint` (etc.).
- Runs in the repository of the current directory, like any git command. Never changes a ref, the
  index or the worktree. (`git pairmerge` writes git objects, as `git merge-tree --write-tree` does.)
- **Records.** One record per line (LF). With `-z`, records end with NUL instead, on input and
  output alike. Fields are separated by one TAB. Data is handled as bytes end to end: no decoding,
  no re-encoding.
- **Input tolerance (LF mode only).** A trailing CR on a record is removed. Empty records are
  skipped. In `-z` mode nothing is removed and empty records are skipped.
- **Unsafe names.** In LF mode, a ref or path containing TAB, LF or CR cannot be written as a record.
  The command then writes an error naming `-z` to stderr and exits 2. It never quotes or escapes.
  In `-z` mode a path may contain TAB or LF; the TAB inside a field is the caller's problem only for
  refs, which git forbids from containing TAB or LF anyway.
- **Malformed input** (too few fields) is an error: message with the record number, exit 2.
- **Exit status**, like grep and diff: 0 = ran, nothing found; 1 = ran, something found; 2 = error.
  On exit 2 no partial result is guaranteed; stdout may hold records written before the error.
- **Errors** go to stderr as one line: `git-<name>: <message>`. Never a traceback.
- **Determinism.** Same repository state and same input give byte-identical output. Sorting is
  bytewise. Output does not depend on locale, time zone or terminal.
- **Refs** may be any commit-ish (`main`, `origin/x`, a SHA, `HEAD~2`). A ref starting with `-` must
  be treated as a ref, never as an option. A ref is printed exactly as the caller wrote it.
- `-h` prints usage and exits 0. (`git footprint --help` is intercepted by git itself; document `-h`.)
  `--version` prints `git-<name> <version>`.
- A closed stdout (e.g. piping into `head`) ends the command quietly with exit 0, not a traceback.
- Git runs without a shell, with `LC_ALL=C`, and with `-z` wherever git offers it.

## git footprint BASE

Lists every file each input ref changes, compared with the merge base of BASE and that ref (what a
pull request's "files changed" shows).

- Input: records of one field, a ref. A repeated ref is processed once, at its first occurrence.
- Output: `ref TAB status TAB path`, where status is git's letter: `A`, `M`, `D` or `T`. Renames
  are not detected: a rename is a `D` of the old path and an `A` of the new one, so both paths are
  in the footprint. Within one ref, records are sorted by path; refs keep input order.
- A ref with no changes writes nothing.
- Exit 0 on success (a footprint is not a "finding"), 2 on error: unknown BASE or ref, a ref that is
  not a commit, or no merge base.

## git overlap [--with REF]

Reports pairs of refs that touch the same path.

- Input: footprint records. The first field is the ref, the LAST field is the path, so both
  `ref TAB path` and `ref TAB status TAB path` are accepted. Fewer than 2 fields is an error.
- Output: `refA TAB refB TAB path`, with refA < refB bytewise, one record per shared path. Records
  sorted by (refA, refB, path). A ref never pairs with itself. Duplicates are written once.
- `--with REF`: only pairs that include REF (REF may print as refA or refB, per the ordering rule).
- Exit 1 if any record is written, else 0. Needs no repository.

## git pairmerge

For each pair of refs, whether merging them with each other conflicts, computed in memory with
`git merge-tree --write-tree` (the same check pr-merge-audit uses). There is no BASE argument: a pair
is merged against its own merge base. To ask whether a ref still merges into main, pair it with
main: `printf 'main\tfeature\n' | git pairmerge`.

- Input: records whose first two fields are refs; further fields are ignored, so `git overlap`
  output pipes straight in. A repeated pair (in either order) is processed once, at its first
  occurrence.
- Output, in input order, refs as given:
  - clean pair: `refA TAB refB TAB clean TAB` (empty fourth field)
  - conflicting pair: one record per conflicted path, `refA TAB refB TAB conflict TAB path`, paths
    sorted bytewise.
- `--attributes FILE`: merge as if FILE were the repository's `.gitattributes` (e.g.
  `CHANGELOG.md merge=union`), without committing it or changing any repository file. Mechanism and
  version floor: see the section below.
- Exit 1 if any pair conflicts, else 0; 2 on error (unknown ref, git too old, merge-tree failure).

### --attributes mechanism

To be fixed from the git-internals verification (HNET-215). Until then: unspecified.

## Recipes (HNET-216)

The README recipes are part of the contract: the test suite runs every one of them against a
fixture repository and checks the output.
