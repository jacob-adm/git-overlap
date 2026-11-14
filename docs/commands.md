# Commands

git-overlap installs exactly three commands. They read records on standard input and write
records on standard output, so they combine with each other and with `sort`, `uniq`, `awk` and
`wc`:

- `git footprint BASE`: the files each ref changes.
- `git overlap`: pairs of refs that change the same file.
- `git pairmerge`: whether each pair of refs merges cleanly or conflicts, and on which files,
  computed in memory without writing to the repository.

Every [recipe](recipes.md) is a documented shell pipeline that combines them with standard Unix
tools; it is not a separate program. Merging a batch of pull requests and running their tests is
the job of [pr-merge-audit](https://github.com/jacob-adm/pr-merge-audit), a separate project.
Merge drivers that git can run during `git pairmerge`, such as Mergiraf, are third-party tools:
they are not part of git-overlap and are installed and configured separately.
[SPEC.md](../SPEC.md) is the full reference.

## When to use it

- Before merging a batch of branches: which pairs conflict, and on which files.
- Before an agent starts work on, or pushes, a branch: does it share a file with any other open
  branch (`git overlap --with`).
- To decide which files need a merge driver, a `merge=union` attribute or a different layout:
  rank the files that conflict most, and see what a driver would leave.
- To measure p for the branches of a repository.

It does not show whether merged code builds or passes its tests. A pair that merges cleanly
can still break; see [Using it with other tools](other-tools.md).

## Example repository

Every example in these pages runs in one repository, which the test suite builds: a `main`
branch and five branches cut from it, `agent/a` to `agent/e`. The shared files are one of each
typed-path kind: `tools/registry.py` is a set (one entry per line, order irrelevant),
`CHANGELOG.md` a log, `openapi.json` derived (generated) and `migrations/` a sequence (numbered
files).

| Branch | Changes |
| :--- | :--- |
| `agent/a` | registry, changelog, `openapi.json`, `src/app.py`; adds `src/fetch.py` |
| `agent/b` | registry, changelog, `openapi.json`, `src/app.py`; adds `src/lint.py` |
| `agent/c` | registry, changelog; adds `migrations/0002_users.sql` |
| `agent/d` | changelog, `openapi.json`; adds `migrations/0002_orders.sql` |
| `agent/e` | adds `docs/guide.md` only |

Branches that share a file edit the same lines of it. One more branch, `odd`, outside
`refs/heads/agent/`, adds a file whose name contains a TAB; it is used only in the `-z` example.
Beside the repository, `../snapshots/2026-11-13.bundle` is a snapshot bundle in pr-merge-audit's
layout, used only in [Using it with other tools](other-tools.md).

## Records

Records are one per line, fields separated by one TAB. With `-z`, every command reads and writes
records ending in NUL instead of LF; use it when a path may contain TAB, LF or CR. In LF mode
such a name is an error (exit 2) whose message names `-z`; the commands never quote or escape a
field. Refs are printed exactly as given on input. Paths are relative to the repository root,
whatever directory the command runs in. The same repository and input give byte-identical
output, whatever the locale or time zone.

## git footprint BASE

Synopsis: `git footprint [-z] BASE`.

Reads one ref per record. For each ref, writes `ref TAB status TAB path` for every file the ref
changes compared with its merge base with BASE: the files a pull request's "files changed" shows.
Status is git's letter: `A`, `M`, `D` or `T`.

```sh
printf '%s\n' agent/c agent/d | git footprint main
```

```text
agent/c	M	CHANGELOG.md
agent/c	A	migrations/0002_users.sql
agent/c	M	tools/registry.py
agent/d	M	CHANGELOG.md
agent/d	A	migrations/0002_orders.sql
agent/d	M	openapi.json
```

- Paths are sorted bytewise within a ref; refs keep input order. A repeated ref is listed once.
- A submodule pointer change counts as a change.
- Does not detect renames: a rename is a `D` of the old path and an `A` of the new one.
- Does not compare with the tip of BASE: changes made on BASE after the branch point are not in
  the footprint.
- Does not read the worktree or the index: only committed refs.
- Writes nothing for a ref with no changes, and exits 0 on success; a footprint is not a finding.
- Runs no external diff or textconv command, whatever git config says.

## git overlap [--with REF]

Synopsis: `git overlap [-z] [--with REF]`.

Reads footprint records and writes `refA TAB refB TAB path` for every path that two refs both
change, with refA before refB bytewise, sorted by refA, refB, path.

```sh
printf '%s\n' agent/c agent/d | git footprint main | git overlap
```

```text
agent/c	agent/d	CHANGELOG.md
```

`--with REF` keeps only the pairs that include REF, spelled as it appears in the input. It is
the check for one branch against all others:

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap --with agent/d
```

```text
agent/a	agent/d	CHANGELOG.md
agent/a	agent/d	openapi.json
agent/b	agent/d	CHANGELOG.md
agent/b	agent/d	openapi.json
agent/c	agent/d	CHANGELOG.md
```

- Exits 1 if it writes any pair, 0 if none.
- Ignores the status field. Needs no repository and runs no git, so it also works on saved
  footprint files.
- Does not look at file contents: two refs that change different lines of one file overlap.
- Compares paths exactly and case-sensitively; see
  [Limitations](limitations-and-security.md#limitations).

## git pairmerge [--attributes FILE]

Synopsis: `git pairmerge [-z] [--attributes FILE]`.

Reads records whose first two fields are refs (further fields are ignored, so `git overlap`
output pipes straight in) and merges each pair with each other, at the pair's own merge base,
with `git merge-tree --write-tree`. For a clean pair it writes `refA TAB refB TAB clean TAB`
(the fourth field is empty); for a conflicting pair, one `refA TAB refB TAB conflict TAB path`
per conflicted path, paths sorted bytewise. Pairs keep input order; a pair repeated in either
order is merged once.

```sh
printf 'agent/a\tagent/b\n' | git pairmerge
```

```text
agent/a	agent/b	conflict	CHANGELOG.md
agent/a	agent/b	conflict	openapi.json
agent/a	agent/b	conflict	src/app.py
agent/a	agent/b	conflict	tools/registry.py
```

There is no BASE argument. To ask whether a branch still merges into `main`, pair it with `main`
(here `cut` drops the empty fourth field of the clean records):

```sh
printf '%s\t%s\n' main agent/a main agent/e | git pairmerge | cut -f1-3
```

```text
main	agent/a	clean
main	agent/e	clean
```

`--attributes FILE` merges as if FILE were the repository's root `.gitattributes`, without
committing it or changing any file. FILE replaces the committed `.gitattributes` files; it is
not combined with them. To start from the committed ones, run
`git show main:.gitattributes > FILE` and edit FILE. Here `merge=union` on the log and the set
leaves two of the four conflicts:

```sh
attrs=$(mktemp)
printf '%s\n' 'CHANGELOG.md merge=union' 'tools/registry.py merge=union' > "$attrs"
printf 'agent/a\tagent/b\n' | git pairmerge --attributes "$attrs"
rm -f "$attrs"
```

```text
agent/a	agent/b	conflict	openapi.json
agent/a	agent/b	conflict	src/app.py
```

- Exits 1 if any pair conflicts, 0 if none.
- Does not merge into BASE or any other branch, and does not merge three or more refs together.
- Does not write to the repository: refs, index, worktree and object store are unchanged. See
  [Security](limitations-and-security.md#security).
- Without `--attributes`, ignores the `.gitattributes` files committed in the repository: the
  merge is git's plain line merge. `$GIT_DIR/info/attributes`, `core.attributesFile` and merge
  drivers defined in git config still apply, so a syntax-aware merge driver (a third-party tool
  such as Mergiraf) can be evaluated by configuring it in git config and naming it in FILE.
- Pins `merge.renames=true`, `merge.directoryRenames=conflict` and `merge.renameLimit=7000`
  (git's documented defaults), so user configuration cannot change a verdict.
- Decides clean or conflict by git's exit status and never parses git's messages. A conflict for
  which git lists no path is written with an empty path field.

## -z

In LF mode, a path containing a TAB stops the command:

```sh
echo odd | git footprint main 2>&1; echo "exit $?"
```

```text
git-footprint: record 1: path contains TAB, LF or CR and cannot be written as a line; use -z: notes\tdraft.txt
exit 2
```

With `-z` the record is written. In a `-z` record the path is everything after the second TAB,
so it may hold a TAB itself. Below, `tr` turns the NUL into a line end and `sed -n l` shows each
TAB as `\t` and the line end as `$`:

```sh
printf 'odd\0' | git footprint -z main | tr '\0' '\n' | sed -n l
```

```text
odd\tA\tnotes\tdraft.txt$
```

## Exit status

Like `grep` and `diff`:

| Status | `git footprint` | `git overlap` | `git pairmerge` |
| :--- | :--- | :--- | :--- |
| 0 | success | no pair shares a path | every pair merges cleanly |
| 1 | not used | at least one pair written | at least one pair conflicts |
| 2 | error | error | error |

An error is one line on standard error, `git-<name>: <message>`, never a traceback. A message
about an input record starts with `record N: `. On exit 2, standard output may hold records
written before the error.

```sh
printf 'agent/a\tagent/e\n' | git pairmerge > /dev/null; echo "clean pair: $?"
printf 'agent/a\tagent/b\n' | git pairmerge > /dev/null; echo "conflicting pair: $?"
printf 'agent/a\tnosuch\n' | git pairmerge 2>&1; echo "unknown ref: $?"
```

```text
clean pair: 0
conflicting pair: 1
git-pairmerge: record 1: unknown ref or not a commit: nosuch
unknown ref: 2
```

`-h` and `--version` exit 0. A reader that closes the pipe early (`| head`) ends the command
quietly with exit 0. `git pairmerge` exits 128 plus the signal number on SIGINT, SIGTERM or
SIGHUP, after removing its temporary directory.

In scripts, exit 1 is a result, not a failure. Under `set -e`, capture it:

```sh
set -e
status=0
printf 'agent/c\tagent/d\n' | git pairmerge > /dev/null || status=$?
case $status in
  0) echo "merges cleanly" ;;
  1) echo "conflicts" ;;
  *) echo "error" >&2; exit 2 ;;
esac
```

```text
conflicts
```

The status of a pipeline is that of its last command. If an earlier stage fails, the next stage
reads partial input and may still exit 0. To catch that, run the stages one at a time into files
and check each status, or treat any output on standard error as a failure. The Pre-work check
recipe below uses the status of `git overlap --with` in a loop.
