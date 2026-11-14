# Using it with other tools

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
