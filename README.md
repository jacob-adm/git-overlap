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
(4 November 2026). It covers the measurement side the article calls for; see
[Relation to the article](https://github.com/jacob-adm/git-overlap/blob/main/docs/article.md).

## Requirements and portability

- Python 3.8 or later, standard library only.
- git 2.38 or later. `git pairmerge` needs git 2.43 or later; on an older git it exits 2 with a
  message naming 2.43. git must be an executable named `git` on `PATH`.
- Linux, macOS and Windows, all tested in CI.
- The examples are POSIX `sh`; on Windows, run them from Git Bash. Windows has two further
  limits on refs and git shims; see [Limitations](https://github.com/jacob-adm/git-overlap/blob/main/docs/limitations-and-security.md#limitations).

## Install

From a release tag:

```
pip install git+https://github.com/jacob-adm/git-overlap@v0.3.0
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
git-footprint 0.3.0
```

Use `-h` for usage: `git footprint --help` is answered by git itself, which looks for a manual
page. Without installing, each command also runs from a clone as
`python3 -m git_overlap.footprint` (and `.overlap`, `.pairmerge`).

## Quick start

Inside the repository to check, list its branches one per line and pipe them through the three
commands. Replace `main` with the base branch and `refs/heads/agent/` with the refs to check,
for example `refs/remotes/origin/` for every branch fetched from `origin`. Below, the commands
run in the [example repository](https://github.com/jacob-adm/git-overlap/blob/main/docs/commands.md#example-repository).

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge --onto main | cut -f1-3 | uniq
```

```text
agent/a	agent/b	conflict
agent/a	agent/c	conflict
agent/a	agent/d	conflict
agent/b	agent/c	conflict
agent/b	agent/d	conflict
agent/c	agent/d	conflict
agent/f	agent/g	stale
```

Each line is a pair of branches that conflict when both land on `main`, one after the other. Drop
`| cut -f1-3 | uniq` to see the conflicted files. `agent/e` shares no file with any other
branch, so `git overlap` passes on no pair that includes it and `git pairmerge` never merges it.
`agent/f` no longer merges into `main`, so its pair is `stale`, not a conflict; the
[Stale branches](https://github.com/jacob-adm/git-overlap/blob/main/docs/recipes.md#stale-branches-which-branches-no-longer-merge-into-the-base)
recipe finds such branches. Without `--onto main`, each pair is merged at its own merge base,
where `agent/f` and `agent/g` conflict because `agent/g` carries the change to `main` that
`agent/f` conflicts with.

Output read on a terminal shows control bytes in names as `\xHH`; output piped to another program
is always the raw bytes.

## Documentation

- [Commands](https://github.com/jacob-adm/git-overlap/blob/main/docs/commands.md): each command's input, output, options and exit status, with
  examples.
- [Recipes](https://github.com/jacob-adm/git-overlap/blob/main/docs/recipes.md): pipelines that find hotspots, measure p and test merge drivers.
- [Use cases](https://github.com/jacob-adm/git-overlap/blob/main/docs/use-cases.md): real runs on three public repositories, with recorded output
  and timings.
- [Limitations and security](https://github.com/jacob-adm/git-overlap/blob/main/docs/limitations-and-security.md): what the commands miss, read
  and write.
- [Using it with other tools](https://github.com/jacob-adm/git-overlap/blob/main/docs/other-tools.md): pr-merge-audit, line-oriented tools and
  bug-age.
- [Relation to the article](https://github.com/jacob-adm/git-overlap/blob/main/docs/article.md): what the article finds and which parts are
  covered.
- [SPEC.md](https://github.com/jacob-adm/git-overlap/blob/main/SPEC.md): the full reference: every option, record format, error and edge case.

## Licence

Apache-2.0; see `LICENSE`.
