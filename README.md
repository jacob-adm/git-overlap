# git-overlap

git-overlap is a package of three git commands: `git footprint`, `git overlap` and
`git pairmerge`. Installing it puts `git-footprint`, `git-overlap` and `git-pairmerge` on your
`PATH`; git runs any `git-NAME` program on `PATH` as `git NAME`, so no configuration is needed.

Several branches, often from AI agents working in parallel, edit the same few files. Each
branch is fine alone, but when one lands the others conflict or must be re-tested, so landing
them one at a time multiplies CI runs and agent time. git-overlap shows, before any merge, which
branches change the same files and which pairs really conflict, so they can be batched,
reordered or fixed first.

git-overlap was inspired by Xuchen Yao's article
["Rethinking Merging and Continous Integration for Agentic Workflows"](https://www.linkedin.com/pulse/rethinking-merging-continous-integration-agentic-workflows-xuchen-yao-jkaxc/)
(4 November 2026). The article finds that pull requests from many parallel agents collide in a
few shared files (a registry, a changelog, a generated API description, numbered migrations),
that landing them one at a time re-tests everything still waiting, so N pull requests cost
N(N+1)/2 full CI runs, and that typed paths (set, log, derived, sequence) and merge drivers
remove most textual conflicts, while the fraction p of pull-request pairs that truly overlap is
unmeasured. git-overlap covers the measurement side it calls for. Run inside any git
repository, it measures textual collisions between that repository's branches before any of them
is merged: which branches change the same files, which pairs conflict when merged with each
other and on which files, which files conflict most, and p; and it shows which of those
conflicts a merge driver would remove. It does not run builds or tests, and it does not
implement the system the article proposes; see
[Relation to the article](#relation-to-the-article).

The commands read records on standard input and write records on standard output, so they
combine with each other and with `sort`, `uniq`, `awk` and `wc`:

- `git footprint BASE`: the files each ref changes.
- `git overlap`: pairs of refs that change the same file.
- `git pairmerge`: whether each pair of refs merges cleanly or conflicts, and on which files,
  computed in memory without writing to the repository.

git-overlap installs exactly these three commands. Everything under [Recipes](#recipes) is a
documented shell pipeline that combines them with standard Unix tools; it is not a separate
program. Merging a batch of pull requests and running their tests is the job of
[pr-merge-audit](https://github.com/jacob-adm/pr-merge-audit), a separate project. Merge drivers
that git can run during `git pairmerge`, such as Mergiraf, are third-party tools: they are not
part of git-overlap and are installed and configured separately.

[SPEC.md](SPEC.md) is the full reference: every option, record format, error and edge case.

## When to use it

- Before merging a batch of branches: which pairs conflict, and on which files.
- Before an agent starts work on, or pushes, a branch: does it share a file with any other open
  branch (`git overlap --with`).
- To decide which files need a merge driver, a `merge=union` attribute or a different layout:
  rank the files that conflict most, and see what a driver would leave.
- To measure p for the branches of a repository.

It does not show whether merged code builds or passes its tests. A pair that merges cleanly
can still break; see [Using it with other tools](#using-it-with-other-tools).

## Requirements

- Python 3.8 or later, standard library only.
- git 2.38 or later. `git pairmerge` needs git 2.43 or later; on an older git it exits 2 with a
  message naming 2.43.
- Linux, macOS or Windows. git must be an executable named `git` on `PATH`.
- The examples and recipes below are POSIX `sh`. On Windows, run them from Git Bash.

## Install

From a release tag:

```
pip install git+https://github.com/jacob-adm/git-overlap@v0.1.0
```

From a clone:

```
git clone https://github.com/jacob-adm/git-overlap
cd git-overlap
pip install .
```

Either way pip installs `git-footprint`, `git-overlap` and `git-pairmerge` as console scripts.
If git answers that `footprint` is not a git command, the directory pip installs scripts into is
not on `PATH`. Check the install:

```sh
git footprint --version
```

```text
git-footprint 0.1.0
```

Use `-h` for usage: `git footprint --help` is answered by git itself, which looks for a manual
page. Without installing, each command also runs from a clone as
`python3 -m git_overlap.footprint` (and `.overlap`, `.pairmerge`).

## Example repository

Every example in this README runs in one repository, which the test suite builds: a `main`
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
layout, used only in [Using it with other tools](#using-it-with-other-tools).

## Quick start

Change into the git repository whose branches are to be checked (any directory inside it), list
those branches one per line, and pipe them through the three commands. In that repository,
replace `main` with its base branch and `refs/heads/agent/` with the refs to check, for example
`refs/remotes/origin/` for every branch fetched from `origin`. Below, the commands run in the
example repository.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge | cut -f1-3 | uniq
```

```text
agent/a	agent/b	conflict
agent/a	agent/c	conflict
agent/a	agent/d	conflict
agent/b	agent/c	conflict
agent/b	agent/d	conflict
agent/c	agent/d	conflict
```

Each line is a pair of branches that conflict when merged with each other. Drop
`| cut -f1-3 | uniq` to see the conflicted files. `agent/e` shares no file with any other
branch, so `git overlap` passes on no pair that includes it and `git pairmerge` never merges it.

## Commands

Records are one per line, fields separated by one TAB. With `-z`, every command reads and writes
records ending in NUL instead of LF; use it when a path may contain TAB, LF or CR. In LF mode
such a name is an error (exit 2) whose message names `-z`; the commands never quote or escape a
field. Refs are printed exactly as given on input. Paths are relative to the repository root,
whatever directory the command runs in. The same repository and input give byte-identical
output, whatever the locale or time zone.

### git footprint BASE

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

### git overlap [--with REF]

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
- Compares paths exactly and case-sensitively; see [Limitations](#limitations).

### git pairmerge [--attributes FILE]

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
  [Security](#security).
- Without `--attributes`, ignores the `.gitattributes` files committed in the repository: the
  merge is git's plain line merge. `$GIT_DIR/info/attributes`, `core.attributesFile` and merge
  drivers defined in git config still apply, so a syntax-aware merge driver (a third-party tool
  such as Mergiraf) can be evaluated by configuring it in git config and naming it in FILE.
- Pins `merge.renames=true`, `merge.directoryRenames=conflict` and `merge.renameLimit=7000`
  (git's documented defaults), so user configuration cannot change a verdict.
- Decides clean or conflict by git's exit status and never parses git's messages. A conflict for
  which git lists no path is written with an empty path field.

### -z

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

## Recipes

These pipelines answer the open questions of Xuchen Yao's article "Rethinking Merging and
Continous Integration for Agentic Workflows" (4 November 2026, linked above) for one repository:
the git repository of the current directory. Run them from inside the repository whose branches
are to be checked; every command works on that repository's refs, as any git command does. The
questions are: in which shared files parallel branches collide, what fraction p of branch pairs
truly conflicts, and how many conflicts typed paths would remove. Each recipe is a shell
pipeline, not a separate program: the three commands combined with plain POSIX `sh`, git, `awk`,
`sort`, `uniq`, `cut`, `grep` and `printf`. As written they check the branches
`refs/heads/agent/*` against `main`, with the four typed paths of the
[example repository](#example-repository); the test suite runs every block below there and
checks its exact output.

### Footprint: which files does each branch change?

The files each agent branch changes, compared with its merge base with `main`.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ | git footprint main
```

### Who overlaps whom: which branches change the same files?

Pairs of agent branches that change at least one same file.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | cut -f1,2 | uniq
```

### Real conflicts: which pairs conflict, and on which files?

Which overlapping pairs conflict when merged with each other, and on which files.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge
```

### Hotspots: which files conflict most?

Conflicted files, ranked by the number of branch pairs that conflict on them.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge |
  awk -F '\t' '$3 == "conflict" { print $4 }' |
  LC_ALL=C sort | uniq -c | LC_ALL=C sort -k1,1nr -k2 |
  awk '{ n = $1; sub(/^ *[0-9]+ /, ""); print n "\t" $0 }'
```

### Measured p: what fraction of branch pairs conflicts?

p = conflicting pairs / all pairs. The denominator counts every unordered pair of distinct
branches, n(n-1)/2 for n branches, whether or not they overlap. Each pair is merged with the
other at their own merge base (not with `main`) and counts as conflicting if git reports any
conflict.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  awk '{ r[NR] = $0 }
    END { for (i = 1; i <= NR; i++) for (j = i + 1; j <= NR; j++) print r[i] "\t" r[j] }' |
  git pairmerge |
  awk -F '\t' '{ all[$1 FS $2] = 1; if ($3 == "conflict") bad[$1 FS $2] = 1 }
    END { n = 0; c = 0; for (k in all) n++; for (k in bad) c++
          printf "conflicting pairs %d of %d, p = %.2f\n", c, n, n ? c / n : 0 }'
```

### What-if merge drivers: which conflicts would a union merge remove?

The same pairs as Real conflicts, merged as if `.gitattributes` marked the set and the log
`merge=union` (nothing is committed). A union merge keeps both sides' lines. That is safe for a
file with one entry per line, but it can make other files wrong (for example a list entry
without its trailing comma), so the tests still decide correctness.

```sh
attrs=$(mktemp)
printf '%s\n' 'tools/registry.py merge=union' 'CHANGELOG.md merge=union' > "$attrs"
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge --attributes "$attrs"
rm -f "$attrs"
```

### Derived files: which conflicts are left for a person?

Generated files are regenerated after a merge, never merged, so their conflicts are dropped with
a pattern list. With the union attributes as well, what is left needs a person.

```sh
attrs=$(mktemp)
printf '%s\n' 'tools/registry.py merge=union' 'CHANGELOG.md merge=union' > "$attrs"
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge --attributes "$attrs" |
  awk -F '\t' '$3 == "conflict"' | grep -v -e 'openapi\.json$' -e '_pb2\.py$'
rm -f "$attrs"
```

### Sequence collisions: which branches add files with the same sequence number?

Files added on different branches in the same directory with the same leading number, such as
two migrations `0002_*`. git merges them without a conflict because the files differ; the
collision shows up later, when both run.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ | git footprint main |
  awk -F '\t' '$2 == "A" {
      name = $3; sub(/.*\//, "", name)
      if (!match(name, /^[0-9]+/)) next
      key = substr($3, 1, length($3) - length(name)) substr(name, 1, RLENGTH)
      line[key] = line[key] key "\t" $1 "\t" $3 "\n"
      if (!((key, $1) in seen)) { seen[key, $1] = 1; refs[key]++ }
    }
    END { for (k in refs) if (refs[k] > 1) printf "%s", line[k] }' |
  LC_ALL=C sort
```

### Pre-work check: does a branch share a file with any other branch?

Before an agent works on (or pushes) its branch, check whether the branch shares a file with any
other agent branch. `git overlap` exits 1 when it finds a pair, 0 when not and 2 on error.

```sh
for b in agent/a agent/e; do
  git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
    git footprint main | git overlap --with "$b" > /dev/null
  case $? in
    0) echo "$b: clear" ;;
    1) echo "$b: collision" ;;
    *) echo "$b: error" ;;
  esac
done
```

## Limitations

- **Exact paths.** `git overlap` compares paths exactly. A file `foo` added on one branch and a
  file `foo/bar` on another are not an overlap, although `git pairmerge` reports the pair as a
  conflict. A directory renamed on one branch and a file added to the old directory on another
  share no path either, and also conflict. To catch these, give `git pairmerge` every pair, as
  the Measured p recipe does, not only the overlapping ones.
- **Case.** Paths and refs are compared bytewise and case-sensitively, as git's trees are.
  `Foo.txt` and `foo.txt` on two branches are not an overlap, although they collide on a
  case-insensitive file system.
- **Directory/file conflicts.** git reports the file under a generated name, `PATH~<commit id>`,
  and `git pairmerge` prints that name unchanged.
- **Pairs only.** `git pairmerge` merges two refs at a time.
- **Text only.** A clean merge says nothing about whether the result builds or passes its tests.
- **Shallow or unrelated histories.** A ref with no merge base with BASE, or a pair with no merge
  base, is an error (exit 2) whose message contains `no merge base`.
- **Windows.** Program arguments are Unicode there, so a ref that is not valid UTF-8 cannot be
  passed to git: exit 2 with `cannot pass a name that is not valid UTF-8 to git on this
  platform`. git must be an executable; a `.cmd` or `.bat` shim alone is not found.

## Security

- `git footprint` and `git overlap` write nothing. `git overlap` runs no git at all.
- `git pairmerge` writes nothing to the repository. Every git call that can write an object runs
  with `GIT_OBJECT_DIRECTORY` set to a private temporary directory, `git-pairmerge-*` in the
  temporary directory Python chooses (`TMPDIR` if set), and the repository's object directory as
  an alternate, so new objects land in the temporary directory. It is removed on normal exit, on
  error, and on SIGINT, SIGTERM and SIGHUP; only SIGKILL can leave it behind.
- `git pairmerge` reads the refs and objects of the repository, git config,
  `$GIT_DIR/info/attributes`, `core.attributesFile` and the `--attributes` FILE. It does not read
  the worktree, the index, or the `.gitattributes` files committed on the branches, so a branch
  cannot choose how it is merged.
- Merge drivers from git config still run. A driver (`merge.<name>.driver`) is a command; git
  runs it as the user who runs `git pairmerge`, on the three versions of the file, whenever an
  attribute selects it. Check what every driver in the system, global and repository git config
  does before merging branches that are not trusted.
- The commands start git without a shell. A ref or path quoted in an error message is escaped,
  so the message stays one line.

## Using it with other tools

**pr-merge-audit.** [pr-merge-audit](https://github.com/jacob-adm/pr-merge-audit) is a separate
project. It merges the open pull requests of a repository onto the base branch one after another
and runs the test suite after each merge, so it finds behavioural breakage between pull
requests, not only textual conflicts, and it can keep the batch that merged and passed as a
branch (`build --emit-branch`). Its running time is counted in test-suite runs. git-overlap runs
only git's diff and in-memory merge (`git merge-tree --write-tree`, the same conflict check
pr-merge-audit uses), runs no tests and writes nothing to the repository. Each works alone. Used
together, git-overlap comes first as the fast filter and pr-merge-audit second.

A pr-merge-audit snapshot bundle (`snapshots/DATE.bundle`) holds the base as `refs/audit/base`
and each pull request head as `refs/audit/pr/N`. Fetch those refs into a clone of the audited
repository and run git-overlap on them, from inside that clone. In the example repository,
pull requests 12, 15 and 17 are the branches `agent/a`, `agent/c` and `agent/e`:

```sh
git fetch --quiet ../snapshots/2026-11-13.bundle 'refs/audit/*:refs/audit/*'
git for-each-ref --format='%(refname)' refs/audit/pr/ | git footprint refs/audit/base | git overlap
```

```text
refs/audit/pr/12	refs/audit/pr/15	CHANGELOG.md
refs/audit/pr/12	refs/audit/pr/15	tools/registry.py
```

**Line-oriented tools.** Every record is one line (or one NUL-terminated record with `-z`) of
TAB-separated fields, and the exit status follows `grep`: 0 nothing found, 1 found, 2 error. Any
program that reads lines works on the output: `sort`, `awk`, a CI script, an agent's shell.

**bug-age.** [bug-age](https://github.com/jacob-adm/bug-age) answers a different question: how
long bugs that are already fixed lived in a repository's history. It shares no data with
git-overlap. Both can run on the same repository, but there is no pipe between them.

## Relation to the article

How each point of the article maps to git-overlap and to pr-merge-audit (a separate project, see
[Using it with other tools](#using-it-with-other-tools)). Coverage is Yes, Partly or Not
addressed.

**The night the article describes**

| Article point | What covers it | Coverage |
| :--- | :--- | :--- |
| Pull requests collided in the same five shared files | Hotspots recipe: conflicted files ranked by conflicting pairs | Yes |
| Agents' wait loops watched CI, not whether the branch still merged | `printf 'main\tBRANCH\n' \| git pairmerge` exits 1 once BRANCH no longer merges into `main` | Yes |
| Generated files were merged instead of regenerated | Derived files recipe separates conflicts on generated files (a pattern list) from the rest; nothing is regenerated | Partly |
| Nine pull requests chained by hand into one integration pull request | pr-merge-audit `build --emit-branch` keeps the batch that merged and passed as one branch | Yes (pr-merge-audit) |
| 10 pull requests cost 55 CI runs | pr-merge-audit's report states N(N+1)/2 against the runs its build used; `estimate` gives both in hours | Yes (pr-merge-audit) |

**The six assumptions**

| Article point | What covers it | Coverage |
| :--- | :--- | :--- |
| 1. CI evidence is keyed to the whole tree | Nothing; this needs input-keyed build tools | Not addressed |
| 2. Merging is line-based | `git pairmerge --attributes` shows which conflicts merge drivers would remove; drivers configured in git config (for example Mergiraf, installed separately) are evaluated too. No typed merge is implemented | Partly |
| 3. Conflicts cannot be handed off | Nothing | Not addressed |
| 4. Intent is invisible until merge | `git overlap --with BRANCH` (Pre-work check recipe) reports by exit status whether a branch shares a file with another; file level, committed and fetched branches only | Partly |
| 5. Landing is serial | pr-merge-audit emits one tested batch; there is no continuous merge queue | Partly |
| 6. Evidence does not travel with the code | Nothing; pr-merge-audit's manifest of included and excluded pull requests is not signed | Not addressed |

**The open questions**

| Article point | What covers it | Coverage |
| :--- | :--- | :--- |
| p is a guess | Measured p recipe, for textual conflicts only | Yes, for textual conflicts |
| The 28% textual conflict rate (AgenticFlict) | Pair each branch with `main` in `git pairmerge` to get the rate for one repository | Yes |
| Type declarations can be wrong | `--attributes` tries a declaration on real pairs before it is committed | Partly |
| Replay one night three ways and compare | pr-merge-audit replays a frozen snapshot and prices serial landing; git-overlap measures p and the merge-driver what-if; no timed comparison of three stacks | Partly |

The system the article proposes is not implemented: no change objects, input-keyed evidence,
continuous speculative merging, conflict objects, signed provenance or test budget.

## Licence

Apache-2.0; see `LICENSE`.
