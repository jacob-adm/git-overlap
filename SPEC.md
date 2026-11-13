# git-overlap: interface contract

Three git subcommands that read records on stdin and write records on stdout. This file is the
contract: the implementation and the tests are both written from it, separately.

## Common rules (all three commands)

- Python 3.8 or later, standard library only. git 2.38 or later.
- Installed as console scripts `git-footprint`, `git-overlap`, `git-pairmerge`, so `git footprint`
  works on Linux, macOS and Windows. Also runnable as `python3 -m git_overlap.footprint` (etc.).
- Runs in the repository of the current directory, like any git command. Never changes a ref, the
  index, the worktree or the object store.
- **Records.** One record per line (LF). With `-z`, records end with NUL instead, on input and
  output alike. Fields are separated by one TAB. Data is handled as bytes end to end: no decoding,
  no re-encoding.
- **Input tolerance (LF mode only).** A trailing CR on a record is removed. Empty records are
  skipped. In `-z` mode nothing is removed and empty records are skipped.
- **Unsafe names.** In LF mode, a ref or path containing TAB, LF or CR cannot be written as a record.
  The command then writes an error naming `-z` to stderr and exits 2. It never quotes or escapes.
  In `-z` mode a path may contain TAB or LF; the TAB inside a field is the caller's problem only for
  refs, which git forbids from containing TAB or LF anyway.
- **Malformed input** (too few fields, or an empty ref or path field) is an error: message with
  the record number (counting every record from 1, skipped empty ones included), exit 2.
- **Options.** Only the options a command documents are options; any other argument, even one
  starting with `-`, is a positional (so a ref named `-x` works as BASE); `--` is also accepted.
  A command given an unexpected positional exits 2.
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

- Input: footprint records, `ref TAB status TAB path`. The ref is everything before the first
  TAB, the status (any value; ignored) is between the first and second TAB, and the path is
  everything after the second TAB, so in `-z` mode a path may itself contain TAB. Fewer than 3
  fields, or an empty ref or path, is an error. There is no 2-field form: one parse rule, no
  guessing.
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
  version floor: see the section below. An unreadable FILE is exit 2.
- Exit 1 if any pair conflicts, else 0; 2 on error (unknown ref, git too old, merge-tree failure).

### Merge mechanism (fixed by experiment on git 2.50.1; floors from git release notes)

- Each ref is first checked with `git rev-parse --verify --quiet --end-of-options REF^{commit}`;
  failure is exit 2 before any merge runs.
- Per pair: `git --attr-source=T merge-tree --write-tree -z --name-only --no-messages
  --end-of-options A B`. Exit 0 = clean. Exit 1 **and** a first NUL field that is a 40- or 64-hex
  object id = conflict; the remaining fields are the conflicted paths. Anything else (another exit
  code, or exit 1 with empty stdout, which is how git reports an unmergeable ref) = exit 2.
- A conflict may list no paths (some directory-rename conflicts). It is then written as one
  record with an empty path field: `refA TAB refB TAB conflict TAB`. Clean/conflict is decided by
  the exit code, never by the path list. git's message text is never parsed.
- **The attribute source is always pinned**, so the result does not depend on what is checked out.
  Without `--attributes`, T is the empty tree: a plain line merge, as a bare server performs it.
  With `--attributes FILE`, T is a tree holding FILE (read as bytes) as its root `.gitattributes`.
  FILE replaces the repository's own `.gitattributes` files; it is not combined with them. To start
  from the committed ones: `git show main:.gitattributes > f`, edit `f`, pass `--attributes f`.
- Still honoured, because git gives them precedence or they are user configuration:
  `$GIT_DIR/info/attributes`, `core.attributesFile`, and merge drivers defined in git config (which
  is how a syntax-aware driver such as Mergiraf can be evaluated). The README states this.
- **Nothing is written to the repository.** All git calls of `git pairmerge` run with
  `GIT_OBJECT_DIRECTORY` set to a private temporary directory and
  `GIT_ALTERNATE_OBJECT_DIRECTORIES` set to the repository's own object directory (absolute path
  from `git rev-parse --git-path objects`). New objects land in the temporary directory, which is
  removed on exit, including on error.
- Version floor for `git pairmerge`: **git 2.43** (`--attr-source` arrived in 2.41; merge-tree
  crashed with it until 2.43). The command reads `git version` and exits 2 with a message naming
  the floor if git is older. `git footprint` and `git overlap` keep the 2.38 floor.

## Recipes (HNET-216)

The README recipes are part of the contract: the test suite runs every one of them against a
fixture repository and checks the output.
