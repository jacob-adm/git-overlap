# Using it with other tools

git-overlap runs only git's diff and in-memory merge, runs no tests and writes nothing to the
repository. This page shows the tested stacks it is part of.

The commands on this page use [git-overlap](https://github.com/jacob-adm/git-overlap),
[git-surface](https://github.com/jacob-adm/git-surface),
[trust-path](https://github.com/jacob-adm/trust-path) and
[pr-merge-audit](https://github.com/jacob-adm/pr-merge-audit). git-surface and trust-path are not on
PyPI yet and install from GitHub:

```
pip install git-overlap
pip install git+https://github.com/jacob-adm/git-surface
pip install git+https://github.com/jacob-adm/trust-path
pip install pr-merge-audit
```

The examples come from a test repository whose `main` has a small Python module, its tests, a
`requirements.txt`, a changelog and a Markdown guide, and six branches as an agent might push
them: `agent/a` renames a function, `agent/b` changes the same line, `agent/c` adds a module that
calls the function by its old name, `agent/ci` adds a workflow that passes the pull request title
to a step holding a secret, `agent/deps` bumps a pinned version in `requirements.txt`, and
`agent/docs` adds an HTML comment to the guide. Every command was run on that repository and the
output shown is what it printed.

**Exit status.** `git overlap`, `git pairmerge`, `git surface`, `trust-path` and
`release-drift diff` and `source` exit like `grep`: 0 nothing found, 1 found, 2 error.
`git footprint` and `release-drift fetch` exit 0 on success. With `set -o pipefail`, a pipeline
in which any step found something exits 1: treat 1 as a finding, not a failure, and only 2 as an
error. In a `for` or `while` loop the status is that of the last command only, so read the
output.

## Before merging: what each branch changes

Three commands over the same list of branches, run in a clone that has them. None of them
changes the repository.

**1. What each branch changes that runs or is trusted** (git-overlap's `git footprint`, then
`git surface`):

```
git for-each-ref --format='%(refname:short)' refs/heads/agent/ | git footprint main | git surface main
```

```
agent/ci	ci	A	.github/workflows/triage.yml
agent/deps	deps	M git-overlap 0.3.1->0.3.2	requirements.txt
agent/docs	hidden	<!-- line 4	docs/guide.md
```

`git surface` reads only the ref from each record and runs its own diff, so its report is the
same with or without `git footprint` in front. Its classes are `ci`, `deps`, `exec`, `hidden`,
`hook` and `install`; the `deps` detail shows `name old->new` for a pinned version.

**2. Paths from untrusted input to a secret, on branches that change CI** (`git surface`, then
`trust-path --ref`, which reads the workflows from git at the branch without checking it out):

```
git for-each-ref --format='%(refname:short)' refs/heads/agent/ | git surface main |
    awk -F'\t' '$2 == "ci" {print $1}' | uniq |
    while read -r ref; do echo "$ref:"; trust-path --ref "$ref"; done
```

```
agent/ci:
pull_request_target:github.event.pull_request.title	.github/workflows/triage.yml	label	Label	permissions:default
pull_request_target:github.event.pull_request.title	.github/workflows/triage.yml	label	Label	secrets.TRIAGE_TOKEN
```

The pull request title, in a workflow triggered by `pull_request_target`, reaches the step that
holds `secrets.TRIAGE_TOKEN`, and the job runs with default token permissions.

**3. Conflicts between branches** (git-overlap):

```
git for-each-ref --format='%(refname:short)' refs/heads/agent/ | git footprint main | git overlap | git pairmerge --onto main
```

```
agent/a	agent/b	conflict	CHANGELOG.md
agent/a	agent/b	conflict	app.py
agent/a	agent/b	conflict	tests/test_app.py
```

`git overlap` pairs the branches that change the same file and `git pairmerge` merges each pair in
memory, keeping those that conflict. With `--onto main`, a pair in which a branch also conflicts
with `main` is reported as `stale`: after one more commit to `CHANGELOG.md` on `main`, the same
pair gives `agent/a	agent/b	stale	CHANGELOG.md`.

**What it catches:** changes to CI, dependencies, build files, hooks and hidden content per branch,
paths from untrusted input to secrets and write permissions in changed workflows, and conflicting
or stale pairs. **What it does not catch:** nothing here runs the tests, so `agent/c`, which merges
cleanly but calls the function `agent/a` renames, is not reported.

## Landing a batch: git-overlap, then pr-merge-audit, then git surface

**1. git-overlap first.** Command 3 above, on the pull request branches, finds conflicting pairs
and branches stale against `main` in seconds, without running tests.

**2. pr-merge-audit next.** It merges the pull requests onto the base one after another, runs the
test suite after each merge, and keeps the batch that merged and passed as a branch. It needs a
configuration with the test command and a snapshot (`snapshots/DATE.json` and
`snapshots/DATE.bundle`), which `snapshot` writes for GitHub and which can be written by hand for
other hosts; see its [Commands](https://github.com/jacob-adm/pr-merge-audit/blob/main/docs/commands.md) page.

```
pr-merge-audit --config PROJECT/config.json build --snapshot snapshots/DATE.json \
    --emit-branch refs/heads/integration --emit-repo PATH/TO/CLONE
```

With the six branches as pull requests 1 to 6 (`agent/a`, `agent/b`, `agent/c`, `agent/ci`,
`agent/deps`, `agent/docs`), the report (`pr-merge-audit report --results results/DATE.json`)
says, in part:

```
### Result: 4 of 6 PRs merged together with every test passing

#1, #4, #5, #6

### Text conflicts; tests pass once swapped in (1)
...
| #2 | #1 |

### No text conflict, but a pair fails its tests (1)
...
| #1 + #3 | fail | `test_welcome` |
```

Pull request 3 (`agent/c`) has no text conflict, so git-overlap does not report it; its test fails
once pull request 1 is merged. `build` exits 0 when it ran, whatever it found: the findings are in
the report, and the branch's contents in the `emitted` key of the results file.

**3. git surface on the branch pr-merge-audit kept.** It shows what the combined batch changes in
what runs:

```
cd PATH/TO/CLONE
echo integration | git surface main
```

```
integration	ci	A	.github/workflows/triage.yml
integration	deps	M git-overlap 0.3.1->0.3.2	requirements.txt
integration	hidden	<!-- line 4	docs/guide.md
```

**What it catches:** conflicts and stale branches (git-overlap), test breakage between pull
requests that merge cleanly (pr-merge-audit), and what the batch changes in CI, dependencies,
build files, hooks and hidden content (git surface). **What it does not catch:** breakage the test
suite does not test for, and whether a change in what runs is safe: git surface reports the
change, not a verdict.

### Reading a pr-merge-audit snapshot with git-overlap

A pr-merge-audit snapshot bundle (`snapshots/DATE.bundle`) holds the base as `refs/audit/base`
and each pull request head as `refs/audit/pr/N`. Fetch those refs into a clone of the audited
repository and run git-overlap on them, from inside that clone. In the example repository of
[Commands](commands.md), pull requests 12, 15 and 17 are the branches `agent/a`, `agent/c` and
`agent/e`:

```sh
git fetch --quiet ../snapshots/2026-11-13.bundle 'refs/audit/*:refs/audit/*'
git for-each-ref --format='%(refname)' refs/audit/pr/ | git footprint refs/audit/base | git overlap
```

```text
refs/audit/pr/12	refs/audit/pr/15	CHANGELOG.md
refs/audit/pr/12	refs/audit/pr/15	tools/registry.py
```

## Other notes

**Line-oriented tools.** Every record is one line (or one NUL-terminated record with `-z`) of
TAB-separated fields, and the exit status follows `grep`: 0 nothing found, 1 found, 2 error. Any
program that reads lines works on the output: `sort`, `awk`, a CI script, an agent's shell.

**bug-age.** [bug-age](https://github.com/jacob-adm/bug-age) answers a different question: how
long bugs that are already fixed lived in a repository's history. It shares no data with
git-overlap. Both can run on the same repository, but there is no pipe between them.
