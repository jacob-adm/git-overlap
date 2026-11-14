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
- **Record errors.** An error about an input record is prefixed `record N: ` (N counted as for
  malformed input); an error about BASE or an option is not.
- **Malformed input** (too few fields, or an empty ref or path field) is an error: message with
  the record number (counting every record from 1, skipped empty ones included), exit 2.
- **Options.** Only the options a command documents are options; any other argument, even one
  starting with `-`, is a positional (so a ref named `-x` works as BASE); `--` is also accepted.
  A command given an unexpected positional exits 2.
- **Exit status**, like grep and diff: 0 = ran, nothing found; 1 = ran, something found; 2 = error.
  On exit 2 no partial result is guaranteed; stdout may hold records written before the error.
- **Errors** go to stderr as one line: `git-<name>: <message>`. Never a traceback. A ref or path
  quoted in a message is escaped (backslash escapes for TAB, LF, CR, backslash and any byte that is
  not printable ASCII), so the message stays one line whatever the name holds. The line ends in LF
  on every platform (no CR LF on Windows).
- **Unterminated last record.** A final record without its LF or NUL is accepted.
- **Standard streams.** `-h` and `--version` print to stdout and exit 0, taking precedence over
  any other argument, except where `-h` is the value of an option that takes one (`--with -h`). If file descriptor 0 or 1 is not open at all (`<&-`, `>&-`), the command
  exits 2 with `standard input is not open` / `standard output is not open`. A reader closing the
  pipe early (EPIPE, e.g. `| head`) is not an error: exit 0, quietly.
- **Paths are relative to the repository root**, whatever subdirectory the command runs in, in
  every command's output.
- **Shallow or unrelated histories.** A missing merge base is an error (exit 2) that says so:
  the message contains `no merge base` and names the ref(s) concerned (both refs, for a pair).
- **Determinism.** Same repository state and same input give byte-identical output. Sorting is
  bytewise. Output does not depend on locale, time zone or terminal.
- **Resolving a ref** (all commands, including BASE): `git rev-parse --verify --end-of-options REF`
  must print exactly one 40- or 64-hex object id (this rejects `^a`, ranges and other non-single
  revisions), and `git rev-parse --verify OID^{commit}` must then succeed. Otherwise: exit 2,
  `unknown ref or not a commit: <ref>`, before any merge or diff runs. BASE is resolved before
  stdin is read. "Repeated" means the same spelling, not the same commit.
  On Windows a program's arguments are Unicode, so a ref that is not valid UTF-8 cannot be
  passed to git at all: there it is exit 2 with `cannot pass a name that is not valid UTF-8 to
  git on this platform: <ref>` (escaped as above) instead of `unknown ref or not a commit`.
- **Refs** may be any commit-ish (`main`, `origin/x`, a SHA, `HEAD~2`, `:/message`). A ref starting with `-` must
  be treated as a ref, never as an option. A ref is printed exactly as the caller wrote it.
- `-h` prints usage and exits 0. (`git footprint --help` is intercepted by git itself; document `-h`.)
  `--version` prints `git-<name> <version>`.
- A closed stdout (e.g. piping into `head`) ends the command quietly with exit 0, not a traceback.
- Git runs without a shell, with `LC_ALL=C`, and with `-z` wherever git offers it.
- git must be an executable named `git` on `PATH` (on Windows a `.cmd`/`.bat` shim alone is not
  found).

## git footprint BASE

