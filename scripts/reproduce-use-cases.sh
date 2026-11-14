#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# Re-run every command of docs/use-cases.md on the real repositories, to compare with the page.
#
# Usage: scripts/reproduce-use-cases.sh WORKDIR
#
# For each repository it clones into WORKDIR/NAME (skipped if that directory exists), points the
# branch `base` at the recorded base commit, fetches the pull request heads pinned in
# docs/data/NAME.tsv into refs/pr/N, then runs each command block of the page in the clone.
# Before each block it prints "### block ID"; after it "### time ID SECONDS" (wall clock).
# Setup steps print "### setup ...". The commands write their intermediate files (*.tsv, *.txt,
# union.attrs) into the clone's top directory.
#
# Needs network access, git, python3 (for timestamps) and git-overlap's three commands on PATH.
# It does not run in CI: it depends on GitHub and takes about 15 minutes per run.
set -u

here=$(cd "$(dirname "$0")/.." && pwd)
work=${1:?usage: scripts/reproduce-use-cases.sh WORKDIR}
mkdir -p "$work"
work=$(cd "$work" && pwd)

# Wall-clock timestamps in seconds; python3 because date has no sub-second format on every OS.
now() { python3 -c 'import time; print("%.3f" % time.time())'; }
since() { python3 -c 'import sys, time; print("%.2f" % (time.time() - float(sys.argv[1])))' "$1"; }

# step ID: run the block on standard input with sh, in the current directory, and time it.
step() {
  block=$work/.block.sh
  cat > "$block"
  echo "### block $1"
  t0=$(now)
  sh "$block"
  echo "### time $1 $(since "$t0")"
}

# prepare NAME URL BASE: clone once, then pin `base` and the pull request heads.
prepare() {
  NAME=$1 URL=$2 BASE=$3
  DATA=$here/docs/data/$NAME.tsv
  export NAME URL BASE DATA
  echo "### repository $NAME"
  cd "$work" || exit 2
  if [ ! -d "$NAME" ]; then
    t0=$(now)
git clone -q "$URL" "$NAME"
    echo "### setup clone $NAME $(since "$t0")"
  fi
  t0=$(now)
cd "$NAME"
git branch -f base "$BASE"
git for-each-ref --format='delete %(refname)' refs/pr/ | git update-ref --stdin
awk -F '\t' 'NR > 1 { print $2 }' "$DATA" | xargs -n 100 git fetch -q origin
awk -F '\t' 'NR > 1 { print "update refs/pr/" $1 " " $2 }' "$DATA" | git update-ref --stdin
  echo "### setup fetch $NAME $(since "$t0")"
  echo "### setup packed $(git count-objects -vH | awk '/size-pack/ { print $2 $3 }')" \
    "objects $(git count-objects -v | awk '/in-pack/ { print $2 }')" \
    "refs $(git for-each-ref | wc -l | tr -d ' ')"
}

# The blocks every repository runs, in page order, up to the union what-if.
analyse() {
step stale <<'BLOCK'
git for-each-ref --format='%(refname)' refs/pr/ |
  awk '{ print "base\t" $0 }' | git pairmerge > base-pairs.tsv
awk -F '\t' '$3 == "clean" { print $2 }' base-pairs.tsv > fresh.txt
awk -F '\t' '$3 == "conflict" { print $2 }' base-pairs.tsv | sort -u > stale.txt
printf 'open %d, fresh %d, stale %d\n' "$(git for-each-ref refs/pr/ | wc -l)" \
  "$(wc -l < fresh.txt)" "$(wc -l < stale.txt)"
BLOCK
step stale-files <<'BLOCK'
awk -F '\t' '$3 == "conflict" { print $4 }' base-pairs.tsv |
  LC_ALL=C sort | uniq -c | LC_ALL=C sort -k1,1nr -k2 | head -5
BLOCK
step all-pairs <<'BLOCK'
git for-each-ref --format='%(refname)' refs/pr/ |
  git footprint base | git overlap | git pairmerge > all-pairs.tsv
BLOCK
step fresh-pairs <<'BLOCK'
git footprint base < fresh.txt | git overlap | git pairmerge > fresh-pairs.tsv
BLOCK
step counts <<'BLOCK'
for f in all-pairs.tsv fresh-pairs.tsv; do
  printf '%s: overlapping pairs %d, conflicting pairs %d\n' "$f" \
    "$(cut -f1,2 "$f" | sort -u | wc -l)" \
    "$(awk -F '\t' '$3 == "conflict" { print $1 FS $2 }' "$f" | sort -u | wc -l)"
done
BLOCK
step hotspots <<'BLOCK'
for f in all-pairs.tsv fresh-pairs.tsv; do
  echo "$f"
  awk -F '\t' '$3 == "conflict" { print $4 }' "$f" |
    LC_ALL=C sort | uniq -c | LC_ALL=C sort -k1,1nr -k2 | head -5
done
BLOCK
step overlap-p <<'BLOCK'
awk -F '\t' '$3 == "conflict" { print $1 FS $2 }' fresh-pairs.tsv | sort -u | wc -l |
  awk -v n="$(wc -l < fresh.txt)" '{ m = n * (n - 1) / 2
    printf "conflicting overlapping pairs %d of %d fresh pairs, %.3f\n", $1, m, $1 / m }'
