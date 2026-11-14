# git-overlap

Small git subcommands that show where branches collide before anyone merges them. They read
records on standard input and write records on standard output, so they combine with each other
and with `sort`, `uniq`, `awk` and `wc`.

- `git footprint BASE`: the files each ref changes.
- `git overlap`: pairs of refs that touch the same file.
- `git pairmerge`: whether each pair of refs merges cleanly or conflicts, and on which files,
  computed in memory without touching the repository.

The full interface is in [SPEC.md](SPEC.md).

## Requirements

Python 3.8 or later (standard library only). git 2.38 or later; `git pairmerge` needs git 2.43
or later and says so on older versions.

## Install

```
pip install .
git footprint -h
```

`git footprint --help` is answered by git itself, which looks for a manual page; use `-h`.

## Licence

Apache-2.0.
