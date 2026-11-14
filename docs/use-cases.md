# Use cases

git-overlap run on the open pull requests of three public GitHub repositories. Every command
below ran as shown, and every output is copied unedited from that run. The pull request
snapshot was taken on 13 November 2026; the runs were made on 13 and 14 November 2026.
They were recorded with git-overlap 0.1.x, before `git pairmerge --onto` existed.
`scripts/reproduce-use-cases.sh WORKDIR` repeats them from the pinned data (see
[Reproduce](#reproduce)).

| Repository | Licence | Open pull requests against `main` | Base commit (`main`) | Shows |
| :--- | :--- | ---: | :--- | :--- |
| [sanity-io/sanity](https://github.com/sanity-io/sanity) | MIT | 221 | `08b506252df967bbe4f0e991485a51bf38a1aa79` | pull requests from coding-agent GitHub Apps, measured p |
| [microsoft/aspire](https://github.com/microsoft/aspire) | MIT | 333 | `f67e764c2de5c325abefe6271c2e5395a36ea984` | scale; a union merge that removes nothing |
| [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) | MIT | 60 (the newest) | `f431a5cd95cebf038e587715635b9d1a0a4d0c02` | one shared list file; what a union merge removes and hides |

Only pull requests whose base branch is `main` are included. A pull request opened against
another branch would be compared with `main` too, and its footprint would hold every
difference between the two branches.

## Setup

For each repository, with `URL`, `NAME`, `BASE` (the base commit above) and `DATA` (its file in
[data/](data/)) set:

```sh
git clone -q "$URL" "$NAME"
```

```sh
cd "$NAME"
git branch -f base "$BASE"
git for-each-ref --format='delete %(refname)' refs/pr/ | git update-ref --stdin
awk -F '\t' 'NR > 1 { print $2 }' "$DATA" | xargs -n 100 git fetch -q origin
awk -F '\t' 'NR > 1 { print "update refs/pr/" $1 " " $2 }' "$DATA" | git update-ref --stdin
```

`base` is `main` at the snapshot. `refs/pr/N` is the head commit of pull request N at the
snapshot, fetched by its object id, so later pushes do not change the result. Each data file
lists, per open pull request, its number, its head commit and the GitHub App that opened it
(empty when a user account opened it).

## Which pull requests no longer merge into the base?

Each pull request merged with `base`. A pull request that conflicts is called stale below: it
no longer merges cleanly into `main` and needs a rebase or a fix before it can land. The others
are fresh.

```sh
git for-each-ref --format='%(refname)' refs/pr/ |
  awk '{ print "base\t" $0 }' | git pairmerge > base-pairs.tsv
awk -F '\t' '$3 == "clean" { print $2 }' base-pairs.tsv > fresh.txt
awk -F '\t' '$3 == "conflict" { print $2 }' base-pairs.tsv | sort -u > stale.txt
printf 'open %d, fresh %d, stale %d\n' "$(git for-each-ref refs/pr/ | wc -l)" \
  "$(wc -l < fresh.txt)" "$(wc -l < stale.txt)"
```

sanity-io/sanity:

```text
open 221, fresh 129, stale 92
```

microsoft/aspire:

```text
open 333, fresh 181, stale 152
```

awesome-mcp-servers:

```text
open 60, fresh 60, stale 0
```

The files the stale pull requests conflict on, by number of pull requests:

```sh
awk -F '\t' '$3 == "conflict" { print $4 }' base-pairs.tsv |
  LC_ALL=C sort | uniq -c | LC_ALL=C sort -k1,1nr -k2 | head -5
```

sanity-io/sanity:

```text
   8 packages/sanity/src/core/config/prepareConfig.tsx
   7 pnpm-lock.yaml
   5 AGENTS.md
   3 dev/test-studio/package.json
   3 packages/sanity/src/core/config/types.ts
```

microsoft/aspire:

```text
   9 eng/Versions.props
   7 tests/Aspire.Cli.Tests/Utils/CliTestHelper.cs
   6 Directory.Packages.props
   6 global.json
   6 src/Aspire.Cli/Commands/AddCommand.cs
```

awesome-mcp-servers has no stale pull request, so the output is empty.

For one branch, the exit status is the answer: 1 if it no longer merges, 0 if it does. In
sanity-io/sanity, for the first stale and the first fresh pull request:

```sh
for ref in "$(head -1 stale.txt)" "$(head -1 fresh.txt)"; do
  printf 'base\t%s\n' "$ref" | git pairmerge > /dev/null; echo "exit $?"
done
```

```text
exit 1
exit 0
```

## Which open pull requests collide?

Overlapping pairs (pull requests that change the same file) merged with each other, first for
all open pull requests, then for the fresh ones only:

```sh
git for-each-ref --format='%(refname)' refs/pr/ |
  git footprint base | git overlap | git pairmerge > all-pairs.tsv
```

```sh
git footprint base < fresh.txt | git overlap | git pairmerge > fresh-pairs.tsv
```

```sh
for f in all-pairs.tsv fresh-pairs.tsv; do
  printf '%s: overlapping pairs %d, conflicting pairs %d\n' "$f" \
    "$(cut -f1,2 "$f" | sort -u | wc -l)" \
    "$(awk -F '\t' '$3 == "conflict" { print $1 FS $2 }' "$f" | sort -u | wc -l)"
done
```

sanity-io/sanity:

```text
all-pairs.tsv: overlapping pairs 1139, conflicting pairs 726
fresh-pairs.tsv: overlapping pairs 266, conflicting pairs 19
```

microsoft/aspire:

```text
all-pairs.tsv: overlapping pairs 1986, conflicting pairs 1568
fresh-pairs.tsv: overlapping pairs 225, conflicting pairs 32
```

awesome-mcp-servers:

```text
all-pairs.tsv: overlapping pairs 1770, conflicting pairs 66
fresh-pairs.tsv: overlapping pairs 1770, conflicting pairs 66
```

`git pairmerge` merges a pair at the pair's own merge base, so a stale pull request also
conflicts with every newer pull request that carries the change to `main` it conflicts with,
even where the two pull requests' own edits do not collide. In sanity-io/sanity, 707 of the 726
conflicting pairs among all open pull requests include a stale pull request.

Overlap is not conflict: in awesome-mcp-servers all 60 pull requests change README.md, so all
1770 pairs overlap, and 66 conflict.

## Hotspots: which files conflict most?

Conflicted files ranked by the number of conflicting pairs, all pull requests against fresh
ones (top five; ties sorted bytewise):

```sh
for f in all-pairs.tsv fresh-pairs.tsv; do
  echo "$f"
  awk -F '\t' '$3 == "conflict" { print $4 }' "$f" |
    LC_ALL=C sort | uniq -c | LC_ALL=C sort -k1,1nr -k2 | head -5
done
```

sanity-io/sanity:

```text
all-pairs.tsv
 175 pnpm-lock.yaml
 155 packages/sanity/src/core/config/prepareConfig.tsx
  90 packages/sanity/package.json
  86 AGENTS.md
  85 packages/sanity/src/core/config/types.ts
fresh-pairs.tsv
   2 AGENTS.md
   2 packages/sanity/src/core/config/prepareConfig.tsx
   2 packages/sanity/src/core/releases/components/dialog/ReleaseMetadataForm.tsx
   1 packages/@sanity/vision/src/redesign/components/VisionRedesign.test.tsx
   1 packages/@sanity/vision/src/redesign/components/VisionRedesign.tsx
```

microsoft/aspire:

```text
all-pairs.tsv
 183 eng/Versions.props
 146 src/Aspire.Cli/Commands/AddCommand.cs
 128 src/Aspire.Cli/Projects/DotNetAppHostProject.cs
 121 tests/Aspire.Cli.Tests/Utils/CliTestHelper.cs
 119 src/Aspire.Hosting/Aspire.Hosting.csproj
fresh-pairs.tsv
   3 .github/workflows/analyze-ci-failure.lock.yml
   2 .github/workflows/analyze-ci-failure.md
   2 .github/workflows/reproduce-flaky-tests.yml
   2 eng/pipelines/release-publish-nuget.yml
   2 src/Aspire.Hosting.Azure.Sandboxes/AzureSandboxContainerDeployment.cs
```

awesome-mcp-servers:

```text
all-pairs.tsv
  66 README.md
fresh-pairs.tsv
  66 README.md
```

## Measured p: what fraction of pull request pairs conflicts?

p is the number of conflicting pairs divided by all n(n-1)/2 pairs of the n fresh pull
requests. The overlap-filtered ratio below divides only the conflicting overlapping pairs by
that same denominator. It is a lower bound for p: a pair that shares no file can still conflict
(see [Limitations](limitations-and-security.md#limitations)), and such a pair is not counted.

```sh
awk -F '\t' '$3 == "conflict" { print $1 FS $2 }' fresh-pairs.tsv | sort -u | wc -l |
  awk -v n="$(wc -l < fresh.txt)" '{ m = n * (n - 1) / 2
    printf "conflicting overlapping pairs %d of %d fresh pairs, %.3f\n", $1, m, $1 / m }'
```

sanity-io/sanity:

```text
conflicting overlapping pairs 19 of 8256 fresh pairs, 0.002
```

microsoft/aspire:

```text
conflicting overlapping pairs 32 of 16290 fresh pairs, 0.002
```

awesome-mcp-servers:

```text
conflicting overlapping pairs 66 of 1770 fresh pairs, 0.037
```

p itself merges every pair of fresh pull requests. It ran for sanity-io/sanity (8256 pairs)
and awesome-mcp-servers (1770 pairs). For microsoft/aspire, 181 fresh pull requests give 16290
pairs; at about 0.1 seconds per pair (the all-pull-requests row of [Timings](#timings)) that is
about 27 minutes per run, and it was not run.

```sh
awk '{ r[NR] = $0 }
  END { for (i = 1; i <= NR; i++) for (j = i + 1; j <= NR; j++) print r[i] "\t" r[j] }' fresh.txt |
  git pairmerge |
  awk -F '\t' '{ all[$1 FS $2] = 1; if ($3 == "conflict") bad[$1 FS $2] = 1 }
    END { n = 0; c = 0; for (k in all) n++; for (k in bad) c++
          printf "conflicting pairs %d of %d, p = %.3f\n", c, n, n ? c / n : 0 }'
```

sanity-io/sanity:

```text
conflicting pairs 19 of 8256, p = 0.002
```

awesome-mcp-servers:

```text
conflicting pairs 66 of 1770, p = 0.037
```

In both, p equals the overlap-filtered ratio: no pair that shares no file conflicts.

## What would merge=union remove?

The conflicting fresh pairs merged again as if `.gitattributes` gave the named files
`merge=union`. Nothing is committed. A union merge keeps both sides' lines; that suits a file
with one entry per line and can make other files wrong, so the tests still decide.

sanity-io/sanity, for `AGENTS.md`:

```sh
printf '%s\n' 'AGENTS.md merge=union' > union.attrs
```

microsoft/aspire, for a test registry and the solution file, both lists of entries:

```sh
printf '%s\n' 'eng/github-ci/test-trigger-map.yml merge=union' 'Aspire.slnx merge=union' > union.attrs
```

awesome-mcp-servers, for its list:

```sh
printf '%s\n' 'README.md merge=union' > union.attrs
```

Then, in each repository:

```sh
awk -F '\t' '$3 == "conflict" { print $1 FS $2 }' fresh-pairs.tsv | sort -u > conflicting.tsv
git pairmerge --attributes union.attrs < conflicting.tsv > union-pairs.tsv
printf 'conflicting pairs: %d without, %d with the union attributes\n' \
  "$(wc -l < conflicting.tsv)" \
  "$(awk -F '\t' '$3 == "conflict" { print $1 FS $2 }' union-pairs.tsv | sort -u | wc -l)"
```

sanity-io/sanity:

```text
conflicting pairs: 19 without, 17 with the union attributes
```

microsoft/aspire:

```text
conflicting pairs: 32 without, 32 with the union attributes
```

awesome-mcp-servers:

```text
conflicting pairs: 66 without, 0 with the union attributes
```

In sanity-io/sanity, generated files would be regenerated after a merge rather than merged.
Dropping the lockfile and snapshot conflicts as well changes nothing here, because no fresh pair
conflicts on them:

```sh
awk -F '\t' '$3 == "conflict"' union-pairs.tsv |
  grep -v -e 'pnpm-lock\.yaml$' -e '\.snap$' | cut -f1,2 | sort -u | wc -l |
  awk '{ print "pairs left for a person: " $1 }'
```

```text
pairs left for a person: 17
```

In awesome-mcp-servers, the pull request in the most conflicting pairs:

```sh
tr '\t' '\n' < conflicting.tsv | sort | uniq -c | sort -k1,1nr | head -1 |
  while read -r count ref; do
    echo "in $count of $(awk 'END { print NR }' conflicting.tsv) conflicting pairs:" \
      "$(git diff --shortstat base..."$ref")"
  done
```

```text
in 59 of 66 conflicting pairs:  1 file changed, 1 insertion(+), 5095 deletions(-)
```

One pull request deletes 5,095 lines of README.md. A union merge combines it with the others
without a conflict, so union can hide large deletions.

## Pull requests opened by GitHub Apps

Pull requests opened by GitHub App accounts, by App name: how many are open, how many are
stale, and how many are in a conflicting pair of fresh pull requests.

```sh
awk -F '\t' 'NR > 1 && $3 != "" { print $3 }' "$DATA" | sort -u | while read -r app; do
  awk -F '\t' -v a="$app" '$3 == a { print "refs/pr/" $1 }' "$DATA" | sort > app.txt
  printf '%s: %d open, %d stale, %d in conflicting fresh pairs\n' "$app" \
    "$(wc -l < app.txt)" "$(sort stale.txt | comm -12 app.txt - | wc -l)" \
    "$(tr '\t' '\n' < conflicting.tsv | sort -u | comm -12 app.txt - | wc -l)"
done
```

sanity-io/sanity:

```text
app/copilot-swe-agent: 3 open, 0 stale, 0 in conflicting fresh pairs
app/cursor: 40 open, 7 stale, 0 in conflicting fresh pairs
app/sieve-sanity: 2 open, 2 stale, 0 in conflicting fresh pairs
app/squiggler-app: 5 open, 0 stale, 0 in conflicting fresh pairs
```

microsoft/aspire:

```text
app/aspire-repo-bot: 7 open, 2 stale, 0 in conflicting fresh pairs
app/copilot-pull-request-reviewer: 1 open, 0 stale, 0 in conflicting fresh pairs
app/copilot-swe-agent: 17 open, 11 stale, 1 in conflicting fresh pairs
app/dependabot: 16 open, 2 stale, 5 in conflicting fresh pairs
app/dotnet-maestro: 1 open, 1 stale, 0 in conflicting fresh pairs
app/dotnet-oneloc-localization: 1 open, 1 stale, 0 in conflicting fresh pairs
app/github-actions: 1 open, 0 stale, 0 in conflicting fresh pairs
```

Two of these Apps belong to coding agents: `app/cursor` is Cursor's GitHub App, which its
description says lets agents do work in the background, and `app/copilot-swe-agent` is the
GitHub App of GitHub Copilot's coding agent.
awesome-mcp-servers has no open pull request from a GitHub App in this snapshot.

## Timings

Wall-clock seconds, median [min–max] of three runs of the reproduce script, with warm caches.
Times depend on the machine, the cache state and the repository.

Machine: macOS 26.6.2, Apple M4 Pro, 14 cores (10 performance, 4 efficiency), 48 GB RAM;
git 2.50.1; Python 3.14.8; git-overlap 0.1.0.

Setup, measured once and dominated by the network: cloning took 36.6 minutes for
sanity-io/sanity (at about 1% CPU, so limited by the download), 33 seconds for
microsoft/aspire and 39 seconds for awesome-mcp-servers. The first fetch of the pinned
pull request heads took 2 seconds for sanity-io/sanity; it was not measured separately for the
other two. The table's fetch row re-fetches heads that are already present.

| Step | sanity-io/sanity | microsoft/aspire | awesome-mcp-servers |
| :--- | ---: | ---: | ---: |
| Repository (packed size, objects, refs) | 266.84 MiB, 537,522 objects, 2,775 refs | 251.86 MiB, 347,795 objects, 2,677 refs | 521.65 MiB, 43,386 objects, 70 refs |
| Fetch pinned heads (present) | 1.62 [1.56–1.92] | 2.34 [2.31–3.20] | 0.41 [0.37–0.43] |
| `base` with each pull request (221 / 333 / 60 pairs) | 10.89 [10.08–12.02] | 27.57 [26.40–30.22] | 1.35 [1.31–2.75] |
| All pull requests: footprint, overlap, pairmerge (1139 / 1986 / 1770 pairs) | 63.49 [60.27–68.48] | 215.00 [207.82–237.94] | 64.96 [63.79–76.19] |
| Fresh pull requests: footprint, overlap, pairmerge (266 / 225 / 1770 pairs) | 12.63 [12.39–12.68] | 16.25 [15.89–21.41] | 65.97 [64.65–78.02] |
| Union what-if on conflicting fresh pairs (19 / 32 / 66 pairs) | 1.04 [1.02–1.05] | 1.86 [1.77–1.93] | 3.22 [3.12–3.40] |
| Measured p, every fresh pair (8256 / not run / 1770 pairs) | 199.49 [193.11–200.37] | not run | 68.31 [63.09–70.94] |

The counting, ranking and filtering steps take 0.11 seconds or less each.

## Reproduce

```
scripts/reproduce-use-cases.sh WORKDIR
```

The script clones each repository into WORKDIR (skipped if the directory exists), fetches the
pull request heads pinned in [data/](data/) and runs every command on this page in page order,
printing each block's output after `### block ID` and its wall-clock time after `### time ID`.
It needs network access and takes about 15 minutes per run on the machine above, so it is not
part of the test suite; the suite checks that every command on this page appears verbatim in
the script. Results change only if a pinned commit can no longer be fetched.
