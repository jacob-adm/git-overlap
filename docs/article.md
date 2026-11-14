# Relation to the article

Xuchen Yao's article
["Rethinking Merging and Continous Integration for Agentic Workflows"](https://www.linkedin.com/pulse/rethinking-merging-continous-integration-agentic-workflows-xuchen-yao-jkaxc/)
(4 November 2026) finds that pull requests from many parallel agents collide in a few shared
files (a registry, a changelog, a generated API description, numbered migrations), that landing
them one at a time re-tests everything still waiting, so N pull requests cost N(N+1)/2 full CI
runs, and that typed paths (set, log, derived, sequence) and merge drivers remove most textual
conflicts, while the fraction p of pull-request pairs that truly overlap is unmeasured.

git-overlap covers the measurement side it calls for. Run inside any git repository, it
measures textual collisions between that repository's branches before any of them is merged:
which branches change the same files, which pairs conflict when merged with each other and on
which files, which files conflict most, and p; and it shows which of those conflicts a merge
driver would remove. It does not run builds or tests, and it does not implement the system the
article proposes.

## Coverage

How each point of the article maps to git-overlap and to pr-merge-audit (a separate project, see
[Using it with other tools](other-tools.md)). Coverage is Yes, Partly or Not addressed.

### The night the article describes

| Article point | What covers it | Coverage |
| :--- | :--- | :--- |
| Pull requests collided in the same five shared files | Hotspots recipe: conflicted files ranked by conflicting pairs | Yes |
| Agents' wait loops watched CI, not whether the branch still merged | `printf 'main\tBRANCH\n' \| git pairmerge` exits 1 once BRANCH no longer merges into `main` | Yes |
| Generated files were merged instead of regenerated | Derived files recipe separates conflicts on generated files (a pattern list) from the rest; nothing is regenerated | Partly |
| Nine pull requests chained by hand into one integration pull request | pr-merge-audit `build --emit-branch` keeps the batch that merged and passed as one branch | Yes (pr-merge-audit) |
| 10 pull requests cost 55 CI runs | pr-merge-audit's report states N(N+1)/2 against the runs its build used; `estimate` gives both in hours | Yes (pr-merge-audit) |

### The six assumptions

| Article point | What covers it | Coverage |
| :--- | :--- | :--- |
| 1. CI evidence is keyed to the whole tree | Nothing; this needs input-keyed build tools | Not addressed |
| 2. Merging is line-based | `git pairmerge --attributes` shows which conflicts merge drivers would remove; drivers configured in git config (for example Mergiraf, installed separately) are evaluated too. No typed merge is implemented | Partly |
| 3. Conflicts cannot be handed off | Nothing | Not addressed |
| 4. Intent is invisible until merge | `git overlap --with BRANCH` (Pre-work check recipe) reports by exit status whether a branch shares a file with another; file level, committed and fetched branches only | Partly |
| 5. Landing is serial | pr-merge-audit emits one tested batch; there is no continuous merge queue | Partly |
| 6. Evidence does not travel with the code | Nothing; pr-merge-audit's manifest of included and excluded pull requests is not signed | Not addressed |

### The open questions

| Article point | What covers it | Coverage |
| :--- | :--- | :--- |
| p is a guess | Measured p recipe, for textual conflicts only | Yes, for textual conflicts |
| The 28% textual conflict rate (AgenticFlict) | Pair each branch with `main` in `git pairmerge` to get the rate for one repository | Yes |
| Type declarations can be wrong | `--attributes` tries a declaration on real pairs before it is committed | Partly |
| Replay one night three ways and compare | pr-merge-audit replays a frozen snapshot and prices serial landing; git-overlap measures p and the merge-driver what-if; no timed comparison of three stacks | Partly |

The system the article proposes is not implemented: no change objects, input-keyed evidence,
continuous speculative merging, conflict objects, signed provenance or test budget.
