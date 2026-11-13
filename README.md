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

## Recipes

These pipelines answer, on your own repository, the open questions of Xuchen Yao, "Rethinking
Merging and Continuous Integration for Agentic Workflows" (2026): in which shared files parallel
branches collide, what fraction p of branch pairs truly conflicts, and how many conflicts typed
paths would remove. They use plain POSIX `sh`, git, `awk`, `sort`, `uniq`, `cut`, `grep` and
`printf`. The examples compare the branches `refs/heads/agent/*` against `main`, and use four typed
paths: `tools/registry.py` is a set (one entry per line, order irrelevant), `CHANGELOG.md` a log,
`openapi.json` derived (generated) and `migrations/` a sequence (numbered files). The test suite
runs every block below against a fixture repository and checks its exact output.

### Footprint

The files each agent branch changes, compared with its merge base with `main`.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ | git footprint main
```

### Who overlaps whom

Pairs of agent branches that change at least one same file.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | cut -f1,2 | uniq
```

### Real conflicts

Which overlapping pairs conflict when merged with each other, and on which files.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge
```

### Hotspots

Conflicted files, ranked by the number of branch pairs that conflict on them.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge |
  awk -F '\t' '$3 == "conflict" { print $4 }' |
  LC_ALL=C sort | uniq -c | LC_ALL=C sort -k1,1nr -k2 |
  awk '{ n = $1; sub(/^ *[0-9]+ /, ""); print n "\t" $0 }'
```

### Measured p

p = conflicting pairs / all pairs. The denominator counts every unordered pair of distinct
branches, n(n-1)/2 for n branches, whether or not they overlap; each pair is merged with the other
at their own merge base (not with `main`), and counts as conflicting if git reports any conflict.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  awk '{ r[NR] = $0 }
    END { for (i = 1; i <= NR; i++) for (j = i + 1; j <= NR; j++) print r[i] "\t" r[j] }' |
  git pairmerge |
  awk -F '\t' '{ all[$1 FS $2] = 1; if ($3 == "conflict") bad[$1 FS $2] = 1 }
    END { n = 0; c = 0; for (k in all) n++; for (k in bad) c++
          printf "conflicting pairs %d of %d, p = %.2f\n", c, n, n ? c / n : 0 }'
```

### What-if merge drivers

The same pairs as Real conflicts, merged as if `.gitattributes` marked the set and the log
`merge=union` (nothing is committed). A union merge keeps both sides' lines: that is safe for a
file with one entry per line, but it can make other files wrong (for example a list entry without
its trailing comma), so the tests still decide correctness.

```sh
attrs=$(mktemp)
printf '%s\n' 'tools/registry.py merge=union' 'CHANGELOG.md merge=union' > "$attrs"
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge --attributes "$attrs"
rm -f "$attrs"
```

### Derived files

Generated files are regenerated after a merge, never merged, so their conflicts are dropped with a
pattern list. With the union attributes as well, what is left needs a person.

```sh
attrs=$(mktemp)
printf '%s\n' 'tools/registry.py merge=union' 'CHANGELOG.md merge=union' > "$attrs"
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge --attributes "$attrs" |
  awk -F '\t' '$3 == "conflict"' | grep -v -e 'openapi\.json$' -e '_pb2\.py$'
rm -f "$attrs"
```

### Sequence collisions

Files added on different branches in the same directory with the same leading number, such as two
migrations `0002_*`. git merges them without a conflict because the files differ; the collision
shows up later, when both run.

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

### Pre-work check

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

## Licence

Apache-2.0.
