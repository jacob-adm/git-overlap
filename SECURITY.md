# Security

`git footprint` and `git overlap` write nothing, and `git overlap` runs no git at all.
`git pairmerge` writes nothing to the repository: objects it creates go to a private temporary
directory that is removed when it exits. The commands start git without a shell, and on a
terminal they show control bytes in a ref or path as `\xHH`. Merge drivers from git config still
run, by design, so on a machine that merges untrusted branches, make sure the git configuration
comes from you. See [Limitations and security](docs/limitations-and-security.md#security).

## Reporting a vulnerability

Please report security problems privately, through GitHub's "Report a vulnerability" button on the
repository's Security tab. Please do not open a public issue. You can expect a reply within a week.