BLOCK
}

# The union what-if, after a repository's own union.attrs block.
union() {
step union <<'BLOCK'
awk -F '\t' '$3 == "conflict" { print $1 FS $2 }' fresh-pairs.tsv | sort -u > conflicting.tsv
git pairmerge --attributes union.attrs < conflicting.tsv > union-pairs.tsv
printf 'conflicting pairs: %d without, %d with the union attributes\n' \
  "$(wc -l < conflicting.tsv)" \
  "$(awk -F '\t' '$3 == "conflict" { print $1 FS $2 }' union-pairs.tsv | sort -u | wc -l)"
BLOCK
}

# p over every pair of fresh pull requests, not only the overlapping ones.
measured_p() {
step measured-p <<'BLOCK'
awk '{ r[NR] = $0 }
  END { for (i = 1; i <= NR; i++) for (j = i + 1; j <= NR; j++) print r[i] "\t" r[j] }' fresh.txt |
  git pairmerge |
  awk -F '\t' '{ all[$1 FS $2] = 1; if ($3 == "conflict") bad[$1 FS $2] = 1 }
    END { n = 0; c = 0; for (k in all) n++; for (k in bad) c++
          printf "conflicting pairs %d of %d, p = %.3f\n", c, n, n ? c / n : 0 }'
BLOCK
}

# Pull requests opened by GitHub App accounts: how many, how many stale, how many collide.
apps() {
step apps <<'BLOCK'
awk -F '\t' 'NR > 1 && $3 != "" { print $3 }' "$DATA" | sort -u | while read -r app; do
  awk -F '\t' -v a="$app" '$3 == a { print "refs/pr/" $1 }' "$DATA" | sort > app.txt
  printf '%s: %d open, %d stale, %d in conflicting fresh pairs\n' "$app" \
    "$(wc -l < app.txt)" "$(sort stale.txt | comm -12 app.txt - | wc -l)" \
    "$(tr '\t' '\n' < conflicting.tsv | sort -u | comm -12 app.txt - | wc -l)"
done
BLOCK
}

prepare sanity https://github.com/sanity-io/sanity 08b506252df967bbe4f0e991485a51bf38a1aa79
analyse
step one-branch <<'BLOCK'
for ref in "$(head -1 stale.txt)" "$(head -1 fresh.txt)"; do
  printf 'base\t%s\n' "$ref" | git pairmerge > /dev/null; echo "exit $?"
done
BLOCK
step union-attrs <<'BLOCK'
printf '%s\n' 'AGENTS.md merge=union' > union.attrs
BLOCK
union
step derived <<'BLOCK'
awk -F '\t' '$3 == "conflict"' union-pairs.tsv |
  grep -v -e 'pnpm-lock\.yaml$' -e '\.snap$' | cut -f1,2 | sort -u | wc -l |
  awk '{ print "pairs left for a person: " $1 }'
BLOCK
apps
measured_p

prepare aspire https://github.com/microsoft/aspire f67e764c2de5c325abefe6271c2e5395a36ea984
analyse
step union-attrs <<'BLOCK'
printf '%s\n' 'eng/github-ci/test-trigger-map.yml merge=union' 'Aspire.slnx merge=union' > union.attrs
BLOCK
union
apps

prepare awesome-mcp-servers https://github.com/punkpeye/awesome-mcp-servers \
  f431a5cd95cebf038e587715635b9d1a0a4d0c02
analyse
step union-attrs <<'BLOCK'
printf '%s\n' 'README.md merge=union' > union.attrs
BLOCK
union
step large-deletion <<'BLOCK'
tr '\t' '\n' < conflicting.tsv | sort | uniq -c | sort -k1,1nr | head -1 |
  while read -r count ref; do
    echo "in $count of $(awk 'END { print NR }' conflicting.tsv) conflicting pairs:" \
      "$(git diff --shortstat base..."$ref")"
  done
BLOCK
measured_p