Lists every file each input ref changes, compared with the merge base of BASE and that ref (what a
pull request's "files changed" shows).

- Input: records of one field, a ref. A repeated ref is processed once, at its first occurrence.
- Output: `ref TAB status TAB path`, where status is git's letter: `A`, `M`, `D` or `T`. Renames
  are not detected: a rename is a `D` of the old path and an `A` of the new one, so both paths are
  in the footprint. Within one ref, records are sorted by path; refs keep input order.
- A ref with no changes writes nothing.
- The input record is the whole ref: in LF mode a TAB in it is the unsafe-name error.
- The diff is pinned against user configuration: `--no-relative`, `--ignore-submodules=none`
  (a submodule pointer change is a change), `--no-renames`, `--no-ext-diff`, `--no-textconv`;
  paths are sorted by the tool, not by `diff.orderFile`.
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
  An empty REF is a usage error (exit 2).
- Known limit, by design: paths are compared exactly. A file `foo` in one ref and `foo/bar` in
  another are not an overlap here, although `git pairmerge` reports them as a conflict.
- Known limit, by design: paths and refs are compared bytewise and case-sensitively, as git's
  trees do. `Foo.txt` in one ref and `foo.txt` in another are not an overlap, even though they
  collide on a case-insensitive filesystem.
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
- Fewer than 2 fields, or an empty first or second field, is a malformed record. A ref paired with
  itself is merged like any pair (and is clean).
- Conflicted paths are printed as git reports them, relative to the repository root. For a
  directory/file conflict git reports the file under a generated name `PATH~<commit id>`; the tool
  prints that name unchanged (it is deterministic, because merges are given commit ids).

### Merge mechanism (fixed by experiment on git 2.50.1; floors from git release notes)

- Each ref is resolved as in the common rule "Resolving a ref"; failure is exit 2 before any
  merge runs.
- Per pair: `git --attr-source=T merge-tree --write-tree -z --name-only --no-messages
  --end-of-options A B`. Exit 0 = clean. Exit 1 **and** a first NUL field that is a 40- or 64-hex
  object id = conflict; the remaining fields are the conflicted paths. Anything else (another exit
  code, or exit 1 with empty stdout, which is how git reports an unmergeable ref) = exit 2.
- A conflict may list no paths (some directory-rename conflicts). It is then written as one
  record with an empty path field: `refA TAB refB TAB conflict TAB`. Clean/conflict is decided by
  the exit code, never by the path list. git's message text is never parsed.
- **The attribute source is always pinned**, so the result does not depend on what is checked out.
  Without `--attributes`, T is the empty tree: git's default merge with no attributes.
  With `--attributes FILE`, T is a tree holding FILE (read as bytes) as its root `.gitattributes`.
  FILE replaces the repository's own `.gitattributes` files; it is not combined with them. To start
  from the committed ones: `git show main:.gitattributes > f`, edit `f`, pass `--attributes f`.
- **Merge settings are pinned to git's documented defaults** with `-c` on every merge-tree call:
  `merge.renames`, `merge.directoryRenames` and `merge.renameLimit` (the implementer takes each
  default from git's Documentation for the floor version and cites it in a code comment), so user
  configuration cannot turn a clean pair into a conflict or back.
- Still honoured, because git gives them precedence or because they are the point:
  `$GIT_DIR/info/attributes`, `core.attributesFile`, and merge drivers defined in git config (which
  is how a syntax-aware driver such as Mergiraf can be evaluated). The README states this.
- merge-tree runs at the repository root (or the git directory of a bare repository), so the
  paths it prints are root-relative.
- **Nothing is written to the repository.** Every git call of `git pairmerge` that can write
  objects (`hash-object`, `mktree`, `merge-tree`) runs with
  `GIT_OBJECT_DIRECTORY` set to a private temporary directory and
  `GIT_ALTERNATE_OBJECT_DIRECTORIES` set to the repository's own object directory (absolute path
  from `git rev-parse --git-path objects`). That variable is a path-separator list, so an entry
  containing the separator (`:` on Unix, `;` on Windows) or starting with `"` is written C-quoted
  in double quotes, as git expects; an existing value from the caller is kept after it. New objects
  land in the temporary directory (made with `tempfile.mkdtemp`), which is removed on normal exit,
  on error, and on SIGINT, SIGTERM and SIGHUP (exit 128 + signal number for a signal). SIGPIPE is
  not in that list: Python ignores it, so a reader closing the pipe surfaces as EPIPE on a write,
  which is the quiet exit 0 above, and the directory is removed then too.
  Only SIGKILL can leave it behind. A failure while setting this up is reported as such, never as
  an unknown ref.
- Version floor for `git pairmerge`: **git 2.43** (`--attr-source` arrived in 2.41; merge-tree
  crashed with it until 2.43). The command reads `git version` and exits 2 with a message naming
  the floor if git is older. `git footprint` and `git overlap` keep the 2.38 floor.

## Recipes

The README recipes are part of the contract: the test suite runs every one of them against a
fixture repository and checks the output.
