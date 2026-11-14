# Recipes

These pipelines answer the open questions of Xuchen Yao's article
["Rethinking Merging and Continous Integration for Agentic Workflows"](https://www.linkedin.com/pulse/rethinking-merging-continous-integration-agentic-workflows-xuchen-yao-jkaxc/)
(4 November 2026) for one repository: the git repository of the current directory. Run them from inside the repository whose branches
are to be checked; every command works on that repository's refs, as any git command does. The
questions are: in which shared files parallel branches collide, what fraction p of branch pairs
truly conflicts, and how many conflicts typed paths would remove. Each recipe is a shell
pipeline, not a separate program: the three commands combined with plain POSIX `sh`, git, `awk`,
`sort`, `uniq`, `cut`, `grep` and `printf`. As written they check the branches
`refs/heads/agent/*` against `main`, with the four typed paths of the
[example repository](commands.md#example-repository); the test suite runs every block below
there and checks its exact output.

## Footprint: which files does each branch change?

The files each agent branch changes, compared with its merge base with `main`.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ | git footprint main
```

## Who overlaps whom: which branches change the same files?

Pairs of agent branches that change at least one same file.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | cut -f1,2 | uniq
```

## Real conflicts: which pairs conflict, and on which files?

Which overlapping pairs conflict when merged with each other, and on which files.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge
```

## Hotspots: which files conflict most?

Conflicted files, ranked by the number of branch pairs that conflict on them.

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ |
  git footprint main | git overlap | git pairmerge |
  awk -F '\t' '$3 == "conflict" { print $4 }' |
  LC_ALL=C sort | uniq -c | LC_ALL=C sort -k1,1nr -k2 |
  awk '{ n = $1; sub(/^ *[0-9]+ /, ""); print n "\t" $0 }'
```

## Measured p: what fraction of branch pairs conflicts?

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

## What-if merge drivers: which conflicts would a union merge remove?

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

## Derived files: which conflicts are left for a person?

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

## Sequence collisions: which branches add files with the same sequence number?

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

## Pre-work check: does a branch share a file with any other branch?

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
